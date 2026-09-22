use std::io::{Read, Write};
use std::sync::Mutex;

use cad_copilot_desktop_lib::batch::process::prepare_batch_child_env;
use cad_copilot_desktop_lib::batch::protocol::{
    build_and_serialize_request, parse_progress_line, parse_terminal_response, BatchStderrItem,
};
use cad_copilot_desktop_lib::batch::state::ActiveBatchRunState;
use cad_copilot_desktop_lib::batch::types::{
    BatchEngineStatus, BatchPhase, BatchRunState, CleanupState, ManifestState,
};
use cad_copilot_desktop_lib::run_claim::{RunClaimCoordinator, RunKind};
use cad_copilot_desktop_lib::shared::engine::resolve_source_layout;
use cad_copilot_desktop_lib::shared::windows::{
    force_terminate_process, send_cancellation_signal, spawn_engine_process, wait_process_timeout,
};

static CONSOLE_TEST_LOCK: Mutex<()> = Mutex::new(());

struct ProcessGuard(windows_sys::Win32::Foundation::HANDLE);

impl Drop for ProcessGuard {
    fn drop(&mut self) {
        let _ = force_terminate_process(self.0, 1);
    }
}

/// Verifies that cooperative cancellation via targeted CTRL_BREAK_EVENT unwinds
/// only the targeted batch child and leaves any sibling/unrelated processes untouched.
#[test]
fn test_batch_targeted_cancellation() {
    let _console_lock = CONSOLE_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let layout = resolve_source_layout().expect("Repo layout should resolve in tests");
    let env = prepare_batch_child_env(&layout);

    // Target child script: scoped SIGBREAK handler raising KeyboardInterrupt, then prints READY and waits
    let target_script = r#"
import signal, sys, time

def handler(sig, frame):
    raise KeyboardInterrupt

signal.signal(signal.SIGBREAK, handler)
print("BATCH_TARGET_READY", flush=True)

try:
    while True:
        time.sleep(0.05)
except KeyboardInterrupt:
    print("BATCH_TARGET_UNWOUND", flush=True)
    sys.exit(130)
"#;

    // Control child script: stays alive and should NOT receive the cancellation event
    let control_script = r#"
import time
print("BATCH_CONTROL_READY", flush=True)
while True:
    time.sleep(0.05)
"#;

    // Spawn control child
    let mut control_child = spawn_engine_process(
        &layout.python_exe,
        &["-c", control_script],
        &layout.repo_root,
        &env,
    )
    .expect("Failed to spawn batch control child");
    let _control_guard = ProcessGuard(control_child.process_handle);

    // Read BATCH_CONTROL_READY
    let mut control_stdout = control_child.stdout_read.take().unwrap();
    let mut ctrl_buf = [0u8; 64];
    let n = control_stdout.read(&mut ctrl_buf).unwrap();
    let ctrl_msg = String::from_utf8_lossy(&ctrl_buf[..n]);
    assert!(ctrl_msg.contains("BATCH_CONTROL_READY"));

    // Spawn target child
    let mut target_child = spawn_engine_process(
        &layout.python_exe,
        &["-c", target_script],
        &layout.repo_root,
        &env,
    )
    .expect("Failed to spawn batch target child");
    let _target_guard = ProcessGuard(target_child.process_handle);

    // Read BATCH_TARGET_READY
    let mut target_stdout = target_child.stdout_read.take().unwrap();
    let mut tgt_buf = [0u8; 64];
    let n = target_stdout.read(&mut tgt_buf).unwrap();
    let tgt_msg = String::from_utf8_lossy(&tgt_buf[..n]);
    assert!(tgt_msg.contains("BATCH_TARGET_READY"));

    // Send cooperative cancellation signal to TARGET ONLY
    send_cancellation_signal(target_child.pid, target_child.child_has_private_console)
        .expect("Failed to send cancellation signal");

    // Wait for target to unwind and exit 130
    let target_exit = wait_process_timeout(target_child.process_handle, 5000)
        .expect("Wait target process failed");
    assert_eq!(
        target_exit,
        Some(130),
        "Batch target should exit 130 after unwinding on SIGBREAK"
    );

    // Verify control child is still running (poll with 50ms timeout returns None)
    let control_poll = wait_process_timeout(control_child.process_handle, 50)
        .expect("Wait control process failed");
    assert_eq!(
        control_poll, None,
        "Batch control child must remain running and not receive signal bleed"
    );
}

