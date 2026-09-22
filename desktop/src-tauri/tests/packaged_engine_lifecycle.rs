use std::collections::HashMap;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use cad_copilot_desktop_lib::batch::protocol::build_and_serialize_request;
use cad_copilot_desktop_lib::generation::launcher::format_request_payload;
use cad_copilot_desktop_lib::generation::types::GenerationInput;
use cad_copilot_desktop_lib::shared::engine::resolve_source_layout;
use cad_copilot_desktop_lib::shared::windows::{
    force_terminate_process, send_cancellation_signal, spawn_engine_process_explicit,
    wait_process_timeout,
};

static CONSOLE_TEST_LOCK: Mutex<()> = Mutex::new(());

struct ProcessGuard(windows_sys::Win32::Foundation::HANDLE);

impl Drop for ProcessGuard {
    fn drop(&mut self) {
        let _ = force_terminate_process(self.0, 1);
    }
}

fn get_staged_engine_path() -> Option<PathBuf> {
    let layout = resolve_source_layout().ok()?;
    let exe = layout
        .engine_dir
        .join("dist")
        .join("cad-copilot-engine")
        .join("cad-copilot-engine.exe");
    if exe.is_file() {
        Some(exe)
    } else {
        None
    }
}

struct IsolatedEngine {
    temp_dir: PathBuf,
    exe_path: PathBuf,
    engine_dir: PathBuf,
}

impl Drop for IsolatedEngine {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.temp_dir);
    }
}

fn copy_dir_recursive(src: &Path, dst: &Path) -> std::io::Result<()> {
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        let file_type = entry.file_type()?;
        let dest_path = dst.join(entry.file_name());
        if file_type.is_dir() {
            std::fs::create_dir_all(&dest_path)?;
            copy_dir_recursive(&entry.path(), &dest_path)?;
        } else {
            std::fs::copy(entry.path(), &dest_path)?;
        }
    }
    Ok(())
}

