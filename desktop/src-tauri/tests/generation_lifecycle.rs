use std::collections::HashMap;
use std::io::Read;
use std::sync::Mutex;

use cad_copilot_desktop_lib::generation::launcher::{prepare_child_env, resolve_source_layout};
use cad_copilot_desktop_lib::generation::windows::{
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
    let _console_lock = CONSOLE_TEST_LOCK.lock().unwrap();
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

    let _console_lock = CONSOLE_TEST_LOCK.lock().unwrap();
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
