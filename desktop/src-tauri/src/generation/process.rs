use std::io::{BufReader, Read, Write};
use std::sync::atomic::Ordering;
use std::time::{Duration, Instant};

use tauri::{Emitter, Manager, Window};

use super::launcher::{
    format_request_payload, prepare_child_env, resolve_source_layout, run_prerequisite_probe,
};
use super::protocol::{
    WireResponse, WireResponseData, MAX_STDERR_AGGREGATE_BYTES, MAX_STDERR_LINE_BYTES,
    MAX_STDOUT_BYTES,
};
use super::state::{is_valid_phase_transition, AppState, GenerationState};
use super::types::{
    CleanupState, CommandError, EngineStatus, GenerationInput, ResultAccess, RunPhase, RunState,
};
use super::windows;

pub const RUN_DEADLINE_SECS: u64 = 180;
pub const CANCELLATION_GRACE_SECS: u64 = 10;
pub const POLL_INTERVAL_MS: u64 = 50;

#[derive(Debug)]
enum StderrEvent {
    Progress(RunPhase),
    FatalDiagnostic,
    Finished,
    Error(CommandError),
}

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct StderrProgressRecord {
    #[serde(rename = "type")]
    record_type: String,
    phase: String,
    message: String,
}

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct StderrDiagnosticRecord {
    #[serde(rename = "type")]
    record_type: String,
    phase: String,
    message: String,
}

pub const KNOWN_WARNING_PRESENTATIONS: &[(&str, &str)] = &[
    (
        "PREVIEW_EXPORT_FAILED",
        "Preview image generation was skipped or unavailable; CAD geometry exported successfully.",
    ),
    (
        "PREVIEW_CLEANUP_FAILED",
        "Temporary preview export file cleanup failed.",
    ),
    (
        "VERSION_METADATA_UNAVAILABLE",
        "CAD runtime did not report version metadata.",
    ),
    (
        "GATE_POLICY_RELAXED",
        "Feature validation evaluated under relaxed capability-first policy.",
    ),
    (
        "GATE_POLICY_SHADOW",
        "Feature validation evaluated under shadow gate policy.",
    ),
    (
        "CANONICAL_MULTI_HOLE_FAMILY",
        "Multiple circular through-holes accepted by canonical validation.",
    ),
    (
        "UNKNOWN_BOOLEAN_OPERATION",
        "Unrecognized boolean operation fell back to default union.",
    ),
    (
        "UNKNOWN_PLACEMENT_MODE",
        "Unrecognized placement mode fell back to default absolute placement.",
    ),
    (
        "UNKNOWN_FACE_ALIAS",
        "Unrecognized face alias fell back to default sketch plane.",
    ),
    (
        "UNKNOWN_SLOT_ORIENTATION",
        "Unrecognized slot orientation fell back to default horizontal orientation.",
    ),
    (
        "UNKNOWN_SWEEP_PATH_TYPE",
        "Unrecognized sweep path type fell back to default.",
    ),
    (
        "UNKNOWN_SWEEP_SECTION_TYPE",
        "Unrecognized sweep section type fell back to default.",
    ),
    (
        "UNKNOWN_SWEEP_SECTION_POSITION",
        "Unrecognized sweep section position fell back to default.",
    ),
];

pub const GENERIC_WARNING_MESSAGE: &str = "A non-fatal diagnostic warning was recorded.";

pub fn sanitize_warning(raw: &str) -> String {
    let trimmed = raw.trim();

    // 1. Check if raw matches known warning codes or exact descriptions
    for (code, desc) in KNOWN_WARNING_PRESENTATIONS {
        if trimmed.eq_ignore_ascii_case(code) || trimmed == *desc {
            return (*desc).to_string();
        }
    }

    if trimmed == GENERIC_WARNING_MESSAGE {
        return GENERIC_WARNING_MESSAGE.to_string();
    }

    // 2. Check if raw matches canonical safe DefaultApplied presentation: "Default applied to <path>: applied <val>."
    const PREFIX: &str = "Default applied to ";
    const MID: &str = ": applied ";
    if let Some(rest) = trimmed.strip_prefix(PREFIX) {
        if let Some((path, val_part)) = rest.split_once(MID) {
            if let Some(val) = val_part.strip_suffix('.') {
                let is_safe_path = !path.is_empty()
                    && path.len() <= 64
                    && path.starts_with(|c: char| c.is_ascii_alphabetic())
                    && path.chars().all(|c| {
                        c.is_ascii_alphanumeric() || c == '_' || c == '.' || c == '[' || c == ']'
                    });

                let is_safe_val = !val.is_empty()
                    && val.len() <= 32
                    && val.chars().all(|c| {
                        c.is_ascii_alphanumeric() || c == '_' || c == '.' || c == '+' || c == '-'
                    });

                let contains_secret = |s: &str| -> bool {
                    let lower = s.to_ascii_lowercase();
                    lower.contains("aiza")
                        || lower.contains("key")
                        || lower.contains("secret")
                        || lower.contains("password")
                        || lower.contains("token")
                        || lower.contains("sentinel")
                        || lower.contains("bearer")
                };

                if is_safe_path && is_safe_val && !contains_secret(path) && !contains_secret(val) {
                    return trimmed.chars().take(128).collect();
                }
            }
        }
    }

    // Any arbitrary/unknown text, paths, prompts, or tokens are replaced by the safe generic message
    GENERIC_WARNING_MESSAGE.to_string()
}

