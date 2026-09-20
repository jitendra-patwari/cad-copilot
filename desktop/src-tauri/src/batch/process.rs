use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::PathBuf;
use std::sync::atomic::Ordering;
use std::time::{Duration, Instant};

use tauri::{Emitter, Manager, Window};

use crate::generation::launcher::{resolve_source_layout, SourceLayout};
use crate::generation::windows::{
    force_terminate_process, send_cancellation_signal, spawn_engine_process_explicit,
    wait_process_timeout,
};
use crate::run_claim::{RunClaimCoordinator, RunKind};

use super::protocol::{
    build_and_serialize_request, parse_progress_line, parse_terminal_response,
    verify_progress_tracker_terminal_accounting, BatchProgressTracker, BatchStderrItem,
    MAX_STDERR_AGGREGATE_BYTES, MAX_STDERR_LINE_BYTES, MAX_STDOUT_BYTES,
};
use super::result::{curate_diagnostic_message, validate_batch_result};
use super::state::{AppState, BatchState};
use super::types::{
    BatchEngineStatus, BatchResultResponse, BatchRunState, CleanupState, ManifestState,
};

pub const RUN_DEADLINE_SECS: u64 = 14400; // 4 hours
pub const CANCELLATION_GRACE_SECS: u64 = 60; // 60 seconds
pub const POLL_INTERVAL_MS: u32 = 50; // 50 ms

/// Builds a clean child environment inheriting allowed Windows system variables and prepending .venv/Scripts to PATH.
pub fn prepare_batch_child_env(layout: &SourceLayout) -> HashMap<String, String> {
    let mut env: HashMap<String, String> = HashMap::new();

    let allowed_vars = [
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "COMSPEC",
        "PATHEXT",
        "WINDIR",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "COMMONPROGRAMFILES",
        "COMMONPROGRAMFILES(X86)",
        "ALLUSERSPROFILE",
        "PUBLIC",
    ];

    for (k, v) in std::env::vars() {
        let k_up = k.to_uppercase();
        if allowed_vars
            .iter()
            .any(|allowed| allowed.eq_ignore_ascii_case(&k_up))
        {
            env.insert(k, v);
        }
    }

    let venv_scripts = layout.python_exe.parent().unwrap_or(&layout.repo_root);
    let original_path = std::env::var("PATH").unwrap_or_default();
    let combined_path = format!("{};{}", venv_scripts.display(), original_path);
    env.insert("PATH".to_string(), combined_path);

    env.insert("PYTHONUNBUFFERED".to_string(), "1".to_string());

    // Explicitly remove Python overrides and API keys
    env.remove("PYTHONPATH");
    env.remove("PYTHONHOME");
    env.remove("PYTHONSTARTUP");
    env.remove("PYTHONINSPECT");
    env.remove("PYTHONDEBUG");
    env.remove("GEMINI_API_KEY");

    env
}

pub trait BatchSupervisorHost: Send + Sync + 'static {
    fn with_batch_state(&self, f: &mut dyn FnMut(&mut BatchState));
    fn emit_event(&self, event: &str, payload: serde_json::Value);
    fn release_claim(&self, request_id: &str, incomplete: bool);
    fn destroy_window(&self);
}

impl BatchSupervisorHost for Window {
    fn with_batch_state(&self, f: &mut dyn FnMut(&mut BatchState)) {
        if let Some(app_state) = self.try_state::<AppState>() {
            if let Ok(mut guard) = app_state.lock() {
                f(&mut guard);
            }
        }
    }

    fn emit_event(&self, event: &str, payload: serde_json::Value) {
        let _ = self.emit(event, payload);
    }

    fn release_claim(&self, request_id: &str, incomplete: bool) {
        if let Some(coord) = self.try_state::<std::sync::Mutex<RunClaimCoordinator>>() {
            if let Ok(mut claim_guard) = coord.lock() {
                claim_guard.release(RunKind::Batch, request_id, incomplete);
            }
        }
    }