/// Verifies child stdin wire request delivery, EOF closure, stderr progress collection,
/// stdout terminal response parsing, and completion exit code.
#[test]
fn test_batch_pipe_eof_and_graceful_exit() {
    let layout = resolve_source_layout().expect("Repo layout should resolve");
    let env = prepare_batch_child_env(&layout);

    // Python mock engine: reads stdin until EOF, validates request_id, emits progress on stderr, emits JSON response on stdout
    let script = r#"
import sys, json

stdin_content = sys.stdin.read()
if not stdin_content:
    sys.stderr.write("Empty stdin\n")
    sys.exit(1)

req = json.loads(stdin_content)
req_id = req["request_id"]

# Emit batch started progress event on stderr
progress = {
    "type": "progress",
    "request_id": req_id,
    "phase": "batch_started",
    "total_files": 1,
    "completed_files": 0
}
sys.stderr.write(json.dumps(progress) + "\n")
sys.stderr.flush()

# Emit terminal response on stdout
resp = {
    "contract_version": "1.0",
    "request_id": req_id,
    "status": "completed",
    "summary": {
        "total": 1,
        "accepted": 1,
        "partial": 0,
        "failed": 0,
        "unprocessed": 0,
        "cancelled": 0
    },
    "results": [
        {
            "input": "test_part.par",
            "status": "accepted",
            "artifacts": [
                {
                    "format": "step",
                    "path": "output/test_part.step"
                }
            ],
            "warnings": []
        }
    ],
    "manifest": {
        "path": "output/test-batch-lifecycle-001.batch_manifest.json"
    },
    "warnings": []
}
sys.stdout.write(json.dumps(resp) + "\n")
sys.stdout.flush()
sys.exit(0)
"#;

    let mut child =
        spawn_engine_process(&layout.python_exe, &["-c", script], &layout.repo_root, &env)
            .expect("Spawn failed");
    let _guard = ProcessGuard(child.process_handle);

    // Serialize wire request payload
    let req_bytes = build_and_serialize_request(
        "test-batch-lifecycle-001",
        &layout.repo_root,
        &["test_part.par".to_string()],
        &layout.repo_root.join("output"),
        "export_3d",
        &["step".to_string()],
        true,
        500,
    )
    .expect("Build request failed");

    // Write to stdin and close pipe (EOF)
    {
        let mut stdin = child.stdin_write.take().unwrap();
        stdin.write_all(&req_bytes).unwrap();
        stdin.flush().unwrap();
        drop(stdin); // EOF
    }

    // Wait for process exit 0
    let exit_code = wait_process_timeout(child.process_handle, 5000).unwrap();
    assert_eq!(exit_code, Some(0));

    // Read and verify stderr progress event
    let mut stderr_read = child.stderr_read.take().unwrap();
    let mut stderr_str = String::new();
    stderr_read.read_to_string(&mut stderr_str).unwrap();
    let stderr_line = stderr_str.lines().next().expect("Expected stderr line");
    let expected_formats = vec!["step".to_string()];
    let expected_files = vec!["test_part.par".to_string()];
    let parsed_stderr = parse_progress_line(
        stderr_line,
        "test-batch-lifecycle-001",
        &expected_formats,
        &expected_files,
    )
    .expect("Failed to parse progress line");
    match parsed_stderr {
        BatchStderrItem::Progress(p) => {
            assert_eq!(p.request_id, "test-batch-lifecycle-001");
            assert_eq!(p.total_files, 1);
        }
        _ => panic!("Expected BatchStderrItem::Progress"),
    }

    // Read and verify stdout terminal response
    let mut stdout_read = child.stdout_read.take().unwrap();
    let mut stdout_bytes = Vec::new();
    stdout_read.read_to_end(&mut stdout_bytes).unwrap();
    let requested_files = vec!["test_part.par".to_string()];
    let response =
        parse_terminal_response(&stdout_bytes, "test-batch-lifecycle-001", &requested_files)
            .expect("Failed to parse terminal response");
    assert_eq!(response.status, "completed");
    let summary = response.summary.unwrap();
    assert_eq!(summary.total, 1);
    assert_eq!(summary.accepted, 1);
}