pub fn map_curated_error(code: &str) -> String {
    match code {
        "INVALID_SCHEMA" => "INVALID_SCHEMA: Request failed schema validation.".to_string(),
        "UNSUPPORTED_REQUEST" => {
            "UNSUPPORTED_REQUEST: Request kind or parameters are unsupported.".to_string()
        }
        "PROMPT_INTERPRETATION_FAILED" => {
            "PROMPT_INTERPRETATION_FAILED: Prompt interpretation failed to produce a CAD plan."
                .to_string()
        }
        "CAD_PLAN_REJECTED" => {
            "CAD_PLAN_REJECTED: CAD plan was rejected by safety or geometry rules.".to_string()
        }
        "CAD_EXECUTION_FAILED" => "CAD_EXECUTION_FAILED: CAD engine execution failed.".to_string(),
        "ARTIFACT_EXPORT_FAILED" => {
            "ARTIFACT_EXPORT_FAILED: Failed to export generated artifacts.".to_string()
        }
        "OUTPUT_PATH_NOT_ALLOWED" => {
            "OUTPUT_PATH_NOT_ALLOWED: Target output path is not allowed.".to_string()
        }
        "INTERNAL_ERROR" => "INTERNAL_ERROR: Internal engine error occurred.".to_string(),
        "NATIVE_QA_BLOCKED" => {
            "NATIVE_QA_BLOCKED: Native QA checks blocked generation.".to_string()
        }
        "PAYLOAD_TOO_LARGE" => {
            "PAYLOAD_TOO_LARGE: Request payload exceeded maximum size.".to_string()
        }
        "TARGET_ALREADY_EXISTS" => {
            "TARGET_ALREADY_EXISTS: Output artifact targets already exist.".to_string()
        }
        _ => "INTERNAL_ERROR: An unknown engine error was reported.".to_string(),
    }
}

fn with_state<F, R>(window: &Window, f: F) -> Option<R>
where
    F: FnOnce(&mut GenerationState) -> R,
{
    let app_state = window.state::<AppState>();
    let res = match app_state.lock() {
        Ok(mut guard) => Some(f(&mut guard)),
        Err(_) => None,
    };
    res
}

#[allow(clippy::too_many_arguments)]
fn finalize_terminal(
    window: &Window,
    request_id: &str,
    state: RunState,
    engine_status: Option<EngineStatus>,
    reason: Option<String>,
    result_access: ResultAccess,
    cleanup: CleanupState,
    response_data: Option<WireResponseData>,
    warnings: Vec<String>,
) {
    let result = with_state(window, |guard| {
        let mut destroy = false;
        if let Some(run) = &mut guard.active_run {
            if run.request_id == request_id {
                run.state = state;
                run.engine_status = engine_status;
                run.reason = reason;
                run.result_access = result_access;
                run.cleanup = cleanup;
                run.response_data = response_data;
                run.warnings.extend(warnings);
                destroy = run.close_after_cleanup && cleanup == CleanupState::NoFailureObserved;
            }
        }
        guard.revision += 1;
        if destroy {
            guard.terminating = true;
        }
        (destroy, guard.to_snapshot())
    });

    let (should_destroy, snap) = match result {
        Some(pair) => pair,
        None => return,
    };

    if should_destroy {
        if let Err(e) = window.destroy() {
            let fallback_snap = with_state(window, |g| {
                g.terminating = false;
                if let Some(run) = &mut g.active_run {
                    run.warnings.push(format!("Window destroy failed: {e}"));
                }
                g.revision += 1;
                g.to_snapshot()
            });
            if let Some(fs) = fallback_snap {
                let _ = window.emit("generation-state", &fs);
            }
        }
        return;
    }

    let _ = window.emit("generation-state", &snap);
}

/// Spawns the background supervisor thread for an active generation run.
pub fn spawn_generation_supervisor(window: Window, request_id: String) {
    let thread_name = format!(
        "gen-sup-{}",
        &request_id[..std::cmp::min(12, request_id.len())]
    );
    let _ = std::thread::Builder::new()
        .name(thread_name)
        .spawn(move || {
            run_supervisor(window, request_id);
        });
}

