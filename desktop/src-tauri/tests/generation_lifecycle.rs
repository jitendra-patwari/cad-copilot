use std::collections::HashMap;
use std::io::{Read, Write};
use std::sync::Mutex;

use cad_copilot_desktop_lib::generation::launcher::{format_request_payload, prepare_child_env};
use cad_copilot_desktop_lib::generation::types::GenerationInput;
use cad_copilot_desktop_lib::shared::engine::{resolve_source_layout, verify_engine_compatibility};
use cad_copilot_desktop_lib::shared::windows::{
    force_terminate_process, send_cancellation_signal, spawn_engine_process,
    spawn_engine_process_explicit, wait_process_timeout,
};

static CONSOLE_TEST_LOCK: Mutex<()> = Mutex::new(());

struct ProcessGuard(windows_sys::Win32::Foundation::HANDLE);

impl Drop for ProcessGuard {
    fn drop(&mut self) {
        let _ = force_terminate_process(self.0, 1);
    }
}

#[test]
fn test_targeted_cancellation_unwinds_target_and_preserves_unrelated() {
    let _console_lock = CONSOLE_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let layout = resolve_source_layout().expect("Repo layout should resolve in tests");
    let mut env = HashMap::new();
    for (k, v) in std::env::vars() {
        let k_up = k.to_uppercase();
        if k_up == "SYSTEMROOT"
            || k_up == "WINDIR"
            || k_up == "PATH"
            || k_up == "TEMP"
            || k_up == "TMP"
        {
            env.insert(k, v);
        }
    }
    env.insert("PYTHONUNBUFFERED".to_string(), "1".to_string());

    // Target child script: scoped SIGBREAK handler raising KeyboardInterrupt, then prints READY and waits
    let target_script = r#"
import signal, sys, time

def handler(sig, frame):
    raise KeyboardInterrupt

signal.signal(signal.SIGBREAK, handler)
print("TARGET_READY", flush=True)

try:
    while True:
        time.sleep(0.05)
except KeyboardInterrupt:
    print("TARGET_UNWOUND", flush=True)
    sys.exit(130)
"#;

    // Control child script: stays alive and should NOT receive the cancellation event
    let control_script = r#"
import time
print("CONTROL_READY", flush=True)
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
    .expect("Failed to spawn control child");
    let _control_guard = ProcessGuard(control_child.process_handle);

    // Read CONTROL_READY
    let mut control_stdout = control_child.stdout_read.take().unwrap();
    let mut ctrl_buf = [0u8; 64];
    let n = control_stdout.read(&mut ctrl_buf).unwrap();
    let ctrl_msg = String::from_utf8_lossy(&ctrl_buf[..n]);
    assert!(ctrl_msg.contains("CONTROL_READY"));

    // Spawn target child
    let mut target_child = spawn_engine_process(
        &layout.python_exe,
        &["-c", target_script],
        &layout.repo_root,
        &env,
    )
    .expect("Failed to spawn target child");
    let _target_guard = ProcessGuard(target_child.process_handle);

    // Read TARGET_READY
    let mut target_stdout = target_child.stdout_read.take().unwrap();
    let mut tgt_buf = [0u8; 64];
    let n = target_stdout.read(&mut tgt_buf).unwrap();
    let tgt_msg = String::from_utf8_lossy(&tgt_buf[..n]);
    assert!(tgt_msg.contains("TARGET_READY"));

    // Send cooperative cancellation signal to TARGET ONLY
    send_cancellation_signal(target_child.pid, target_child.child_has_private_console)
        .expect("Failed to send cancellation signal");

    // Wait for target to unwind and exit 130
    let target_exit = wait_process_timeout(target_child.process_handle, 5000)
        .expect("Wait target process failed");
    assert_eq!(
        target_exit,
        Some(130),
        "Target should exit 130 after unwinding"
    );

    // Verify control child is still running (wait with 50ms timeout returns None)
    let control_poll = wait_process_timeout(control_child.process_handle, 50)
        .expect("Wait control process failed");
    assert_eq!(
        control_poll, None,
        "Control child must remain running and not receive signal bleed"
    );
}