/// Verifies that the app-wide RunClaimCoordinator strictly enforces mutual exclusion
/// between single Generation and Batch runs, and prevents new claims while prior cleanup is incomplete.
#[test]
fn test_batch_app_wide_claim_mutual_exclusion() {
    let mut coordinator = RunClaimCoordinator::new();

    // 1. Initial state has no active claim
    assert_eq!(coordinator.get_active(), None);
    assert_eq!(coordinator.get_close_owner(), None);

    // 2. Claim Batch run
    coordinator
        .claim(RunKind::Batch, "batch-req-001")
        .expect("Batch claim should succeed when coordinator is idle");
    assert_eq!(
        coordinator.get_active(),
        Some(cad_copilot_desktop_lib::run_claim::ActiveClaim {
            kind: RunKind::Batch,
            request_id: "batch-req-001".to_string(),
        })
    );

    // 3. Competing Generate run claim is rejected
    let gen_err = coordinator.claim(RunKind::Generation, "gen-req-001");
    assert!(gen_err.is_err());
    assert!(gen_err.unwrap_err().contains("RUN_ACTIVE"));

    // 4. Competing Batch run claim is rejected
    let batch_err = coordinator.claim(RunKind::Batch, "batch-req-002");
    assert!(batch_err.is_err());
    assert!(batch_err.unwrap_err().contains("RUN_ACTIVE"));

    // 5. Release with cleanup_incomplete = true
    let released = coordinator.release(RunKind::Batch, "batch-req-001", true);
    assert!(released);
    assert_eq!(coordinator.get_active(), None);

    // 6. While cleanup is incomplete, neither Generation nor Batch can claim
    let gen_err2 = coordinator.claim(RunKind::Generation, "gen-req-002");
    assert!(gen_err2.is_err());
    assert!(gen_err2.unwrap_err().contains("RUN_ACTIVE"));

    let batch_err2 = coordinator.claim(RunKind::Batch, "batch-req-003");
    assert!(batch_err2.is_err());
    assert!(batch_err2.unwrap_err().contains("RUN_ACTIVE"));

    // 7. Clear incomplete cleanup
    let cleared = coordinator.resolve_incomplete_cleanup(RunKind::Batch, "batch-req-001");
    assert!(cleared);

    // 8. Now Generation can claim
    coordinator
        .claim(RunKind::Generation, "gen-req-003")
        .expect("Generation claim should succeed after cleanup is cleared");
    assert_eq!(
        coordinator.get_active(),
        Some(cad_copilot_desktop_lib::run_claim::ActiveClaim {
            kind: RunKind::Generation,
            request_id: "gen-req-003".to_string(),
        })
    );

    // 9. Batch claim is rejected while Generation is active
    let batch_err3 = coordinator.claim(RunKind::Batch, "batch-req-004");
    assert!(batch_err3.is_err());
    assert!(batch_err3.unwrap_err().contains("RUN_ACTIVE"));

    // 10. Clean release of Generation restores idle state
    let gen_released = coordinator.release(RunKind::Generation, "gen-req-003", false);
    assert!(gen_released);
    assert_eq!(coordinator.get_active(), None);
    assert_eq!(coordinator.get_close_owner(), None);
}