    fn destroy_window(&self) {
        let _ = self.destroy();
    }
}

#[cfg(any(test, feature = "test-support"))]
#[derive(Clone)]
pub struct MockBatchHost {
    pub state: std::sync::Arc<AppState>,
    pub coordinator: std::sync::Arc<std::sync::Mutex<RunClaimCoordinator>>,
    pub events: std::sync::Arc<std::sync::Mutex<Vec<(String, serde_json::Value)>>>,
}

#[cfg(any(test, feature = "test-support"))]
impl MockBatchHost {
    pub fn new(
        state: std::sync::Arc<AppState>,
        coordinator: std::sync::Arc<std::sync::Mutex<RunClaimCoordinator>>,
    ) -> Self {
        Self {
            state,
            coordinator,
            events: std::sync::Arc::new(std::sync::Mutex::new(Vec::new())),
        }
    }
}

#[cfg(any(test, feature = "test-support"))]
impl BatchSupervisorHost for MockBatchHost {
    fn with_batch_state(&self, f: &mut dyn FnMut(&mut BatchState)) {
        if let Ok(mut guard) = self.state.lock() {
            f(&mut guard);
        }
    }

    fn emit_event(&self, event: &str, payload: serde_json::Value) {
        if let Ok(mut evts) = self.events.lock() {
            evts.push((event.to_string(), payload));
        }
    }

    fn release_claim(&self, request_id: &str, incomplete: bool) {
        if let Ok(mut guard) = self.coordinator.lock() {
            guard.release(RunKind::Batch, request_id, incomplete);
        }
    }

    fn destroy_window(&self) {}
}

fn with_state<F, T>(host: &dyn BatchSupervisorHost, f: F) -> Option<T>
where
    F: FnOnce(&mut BatchState) -> T,
{
    let mut res = None;
    let mut f_opt = Some(f);
    host.with_batch_state(&mut |guard| {
        if let Some(f_actual) = f_opt.take() {
            res = Some(f_actual(guard));
        }
    });
    res
}

#[allow(clippy::too_many_arguments)]
fn finalize_terminal(
    host: &dyn BatchSupervisorHost,
    request_id: &str,
    engine_status: Option<BatchEngineStatus>,
    reason: Option<String>,
    cleanup: CleanupState,
    manifest_state: ManifestState,
    cached_result: Option<BatchResultResponse>,
) {
    let result = with_state(host, |guard| {
        let mut destroy = false;
        if let Some(run) = &mut guard.active_run {
            if run.request_id == request_id {
                run.state = BatchRunState::Terminal;
                run.engine_status = engine_status;
                run.reason = reason;
                run.cleanup = cleanup;
                run.manifest_state = manifest_state;
                run.cached_result = cached_result;
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

    host.release_claim(request_id, cleanup == CleanupState::Incomplete);

    if should_destroy {
        host.destroy_window();
    } else {
        host.emit_event(
            "batch-state",
            serde_json::to_value(snap).unwrap_or(serde_json::Value::Null),
        );
    }
}

fn read_line_bounded<R: BufRead>(
    reader: &mut R,
    max_bytes: usize,
) -> std::io::Result<Option<String>> {
    let mut line = Vec::new();
    loop {
        let available = match reader.fill_buf() {
            Ok([]) => {
                if line.is_empty() {
                    return Ok(None);
                } else {
                    return Err(std::io::Error::new(
                        std::io::ErrorKind::UnexpectedEof,
                        "Incomplete line: stream reached EOF without final newline.",
                    ));
                }
            }
            Ok(n) => n,
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(e),
        };

        if let Some(pos) = available.iter().position(|&b| b == b'\n') {
            let take = pos + 1;
            if line.len() + take > max_bytes {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::InvalidData,
                    "Line length exceeds maximum allowed limit.",
                ));
            }
            line.extend_from_slice(&available[..take]);
            reader.consume(take);
            break;
        } else {
            let take = available.len();
            if line.len() + take > max_bytes {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::InvalidData,
                    "Line length exceeds maximum allowed limit.",
                ));
            }
            line.extend_from_slice(available);
            reader.consume(take);
        }
    }

    let s = String::from_utf8(line)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e.to_string()))?;
    Ok(Some(s))
}