#[test]
fn test_pipe_eof_and_graceful_exit() {
    let layout = resolve_source_layout().expect("Repo layout should resolve");
    let mut env = HashMap::new();
    for (k, v) in std::env::vars() {
        let k_up = k.to_uppercase();
        if k_up == "SYSTEMROOT"
            || k_up == "WINDIR"
            || k_up == "PATH"
            || k_up == "TEMP"
            || k_up == "TMP"
        {
            env.insert(k, v);
        }
    }
    env.insert("PYTHONUNBUFFERED".to_string(), "1".to_string());

    // Script reads stdin until EOF, then prints byte count and exits 0
    let script = r#"
import sys
content = sys.stdin.read()
print(f"BYTES_READ:{len(content)}", flush=True)
sys.exit(0)
"#;

    let mut child =
        spawn_engine_process(&layout.python_exe, &["-c", script], &layout.repo_root, &env)
            .expect("Spawn failed");
    let _guard = ProcessGuard(child.process_handle);

    // Write some data and close stdin
    {
        use std::io::Write;
        let mut stdin = child.stdin_write.take().unwrap();
        stdin.write_all(b"hello world from native host").unwrap();
        stdin.flush().unwrap();
        drop(stdin); // Send EOF
    }

    // Wait for child exit
    let exit_code = wait_process_timeout(child.process_handle, 5000).unwrap();
    assert_eq!(exit_code, Some(0));

    // Read stdout
    let mut stdout = child.stdout_read.take().unwrap();
    let mut out_str = String::new();
    stdout.read_to_string(&mut out_str).unwrap();
    assert!(out_str.contains("BYTES_READ:28"));
}