fn qualify_production_supervisor(private_console: bool, test_cancellation: bool) {
    let _console_lock = CONSOLE_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let layout = resolve_source_layout().expect("Repo layout should resolve");

    let test_run_id = format!(
        "batch_sup_{}_{}_{}",
        if private_console { "priv" } else { "shared" },
        if test_cancellation {
            "cancel"
        } else {
            "complete"
        },
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    let temp_output_root = std::env::temp_dir().join(&test_run_id);
    std::fs::create_dir_all(&temp_output_root).expect("Failed to create temp output dir");
    let temp_output_root = cad_copilot_desktop_lib::shared::path::simplify_windows_path(
        &std::fs::canonicalize(&temp_output_root).expect("Failed to resolve temp output dir"),
    );

    struct OutputCleaner<'a>(&'a std::path::Path);
    impl<'a> Drop for OutputCleaner<'a> {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(self.0);
        }
    }
    let _cleaner = OutputCleaner(&temp_output_root);

    let out_id =
        cad_copilot_desktop_lib::shared::path::get_path_filesystem_identity(&temp_output_root)
            .expect("Filesystem identity for output root should succeed");
    let source_id =
        cad_copilot_desktop_lib::shared::path::get_path_filesystem_identity(&layout.repo_root)
            .expect("Filesystem identity for repo root should succeed");

    let state = std::sync::Arc::new(Mutex::new(cad_copilot_desktop_lib::batch::BatchState::new()));
    let coord = std::sync::Arc::new(Mutex::new(RunClaimCoordinator::new()));
    let host = std::sync::Arc::new(cad_copilot_desktop_lib::batch::process::MockBatchHost::new(
        state.clone(),
        coord.clone(),
    ));

    let cancel_token = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let request_id = format!(
        "batch-sup-{}-{}",
        if private_console { "priv" } else { "shared" },
        if test_cancellation {
            "cancel"
        } else {
            "complete"
        }
    );

    let (eligible_files, total_files) = if test_cancellation {
        (vec!["part1.par".to_string(), "part2.par".to_string()], 2)
    } else {
        (vec!["test_part.par".to_string()], 1)
    };

    // 1. Set active run in BatchState
    {
        let mut guard = state.lock().unwrap();
        guard.active_run = Some(ActiveBatchRunState {
            request_id: request_id.clone(),
            source_selection_id: "src-1".to_string(),
            output_selection_id: "out-1".to_string(),
            operation: "export_3d".to_string(),
            formats: vec!["step".to_string()],
            continue_on_error: true,
            max_files: 500,
            eligible_files: eligible_files.clone(),
            source_root: layout.repo_root.clone(),
            output_root: temp_output_root.clone(),
            source_root_identity: source_id,
            output_root_identity: out_id,
            state: BatchRunState::Starting,
            engine_status: None,
            phase: Some(BatchPhase::BatchStarted),
            total_files,
            completed_files: 0,
            current_file: None,
            current_format: None,
            last_file_status: None,
            cleanup: CleanupState::NoFailureObserved,
            close_requested: false,
            close_after_cleanup: false,
            manifest_state: ManifestState::NotApplicable,
            reason: None,
            cancel_token: cancel_token.clone(),
            cached_result: None,
        });
    }

    // 2. Claim coordinator slot
    {
        let mut guard = coord.lock().unwrap();
        guard
            .claim(RunKind::Batch, &request_id)
            .expect("RunClaimCoordinator claim should succeed");
    }

    let fixture_script = if test_cancellation {
        r#"
import sys, time
from ipc.batch_stdio import main
from batch.models import (
    BatchResponse, BatchSummary, BatchFileResult, BatchArtifactRecord, BatchManifestReference
)
from batch.projection import project_batch_response
from batch.execution import BatchProgressUpdate

def composition_handler(req, *, cancellation_check, observer):
    fmt = req.operation.formats[0]
    observer(BatchProgressUpdate(
        request_id=req.request_id,
        phase="batch_started",
        total_files=len(req.input.files),
        completed_files=0,
    ))
    observer(BatchProgressUpdate(
        request_id=req.request_id,
        phase="file_started",
        total_files=len(req.input.files),
        completed_files=0,
        current_file=req.input.files[0],
    ))
    observer(BatchProgressUpdate(
        request_id=req.request_id,
        phase="file_finished",
        total_files=len(req.input.files),
        completed_files=1,
        current_file=req.input.files[0],
        file_status="accepted",
    ))
    observer(BatchProgressUpdate(
        request_id=req.request_id,
        phase="file_started",
        total_files=len(req.input.files),
        completed_files=1,
        current_file=req.input.files[1],
    ))
    while not cancellation_check():
        time.sleep(0.01)

    resp = BatchResponse(
        contract_version="1.0",
        request_id=req.request_id,
        status="cancelled",
        summary=BatchSummary(
            total=len(req.input.files),
            accepted=1,
            partial=0,
            failed=0,
            unprocessed=0,
            cancelled=1,
        ),
        results=(
            BatchFileResult(
                input=req.input.files[0],
                status="accepted",
                artifacts=(
                    BatchArtifactRecord(
                        format=fmt,
                        path=f"{req.output_root}/test_part.{fmt}",
                    ),
                ),
            ),
        ),
        manifest=BatchManifestReference(
            path=f"{req.output_root}/{req.request_id}.batch_manifest.json"
        ),
        unprocessed_files=(),
        cancelled_files=(req.input.files[1],),
        errors=(),
        warnings=(),
    )
    return project_batch_response(resp)

sys.exit(main(_composition_handler=composition_handler))
"#
    } else {
        r#"
import hashlib, json, os, sys, time
from ipc.batch_stdio import main
from batch.models import (
    BatchResponse, BatchSummary, BatchFileResult, BatchArtifactRecord, BatchManifestReference
)
from batch.projection import project_batch_response
from batch.execution import BatchProgressUpdate

def composition_handler(req, *, cancellation_check, observer):
    fmt = req.operation.formats[0]
    observer(BatchProgressUpdate(
        request_id=req.request_id,
        phase="batch_started",
        total_files=len(req.input.files),
        completed_files=0,
    ))
    observer(BatchProgressUpdate(
        request_id=req.request_id,
        phase="file_started",
        total_files=len(req.input.files),
        completed_files=0,
        current_file=req.input.files[0],
    ))
    observer(BatchProgressUpdate(
        request_id=req.request_id,
        phase="file_finished",
        total_files=len(req.input.files),
        completed_files=1,
        current_file=req.input.files[0],
        file_status="accepted",
    ))
    observer(BatchProgressUpdate(
        request_id=req.request_id,
        phase="batch_finished",
        total_files=len(req.input.files),
        completed_files=1,
    ))

    art_name = f"test_part.{fmt}"
    art_path = os.path.join(req.output_root, art_name)
    art_content = b"cad step data"
    with open(art_path, "wb") as f:
        f.write(art_content)

    sha = hashlib.sha256(art_content).hexdigest()

    man_data = {
        "manifest_version": "1.0",
        "contract_version": "1.0",
        "request_id": req.request_id,
        "status": "completed",
        "operation": {
            "type": req.operation.type,
            "formats": list(req.operation.formats),
        },
        "summary": {
            "total": 1,
            "accepted": 1,
            "partial": 0,
            "failed": 0,
            "unprocessed": 0,
            "cancelled": 0,
        },
        "results": [
            {
                "input": req.input.files[0],
                "status": "accepted",
                "artifacts": [
                    {
                        "format": fmt,
                        "relative_path": art_name,
                        "size_bytes": len(art_content),
                        "sha256": sha,
                    }
                ],
            }
        ],
        "unprocessed_files": [],
        "engine_version": "1.0.0",
    }
    man_path = os.path.join(req.output_root, f"{req.request_id}.batch_manifest.json")
    with open(man_path, "w") as f:
        json.dump(man_data, f)

    resp = BatchResponse(
        contract_version="1.0",
        request_id=req.request_id,
        status="completed",
        summary=BatchSummary(
            total=1,
            accepted=1,
            partial=0,
            failed=0,
            unprocessed=0,
            cancelled=0,
        ),
        results=(
            BatchFileResult(
                input=req.input.files[0],
                status="accepted",
                artifacts=(
                    BatchArtifactRecord(
                        format=fmt,
                        path=art_path,
                    ),
                ),
            ),
        ),
        manifest=BatchManifestReference(path=man_path),
        unprocessed_files=(),
        cancelled_files=(),
        errors=(),
        warnings=(),
    )
    return project_batch_response(resp)

sys.exit(main(_composition_handler=composition_handler))
"#
    };

    // 3. Launch supervisor with real child
    cad_copilot_desktop_lib::batch::process::spawn_batch_supervisor_custom(
        host.clone(),
        request_id.clone(),
        Some((
            layout.python_exe.clone(),
            vec!["-c".to_string(), fixture_script.to_string()],
        )),
        Some(private_console),
        None,
    );

    // 4. Poll state until Terminal
    let start_time = std::time::Instant::now();
    let timeout = std::time::Duration::from_secs(10);
    let mut triggered_cancel = false;

    loop {
        if start_time.elapsed() > timeout {
            panic!("Batch supervisor did not reach terminal state within 10s");
        }

        let current_state = {
            let guard = state.lock().unwrap();
            guard.active_run.as_ref().map(|r| {
                (
                    r.state,
                    r.engine_status,
                    r.completed_files,
                    r.total_files,
                    r.cleanup,
                    r.manifest_state,
                    r.current_file.clone(),
                )
            })
        };

        if let Some((st, engine_st, completed, total, cleanup, manifest_st, current_file)) =
            current_state
        {
            if test_cancellation && !triggered_cancel {
                // Wait for observed child progress event (file 2 started after file 1 completed)
                if completed == 1 && current_file.as_deref() == Some("part2.par") {
                    cancel_token.store(true, std::sync::atomic::Ordering::SeqCst);
                    triggered_cancel = true;
                }
            }

            if st == BatchRunState::Terminal {
                if test_cancellation {
                    assert_eq!(
                        engine_st,
                        Some(BatchEngineStatus::Cancelled),
                        "Expected engine status Cancelled"
                    );
                    assert_eq!(completed, 1);
                    assert_eq!(total, 2);
                    assert_eq!(manifest_st, ManifestState::Unavailable);
                } else {
                    assert_eq!(
                        engine_st,
                        Some(BatchEngineStatus::Completed),
                        "Expected engine status Completed"
                    );
                    assert_eq!(completed, 1);
                    assert_eq!(total, 1);
                    assert_eq!(manifest_st, ManifestState::Validated);
                }
                assert_eq!(cleanup, CleanupState::NoFailureObserved);
                break;
            }
        }

        std::thread::sleep(std::time::Duration::from_millis(50));
    }

    // 5. Verify RunClaimCoordinator released claim cleanly
    {
        let guard = coord.lock().unwrap();
        assert_eq!(
            guard.get_active(),
            None,
            "Active claim should be released after terminal completion"
        );
        assert_eq!(
            guard.get_close_owner(),
            None,
            "Close owner should be None after clean termination"
        );
    }
}