fn join_worker_bounded(handle: std::thread::JoinHandle<()>, timeout: Duration) -> bool {
    let start = Instant::now();
    while start.elapsed() < timeout {
        if handle.is_finished() {
            return handle.join().is_ok();
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    false
}

fn reap_workers_bounded(
    stdout_handle: std::thread::JoinHandle<()>,
    stderr_handle: std::thread::JoinHandle<()>,
    stdin_handle: std::thread::JoinHandle<()>,
    timeout_per_worker: Duration,
) -> bool {
    let ok_in = join_worker_bounded(stdin_handle, timeout_per_worker);
    let ok_out = join_worker_bounded(stdout_handle, timeout_per_worker);
    let ok_err = join_worker_bounded(stderr_handle, timeout_per_worker);
    ok_in && ok_out && ok_err
}

fn run_supervisor(window: Window, request_id: String) {
    let overall_start = Instant::now();
    let init_result = with_state(&window, |guard| {
        let run = match &guard.active_run {
            Some(r) if r.request_id == request_id => r,
            _ => return Err("The requested run was not found or is no longer active.".to_string()),
        };

        let output_path = match &guard.selected_output_path {
            Some(p) => p.clone(),
            None => return Err("Selected output directory is missing.".to_string()),
        };

        Ok((
            run.input.clone(),
            output_path,
            guard.session_key.clone(),
            run.cancel_token.clone(),
        ))
    });

    let (input, output_path, session_key, cancel_token) = match init_result {
        Some(Ok(data)) => data,
        Some(Err(msg)) => {
            finalize_terminal(
                &window,
                &request_id,
                RunState::Failed,
                None,
                Some(msg),
                ResultAccess::None,
                CleanupState::NotStarted,
                None,
                vec![],
            );
            return;
        }
        None => {
            finalize_terminal(
                &window,
                &request_id,
                RunState::Failed,
                None,
                Some(
                    "Failed to acquire application lock for supervisor initialization.".to_string(),
                ),
                ResultAccess::None,
                CleanupState::NotStarted,
                None,
                vec![],
            );
            return;
        }
    };

    // Step 1: Pre-launch cancellation check
    if cancel_token.load(Ordering::SeqCst) {
        finalize_terminal(
            &window,
            &request_id,
            RunState::Cancelled,
            None,
            Some("Cancellation requested before process launch.".to_string()),
            ResultAccess::None,
            CleanupState::NoFailureObserved,
            None,
            vec![],
        );
        return;
    }

    // Step 2: Resolve source layout
    let layout = match resolve_source_layout() {
        Ok(l) => l,
        Err(e) => {
            finalize_terminal(
                &window,
                &request_id,
                RunState::Failed,
                None,
                Some(e.message),
                ResultAccess::None,
                CleanupState::NotStarted,
                None,
                vec![],
            );
            return;
        }
    };

    // Step 3: Prerequisite probe
    if let Err(probe_err) = run_prerequisite_probe(&layout, &input) {
        finalize_terminal(
            &window,
            &request_id,
            RunState::Failed,
            None,
            Some(probe_err.error.message),
            ResultAccess::None,
            probe_err.cleanup,
            None,
            vec![],
        );
        return;
    }

    // Step 4: Post-probe cancellation check
    if cancel_token.load(Ordering::SeqCst) {
        finalize_terminal(
            &window,
            &request_id,
            RunState::Cancelled,
            None,
            Some("Cancellation requested before engine launch.".to_string()),
            ResultAccess::None,
            CleanupState::NoFailureObserved,
            None,
            vec![],
        );
        return;
    }

    // Step 5: Format payload & prepare environment
    let payload = match format_request_payload(&request_id, &input) {
        Ok(p) => p,
        Err(e) => {
            finalize_terminal(
                &window,
                &request_id,
                RunState::Failed,
                None,
                Some(e.message),
                ResultAccess::None,
                CleanupState::NotStarted,
                None,
                vec![],
            );
            return;
        }
    };

    let is_prompt = matches!(input, GenerationInput::PromptToCad { .. });
    let child_env = prepare_child_env(&layout, &output_path, session_key.as_deref(), is_prompt);

    // Step 6: Spawn engine child process
    let mut child = match windows::spawn_engine_process(
        &layout.python_exe,
        &["-m", "ipc"],
        &layout.repo_root,
        &child_env,
    ) {
        Ok(c) => c,
        Err(e) => {
            finalize_terminal(
                &window,
                &request_id,
                RunState::Failed,
                None,
                Some(e.message),
                ResultAccess::None,
                CleanupState::NotStarted,
                None,
                vec![],
            );
            return;
        }
    };

    // Step 7: Transition to Running state
    let run_snap = with_state(&window, |guard| {
        if let Some(run) = &mut guard.active_run {
            if run.request_id == request_id && run.state == RunState::Starting {
                run.state = RunState::Running;
                guard.revision += 1;
                return Some(guard.to_snapshot());
            }
        }
        None
    })
    .flatten();

    if let Some(s) = run_snap {
        let _ = window.emit("generation-state", &s);
    }

    // Step 8: Spawn concurrent reader threads for stdout and stderr before writing stdin
    let (stdout_tx, stdout_rx) = std::sync::mpsc::channel::<Result<Vec<u8>, CommandError>>();
    let mut stdout_file = child
        .stdout_read
        .take()
        .expect("Child stdout handle missing");
    let stdout_handle = std::thread::spawn(move || {
        let mut buffer = Vec::new();
        let mut chunk = [0u8; 8192];
        loop {
            match stdout_file.read(&mut chunk) {
                Ok(0) => break,
                Ok(n) => {
                    if buffer.len() + n > MAX_STDOUT_BYTES {
                        let _ = stdout_tx.send(Err(CommandError::new(
                            "TRANSPORT_ERROR",
                            "Stdout exceeded maximum permitted buffer limit (1 MiB).",
                        )));
                        return;
                    }
                    buffer.extend_from_slice(&chunk[..n]);
                }
                Err(e) => {
                    let _ = stdout_tx.send(Err(CommandError::new(
                        "TRANSPORT_ERROR",
                        format!("Stdout read error: {e}"),
                    )));
                    return;
                }
            }
        }
        let _ = stdout_tx.send(Ok(buffer));
    });

    let (stderr_tx, stderr_rx) = std::sync::mpsc::channel::<StderrEvent>();
    let stderr_file = child
        .stderr_read
        .take()
        .expect("Child stderr handle missing");
    let stderr_handle = std::thread::spawn(move || {
        let mut reader = BufReader::new(stderr_file);
        let mut total_bytes = 0;
        let mut line_buf = Vec::new();
        let mut last_phase: Option<RunPhase> = None;

        loop {
            let mut byte = [0u8; 1];
            match reader.read(&mut byte) {
                Ok(0) => break,
                Ok(_) => {
                    total_bytes += 1;
                    if total_bytes > MAX_STDERR_AGGREGATE_BYTES {
                        let _ = stderr_tx.send(StderrEvent::Error(CommandError::new(
                            "TRANSPORT_ERROR",
                            "Stderr exceeded maximum permitted aggregate buffer limit.",
                        )));
                        return;
                    }
                    if byte[0] == b'\n' {
                        if line_buf.ends_with(b"\r") {
                            line_buf.pop();
                        }

                        let line_str = match std::str::from_utf8(&line_buf) {
                            Ok(s) => s,
                            Err(_) => {
                                let _ = stderr_tx.send(StderrEvent::Error(CommandError::new(
                                    "TRANSPORT_ERROR",
                                    "Stderr contained invalid non-UTF-8 bytes.",
                                )));
                                return;
                            }
                        };
                        let line = line_str.trim().to_string();
                        line_buf.clear();

                        if line.is_empty() {
                            let _ = stderr_tx.send(StderrEvent::Error(CommandError::new(
                                "TRANSPORT_ERROR",
                                "Stderr contained an invalid empty record.",
                            )));
                            return;
                        }

                        let val: serde_json::Value = match serde_json::from_str(&line) {
                            Ok(v) => v,
                            Err(_) => {
                                let _ = stderr_tx.send(StderrEvent::Error(CommandError::new(
                                    "TRANSPORT_ERROR",
                                    "Stderr contained invalid JSON record.",
                                )));
                                return;
                            }
                        };

                        let record_type = match val.get("type").and_then(|t| t.as_str()) {
                            Some(t) => t,
                            None => {
                                let _ = stderr_tx.send(StderrEvent::Error(CommandError::new(
                                    "TRANSPORT_ERROR",
                                    "Stderr record missing required 'type' field.",
                                )));
                                return;
                            }
                        };

                        match record_type {
                            "progress" => {
                                let prog: StderrProgressRecord = match serde_json::from_str(&line) {
                                    Ok(p) => p,
                                    Err(_) => {
                                        let _ = stderr_tx.send(StderrEvent::Error(CommandError::new(
                                            "TRANSPORT_ERROR",
                                            "Progress record failed schema validation or contained unknown fields.",
                                        )));
                                        return;
                                    }
                                };

                                if prog.record_type != "progress" {
                                    let _ = stderr_tx.send(StderrEvent::Error(CommandError::new(
                                        "TRANSPORT_ERROR",
                                        "Progress record has mismatched record type.",
                                    )));
                                    return;
                                }

                                if prog.message.trim().is_empty() {
                                    let _ = stderr_tx.send(StderrEvent::Error(CommandError::new(
                                        "TRANSPORT_ERROR",
                                        "Progress record message cannot be empty.",
                                    )));
                                    return;
                                }

                                let phase = match prog.phase.as_str() {
                                    "request_received" => RunPhase::RequestReceived,
                                    "request_validated" => RunPhase::RequestValidated,
                                    "generation_started" => RunPhase::GenerationStarted,
                                    "response_ready" => RunPhase::ResponseReady,
                                    _ => {
                                        let _ =
                                            stderr_tx.send(StderrEvent::Error(CommandError::new(
                                                "TRANSPORT_ERROR",
                                                "Unknown progress phase on stderr.",
                                            )));
                                        return;
                                    }
                                };

                                if !is_valid_phase_transition(last_phase, phase) {
                                    let _ = stderr_tx.send(StderrEvent::Error(CommandError::new(
                                        "TRANSPORT_ERROR",
                                        "Invalid phase transition received on stderr.",
                                    )));
                                    return;
                                }
                                last_phase = Some(phase);
                                if stderr_tx.send(StderrEvent::Progress(phase)).is_err() {
                                    return;
                                }
                            }
                            "diagnostic" => {
                                let diag: StderrDiagnosticRecord = match serde_json::from_str(&line)
                                {
                                    Ok(d) => d,
                                    Err(_) => {
                                        let _ = stderr_tx.send(StderrEvent::Error(CommandError::new(
                                            "TRANSPORT_ERROR",
                                            "Diagnostic record failed schema validation or contained unknown fields.",
                                        )));
                                        return;
                                    }
                                };

                                if diag.record_type != "diagnostic" {
                                    let _ = stderr_tx.send(StderrEvent::Error(CommandError::new(
                                        "TRANSPORT_ERROR",
                                        "Diagnostic record has mismatched record type.",
                                    )));
                                    return;
                                }

                                if diag.phase != "fatal" {
                                    let _ = stderr_tx.send(StderrEvent::Error(CommandError::new(
                                        "TRANSPORT_ERROR",
                                        "Stderr contained non-fatal or invalid diagnostic phase.",
                                    )));
                                    return;
                                }

                                if diag.message.trim().is_empty() {
                                    let _ = stderr_tx.send(StderrEvent::Error(CommandError::new(
                                        "TRANSPORT_ERROR",
                                        "Diagnostic record message cannot be empty.",
                                    )));
                                    return;
                                }

                                let _ = stderr_tx.send(StderrEvent::FatalDiagnostic);
                                return;
                            }
                            _ => {
                                let _ = stderr_tx.send(StderrEvent::Error(CommandError::new(
                                    "TRANSPORT_ERROR",
                                    "Stderr contained unrecognized record type.",
                                )));
                                return;
                            }
                        }
                    } else {
                        if line_buf.len() >= MAX_STDERR_LINE_BYTES {
                            let _ = stderr_tx.send(StderrEvent::Error(CommandError::new(
                                "TRANSPORT_ERROR",
                                "Stderr line exceeded maximum permitted line length.",
                            )));
                            return;
                        }
                        line_buf.push(byte[0]);
                    }
                }
                Err(e) => {
                    let _ = stderr_tx.send(StderrEvent::Error(CommandError::new(
                        "TRANSPORT_ERROR",
                        format!("Stderr read error: {e}"),
                    )));
                    return;
                }
            }
        }

        if !line_buf.is_empty() {
            let _ = stderr_tx.send(StderrEvent::Error(CommandError::new(
                "TRANSPORT_ERROR",
                "Stderr contained unterminated line at EOF.",
            )));
            return;
        }

        let _ = stderr_tx.send(StderrEvent::Finished);
    });

    // Step 9: Write payload to stdin asynchronously on dedicated worker thread
    let mut stdin_writer = match child.stdin_write.take() {
        Some(w) => w,
        None => {
            let _ = windows::force_terminate_process(child.process_handle, 1);
            let _ = windows::wait_process_timeout(child.process_handle, 2000);
            finalize_terminal(
                &window,
                &request_id,
                RunState::Failed,
                None,
                Some("Child stdin handle unavailable.".to_string()),
                ResultAccess::None,
                CleanupState::Incomplete,
                None,
                vec![],
            );
            return;
        }
    };

    let (stdin_tx, stdin_rx) = std::sync::mpsc::channel();
    let stdin_payload = payload.clone();
    let stdin_handle = std::thread::spawn(move || {
        let res = stdin_writer
            .write_all(&stdin_payload)
            .and_then(|_| stdin_writer.flush());
        drop(stdin_writer);
        let _ = stdin_tx.send(res);
    });

    // Step 10: Supervisor polling loop
    let deadline = Duration::from_secs(RUN_DEADLINE_SECS);
    let grace_period = Duration::from_secs(CANCELLATION_GRACE_SECS);
    let mut cancel_sent_time: Option<Instant> = None;
    let mut cancel_by_deadline = false;
    let mut child_exit_code: Option<u32> = None;
    let mut force_terminated = false;
    let mut stderr_finished = false;
    let mut protocol_err: Option<String> = None;
    let mut stdout_result: Option<Result<Vec<u8>, CommandError>> = None;

    loop {
        // A. Check stderr events
        loop {
            match stderr_rx.try_recv() {
                Ok(event) => match event {
                    StderrEvent::Progress(phase) => {
                        let snap = with_state(&window, |guard| {
                            if let Some(run) = &mut guard.active_run {
                                if run.request_id == request_id && run.state == RunState::Running {
                                    run.phase = Some(phase);
                                    guard.revision += 1;
                                    return Some(guard.to_snapshot());
                                }
                            }
                            None
                        })
                        .flatten();
                        if let Some(s) = snap {
                            let _ = window.emit("generation-state", &s);
                        }
                    }
                    StderrEvent::FatalDiagnostic => {
                        if protocol_err.is_none() {
                            protocol_err =
                                Some("Fatal engine diagnostic reported on stderr.".to_string());
                        }
                        break;
                    }
                    StderrEvent::Error(e) => {
                        if protocol_err.is_none() {
                            protocol_err = Some(e.message);
                        }
                        break;
                    }
                    StderrEvent::Finished => {
                        stderr_finished = true;
                    }
                },
                Err(std::sync::mpsc::TryRecvError::Empty) => break,
                Err(std::sync::mpsc::TryRecvError::Disconnected) => {
                    if !stderr_finished && protocol_err.is_none() {
                        protocol_err =
                            Some("Engine stderr stream disconnected unexpectedly.".to_string());
                    }
                    break;
                }
            }
        }

        // B. Check early stdout results (preserve successful output and record early error)
        if stdout_result.is_none() {
            match stdout_rx.try_recv() {
                Ok(res) => {
                    if let Err(e) = &res {
                        if protocol_err.is_none() {
                            protocol_err = Some(e.message.clone());
                        }
                    }
                    stdout_result = Some(res);
                }
                Err(std::sync::mpsc::TryRecvError::Empty) => {}
                Err(std::sync::mpsc::TryRecvError::Disconnected) => {}
            }
        }

        // C. Check asynchronous stdin write error
        if let Ok(Err(e)) = stdin_rx.try_recv() {
            if protocol_err.is_none() {
                protocol_err = Some(format!("Failed to write request payload to engine: {e}"));
            }
        }

        // D. Check exit
        match windows::wait_process_timeout(child.process_handle, POLL_INTERVAL_MS as u32) {
            Ok(Some(code)) => {
                child_exit_code = Some(code);
                break;
            }
            Ok(None) => {}
            Err(_) => {
                let _ = windows::force_terminate_process(child.process_handle, 1);
                let _ = windows::wait_process_timeout(child.process_handle, 2000);
                force_terminated = true;
                break;
            }
        }

        // E. Check cancellation / deadline / protocol error
        let user_cancelled = cancel_token.load(Ordering::SeqCst);
        let deadline_exceeded = overall_start.elapsed() >= deadline;
        let has_protocol_err = protocol_err.is_some();

        if (user_cancelled || deadline_exceeded || has_protocol_err) && cancel_sent_time.is_none() {
            cancel_sent_time = Some(Instant::now());
            cancel_by_deadline = !user_cancelled && !has_protocol_err && deadline_exceeded;
            let reason = if let Some(ref err) = protocol_err {
                err.clone()
            } else if cancel_by_deadline {
                "Overall generation run deadline (180s) exceeded.".to_string()
            } else {
                "Cancellation requested by user.".to_string()
            };

            let cancel_snap = with_state(&window, |guard| {
                if let Some(run) = &mut guard.active_run {
                    if run.request_id == request_id && run.state != RunState::Cancelling {
                        run.state = RunState::Cancelling;
                        run.cleanup = CleanupState::Pending;
                        run.reason = Some(reason);
                        guard.revision += 1;
                        return Some(guard.to_snapshot());
                    }
                }
                None
            })
            .flatten();

            if let Some(s) = cancel_snap {
                let _ = window.emit("generation-state", &s);
            }

            // Attempt cooperative cancellation before force-killing
            let _ = windows::send_cancellation_signal(child.pid, child.child_has_private_console);
        }

        // F. Check grace period
        if let Some(c_time) = cancel_sent_time {
            if c_time.elapsed() >= grace_period {
                let _ = windows::force_terminate_process(child.process_handle, 1);
                let _ = windows::wait_process_timeout(child.process_handle, 2000);
                force_terminated = true;
                break;
            }
        }
    }

    // Step 11: Bounded reader completion and Terminal Reconciliation
    let mut process_terminated_cleanly = child_exit_code.is_some();
    if force_terminated || child_exit_code.is_none() {
        let term_res = windows::wait_process_timeout(child.process_handle, 2000);
        match term_res {
            Ok(Some(code)) => {
                if child_exit_code.is_none() {
                    child_exit_code = Some(code);
                }
                process_terminated_cleanly = true;
            }
            _ => {
                process_terminated_cleanly = false;
            }
        }
    }

    let stdout_bytes = match stdout_result.take() {
        Some(Ok(bytes)) => bytes,
        Some(Err(e)) => {
            if !force_terminated && !process_terminated_cleanly {
                let _ = windows::force_terminate_process(child.process_handle, 1);
                let _ = windows::wait_process_timeout(child.process_handle, 2000);
            }
            let _ = reap_workers_bounded(
                stdout_handle,
                stderr_handle,
                stdin_handle,
                Duration::from_millis(500),
            );
            finalize_terminal(
                &window,
                &request_id,
                RunState::Failed,
                None,
                Some(e.message),
                ResultAccess::None,
                CleanupState::Incomplete,
                None,
                vec![],
            );
            return;
        }
        None => match stdout_rx.recv_timeout(Duration::from_millis(2000)) {
            Ok(Ok(bytes)) => bytes,
            Ok(Err(e)) => {
                if !force_terminated && !process_terminated_cleanly {
                    let _ = windows::force_terminate_process(child.process_handle, 1);
                    let _ = windows::wait_process_timeout(child.process_handle, 2000);
                }
                let _ = reap_workers_bounded(
                    stdout_handle,
                    stderr_handle,
                    stdin_handle,
                    Duration::from_millis(500),
                );
                finalize_terminal(
                    &window,
                    &request_id,
                    RunState::Failed,
                    None,
                    Some(e.message),
                    ResultAccess::None,
                    CleanupState::Incomplete,
                    None,
                    vec![],
                );
                return;
            }
            Err(_) => {
                if !force_terminated && !process_terminated_cleanly {
                    let _ = windows::force_terminate_process(child.process_handle, 1);
                    let _ = windows::wait_process_timeout(child.process_handle, 2000);
                }
                let workers_ok = reap_workers_bounded(
                    stdout_handle,
                    stderr_handle,
                    stdin_handle,
                    Duration::from_millis(500),
                );
                let reason = if !workers_ok || !process_terminated_cleanly {
                    "Timed out waiting for engine stdout stream to close, and process or workers did not terminate within bounds."
                } else {
                    "Timed out waiting for engine stdout stream to close."
                };
                finalize_terminal(
                    &window,
                    &request_id,
                    RunState::Failed,
                    None,
                    Some(reason.to_string()),
                    ResultAccess::None,
                    CleanupState::Incomplete,
                    None,
                    vec![],
                );
                return;
            }
        },
    };

    // Drain any remaining stderr events within 2000ms
    let mut stderr_terminal_err: Option<String> = protocol_err;
    if !stderr_finished && stderr_terminal_err.is_none() {
        let start_drain = Instant::now();
        let drain_timeout = Duration::from_millis(2000);
        while !stderr_finished && start_drain.elapsed() < drain_timeout {
            match stderr_rx.recv_timeout(Duration::from_millis(50)) {
                Ok(StderrEvent::Progress(phase)) => {
                    let snap = with_state(&window, |guard| {
                        if let Some(run) = &mut guard.active_run {
                            if run.request_id == request_id && run.state == RunState::Running {
                                run.phase = Some(phase);
                                guard.revision += 1;
                                return Some(guard.to_snapshot());
                            }
                        }
                        None
                    })
                    .flatten();
                    if let Some(s) = snap {
                        let _ = window.emit("generation-state", &s);
                    }
                }
                Ok(StderrEvent::FatalDiagnostic) => {
                    stderr_terminal_err =
                        Some("Fatal engine diagnostic reported on stderr.".to_string());
                    break;
                }
                Ok(StderrEvent::Error(e)) => {
                    stderr_terminal_err = Some(e.message);
                    break;
                }
                Ok(StderrEvent::Finished) => {
                    stderr_finished = true;
                    break;
                }
                Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
                Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {
                    if !stderr_finished && stderr_terminal_err.is_none() {
                        stderr_terminal_err = Some(
                            "Engine stderr stream disconnected unexpectedly before completion."
                                .to_string(),
                        );
                    }
                    break;
                }
            }
        }
        if !stderr_finished && stderr_terminal_err.is_none() {
            stderr_terminal_err =
                Some("Timed out waiting for engine stderr stream to close.".to_string());
        }
    }

    let workers_reaped = reap_workers_bounded(
        stdout_handle,
        stderr_handle,
        stdin_handle,
        Duration::from_millis(500),
    );

    if !workers_reaped {
        finalize_terminal(
            &window,
            &request_id,
            RunState::Failed,
            None,
            Some(
                "Generation worker thread panicked or failed to terminate within bounded timeout."
                    .to_string(),
            ),
            ResultAccess::None,
            CleanupState::Incomplete,
            None,
            vec![],
        );
        return;
    }

    if force_terminated || !process_terminated_cleanly {
        let reason = if !process_terminated_cleanly {
            "Engine process or its descendants failed to terminate within bounded timeout."
        } else if cancel_sent_time.is_some() {
            "Cancellation grace period (10s) expired; engine was forcibly terminated."
        } else {
            "Overall generation run deadline (180s) expired; engine was forcibly terminated."
        };
        finalize_terminal(
            &window,
            &request_id,
            RunState::Failed,
            None,
            Some(reason.to_string()),
            ResultAccess::None,
            CleanupState::Incomplete,
            None,
            vec![],
        );
        return;
    }

    if let Some(msg) = stderr_terminal_err {
        finalize_terminal(
            &window,
            &request_id,
            RunState::Failed,
            None,
            Some(msg),
            ResultAccess::None,
            CleanupState::Incomplete,
            None,
            vec![],
        );
        return;
    }

    let effective_cleanup = |base: CleanupState| -> CleanupState {
        if !workers_reaped || !process_terminated_cleanly {
            CleanupState::Incomplete
        } else {
            base
        }
    };

    // Settle terminal response based on exit code and cancellation status
    if cancel_sent_time.is_some() {
        if child_exit_code == Some(130) {
            if stdout_bytes.is_empty() {
                if cancel_by_deadline {
                    finalize_terminal(
                        &window,
                        &request_id,
                        RunState::Failed,
                        None,
                        Some(
                            "Overall generation run deadline (180s) expired; engine cancelled."
                                .to_string(),
                        ),
                        ResultAccess::None,
                        CleanupState::Incomplete,
                        None,
                        vec![],
                    );
                } else {
                    finalize_terminal(
                        &window,
                        &request_id,
                        RunState::Cancelled,
                        None,
                        Some("Cancellation completed successfully.".to_string()),
                        ResultAccess::None,
                        effective_cleanup(CleanupState::NoFailureObserved),
                        None,
                        vec![],
                    );
                }
            } else {
                finalize_terminal(
                    &window,
                    &request_id,
                    RunState::Failed,
                    None,
                    Some(
                        "Engine process was cancelled (exit 130) but emitted unexpected stdout."
                            .to_string(),
                    ),
                    ResultAccess::None,
                    CleanupState::Incomplete,
                    None,
                    vec![],
                );
            }
        } else if child_exit_code == Some(0) {
            // Valid completed response wins a late cancellation request (Section 12.3)
            match WireResponse::parse_stdout(&stdout_bytes, &request_id) {
                Ok(resp) => {
                    let eng_status = resp.engine_status();
                    let (run_state, cleanup, res_access, reason) = match eng_status {
                        EngineStatus::Accepted => {
                            let r = if cancel_by_deadline {
                                "Generation completed successfully after deadline expired."
                                    .to_string()
                            } else {
                                "Generation completed successfully while cancellation was pending."
                                    .to_string()
                            };
                            (
                                RunState::Succeeded,
                                effective_cleanup(CleanupState::NoFailureObserved),
                                ResultAccess::None,
                                Some(r),
                            )
                        }
                        EngineStatus::Rejected => (
                            RunState::Rejected,
                            effective_cleanup(CleanupState::NoFailureObserved),
                            ResultAccess::None,
                            resp.errors().first().map(|e| map_curated_error(&e.code)),
                        ),
                        EngineStatus::Failed => (
                            RunState::Failed,
                            CleanupState::Incomplete,
                            ResultAccess::None,
                            resp.errors().first().map(|e| map_curated_error(&e.code)),
                        ),
                    };
                    let sanitized_warnings: Vec<String> = resp
                        .warnings()
                        .iter()
                        .map(|w| sanitize_warning(w))
                        .filter(|w| !w.is_empty())
                        .collect();
                    finalize_terminal(
                        &window,
                        &request_id,
                        run_state,
                        Some(eng_status),
                        reason,
                        res_access,
                        cleanup,
                        resp.data_cloned(),
                        sanitized_warnings,
                    );
                }
                Err(e) => {
                    finalize_terminal(
                        &window,
                        &request_id,
                        RunState::Failed,
                        None,
                        Some(format!(
                            "Engine completed with exit 0 but output was invalid: {}",
                            e.message
                        )),
                        ResultAccess::None,
                        CleanupState::Incomplete,
                        None,
                        vec![],
                    );
                }
            }
        } else {
            finalize_terminal(
                &window,
                &request_id,
                RunState::Failed,
                None,
                Some(format!(
                    "Engine process exited with code {:?} during cancellation.",
                    child_exit_code
                )),
                ResultAccess::None,
                CleanupState::Incomplete,
                None,
                vec![],
            );
        }
    } else {
        // Cancellation was not active
        if child_exit_code == Some(0) {
            match WireResponse::parse_stdout(&stdout_bytes, &request_id) {
                Ok(resp) => {
                    let eng_status = resp.engine_status();
                    let (run_state, cleanup, res_access) = match eng_status {
                        EngineStatus::Accepted => (
                            RunState::Succeeded,
                            effective_cleanup(CleanupState::NoFailureObserved),
                            ResultAccess::None,
                        ),
                        EngineStatus::Rejected => (
                            RunState::Rejected,
                            effective_cleanup(CleanupState::NoFailureObserved),
                            ResultAccess::None,
                        ),
                        EngineStatus::Failed => (
                            RunState::Failed,
                            CleanupState::Incomplete,
                            ResultAccess::None,
                        ),
                    };
                    let err_reason = resp.errors().first().map(|e| map_curated_error(&e.code));
                    let sanitized_warnings: Vec<String> = resp
                        .warnings()
                        .iter()
                        .map(|w| sanitize_warning(w))
                        .filter(|w| !w.is_empty())
                        .collect();
                    finalize_terminal(
                        &window,
                        &request_id,
                        run_state,
                        Some(eng_status),
                        err_reason,
                        res_access,
                        cleanup,
                        resp.data_cloned(),
                        sanitized_warnings,
                    );
                }
                Err(e) => {
                    finalize_terminal(
                        &window,
                        &request_id,
                        RunState::Failed,
                        None,
                        Some(format!(
                            "Failed to parse engine response JSON: {}",
                            e.message
                        )),
                        ResultAccess::None,
                        CleanupState::Incomplete,
                        None,
                        vec![],
                    );
                }
            }
        } else if child_exit_code == Some(130) {
            // Unexpected exit 130 without cancellation
            finalize_terminal(
                &window,
                &request_id,
                RunState::Failed,
                None,
                Some("Engine process was interrupted unexpectedly (exit code 130).".to_string()),
                ResultAccess::None,
                CleanupState::Incomplete,
                None,
                vec![],
            );
        } else {
            finalize_terminal(
                &window,
                &request_id,
                RunState::Failed,
                None,
                Some(format!(
                    "Engine process exited with unexpected code {:?}.",
                    child_exit_code
                )),
                ResultAccess::None,
                CleanupState::Incomplete,
                None,
                vec![],
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sanitize_warning_known_and_fallback() {
        // Known warning code (case-insensitive)
        let out = sanitize_warning("PREVIEW_EXPORT_FAILED");
        assert_eq!(
            out,
            "Preview image generation was skipped or unavailable; CAD geometry exported successfully."
        );
        let out_lower = sanitize_warning("preview_export_failed");
        assert_eq!(
            out_lower,
            "Preview image generation was skipped or unavailable; CAD geometry exported successfully."
        );

        // Safe DefaultApplied presentation: "Default applied to <path>: applied <val>."
        let safe_default = "Default applied to features[0].depth: applied 10.";
        assert_eq!(sanitize_warning(safe_default), safe_default);

        let safe_float_default = "Default applied to pitch_diameter: applied 40.5.";
        assert_eq!(sanitize_warning(safe_float_default), safe_float_default);

        // Disallowed DefaultApplied (e.g. secret keywords in path or val)
        let secret_in_path = "Default applied to api_key: applied 10.";
        assert_eq!(sanitize_warning(secret_in_path), GENERIC_WARNING_MESSAGE);

        let secret_in_val = "Default applied to features[0].depth: applied AIzaSyD91823.";
        assert_eq!(sanitize_warning(secret_in_val), GENERIC_WARNING_MESSAGE);

        // Disallowed path traversal or syntax
        let unsafe_default = "Default applied to ../secret: applied 10.";
        assert_eq!(sanitize_warning(unsafe_default), GENERIC_WARNING_MESSAGE);

        // Sensitive paths or tokens fall back to generic message
        let input = "Failed to open C:\\Users\\Administrator\\secret.key with token AIzaSyD9182374619827364129";
        assert_eq!(sanitize_warning(input), GENERIC_WARNING_MESSAGE);

        let unix_input = "Error loading /var/log/secrets/app.log key sk-1234567890abcdef123456";
        assert_eq!(sanitize_warning(unix_input), GENERIC_WARNING_MESSAGE);

        // Quoted Windows paths, UNC paths, prompt text fall back safely
        let unc_input = r#"Failed to access \\server\share\file.txt"#;
        assert_eq!(sanitize_warning(unc_input), GENERIC_WARNING_MESSAGE);
    }

    #[test]
    fn test_sanitize_warning_unicode_safety_and_filtering() {
        // Multi-byte Unicode must not panic (e.g. "a€")
        let euro = "a€";
        assert_eq!(sanitize_warning(euro), GENERIC_WARNING_MESSAGE);

        let non_ascii = "Warning: alert \u{0007} beep 🚀";
        assert_eq!(sanitize_warning(non_ascii), GENERIC_WARNING_MESSAGE);

        // Extremely long input safely handled without panic
        let long_input = "A".repeat(500);
        let out = sanitize_warning(&long_input);
        assert_eq!(out, GENERIC_WARNING_MESSAGE);
    }

    #[test]
    fn test_join_worker_bounded_clean_and_panic_handling() {
        // Clean thread join returns true
        let clean_handle = std::thread::spawn(|| {
            std::thread::sleep(Duration::from_millis(10));
        });
        assert!(join_worker_bounded(
            clean_handle,
            Duration::from_millis(200)
        ));

        // Panicking thread fails closed and returns false
        let panic_handle = std::thread::spawn(|| {
            panic!("Simulated worker panic");
        });
        assert!(!join_worker_bounded(
            panic_handle,
            Duration::from_millis(200)
        ));

        // Hanging thread times out and returns false
        let hang_handle = std::thread::spawn(|| {
            std::thread::sleep(Duration::from_millis(500));
        });
        assert!(!join_worker_bounded(hang_handle, Duration::from_millis(50)));
    }
}