fn qualify_live_ipc_cancellation(private_console: bool) {
    if std::env::var("CAD_COPILOT_LIVE_TESTS")
        .map(|v| v != "1")
        .unwrap_or(true)
    {
        eprintln!("Skipping live IPC cancellation test (set CAD_COPILOT_LIVE_TESTS=1 to run)");
        return;
    }

    let _console_lock = CONSOLE_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let layout = resolve_source_layout().expect("Repo layout should resolve");

    let test_run_id = format!(
        "cad_qual_{}_{}",
        if private_console { "priv" } else { "shared" },
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

    let env = prepare_child_env(&layout, &temp_output_root, None, false);

    let mut child = spawn_engine_process_explicit(
        &layout.python_exe,
        &["-m", "ipc"],
        &layout.repo_root,
        &env,
        Some(private_console),
    )
    .expect("Spawn live python -m ipc failed");
    let _guard = ProcessGuard(child.process_handle);

    assert_eq!(child.child_has_private_console, private_console);

    // Send valid request payload to stdin
    let req_payload = br#"{"contract_version":"1.0","request_id":"req_live_cancel","kind":"example_plan","example_id":"spur_gear","unit":"mm"}
"#;
    {
        use std::io::Write;
        let mut stdin = child.stdin_write.take().unwrap();
        stdin.write_all(req_payload).unwrap();
        stdin.flush().unwrap();
        drop(stdin);
    }

    // Read stderr until request_received progress event is observed via timed channel
    let stderr_file = child.stderr_read.take().unwrap();
    let (tx, rx) = std::sync::mpsc::channel();
    let reader_thread = std::thread::spawn(move || {
        use std::io::BufRead;
        let reader = std::io::BufReader::new(stderr_file);
        for line in reader.lines() {
            match line {
                Ok(l) => {
                    if tx.send(l).is_err() {
                        break;
                    }
                }
                Err(_) => break,
            }
        }
    });

    let mut received = false;
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    while std::time::Instant::now() < deadline {
        match rx.recv_timeout(std::time::Duration::from_millis(50)) {
            Ok(line) => {
                if line.contains("generation_started") {
                    received = true;
                    break;
                }
            }
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
        }
    }
    assert!(
        received,
        "Engine did not emit generation_started on stderr within 5s"
    );

    // Signal cancellation to child
    send_cancellation_signal(child.pid, child.child_has_private_console)
        .expect("Send cancellation to live engine failed");

    // Wait for child process exit
    let exit_code =
        wait_process_timeout(child.process_handle, 10000).expect("Wait live process failed");

    assert_eq!(
        exit_code,
        Some(130),
        "Live python -m ipc should exit 130 on cancellation"
    );

    // Stdout must be completely empty on cancellation
    let mut stdout = child.stdout_read.take().unwrap();
    let mut stdout_bytes = Vec::new();
    stdout.read_to_end(&mut stdout_bytes).unwrap();
    assert!(
        stdout_bytes.is_empty(),
        "Stdout must be empty on cancellation"
    );

    let _ = reader_thread.join();

    // Verify output directory has no leftover partial files
    let remaining_entries: Vec<_> = std::fs::read_dir(&temp_output_root)
        .expect("Read temp output dir failed")
        .filter_map(|e| e.ok())
        .collect();
    assert!(
        remaining_entries.is_empty(),
        "Expected clean output root without partial files, but found {:?}",
        remaining_entries
            .iter()
            .map(|e| e.path())
            .collect::<Vec<_>>()
    );
}

#[test]
fn test_live_ipc_cancellation_shared_console() {
    qualify_live_ipc_cancellation(false);
}

#[test]
fn test_live_ipc_cancellation_private_console() {
    qualify_live_ipc_cancellation(true);
}

#[test]
fn test_generation_child_prerequisite_failure_before_cad_activation() {
    let _console_lock = CONSOLE_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let layout = resolve_source_layout().expect("Repo layout should resolve in tests");
    let temp_output_root = std::env::temp_dir().join(format!(
        "cad_gen_prereq_{}",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    std::fs::create_dir_all(&temp_output_root).expect("Failed to create temp output dir");
    struct Cleaner<'a>(&'a std::path::Path);
    impl<'a> Drop for Cleaner<'a> {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(self.0);
        }
    }
    let _cleaner = Cleaner(&temp_output_root);

    // Case 1: Mismatched engine artifact version rejected before execution
    let temp_engine_dir = std::env::temp_dir().join(format!(
        "cad_gen_mismatch_{}",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    std::fs::create_dir_all(&temp_engine_dir).expect("Create temp engine dir");
    let dummy_pyproject = temp_engine_dir.join("pyproject.toml");
    std::fs::write(
        &dummy_pyproject,
        "[project]\nname = \"cad-copilot\"\nversion = \"0.9.9\"\n",
    )
    .expect("Write dummy pyproject");
    let compat_res = verify_engine_compatibility(&temp_engine_dir);
    assert!(
        compat_res.is_err(),
        "Mismatched engine version must be rejected"
    );
    let compat_err = compat_res.unwrap_err();
    assert_eq!(compat_err.code, "ENGINE_UNAVAILABLE");
    assert_eq!(
        compat_err.message,
        "Engine specification version does not match supported desktop baseline."
    );
    let _ = std::fs::remove_dir_all(&temp_engine_dir);

    // Common environment
    let mut base_env = HashMap::new();
    for (k, v) in std::env::vars() {
        let k_up = k.to_uppercase();
        if k_up == "SYSTEMROOT"
            || k_up == "WINDIR"
            || k_up == "PATH"
            || k_up == "TEMP"
            || k_up == "TMP"
        {
            base_env.insert(k, v);
        }
    }
    base_env.insert("PYTHONUNBUFFERED".to_string(), "1".to_string());
    base_env.insert(
        "CAD_OUTPUT_ROOT".to_string(),
        temp_output_root.to_string_lossy().to_string(),
    );

    // Case 2: Mismatched installed cad-copilot distribution metadata exits with code 1 before CAD activation
    let mismatch_meta_cmd = "import sys, importlib.metadata; orig = importlib.metadata.version; importlib.metadata.version = lambda n: '9.9.9' if n == 'cad-copilot' else orig(n); from ipc.stdio import main; sys.exit(main())";
    let mut child_mismatch = spawn_engine_process(
        &layout.python_exe,
        &["-c", mismatch_meta_cmd],
        &layout.repo_root,
        &base_env,
    )
    .expect("Failed to spawn child for mismatched distribution metadata test");
    let _guard_mismatch = ProcessGuard(child_mismatch.process_handle);

    let exit_mismatch = wait_process_timeout(child_mismatch.process_handle, 5000)
        .expect("Wait mismatched metadata child failed");
    assert_eq!(
        exit_mismatch,
        Some(1),
        "Child with mismatched installed engine metadata must exit with code 1"
    );

    let mut stderr_mismatch = child_mismatch.stderr_read.take().unwrap();
    let mut stderr_bytes = Vec::new();
    stderr_mismatch
        .read_to_end(&mut stderr_bytes)
        .expect("Read stderr failed");
    let stderr_str = String::from_utf8_lossy(&stderr_bytes);
    assert!(
        stderr_str.contains(r#""phase":"fatal""#),
        "Mismatched metadata child must emit fatal diagnostic on stderr, got: {stderr_str}"
    );

    let mut stdout_mismatch = child_mismatch.stdout_read.take().unwrap();
    let mut stdout_bytes = Vec::new();
    stdout_mismatch
        .read_to_end(&mut stdout_bytes)
        .expect("Read stdout failed");
    assert!(
        stdout_bytes.is_empty(),
        "Mismatched metadata child must produce no stdout"
    );

    // Also verify absent distribution metadata exits with code 1 before CAD activation
    let absent_meta_cmd = "import sys, importlib.metadata\ndef _raise(n):\n    raise importlib.metadata.PackageNotFoundError(n)\nimportlib.metadata.version = _raise\nfrom ipc.stdio import main\nsys.exit(main())";
    let mut child_absent = spawn_engine_process(
        &layout.python_exe,
        &["-c", absent_meta_cmd],
        &layout.repo_root,
        &base_env,
    )
    .expect("Failed to spawn child for absent distribution metadata test");
    let _guard_absent = ProcessGuard(child_absent.process_handle);

    let exit_absent = wait_process_timeout(child_absent.process_handle, 5000)
        .expect("Wait absent metadata child failed");
    assert_eq!(
        exit_absent,
        Some(1),
        "Child with absent installed engine metadata must exit with code 1"
    );

    let mut stderr_absent = child_absent.stderr_read.take().unwrap();
    let mut stderr_bytes = Vec::new();
    stderr_absent
        .read_to_end(&mut stderr_bytes)
        .expect("Read stderr failed");
    let stderr_str = String::from_utf8_lossy(&stderr_bytes);
    assert!(
        stderr_str.contains(r#""phase":"fatal""#),
        "Absent metadata child must emit fatal diagnostic on stderr, got: {stderr_str}"
    );

    let mut stdout_absent = child_absent.stdout_read.take().unwrap();
    let mut stdout_bytes = Vec::new();
    stdout_absent
        .read_to_end(&mut stdout_bytes)
        .expect("Read stdout failed");
    assert!(
        stdout_bytes.is_empty(),
        "Absent metadata child must produce no stdout"
    );

    // Case 3: Unavailable Gemini SDK fails in Phase B before CAD activation (even when key is present)
    let mut sdk_env = base_env.clone();
    sdk_env.insert("GEMINI_API_KEY".to_string(), "test_session_key".to_string());
    // Simulate unavailable Gemini SDK by blocking the 'google' package in sys.modules
    let block_sdk_cmd =
        "import sys; sys.modules['google'] = None; from ipc.stdio import main; sys.exit(main())";
    let mut child_no_sdk = spawn_engine_process(
        &layout.python_exe,
        &["-c", block_sdk_cmd],
        &layout.repo_root,
        &sdk_env,
    )
    .expect("Failed to spawn child simulating unavailable Gemini SDK");
    let _guard_sdk = ProcessGuard(child_no_sdk.process_handle);

    let mut stdin_sdk = child_no_sdk.stdin_write.take().unwrap();
    let prompt_payload = format_request_payload(
        "req-prereq-no-sdk",
        &GenerationInput::PromptToCad {
            prompt: "Create a simple mounting bracket".to_string(),
        },
    )
    .expect("Format prompt payload failed");
    stdin_sdk
        .write_all(&prompt_payload)
        .expect("Write prompt request failed");
    drop(stdin_sdk);

    let sdk_exit =
        wait_process_timeout(child_no_sdk.process_handle, 10000).expect("Wait sdk child failed");
    assert_eq!(
        sdk_exit,
        Some(0),
        "Single execution child must handle unavailable SDK gracefully with exit 0"
    );

    let mut sdk_stdout = child_no_sdk.stdout_read.take().unwrap();
    let mut sdk_stdout_bytes = Vec::new();
    sdk_stdout
        .read_to_end(&mut sdk_stdout_bytes)
        .expect("Read sdk stdout failed");
    let sdk_stdout_str = String::from_utf8_lossy(&sdk_stdout_bytes);
    assert!(
        sdk_stdout_str.contains(r#""status":"failed""#)
            || sdk_stdout_str.contains(r#""status": "failed""#),
        "Expected failed status when SDK is unavailable, got: {sdk_stdout_str}"
    );
    assert!(
        sdk_stdout_str.contains("PROMPT_INTERPRETATION_FAILED"),
        "Expected PROMPT_INTERPRETATION_FAILED error code, got: {sdk_stdout_str}"
    );
    assert!(
        !sdk_stdout_str.contains("CAD_EXECUTION_FAILED"),
        "Must fail in Phase B before Phase C CAD activation, got: {sdk_stdout_str}"
    );

    // Verify that output directory was never written to (zero CAD artifacts created)
    let out_entries = std::fs::read_dir(&temp_output_root)
        .expect("Read output dir")
        .count();
    assert_eq!(
        out_entries, 0,
        "No CAD artifacts or run directory should be created when SDK is unavailable"
    );

    // Case 4: Missing Gemini key also fails closed in Phase B before CAD activation
    let mut child_prompt = spawn_engine_process(
        &layout.python_exe,
        &["-m", "ipc"],
        &layout.repo_root,
        &base_env, // no GEMINI_API_KEY
    )
    .expect("Failed to spawn live ipc child for prompt test");
    let _guard_prompt = ProcessGuard(child_prompt.process_handle);

    let mut stdin_prompt = child_prompt.stdin_write.take().unwrap();
    stdin_prompt
        .write_all(&prompt_payload)
        .expect("Write prompt request failed");
    drop(stdin_prompt);

    let prompt_exit =
        wait_process_timeout(child_prompt.process_handle, 10000).expect("Wait prompt child failed");
    assert_eq!(
        prompt_exit,
        Some(0),
        "Child should handle missing key failure cleanly with exit 0"
    );

    let mut prompt_stdout = child_prompt.stdout_read.take().unwrap();
    let mut prompt_stdout_bytes = Vec::new();
    prompt_stdout
        .read_to_end(&mut prompt_stdout_bytes)
        .expect("Read prompt stdout failed");
    let prompt_stdout_str = String::from_utf8_lossy(&prompt_stdout_bytes);
    assert!(
        prompt_stdout_str.contains(r#""status":"failed""#)
            || prompt_stdout_str.contains(r#""status": "failed""#),
        "Expected failed status when prompt key is missing, got: {prompt_stdout_str}"
    );
    assert!(
        prompt_stdout_str.contains("PROMPT_INTERPRETATION_FAILED"),
        "Expected PROMPT_INTERPRETATION_FAILED error code, got: {prompt_stdout_str}"
    );
    assert!(
        !prompt_stdout_str.contains("CAD_EXECUTION_FAILED"),
        "Must fail in Phase B before Phase C CAD activation, got: {prompt_stdout_str}"
    );

    // Case 5: Wire schema rejection for invalid example ID in single child
    let mut child_wire = spawn_engine_process(
        &layout.python_exe,
        &["-m", "ipc"],
        &layout.repo_root,
        &base_env,
    )
    .expect("Failed to spawn live ipc child");
    let _guard_wire = ProcessGuard(child_wire.process_handle);

    let mut stdin_wire = child_wire.stdin_write.take().unwrap();
    let wire_payload = format_request_payload(
        "req-prereq-wire",
        &GenerationInput::ExamplePlan {
            example_id: "nonexistent_example_catalog_item".to_string(),
        },
    )
    .expect("Format request payload failed");
    stdin_wire
        .write_all(&wire_payload)
        .expect("Write request failed");
    drop(stdin_wire);

    let wire_exit =
        wait_process_timeout(child_wire.process_handle, 10000).expect("Wait wire child failed");
    assert_eq!(
        wire_exit,
        Some(0),
        "Single execution child should handle rejection cleanly with exit 0"
    );

    let mut wire_stdout = child_wire.stdout_read.take().unwrap();
    let mut wire_stdout_bytes = Vec::new();
    wire_stdout
        .read_to_end(&mut wire_stdout_bytes)
        .expect("Read stdout failed");
    let wire_stdout_str = String::from_utf8_lossy(&wire_stdout_bytes);
    assert!(
        wire_stdout_str.contains(r#""status":"rejected""#)
            || wire_stdout_str.contains(r#""status": "rejected""#),
        "Expected rejected status in response, got: {wire_stdout_str}"
    );
    assert!(
        wire_stdout_str.contains("INVALID_SCHEMA"),
        "Expected INVALID_SCHEMA error code for invalid example catalog item, got: {wire_stdout_str}"
    );
}

#[test]
fn test_generation_child_fatal_teardown_failure_produces_exit_1_and_empty_stdout() {
    let _console_lock = CONSOLE_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let layout = resolve_source_layout().expect("Repo layout should resolve in tests");
    let mut env = HashMap::new();
    for (k, v) in std::env::vars() {
        let k_up = k.to_uppercase();
        if k_up == "SYSTEMROOT"
            || k_up == "WINDIR"
            || k_up == "PATH"
            || k_up == "TEMP"
            || k_up == "TMP"
        {
            env.insert(k, v);
        }
    }
    env.insert("PYTHONUNBUFFERED".to_string(), "1".to_string());

    // Python command simulating _run_stdio where composition handler raises TeardownIncompleteError
    let teardown_fail_cmd = r#"
import sys
from interfaces.exceptions import TeardownIncompleteError
from ipc.stdio import _run_stdio
from application.models import GenerationRequest

def failing_handler(req: GenerationRequest):
    raise TeardownIncompleteError("Teardown failed completely")

sys.exit(_run_stdio(argv=[], _composition_handler=failing_handler))
"#;

    let mut child = spawn_engine_process(
        &layout.python_exe,
        &["-c", teardown_fail_cmd],
        &layout.repo_root,
        &env,
    )
    .expect("Failed to spawn fatal teardown simulation child");
    let _guard = ProcessGuard(child.process_handle);

    // Provide valid request so it reaches composition handler
    let mut stdin = child.stdin_write.take().unwrap();
    let req_payload = br#"{"contract_version":"1.0","request_id":"req-fatal-teardown","kind":"example_plan","example_id":"spur_gear","unit":"mm"}"#;
    stdin.write_all(req_payload).expect("Write request failed");
    drop(stdin);

    let exit_code = wait_process_timeout(child.process_handle, 10000).expect("Wait child failed");
    assert_eq!(
        exit_code,
        Some(1),
        "Incomplete teardown error must result in fatal process exit 1"
    );

    // Stdout must be completely empty
    let mut stdout = child.stdout_read.take().unwrap();
    let mut stdout_bytes = Vec::new();
    stdout
        .read_to_end(&mut stdout_bytes)
        .expect("Read stdout failed");
    assert!(
        stdout_bytes.is_empty(),
        "Stdout must be empty when teardown fails with fatal exit 1"
    );

    // Stderr must contain fatal diagnostic and must NOT contain response_ready
    let mut stderr = child.stderr_read.take().unwrap();
    let mut stderr_bytes = Vec::new();
    stderr
        .read_to_end(&mut stderr_bytes)
        .expect("Read stderr failed");
    let stderr_str = String::from_utf8_lossy(&stderr_bytes);
    assert!(
        stderr_str.contains(r#""phase":"fatal""#) || stderr_str.contains(r#""phase": "fatal""#),
        "Stderr must contain fatal diagnostic, got: {stderr_str}"
    );
    assert!(
        !stderr_str.contains("response_ready"),
        "Stderr must NOT contain response_ready event when teardown fails"
    );
}

#[test]
fn test_generation_supervisor_terminates_child_hanging_after_response_ready() {
    let _console_lock = CONSOLE_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let layout = resolve_source_layout().expect("Repo layout should resolve in tests");

    let test_run_id = format!(
        "cad_gen_hang_{}",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    let temp_output_root = std::env::temp_dir().join(&test_run_id);
    std::fs::create_dir_all(&temp_output_root).expect("Failed to create temp output dir");
    struct Cleaner<'a>(&'a std::path::Path);
    impl<'a> Drop for Cleaner<'a> {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(self.0);
        }
    }
    let _cleaner = Cleaner(&temp_output_root);

    let state = std::sync::Arc::new(std::sync::Mutex::new(
        cad_copilot_desktop_lib::generation::GenerationState::new(),
    ));
    let coord = std::sync::Arc::new(std::sync::Mutex::new(
        cad_copilot_desktop_lib::run_claim::RunClaimCoordinator::new(),
    ));
    let host = std::sync::Arc::new(
        cad_copilot_desktop_lib::generation::process::MockGenerationHost::new(
            state.clone(),
            coord.clone(),
        ),
    );

    let selection_id = "sel-hang".to_string();

    // 1. Reserve run in GenerationState
    let request_id = {
        let mut guard = state.lock().unwrap();
        guard
            .set_output(
                cad_copilot_desktop_lib::generation::types::GenerationOutputSelection {
                    selection_id: selection_id.clone(),
                    display_path: temp_output_root.to_string_lossy().to_string(),
                },
                temp_output_root.clone(),
            )
            .expect("set_output should succeed");
        let snap = guard
            .reserve_run(
                &selection_id,
                cad_copilot_desktop_lib::generation::types::GenerationInput::ExamplePlan {
                    example_id: "spur_gear".to_string(),
                },
            )
            .expect("reserve_run should succeed");
        snap.run.expect("Active run snapshot must exist").request_id
    };

    // 2. Claim coordinator slot
    {
        let mut guard = coord.lock().unwrap();
        guard
            .claim(
                cad_copilot_desktop_lib::run_claim::RunKind::Generation,
                &request_id,
            )
            .expect("Coordinator claim should succeed");
    }

    // 3. Child script simulating:
    //    1. Child emits valid progress through response_ready
    //    2. Child writes a valid terminal response
    //    3. Child deliberately remains alive without closing stdout
    let hanging_script = r#"
import json, sys, time

# Read stdin request payload
raw_req = sys.stdin.readline()
req_data = json.loads(raw_req)
req_id = req_data.get("request_id", "")

# 1. Child emits valid progress through response_ready
for phase in ["request_received", "request_validated", "generation_started", "response_ready"]:
    sys.stderr.write(json.dumps({"type": "progress", "phase": phase, "message": f"Phase {phase}"}) + "\n")
    sys.stderr.flush()

# 2. Child writes a valid terminal response
resp = {
    "contract_version": "1.0",
    "request_id": req_id,
    "status": "failed",
    "errors": [{"code": "CAD_EXECUTION_FAILED", "message": "Simulated CAD failure"}],
    "warnings": []
}
sys.stdout.write(json.dumps(resp) + "\n")
sys.stdout.flush()

# 3. Child deliberately remains alive
while True:
    time.sleep(0.05)
"#;

    // 4. Supervisor launches with 1000ms post-response grace override
    let grace_override = std::time::Duration::from_millis(1000);
    cad_copilot_desktop_lib::generation::process::spawn_generation_supervisor_custom(
        host.clone(),
        request_id.clone(),
        Some((
            layout.python_exe.clone(),
            vec!["-c".to_string(), hanging_script.to_string()],
        )),
        Some(true),
        Some(grace_override),
    );

    // 4 & 5. Supervisor terminates it within the configured bound; cleanup becomes Incomplete
    let start_time = std::time::Instant::now();
    let timeout = std::time::Duration::from_secs(6);
    let mut reached_terminal = false;

    while start_time.elapsed() < timeout {
        {
            let guard = state.lock().unwrap();
            if let Some(run) = &guard.active_run {
                if run.state == cad_copilot_desktop_lib::generation::types::RunState::Failed {
                    reached_terminal = true;
                    assert_eq!(
                        run.cleanup,
                        cad_copilot_desktop_lib::generation::types::CleanupState::Incomplete,
                        "Hanging child must result in CleanupState::Incomplete"
                    );
                    assert!(
                        run.reason
                            .as_deref()
                            .unwrap_or("")
                            .to_ascii_lowercase()
                            .contains("post-response exit grace period"),
                        "Reason must identify post-response grace period termination: {:?}",
                        run.reason
                    );
                    break;
                }
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }

    assert!(
        reached_terminal,
        "Supervisor should terminate hanging child within configured bound and transition to Failed"
    );

    // 6. A subsequent run remains blocked
    {
        let mut guard = state.lock().unwrap();
        let rerun_res = guard.reserve_run(
            &selection_id,
            cad_copilot_desktop_lib::generation::types::GenerationInput::ExamplePlan {
                example_id: "spur_gear".to_string(),
            },
        );
        assert!(
            rerun_res.is_err(),
            "Subsequent run must be blocked when cleanup is Incomplete"
        );
        assert_eq!(
            rerun_res.unwrap_err().code,
            "INCOMPLETE_CLEANUP",
            "Error code must be INCOMPLETE_CLEANUP"
        );
    }
}