#[test]
fn test_live_batch_supervisor_completed_shared_console() {
    qualify_production_supervisor(false, false);
}

#[test]
fn test_live_batch_supervisor_completed_private_console() {
    qualify_production_supervisor(true, false);
}

#[test]
fn test_live_batch_cancellation_shared_console() {
    qualify_production_supervisor(false, true);
}

#[test]
fn test_live_batch_cancellation_private_console() {
    qualify_production_supervisor(true, true);
}

#[test]
#[cfg(not(feature = "packaged-engine"))]
fn test_live_batch_supervisor_installed_module_early_rejection() {
    let _console_lock = CONSOLE_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let layout = resolve_source_layout().expect("Repo layout should resolve");

    let test_run_id = format!(
        "batch_sup_reject_{}",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    let temp_output_root = std::env::temp_dir().join(&test_run_id);
    std::fs::create_dir_all(&temp_output_root).expect("Failed to create temp output dir");

    struct OutputCleaner<'a>(&'a std::path::Path);
    impl<'a> Drop for OutputCleaner<'a> {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(self.0);
        }
    }
    let _cleaner = OutputCleaner(&temp_output_root);

    let out_id =
        cad_copilot_desktop_lib::shared::path::get_path_filesystem_identity(&temp_output_root)
            .expect("Filesystem identity for output root should succeed");
    let source_id =
        cad_copilot_desktop_lib::shared::path::get_path_filesystem_identity(&layout.repo_root)
            .expect("Filesystem identity for repo root should succeed");

    let state = std::sync::Arc::new(Mutex::new(cad_copilot_desktop_lib::batch::BatchState::new()));
    let coord = std::sync::Arc::new(Mutex::new(RunClaimCoordinator::new()));
    let host = std::sync::Arc::new(cad_copilot_desktop_lib::batch::process::MockBatchHost::new(
        state.clone(),
        coord.clone(),
    ));

    let cancel_token = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let request_id = "batch-sup-reject-test".to_string();

    // Point source_root to a unique, verified non-existent directory to guarantee deterministic early engine rejection without COM
    let nonexistent_source = std::env::temp_dir().join(format!(
        "cad_copilot_absent_source_{}_{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    if nonexistent_source.exists() {
        let _ = std::fs::remove_dir_all(&nonexistent_source);
    }
    assert!(
        !nonexistent_source.exists(),
        "Source root must be guaranteed absent"
    );

    {
        let mut guard = state.lock().unwrap();
        guard.active_run = Some(ActiveBatchRunState {
            request_id: request_id.clone(),
            source_selection_id: "src-1".to_string(),
            output_selection_id: "out-1".to_string(),
            operation: "export_3d".to_string(),
            formats: vec!["step".to_string()],
            continue_on_error: true,
            max_files: 500,
            eligible_files: vec!["test_part.par".to_string()],
            source_root: nonexistent_source,
            output_root: temp_output_root.clone(),
            source_root_identity: source_id,
            output_root_identity: out_id,
            state: BatchRunState::Starting,
            engine_status: None,
            phase: Some(BatchPhase::BatchStarted),
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
            cancel_token: cancel_token.clone(),
            cached_result: None,
        });
    }

    {
        let mut guard = coord.lock().unwrap();
        guard
            .claim(RunKind::Batch, &request_id)
            .expect("RunClaimCoordinator claim should succeed");
    }

    // Launch production supervisor with NO command override (fixed -m ipc.batch_stdio)
    cad_copilot_desktop_lib::batch::process::spawn_batch_supervisor_custom(
        host.clone(),
        request_id.clone(),
        None,
        Some(true),
        None,
    );

    let start_time = std::time::Instant::now();
    let timeout = std::time::Duration::from_secs(10);

    loop {
        if start_time.elapsed() > timeout {
            let s = {
                let guard = state.lock().unwrap();
                format!("{:?}", guard.active_run)
            };
            panic!(
                "Batch supervisor did not reach terminal state within 10s. Current run state: {}",
                s
            );
        }

        let current_state = {
            let guard = state.lock().unwrap();
            guard
                .active_run
                .as_ref()
                .map(|r| (r.state, r.engine_status, r.cleanup, r.reason.clone()))
        };

        if let Some((st, engine_st, cleanup, reason)) = current_state {
            if st == BatchRunState::Terminal {
                assert_eq!(
                    engine_st,
                    Some(BatchEngineStatus::Rejected),
                    "Expected engine status Rejected for non-existent source directory"
                );
                assert_eq!(cleanup, CleanupState::NoFailureObserved);
                assert!(
                    reason
                        .as_deref()
                        .unwrap_or("")
                        .contains("INPUT_ROOT_NOT_FOUND"),
                    "Expected INPUT_ROOT_NOT_FOUND rejection reason, got {:?}",
                    reason
                );
                break;
            }
        }

        std::thread::sleep(std::time::Duration::from_millis(50));
    }

    // Verify coordinator released claim cleanly
    {
        let guard = coord.lock().unwrap();
        assert_eq!(guard.get_active(), None);
        assert_eq!(guard.get_close_owner(), None);
    }
}