pub fn join_worker_bounded<T>(handle: std::thread::JoinHandle<T>, timeout: Duration) -> Option<T> {
    let start = Instant::now();
    while start.elapsed() < timeout {
        if handle.is_finished() {
            return handle.join().ok();
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    None
}

/// Spawns the production batch supervisor on a dedicated background OS thread.
pub fn spawn_batch_supervisor(window: Window, request_id: String) {
    let host = std::sync::Arc::new(window);
    std::thread::Builder::new()
        .name(format!("batch-supervisor-{}", request_id))
        .spawn(move || {
            run_batch_supervisor(host, request_id, None, None, None);
        })
        .expect("Failed to spawn batch supervisor thread");
}

/// Test-only launcher accepting command, console, and grace period overrides.
#[cfg(any(test, feature = "test-support"))]
pub fn spawn_batch_supervisor_custom(
    host: std::sync::Arc<dyn BatchSupervisorHost>,
    request_id: String,
    cmd_override: Option<(PathBuf, Vec<String>)>,
    force_private_console: Option<bool>,
    grace_override: Option<Duration>,
) {
    std::thread::Builder::new()
        .name(format!("batch-supervisor-{}", request_id))
        .spawn(move || {
            run_batch_supervisor(
                host,
                request_id,
                cmd_override,
                force_private_console,
                grace_override,
            );
        })
        .expect("Failed to spawn batch supervisor thread");
}

fn run_batch_supervisor(
    host: std::sync::Arc<dyn BatchSupervisorHost>,
    request_id: String,
    cmd_override: Option<(PathBuf, Vec<String>)>,
    force_private_console: Option<bool>,
    grace_override: Option<Duration>,
) {
    // 1. Retrieve run parameters from BatchState
    let run_config = with_state(host.as_ref(), |guard| {
        let run = guard.active_run.as_ref()?;
        if run.request_id != request_id {
            return None;
        }
        Some((
            run.source_root.clone(),
            run.output_root.clone(),
            run.source_root_identity,
            run.output_root_identity,
            run.eligible_files.clone(),
            run.operation.clone(),
            run.formats.clone(),
            run.continue_on_error,
            run.max_files,
            run.cancel_token.clone(),
        ))
    })
    .flatten();

    let (
        source_root,
        output_root,
        _source_root_id,
        output_root_id,
        eligible_files,
        operation,
        formats,
        continue_on_error,
        max_files,
        cancel_token,
    ) = match run_config {
        Some(c) => c,
        None => {
            finalize_terminal(
                host.as_ref(),
                &request_id,
                Some(BatchEngineStatus::Failed),
                Some("Failed to load run configuration from state.".to_string()),
                CleanupState::NoFailureObserved,
                ManifestState::NotApplicable,
                None,
            );
            return;
        }
    };

    // Pre-launch cancellation check
    if cancel_token.load(Ordering::SeqCst) {
        finalize_terminal(
            host.as_ref(),
            &request_id,
            None,
            Some("Cancelled before start.".to_string()),
            CleanupState::NoFailureObserved,
            ManifestState::NotApplicable,
            None,
        );
        return;
    }

    // 2. Resolve source layout and build request payload
    let layout = match resolve_source_layout() {
        Ok(l) => l,
        Err(e) => {
            finalize_terminal(
                host.as_ref(),
                &request_id,
                Some(BatchEngineStatus::Failed),
                Some(e.message),
                CleanupState::NoFailureObserved,
                ManifestState::NotApplicable,
                None,
            );
            return;
        }
    };

    let payload = match build_and_serialize_request(
        &request_id,
        &source_root,
        &eligible_files,
        &output_root,
        &operation,
        &formats,
        continue_on_error,
        max_files,
    ) {
        Ok(p) => p,
        Err(e) => {
            finalize_terminal(
                host.as_ref(),
                &request_id,
                Some(BatchEngineStatus::Failed),
                Some(e.message),
                CleanupState::NoFailureObserved,
                ManifestState::NotApplicable,
                None,
            );
            return;
        }
    };

    let env = prepare_batch_child_env(&layout);

    // Transition to Running state
    let snap = with_state(host.as_ref(), |guard| {
        if let Some(run) = &mut guard.active_run {
            if run.request_id == request_id {
                run.state = BatchRunState::Running;
            }
        }
        guard.revision += 1;
        guard.to_snapshot()
    });
    if let Some(s) = snap {
        host.emit_event(
            "batch-state",
            serde_json::to_value(s).unwrap_or(serde_json::Value::Null),
        );
    }

    // 3. Spawn engine process
    let (program, args_vec) = match &cmd_override {
        Some((prog, args)) => (prog.clone(), args.clone()),
        None => (
            layout.python_exe.clone(),
            vec!["-m".to_string(), "ipc.batch_stdio".to_string()],
        ),
    };
    let args_slices: Vec<&str> = args_vec.iter().map(|s| s.as_str()).collect();

    let mut child = match spawn_engine_process_explicit(
        &program,
        &args_slices,
        &layout.engine_dir,
        &env,
        force_private_console,
    ) {
        Ok(c) => c,
        Err(e) => {
            finalize_terminal(
                host.as_ref(),
                &request_id,
                Some(BatchEngineStatus::Failed),
                Some(format!("Failed to spawn batch engine: {}", e.message)),
                CleanupState::NoFailureObserved,
                ManifestState::NotApplicable,
                None,
            );
            return;
        }
    };

    // 4. Spawn stdout and stderr reader threads before writing stdin
    let stdout_file = child
        .stdout_read
        .take()
        .expect("Child stdout must be available");
    let stdout_reader_handle = std::thread::spawn(move || -> Result<Vec<u8>, std::io::Error> {
        let mut buf = Vec::new();
        let mut reader = stdout_file.take((MAX_STDOUT_BYTES + 1) as u64);
        reader.read_to_end(&mut buf)?;
        Ok(buf)
    });

    let stderr_file = child
        .stderr_read
        .take()
        .expect("Child stderr must be available");
    let host_clone = host.clone();
    let req_id_clone = request_id.clone();
    let formats_clone = formats.clone();
    let files_clone = eligible_files.clone();
    let cancel_token_clone = cancel_token.clone();

    let stream_violation = std::sync::Arc::new(std::sync::Mutex::new(None::<String>));
    let stream_violation_clone = stream_violation.clone();

    let stderr_reader_handle = std::thread::spawn(move || {
        let mut reader = BufReader::new(stderr_file);
        let mut total_stderr_bytes = 0usize;
        let mut tracker = BatchProgressTracker::new(files_clone.len() as u32);

        loop {
            let line_res = read_line_bounded(&mut reader, MAX_STDERR_LINE_BYTES);
            let line = match line_res {
                Ok(Some(l)) => l,
                Ok(None) => break,
                Err(e) => {
                    let err_msg = format!("Progress stream error: {}", e);
                    *stream_violation_clone.lock().unwrap() = Some(err_msg);
                    cancel_token_clone.store(true, Ordering::SeqCst);
                    break;
                }
            };

            total_stderr_bytes += line.len();
            if total_stderr_bytes > MAX_STDERR_AGGREGATE_BYTES {
                let err_msg = "Progress stream exceeded maximum aggregate size.".to_string();
                *stream_violation_clone.lock().unwrap() = Some(err_msg);
                cancel_token_clone.store(true, Ordering::SeqCst);
                break;
            }

            match parse_progress_line(&line, &req_id_clone, &formats_clone, &files_clone) {
                Ok(BatchStderrItem::Progress(evt)) => {
                    if let Err(track_err) = tracker.update(&evt) {
                        eprintln!("Progress stream sequence violation: {}", track_err.message);
                        *stream_violation_clone.lock().unwrap() = Some(
                            "Protocol violation: Progress sequence or completion count was invalid."
                                .to_string(),
                        );
                        cancel_token_clone.store(true, Ordering::SeqCst);
                        break;
                    }

                    let snap = with_state(host_clone.as_ref(), |guard| {
                        if let Some(run) = &mut guard.active_run {
                            if run.request_id == req_id_clone && run.state == BatchRunState::Running
                            {
                                run.phase = Some(evt.phase);
                                run.completed_files = evt.completed_files;
                                run.current_file = evt.current_file.clone();
                                run.current_format = evt.current_format.clone();
                                run.last_file_status = evt.file_status.clone();
                            }
                        }
                        guard.revision += 1;
                        guard.to_snapshot()
                    });
                    if let Some(s) = snap {
                        host_clone.emit_event(
                            "batch-state",
                            serde_json::to_value(s).unwrap_or(serde_json::Value::Null),
                        );
                    }
                    host_clone.emit_event(
                        "batch-progress",
                        serde_json::to_value(&evt).unwrap_or(serde_json::Value::Null),
                    );
                }
                Ok(BatchStderrItem::FatalDiagnostic(msg)) => {
                    eprintln!("Fatal engine diagnostic on stderr: {}", msg);
                    *stream_violation_clone.lock().unwrap() =
                        Some("Batch process failed before producing a response.".to_string());
                    break;
                }
                Err(parse_err) => {
                    eprintln!("Malformed progress line: {}", parse_err.message);
                    *stream_violation_clone.lock().unwrap() = Some(
                        "Protocol violation: Engine progress stream was malformed.".to_string(),
                    );
                    cancel_token_clone.store(true, Ordering::SeqCst);
                    break;
                }
            }
        }
        tracker
    });

    // 5. Write payload to stdin asynchronously so readers actively consume stdout/stderr
    let stdin_file = child.stdin_write.take();
    let stdin_writer_handle = std::thread::spawn(move || -> Result<(), std::io::Error> {
        if let Some(mut stdin) = stdin_file {
            let res = stdin.write_all(&payload).and_then(|_| stdin.flush());
            drop(stdin); // Explicit close to deliver EOF
            res
        } else {
            Ok(())
        }
    });

    // 6. Supervisory polling loop: monitors exit, cancellation, and deadline
    let start_time = Instant::now();
    let overall_deadline = Duration::from_secs(RUN_DEADLINE_SECS);
    let mut cancellation_sent = false;
    let mut cancellation_deadline: Option<Instant> = None;
    let mut cleanup = CleanupState::NoFailureObserved;

    let exit_code = loop {
        match wait_process_timeout(child.process_handle, POLL_INTERVAL_MS) {
            Ok(Some(code)) => break Some(code),
            Ok(None) => {}
            Err(_) => {
                cleanup = CleanupState::Incomplete;
                let _ = force_terminate_process(child.process_handle, 1);
                break None;
            }
        }

        // Check cancellation
        if cancel_token.load(Ordering::SeqCst) && !cancellation_sent {
            if let Err(e) = send_cancellation_signal(child.pid, child.child_has_private_console) {
                eprintln!("Failed to send cancellation signal: {}", e.message);
            }
            cancellation_sent = true;
            let grace =
                grace_override.unwrap_or_else(|| Duration::from_secs(CANCELLATION_GRACE_SECS));
            cancellation_deadline = Some(Instant::now() + grace);

            let snap = with_state(host.as_ref(), |guard| {
                if let Some(run) = &mut guard.active_run {
                    if run.request_id == request_id && run.state != BatchRunState::Cancelling {
                        run.state = BatchRunState::Cancelling;
                    }
                }
                guard.revision += 1;
                guard.to_snapshot()
            });
            if let Some(s) = snap {
                host.emit_event(
                    "batch-state",
                    serde_json::to_value(s).unwrap_or(serde_json::Value::Null),
                );
            }
        }

        if let Some(c_deadline) = cancellation_deadline {
            if Instant::now() > c_deadline {
                eprintln!(
                    "Cancellation grace expired; force terminating child PID {}",
                    child.pid
                );
                cleanup = CleanupState::Incomplete;
                let _ = force_terminate_process(child.process_handle, 1);
                let _ = wait_process_timeout(child.process_handle, 1000);
                break None;
            }
        }

        // Check overall deadline
        if start_time.elapsed() > overall_deadline && !cancellation_sent {
            let _ = send_cancellation_signal(child.pid, child.child_has_private_console);
            cancellation_sent = true;
            let grace =
                grace_override.unwrap_or_else(|| Duration::from_secs(CANCELLATION_GRACE_SECS));
            cancellation_deadline = Some(Instant::now() + grace);

            let snap = with_state(host.as_ref(), |guard| {
                if let Some(run) = &mut guard.active_run {
                    if run.request_id == request_id && run.state != BatchRunState::Cancelling {
                        run.state = BatchRunState::Cancelling;
                    }
                }
                guard.revision += 1;
                guard.to_snapshot()
            });
            if let Some(s) = snap {
                host.emit_event(
                    "batch-state",
                    serde_json::to_value(s).unwrap_or(serde_json::Value::Null),
                );
            }
        }
    };

    // 7. Join stream reader and writer threads with bounds
    let worker_settlement_timeout = Duration::from_millis(2000);
    let stdin_res = join_worker_bounded(stdin_writer_handle, worker_settlement_timeout);
    let stdout_res = join_worker_bounded(stdout_reader_handle, worker_settlement_timeout);
    let tracker_opt = join_worker_bounded(stderr_reader_handle, worker_settlement_timeout);

    if stdin_res.is_none() {
        finalize_terminal(
            host.as_ref(),
            &request_id,
            Some(BatchEngineStatus::Failed),
            Some("Batch process stdin pipe did not settle within timeout.".to_string()),
            CleanupState::Incomplete,
            ManifestState::Unavailable,
            None,
        );
        return;
    }

    if stdout_res.is_none() {
        finalize_terminal(
            host.as_ref(),
            &request_id,
            Some(BatchEngineStatus::Failed),
            Some("Batch process stdout pipe did not settle within timeout.".to_string()),
            CleanupState::Incomplete,
            ManifestState::Unavailable,
            None,
        );
        return;
    }

    let stdout_bytes = match stdout_res.unwrap() {
        Ok(bytes) => bytes,
        Err(e) => {
            finalize_terminal(
                host.as_ref(),
                &request_id,
                Some(BatchEngineStatus::Failed),
                Some(format!("Failed to read batch process stdout: {}", e)),
                CleanupState::Incomplete,
                ManifestState::Unavailable,
                None,
            );
            return;
        }
    };

    if tracker_opt.is_none() {
        finalize_terminal(
            host.as_ref(),
            &request_id,
            Some(BatchEngineStatus::Failed),
            Some("Batch process stderr pipe did not settle within timeout.".to_string()),
            CleanupState::Incomplete,
            ManifestState::Unavailable,
            None,
        );
        return;
    }
    let tracker = tracker_opt.unwrap();

    if let Some(Err(e)) = stdin_res {
        if exit_code == Some(0) {
            finalize_terminal(
                host.as_ref(),
                &request_id,
                Some(BatchEngineStatus::Failed),
                Some(format!(
                    "Failed to deliver complete request to batch process: {}",
                    e
                )),
                CleanupState::Incomplete,
                ManifestState::Unavailable,
                None,
            );
            return;
        }
    }

    let violation = stream_violation.lock().unwrap().clone();
    if let Some(err_msg) = violation {
        finalize_terminal(
            host.as_ref(),
            &request_id,
            Some(BatchEngineStatus::Failed),
            Some(err_msg),
            cleanup,
            ManifestState::Unavailable,
            None,
        );
        return;
    }

    // 8. Process result and terminal finalization
    match exit_code {
        Some(0) => match parse_terminal_response(&stdout_bytes, &request_id, &eligible_files) {
            Ok(resp) => {
                if let Err(e) = verify_progress_tracker_terminal_accounting(&tracker, &resp) {
                    eprintln!("Progress tracker accounting mismatch: {}", e.message);
                    finalize_terminal(
                        host.as_ref(),
                        &request_id,
                        Some(BatchEngineStatus::Failed),
                        Some(
                            "Terminal response accounting did not match progress stream."
                                .to_string(),
                        ),
                        CleanupState::NoFailureObserved,
                        ManifestState::Unavailable,
                        None,
                    );
                    return;
                }

                let result_resp = validate_batch_result(
                    &output_root,
                    output_root_id,
                    &request_id,
                    &operation,
                    &formats,
                    &resp,
                    &eligible_files,
                );

                match result_resp {
                    Ok(res) => {
                        finalize_terminal(
                            host.as_ref(),
                            &request_id,
                            Some(res.engine_status),
                            res.reason.clone(),
                            cleanup,
                            res.manifest_state,
                            Some(res),
                        );
                    }
                    Err(e) => {
                        eprintln!("Batch result validation error: {}", e.message);
                        finalize_terminal(
                            host.as_ref(),
                            &request_id,
                            Some(BatchEngineStatus::Failed),
                            Some(curate_diagnostic_message(&e.code, None)),
                            CleanupState::NoFailureObserved,
                            ManifestState::Unavailable,
                            None,
                        );
                    }
                }
            }
            Err(e) => {
                eprintln!("Terminal response validation failed: {}", e.message);
                finalize_terminal(
                    host.as_ref(),
                    &request_id,
                    Some(BatchEngineStatus::Failed),
                    Some("Terminal response failed validation.".to_string()),
                    CleanupState::NoFailureObserved,
                    ManifestState::Unavailable,
                    None,
                );
            }
        },
        Some(130) => {
            finalize_terminal(
                host.as_ref(),
                &request_id,
                Some(BatchEngineStatus::Failed),
                Some(
                    "Batch execution was interrupted outside cooperative cancellation scope."
                        .to_string(),
                ),
                CleanupState::Incomplete,
                ManifestState::Unavailable,
                None,
            );
        }
        Some(code) => {
            let error_desc = if stdout_bytes.is_empty() {
                format!("Engine process exited unexpectedly with code {}.", code)
            } else if let Ok(resp) =
                parse_terminal_response(&stdout_bytes, &request_id, &eligible_files)
            {
                resp.errors
                    .first()
                    .map(|e| curate_diagnostic_message(&e.code, e.format.as_deref()))
                    .unwrap_or_else(|| format!("Engine exited with code {}.", code))
            } else {
                format!("Engine process failed with code {}.", code)
            };

            finalize_terminal(
                host.as_ref(),
                &request_id,
                Some(BatchEngineStatus::Failed),
                Some(error_desc),
                CleanupState::Incomplete,
                ManifestState::Unavailable,
                None,
            );
        }
        None => {
            finalize_terminal(
                host.as_ref(),
                &request_id,
                Some(BatchEngineStatus::Failed),
                Some("Engine process terminated or timed out.".to_string()),
                CleanupState::Incomplete,
                ManifestState::Unavailable,
                None,
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_join_worker_bounded_clean_and_timeout() {
        let fast_handle = std::thread::spawn(|| {
            std::thread::sleep(Duration::from_millis(20));
            42
        });
        let res = join_worker_bounded(fast_handle, Duration::from_millis(500));
        assert_eq!(res, Some(42));

        let hanging_handle = std::thread::spawn(|| {
            std::thread::sleep(Duration::from_millis(500));
            99
        });
        let res_timeout = join_worker_bounded(hanging_handle, Duration::from_millis(50));
        assert!(res_timeout.is_none());

        let panicking_handle = std::thread::spawn(|| {
            panic!("Worker panicked unexpectedly");
        });
        let res_panic = join_worker_bounded(panicking_handle, Duration::from_millis(500));
        assert!(
            res_panic.is_none(),
            "Panicking worker handle must return None from join_worker_bounded"
        );
    }

    #[test]
    fn test_finalize_terminal_incomplete_cleanup_blocks_coordinator() {
        use crate::batch::state::ActiveBatchRunState;
        use std::sync::{Arc, Mutex};
        let state = Arc::new(Mutex::new(BatchState::new()));
        let coord = Arc::new(Mutex::new(RunClaimCoordinator::new()));
        let host = MockBatchHost::new(state.clone(), coord.clone());

        let request_id = "test-unsettled-001";
        // 1. Claim in coordinator
        coord
            .lock()
            .unwrap()
            .claim(RunKind::Batch, request_id)
            .unwrap();

        // 2. Set up active run in state
        {
            let mut guard = state.lock().unwrap();
            guard.active_run = Some(ActiveBatchRunState {
                request_id: request_id.to_string(),
                source_selection_id: "s1".to_string(),
                output_selection_id: "o1".to_string(),
                operation: "export_3d".to_string(),
                formats: vec!["step".to_string()],
                continue_on_error: true,
                max_files: 500,
                eligible_files: vec!["part.par".to_string()],
                source_root: PathBuf::from("C:\\src"),
                output_root: PathBuf::from("C:\\out"),
                source_root_identity: crate::generation::output::FileSystemIdentity {
                    volume_serial_number: 1,
                    file_index: 1,
                },
                output_root_identity: crate::generation::output::FileSystemIdentity {
                    volume_serial_number: 1,
                    file_index: 2,
                },
                state: BatchRunState::Running,
                engine_status: None,
                phase: None,
                total_files: 1,
                completed_files: 0,
                current_file: None,
                current_format: None,
                last_file_status: None,
                cleanup: CleanupState::NoFailureObserved,
                close_requested: false,
                close_after_cleanup: false,
                manifest_state: ManifestState::NotApplicable,
                reason: None,
                cancel_token: std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false)),
                cached_result: None,
            });
        }

        // 3. Call finalize_terminal with CleanupState::Incomplete (as happens when a worker times out or panics)
        finalize_terminal(
            &host,
            request_id,
            Some(BatchEngineStatus::Failed),
            Some("Batch process stdin pipe did not settle within timeout.".to_string()),
            CleanupState::Incomplete,
            ManifestState::Unavailable,
            None,
        );

        // 4. Verify state: run is terminal, cleanup is Incomplete
        {
            let guard = state.lock().unwrap();
            let run = guard.active_run.as_ref().unwrap();
            assert_eq!(run.state, BatchRunState::Terminal);
            assert_eq!(run.cleanup, CleanupState::Incomplete);
            assert_eq!(run.engine_status, Some(BatchEngineStatus::Failed));
        }

        // 5. Verify RunClaimCoordinator: claim is released from active, BUT cleanup_incomplete blocks new claims!
        {
            let mut guard = coord.lock().unwrap();
            assert_eq!(guard.get_active(), None);
            let next_claim = guard.claim(RunKind::Batch, "test-next-001");
            assert!(
                next_claim.is_err(),
                "Next run claim must be blocked when prior run cleanup was incomplete"
            );
            assert!(next_claim.unwrap_err().contains("RUN_ACTIVE"));
        }
    }
}