/// Copies the staged payload outside the repository checkout into a temporary directory
/// to prove complete runtime independence from developer virtualenv and repository paths.
fn deploy_engine_outside_checkout() -> Option<IsolatedEngine> {
    let staged_exe = get_staged_engine_path()?;
    let staged_dir = staged_exe.parent()?;

    let temp_root = std::env::temp_dir().join(format!(
        "cad_isolated_rt_{}_{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    let engine_dir = temp_root.join("engine");
    std::fs::create_dir_all(&engine_dir).ok()?;

    copy_dir_recursive(staged_dir, &engine_dir).ok()?;

    let exe_path = engine_dir.join("cad-copilot-engine.exe");
    if exe_path.is_file() {
        Some(IsolatedEngine {
            temp_dir: temp_root,
            exe_path,
            engine_dir,
        })
    } else {
        None
    }
}

fn build_isolated_env(engine_dir: &Path) -> HashMap<String, String> {
    let mut env = HashMap::new();
    let allowed_vars = [
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
    ];

    for var in allowed_vars {
        if let Ok(val) = std::env::var(var) {
            env.insert(var.to_string(), val);
        }
    }

    let sys_root = env
        .get("SYSTEMROOT")
        .cloned()
        .or_else(|| env.get("WINDIR").cloned())
        .unwrap_or_else(|| "C:\\Windows".to_string());

    // Controlled PATH: ONLY isolated engine directory + Windows system directories.
    // Explicitly excludes developer PATH, .venv\Scripts, Cargo, Node.js, and repository.
    let controlled_path = format!(
        "{};{}\\System32;{};{}\\System32\\Wbem",
        engine_dir.display(),
        sys_root,
        sys_root,
        sys_root
    );
    env.insert("PATH".to_string(), controlled_path);

    env.insert("PYTHONUNBUFFERED".to_string(), "1".to_string());
    env.insert("PYTHONDONTWRITEBYTECODE".to_string(), "1".to_string());
    env.insert("PYTHONNOUSERSITE".to_string(), "1".to_string());

    // Explicitly purge Python environment variables
    env.remove("PYTHONPATH");
    env.remove("PYTHONHOME");
    env.remove("VIRTUAL_ENV");
    env.remove("PYTHONSTARTUP");
    env.remove("PYTHONINSPECT");

    env
}

#[test]
#[ignore = "Requires staged engine payload at engine/dist/cad-copilot-engine/cad-copilot-engine.exe"]
fn test_packaged_engine_usage_when_no_subcommand() {
    let isolated = deploy_engine_outside_checkout().expect(
        "Staged engine not found at engine/dist/cad-copilot-engine/cad-copilot-engine.exe; run 'pnpm --filter @cad-copilot/engine package:desktop' first",
    );
    let env = build_isolated_env(&isolated.engine_dir);

    let mut child = spawn_engine_process_explicit(
        &isolated.exe_path,
        &[],
        &isolated.engine_dir,
        &env,
        Some(false),
    )
    .expect("Failed to spawn packaged engine without args");
    let _guard = ProcessGuard(child.process_handle);

    drop(child.stdin_write.take());

    let exit_code = wait_process_timeout(child.process_handle, 5000).expect("Process wait failed");
    assert_eq!(exit_code, Some(1), "Expected exit 1 for missing subcommand");

    let mut stderr_bytes = Vec::new();
    if let Some(mut err_read) = child.stderr_read.take() {
        let _ = err_read.read_to_end(&mut stderr_bytes);
    }
    let err_str = String::from_utf8_lossy(&stderr_bytes);
    assert!(
        err_str.contains("Usage: cad-copilot-engine <generate|batch>"),
        "Expected usage instructions in stderr, got: {err_str}"
    );
}

#[test]
#[ignore = "Requires staged engine payload at engine/dist/cad-copilot-engine/cad-copilot-engine.exe"]
fn test_packaged_engine_generate_isolated_rejection() {
    let _lock = CONSOLE_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let isolated = deploy_engine_outside_checkout().expect(
        "Staged engine not found at engine/dist/cad-copilot-engine/cad-copilot-engine.exe; run 'pnpm --filter @cad-copilot/engine package:desktop' first",
    );
    let env = build_isolated_env(&isolated.engine_dir);

    let mut child = spawn_engine_process_explicit(
        &isolated.exe_path,
        &["generate"],
        &isolated.engine_dir,
        &env,
        Some(false),
    )
    .expect("Failed to spawn packaged engine generate");
    let _guard = ProcessGuard(child.process_handle);

    let payload = format_request_payload(
        "req-pkg-test",
        &GenerationInput::ExamplePlan {
            example_id: "nonexistent_example_id".to_string(),
        },
    )
    .expect("Format request payload failed");

    {
        let mut stdin = child
            .stdin_write
            .take()
            .expect("Failed to take child stdin");
        stdin
            .write_all(&payload)
            .expect("Failed to write payload to child stdin");
        drop(stdin);
    }

    let mut stdout_bytes = Vec::new();
    if let Some(mut out_read) = child.stdout_read.take() {
        let _ = out_read.read_to_end(&mut stdout_bytes);
    }
    let out_str = String::from_utf8_lossy(&stdout_bytes);

    let mut stderr_bytes = Vec::new();
    if let Some(mut err_read) = child.stderr_read.take() {
        let _ = err_read.read_to_end(&mut stderr_bytes);
    }
    let err_str = String::from_utf8_lossy(&stderr_bytes);

    let exit_code = wait_process_timeout(child.process_handle, 10000).expect("Process wait failed");
    assert_eq!(
        exit_code,
        Some(0),
        "Expected exit 0 for handled rejection in packaged generate child. stderr: {err_str}"
    );
    assert!(
        out_str.contains(r#""status":"rejected""#) || out_str.contains(r#""status": "rejected""#),
        "Expected rejected status in response, got: {out_str}"
    );
    assert!(
        out_str.contains("INVALID_SCHEMA"),
        "Expected INVALID_SCHEMA error code in response, got: {out_str}"
    );
}

#[test]
#[ignore = "Requires staged engine payload at engine/dist/cad-copilot-engine/cad-copilot-engine.exe"]
fn test_packaged_engine_batch_isolated_rejection() {
    let _lock = CONSOLE_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let isolated = deploy_engine_outside_checkout().expect(
        "Staged engine not found at engine/dist/cad-copilot-engine/cad-copilot-engine.exe; run 'pnpm --filter @cad-copilot/engine package:desktop' first",
    );
    let env = build_isolated_env(&isolated.engine_dir);

    let nonexistent_source = std::env::temp_dir().join(format!(
        "cad_pkg_absent_src_{}_{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    let temp_output = std::env::temp_dir().join(format!(
        "cad_pkg_out_{}_{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    std::fs::create_dir_all(&temp_output).expect("Create temp output");

    struct OutputCleaner<'a>(&'a Path);
    impl<'a> Drop for OutputCleaner<'a> {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(self.0);
        }
    }
    let _cleaner = OutputCleaner(&temp_output);

    let mut child = spawn_engine_process_explicit(
        &isolated.exe_path,
        &["batch"],
        &isolated.engine_dir,
        &env,
        Some(true),
    )
    .expect("Failed to spawn packaged engine batch");
    let _guard = ProcessGuard(child.process_handle);

    let payload = build_and_serialize_request(
        "req-pkg-batch",
        &nonexistent_source,
        &["test.par".to_string()],
        &temp_output,
        "export_3d",
        &["step".to_string()],
        true,
        500,
    )
    .expect("Build batch request failed");

    {
        let mut stdin = child
            .stdin_write
            .take()
            .expect("Failed to take child stdin");
        stdin.write_all(&payload).expect("Write payload failed");
        drop(stdin);
    }

    let mut stdout_bytes = Vec::new();
    if let Some(mut out_read) = child.stdout_read.take() {
        let _ = out_read.read_to_end(&mut stdout_bytes);
    }
    let out_str = String::from_utf8_lossy(&stdout_bytes);

    let mut stderr_bytes = Vec::new();
    if let Some(mut err_read) = child.stderr_read.take() {
        let _ = err_read.read_to_end(&mut stderr_bytes);
    }
    let err_str = String::from_utf8_lossy(&stderr_bytes);

    let exit_code = wait_process_timeout(child.process_handle, 10000).expect("Process wait failed");
    assert_eq!(
        exit_code,
        Some(0),
        "Expected exit 0 for handled batch rejection in packaged child. stderr: {err_str}"
    );
    assert!(
        out_str.contains(r#""status":"rejected""#) || out_str.contains(r#""status": "rejected""#),
        "Expected rejected batch status, got: {out_str}"
    );
    assert!(
        out_str.contains("INPUT_ROOT_NOT_FOUND"),
        "Expected INPUT_ROOT_NOT_FOUND in batch rejection, got: {out_str}"
    );
}

#[test]
#[ignore = "Requires staged engine payload at engine/dist/cad-copilot-engine/cad-copilot-engine.exe"]
fn test_packaged_engine_unexpected_args_fatal_diagnostic() {
    let isolated = deploy_engine_outside_checkout().expect(
        "Staged engine not found at engine/dist/cad-copilot-engine/cad-copilot-engine.exe; run 'pnpm --filter @cad-copilot/engine package:desktop' first",
    );
    let env = build_isolated_env(&isolated.engine_dir);

    let mut child = spawn_engine_process_explicit(
        &isolated.exe_path,
        &["generate", "--illegal-flag"],
        &isolated.engine_dir,
        &env,
        Some(false),
    )
    .expect("Failed to spawn packaged engine generate with bad args");
    let _guard = ProcessGuard(child.process_handle);

    drop(child.stdin_write.take());

    let exit_code = wait_process_timeout(child.process_handle, 5000).expect("Process wait failed");
    assert_eq!(
        exit_code,
        Some(1),
        "Expected exit 1 on unexpected arguments"
    );

    let mut stderr_bytes = Vec::new();
    if let Some(mut err_read) = child.stderr_read.take() {
        let _ = err_read.read_to_end(&mut stderr_bytes);
    }
    let err_str = String::from_utf8_lossy(&stderr_bytes);
    assert!(
        err_str.contains(r#""phase":"fatal""#) || err_str.contains(r#""phase": "fatal""#),
        "Expected fatal JSON diagnostic on stderr, got: {err_str}"
    );
}

#[test]
#[ignore = "Requires staged engine payload at engine/dist/cad-copilot-engine/cad-copilot-engine.exe"]
fn test_packaged_engine_generate_cancellation_unwinds_exit_130() {
    let _lock = CONSOLE_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let isolated = deploy_engine_outside_checkout().expect(
        "Staged engine not found at engine/dist/cad-copilot-engine/cad-copilot-engine.exe; run 'pnpm --filter @cad-copilot/engine package:desktop' first",
    );
    let env = build_isolated_env(&isolated.engine_dir);

    // Test harmless non-COM signal cancellation while waiting for input under both topologies:
    // private console (true) and shared console (false).
    for private_console in [true, false] {
        // Spawn control child: must NOT receive the cancellation event (tests targeted delivery)
        let mut control_child = spawn_engine_process_explicit(
            &isolated.exe_path,
            &["generate"],
            &isolated.engine_dir,
            &env,
            Some(private_console),
        )
        .expect("Failed to spawn control packaged engine");
        let _control_guard = ProcessGuard(control_child.process_handle);

        // Spawn target child: will receive cancellation signal while safely waiting for input
        let mut target_child = spawn_engine_process_explicit(
            &isolated.exe_path,
            &["generate"],
            &isolated.engine_dir,
            &env,
            Some(private_console),
        )
        .expect("Failed to spawn target packaged engine");
        let _target_guard = ProcessGuard(target_child.process_handle);

        assert_eq!(target_child.child_has_private_console, private_console);

        // Allow both processes to initialize and enter standard input wait loop (no CAD activation)
        std::thread::sleep(std::time::Duration::from_millis(2500));

        // Send targeted cancellation signal to TARGET ONLY
        let sig_res =
            send_cancellation_signal(target_child.pid, target_child.child_has_private_console);
        assert!(
            sig_res.is_ok(),
            "Signal send must succeed for private_console={}, error: {:?}",
            private_console,
            sig_res.err()
        );

        // Close target stdin to unblock the input read stream, triggering Python signal handler evaluation
        drop(target_child.stdin_write.take());

        // Wait with bounded timeout for target child to unwind its scoped finally blocks and exit
        let exit_code =
            wait_process_timeout(target_child.process_handle, 5000).expect("Process wait failed");
        assert_eq!(
            exit_code,
            Some(130),
            "Packaged engine generate must exit exactly 130 on SIGBREAK under private_console={}",
            private_console
        );

        // Verify stdout is completely empty on cancellation (no partial wire JSON)
        let mut stdout_bytes = Vec::new();
        if let Some(mut out_read) = target_child.stdout_read.take() {
            let _ = out_read.read_to_end(&mut stdout_bytes);
        }
        assert!(
            stdout_bytes.is_empty(),
            "Stdout must be completely empty on cancellation, got {} bytes: {}",
            stdout_bytes.len(),
            String::from_utf8_lossy(&stdout_bytes)
        );

        // Verify control child is still running (wait 50ms returns None) — proves targeted delivery without signal bleed
        let control_poll = wait_process_timeout(control_child.process_handle, 50)
            .expect("Control process poll failed");
        assert_eq!(
            control_poll, None,
            "Control child must remain running and not receive signal bleed under private_console={}",
            private_console
        );

        // Cleanly terminate control child
        drop(control_child.stdin_write.take());
        let _ = wait_process_timeout(control_child.process_handle, 5000);
    }
}

#[test]
#[ignore = "Requires staged engine payload at engine/dist/cad-copilot-engine/cad-copilot-engine.exe"]
fn test_packaged_engine_gemini_bootstrap_offline_smoke() {
    let _lock = CONSOLE_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let isolated = deploy_engine_outside_checkout().expect(
        "Staged engine not found at engine/dist/cad-copilot-engine/cad-copilot-engine.exe; run 'pnpm --filter @cad-copilot/engine package:desktop' first",
    );
    let env = build_isolated_env(&isolated.engine_dir);

    let mut child = spawn_engine_process_explicit(
        &isolated.exe_path,
        &["generate"],
        &isolated.engine_dir,
        &env,
        Some(false),
    )
    .expect("Failed to spawn packaged engine generate for Gemini bootstrap smoke test");
    let _guard = ProcessGuard(child.process_handle);

    // Intentionally malformed prompt_to_cad payload violating contract schema (missing required unit and prompt).
    // This proves that during generate bootstrap, the packaged runtime successfully imports google.genai
    // and initializes without crashing, while the malformed payload is rejected purely at schema validation
    // before any provider dispatch or external network connection can occur.
    let malformed_payload =
        br#"{"contract_version":"1.0","request_id":"req-pkg-ai-smoke","kind":"prompt_to_cad"}"#;

    {
        let mut stdin = child
            .stdin_write
            .take()
            .expect("Failed to take child stdin");
        stdin
            .write_all(malformed_payload)
            .expect("Failed to write payload to child stdin");
        drop(stdin);
    }

    let mut stdout_bytes = Vec::new();
    if let Some(mut out_read) = child.stdout_read.take() {
        let _ = out_read.read_to_end(&mut stdout_bytes);
    }
    let out_str = String::from_utf8_lossy(&stdout_bytes);

    let mut stderr_bytes = Vec::new();
    if let Some(mut err_read) = child.stderr_read.take() {
        let _ = err_read.read_to_end(&mut stderr_bytes);
    }
    let err_str = String::from_utf8_lossy(&stderr_bytes);

    let exit_code = wait_process_timeout(child.process_handle, 10000).expect("Process wait failed");
    assert_eq!(
        exit_code,
        Some(0),
        "Expected exit 0 for handled schema rejection in packaged child. stderr: {err_str}"
    );

    assert!(
        out_str.contains(r#""status":"rejected""#) || out_str.contains(r#""status": "rejected""#),
        "Expected rejected status in response, got: {out_str}"
    );
    assert!(
        out_str.contains("INVALID_SCHEMA"),
        "Expected INVALID_SCHEMA error code in response, got: {out_str}"
    );
}