#[test]
fn test_batch_supervisor_pre_handler_exit_130() {
    let _console_lock = CONSOLE_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let layout = resolve_source_layout().expect("Repo layout should resolve");

    let test_run_id = format!(
        "batch_sup_exit130_{}",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    let temp_output_root = std::env::temp_dir().join(&test_run_id);
    std::fs::create_dir_all(&temp_output_root).expect("Failed to create temp output dir");

    struct OutputCleaner<'a>(&'a std::path::Path);
    impl<'a> Drop for OutputCleaner<'a> {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(self.0);
        }
    }
    let _cleaner = OutputCleaner(&temp_output_root);

    let out_id =
        cad_copilot_desktop_lib::shared::path::get_path_filesystem_identity(&temp_output_root)
            .expect("Filesystem identity for output root should succeed");
    let source_id =
        cad_copilot_desktop_lib::shared::path::get_path_filesystem_identity(&layout.repo_root)
            .expect("Filesystem identity for repo root should succeed");

    let state = std::sync::Arc::new(Mutex::new(cad_copilot_desktop_lib::batch::BatchState::new()));
    let coord = std::sync::Arc::new(Mutex::new(RunClaimCoordinator::new()));
    let host = std::sync::Arc::new(cad_copilot_desktop_lib::batch::process::MockBatchHost::new(
        state.clone(),
        coord.clone(),
    ));

    let cancel_token = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let request_id = "batch-sup-exit130-test".to_string();

    {
        let mut guard = state.lock().unwrap();
        guard.active_run = Some(ActiveBatchRunState {
            request_id: request_id.clone(),
            source_selection_id: "src-1".to_string(),
            output_selection_id: "out-1".to_string(),
            operation: "export_3d".to_string(),
            formats: vec!["step".to_string()],
            continue_on_error: true,
            max_files: 500,
            eligible_files: vec!["test_part.par".to_string()],
            source_root: layout.repo_root.clone(),
            output_root: temp_output_root.clone(),
            source_root_identity: source_id,
            output_root_identity: out_id,
            state: BatchRunState::Starting,
            engine_status: None,
            phase: Some(BatchPhase::BatchStarted),
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
            cancel_token: cancel_token.clone(),
            cached_result: None,
        });
    }

    {
        let mut guard = coord.lock().unwrap();
        guard
            .claim(RunKind::Batch, &request_id)
            .expect("RunClaimCoordinator claim should succeed");
    }

    // Immediate exit 130 script
    let exit_130_script = "import sys; sys.exit(130)";

    cad_copilot_desktop_lib::batch::process::spawn_batch_supervisor_custom(
        host.clone(),
        request_id.clone(),
        Some((
            layout.python_exe.clone(),
            vec!["-c".to_string(), exit_130_script.to_string()],
        )),
        Some(true),
        None,
    );

    let start_time = std::time::Instant::now();
    let timeout = std::time::Duration::from_secs(10);

    loop {
        if start_time.elapsed() > timeout {
            panic!("Batch supervisor did not reach terminal state within 10s");
        }

        let current_state = {
            let guard = state.lock().unwrap();
            guard
                .active_run
                .as_ref()
                .map(|r| (r.state, r.engine_status, r.cleanup, r.reason.clone()))
        };

        if let Some((st, engine_st, cleanup, reason)) = current_state {
            if st == BatchRunState::Terminal {
                assert_eq!(
                    engine_st,
                    Some(BatchEngineStatus::Failed),
                    "Exit 130 outside cooperative scope should have Failed engine status"
                );
                assert_eq!(cleanup, CleanupState::Incomplete);
                assert_eq!(
                    reason,
                    Some(
                        "Batch execution was interrupted outside cooperative cancellation scope."
                            .to_string()
                    )
                );
                break;
            }
        }

        std::thread::sleep(std::time::Duration::from_millis(50));
    }

    // Verify coordinator: active claim is None, but has incomplete cleanup blocking next runs
    {
        let mut guard = coord.lock().unwrap();
        assert_eq!(guard.get_active(), None);
        assert!(guard.has_incomplete_cleanup());
        assert!(guard.claim(RunKind::Batch, "another-run").is_err());
        assert!(guard.resolve_incomplete_cleanup(RunKind::Batch, &request_id));
        assert!(guard.claim(RunKind::Batch, "another-run").is_ok());
        guard.release(RunKind::Batch, "another-run", false);
    }
}

#[test]
fn test_batch_stalled_child_and_worker_settlement() {
    let _console_lock = CONSOLE_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let layout = resolve_source_layout().expect("Repo layout should resolve in tests");

    let test_run_id = format!(
        "batch_sup_stalled_{}",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    let temp_output_root = std::env::temp_dir().join(&test_run_id);
    std::fs::create_dir_all(&temp_output_root).expect("Failed to create temp output dir");

    struct OutputCleaner<'a>(&'a std::path::Path);
    impl<'a> Drop for OutputCleaner<'a> {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(self.0);
        }
    }
    let _cleaner = OutputCleaner(&temp_output_root);

    let out_id =
        cad_copilot_desktop_lib::shared::path::get_path_filesystem_identity(&temp_output_root)
            .expect("Filesystem identity for output root should succeed");
    let source_id =
        cad_copilot_desktop_lib::shared::path::get_path_filesystem_identity(&layout.repo_root)
            .expect("Filesystem identity for repo root should succeed");

    let state = std::sync::Arc::new(Mutex::new(cad_copilot_desktop_lib::batch::BatchState::new()));
    let coord = std::sync::Arc::new(Mutex::new(RunClaimCoordinator::new()));
    let host = std::sync::Arc::new(cad_copilot_desktop_lib::batch::process::MockBatchHost::new(
        state.clone(),
        coord.clone(),
    ));

    let cancel_token = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let request_id = "batch-sup-stalled-test".to_string();

    {
        let mut guard = state.lock().unwrap();
        guard.active_run = Some(ActiveBatchRunState {
            request_id: request_id.clone(),
            source_selection_id: "src-1".to_string(),
            output_selection_id: "out-1".to_string(),
            operation: "export_3d".to_string(),
            formats: vec!["step".to_string()],
            continue_on_error: true,
            max_files: 500,
            eligible_files: vec!["test_part.par".to_string()],
            source_root: layout.repo_root.clone(),
            output_root: temp_output_root.clone(),
            source_root_identity: source_id,
            output_root_identity: out_id,
            state: BatchRunState::Starting,
            engine_status: None,
            phase: Some(BatchPhase::BatchStarted),
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
            cancel_token: cancel_token.clone(),
            cached_result: None,
        });
    }

    {
        let mut guard = coord.lock().unwrap();
        guard
            .claim(RunKind::Batch, &request_id)
            .expect("RunClaimCoordinator claim should succeed");
    }

    // Stalled child script: explicitly ignores SIGBREAK and never exits on its own
    let stalled_script = r#"
import signal, sys, time, json

signal.signal(signal.SIGBREAK, signal.SIG_IGN)

req_line = sys.stdin.readline()

# Emit progress events to prove SIGBREAK handler is installed, stdin is read, and child is running
b_start = {
    "type": "progress",
    "request_id": "batch-sup-stalled-test",
    "phase": "batch_started",
    "total_files": 1,
    "completed_files": 0,
}
f_start = {
    "type": "progress",
    "request_id": "batch-sup-stalled-test",
    "phase": "file_started",
    "total_files": 1,
    "completed_files": 0,
    "current_file": "test_part.par",
}
sys.stderr.write(json.dumps(b_start) + "\n")
sys.stderr.write(json.dumps(f_start) + "\n")
sys.stderr.flush()

while True:
    time.sleep(0.05)
"#;

    // Launch with 500ms grace override
    cad_copilot_desktop_lib::batch::process::spawn_batch_supervisor_custom(
        host.clone(),
        request_id.clone(),
        Some((
            layout.python_exe.clone(),
            vec!["-c".to_string(), stalled_script.to_string()],
        )),
        Some(true),
        Some(std::time::Duration::from_millis(500)),
    );

    // Wait until child has started, installed its SIGBREAK handler, and emitted file_started
    let ready_deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    let mut child_ready = false;
    while std::time::Instant::now() < ready_deadline {
        let is_ready = {
            let guard = state.lock().unwrap();
            guard
                .active_run
                .as_ref()
                .map(|r| {
                    r.phase == Some(BatchPhase::FileStarted)
                        && r.current_file.as_deref() == Some("test_part.par")
                })
                .unwrap_or(false)
        };
        if is_ready {
            child_ready = true;
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(20));
    }
    assert!(
        child_ready,
        "Child must be observed running with SIGBREAK handler installed before cancellation is signaled"
    );

    cancel_token.store(true, std::sync::atomic::Ordering::SeqCst);

    let start_time = std::time::Instant::now();
    let timeout = std::time::Duration::from_secs(10);

    loop {
        if start_time.elapsed() > timeout {
            panic!("Stalled batch supervisor did not terminate within 10s");
        }

        let current_state = {
            let guard = state.lock().unwrap();
            guard
                .active_run
                .as_ref()
                .map(|r| (r.state, r.engine_status, r.cleanup, r.reason.clone()))
        };

        if let Some((st, engine_st, cleanup, reason)) = current_state {
            if st == BatchRunState::Terminal {
                assert_eq!(
                    engine_st,
                    Some(BatchEngineStatus::Failed),
                    "Stalled child force-killed after grace should have Failed engine status"
                );
                assert_eq!(
                    cleanup,
                    CleanupState::Incomplete,
                    "Cleanup must be Incomplete after force kill"
                );
                assert_eq!(
                    reason,
                    Some("Engine process terminated or timed out.".to_string()),
                    "Reason must distinguish grace-expiry forced termination from exit 130"
                );
                break;
            }
        }

        std::thread::sleep(std::time::Duration::from_millis(50));
    }

    // Verify coordinator: active claim is None, but subsequent claim is blocked due to incomplete cleanup!
    {
        let mut guard = coord.lock().unwrap();
        assert_eq!(guard.get_active(), None);
        let next_claim = guard.claim(RunKind::Batch, "next-run-attempt");
        assert!(
            next_claim.is_err(),
            "Next claim must be rejected due to incomplete cleanup"
        );
        assert!(guard.resolve_incomplete_cleanup(RunKind::Batch, &request_id));
        assert!(
            guard.claim(RunKind::Batch, "next-run-attempt").is_ok(),
            "Claim succeeds after clearing incomplete cleanup"
        );
        guard.release(RunKind::Batch, "next-run-attempt", false);
    }
}
