use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::time::Duration;

use super::protocol::{WireRequest, MAX_REQUEST_PAYLOAD_BYTES};
use super::types::{CleanupState, CommandError, GenerationInput};

pub const PROBE_TIMEOUT_SECS: u64 = 5;
pub const MAX_PROBE_OUTPUT_BYTES: usize = 65_536; // 64 KiB

#[derive(Debug, Clone)]
pub struct SourceLayout {
    pub repo_root: PathBuf,
    pub python_exe: PathBuf,
    pub engine_dir: PathBuf,
    pub cargo_manifest: PathBuf,
    pub generate_cmd: PathBuf,
    pub batch_cmd: PathBuf,
}

impl SourceLayout {
    pub fn verify_files(&self) -> bool {
        self.cargo_manifest.is_file()
            && self.python_exe.is_file()
            && self.generate_cmd.is_file()
            && self.batch_cmd.is_file()
            && self.engine_dir.join("pyproject.toml").is_file()
    }
}

/// Resolves the repository root and verified source layout from executable or manifest ancestry.
pub fn resolve_source_layout() -> Result<SourceLayout, CommandError> {
    let mut candidates: Vec<PathBuf> = Vec::new();

    if let Ok(exe) = std::env::current_exe() {
        let mut curr = exe.parent();
        let mut count = 0;
        while let Some(parent) = curr {
            if count > 8 {
                break;
            }
            candidates.push(parent.to_path_buf());
            curr = parent.parent();
            count += 1;
        }
    }

    if let Some(manifest_dir) = option_env!("CARGO_MANIFEST_DIR") {
        let manifest_path = PathBuf::from(manifest_dir);
        let mut curr = Some(manifest_path.as_path());
        let mut count = 0;
        while let Some(parent) = curr {
            if count > 5 {
                break;
            }
            candidates.push(parent.to_path_buf());
            curr = parent.parent();
            count += 1;
        }
    }

    for cand in candidates {
        let cargo_manifest = cand.join("desktop").join("src-tauri").join("Cargo.toml");
        let engine_dir = cand.join("engine");
        let generate_cmd = engine_dir.join("scripts").join("generate.cmd");
        let batch_cmd = engine_dir.join("scripts").join("batch.cmd");
        let python_exe = cand.join(".venv").join("Scripts").join("python.exe");

        let layout = SourceLayout {
            repo_root: cand,
            python_exe,
            engine_dir,
            cargo_manifest,
            generate_cmd,
            batch_cmd,
        };

        if layout.verify_files() {
            return Ok(layout);
        }
    }

    Err(CommandError::new(
        "ENGINE_UNAVAILABLE",
        "Failed to resolve CAD Copilot repository root and Python virtual environment.",
    ))
}

#[derive(Debug, Clone)]
pub struct ProbeError {
    pub error: CommandError,
    pub cleanup: CleanupState,
}

impl std::fmt::Display for ProbeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.error.code, self.error.message)
    }
}

impl std::error::Error for ProbeError {}

impl ProbeError {
    pub fn new(error: CommandError, cleanup: CleanupState) -> Self {
        Self { error, cleanup }
    }
}

#[derive(Debug)]
enum ProbeStreamResult {
    Ok,
    BufferLimitExceeded,
    IoError(std::io::Error),
}

fn read_probe_stream<R: std::io::Read>(mut reader: R) -> ProbeStreamResult {
    let mut total = 0;
    let mut chunk = [0u8; 4096];
    loop {
        match reader.read(&mut chunk) {
            Ok(0) => break,
            Ok(n) => {
                total += n;
                if total > MAX_PROBE_OUTPUT_BYTES {
                    return ProbeStreamResult::BufferLimitExceeded;
                }
            }
            Err(e) => return ProbeStreamResult::IoError(e),
        }
    }
    ProbeStreamResult::Ok
}

/// Runs the 5-second prerequisite probe on the resolved Python interpreter.
pub fn run_prerequisite_probe(
    layout: &SourceLayout,
    input: &GenerationInput,
) -> Result<(), ProbeError> {
    let probe_code = match input {
        GenerationInput::ExamplePlan { .. } => {
            "import importlib.metadata as m; import ipc.stdio; v = m.version('cad-copilot'); assert v == '0.1.0'"
        }
        GenerationInput::PromptToCad { .. } => {
            "import importlib.metadata as m; import ipc.stdio; import google.genai; v = m.version('cad-copilot'); assert v == '0.1.0'"
        }
    };

    let mut cmd = std::process::Command::new(&layout.python_exe);
    cmd.arg("-c").arg(probe_code);
    cmd.current_dir(&layout.repo_root);
    cmd.stdin(std::process::Stdio::null());
    cmd.stdout(std::process::Stdio::piped());
    cmd.stderr(std::process::Stdio::piped());

    // Isolate probe environment
    cmd.env_clear();

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    // Basic system env
    for (k, v) in std::env::vars() {
        let k_up = k.to_uppercase();
        if k_up == "SYSTEMROOT"
            || k_up == "SYSTEMDRIVE"
            || k_up == "WINDIR"
            || k_up == "PATH"
            || k_up == "TEMP"
            || k_up == "TMP"
        {
            cmd.env(k, v);
        }
    }

    let mut child = cmd.spawn().map_err(|e| {
        ProbeError::new(
            CommandError::new(
                "ENGINE_UNAVAILABLE",
                format!("Failed to spawn prerequisite probe: {e}"),
            ),
            CleanupState::NoFailureObserved,
        )
    })?;

    #[cfg(windows)]
    let owned_handle = {
        use std::os::windows::io::AsRawHandle;
        let raw = child.as_raw_handle() as windows_sys::Win32::Foundation::HANDLE;
        match super::windows::duplicate_process_handle(raw) {
            Ok(h) => h,
            Err(e) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(ProbeError::new(e, CleanupState::Incomplete));
            }
        }
    };

    let (tx, rx) = std::sync::mpsc::channel();
    let child_stdout = child.stdout.take();
    let child_stderr = child.stderr.take();

    let supervisor_handle = std::thread::spawn(move || {
        let stdout_handle = std::thread::spawn(move || -> ProbeStreamResult {
            if let Some(r) = child_stdout {
                read_probe_stream(r)
            } else {
                ProbeStreamResult::Ok
            }
        });

        let stderr_handle = std::thread::spawn(move || -> ProbeStreamResult {
            if let Some(r) = child_stderr {
                read_probe_stream(r)
            } else {
                ProbeStreamResult::Ok
            }
        });

        let status = child.wait();
        let stdout_res = stdout_handle.join();
        let stderr_res = stderr_handle.join();

        let _ = tx.send((status, stdout_res, stderr_res));
    });

    let timeout = Duration::from_secs(PROBE_TIMEOUT_SECS);
    match rx.recv_timeout(timeout) {
        Ok((status_res, stdout_join, stderr_join)) => {
            let _ = supervisor_handle.join();

            // First: Verify child process termination wait outcome
            let process_exit_status = match status_res {
                Ok(status) => status,
                Err(e) => {
                    #[cfg(windows)]
                    {
                        let _ = super::windows::force_terminate_process(owned_handle.raw(), 1);
                        let _ = super::windows::wait_process_timeout(owned_handle.raw(), 2000);
                    }
                    return Err(ProbeError::new(
                        CommandError::new(
                            "ENGINE_UNAVAILABLE",
                            format!("Prerequisite probe process wait error: {e}"),
                        ),
                        CleanupState::Incomplete,
                    ));
                }
            };

            // Second: Check for worker thread panics
            let stdout_res = match stdout_join {
                Ok(r) => r,
                Err(_) => {
                    return Err(ProbeError::new(
                        CommandError::new(
                            "TRANSPORT_ERROR",
                            "Prerequisite probe stdout reader thread panicked.",
                        ),
                        CleanupState::Incomplete,
                    ));
                }
            };
            let stderr_res = match stderr_join {
                Ok(r) => r,
                Err(_) => {
                    return Err(ProbeError::new(
                        CommandError::new(
                            "TRANSPORT_ERROR",
                            "Prerequisite probe stderr reader thread panicked.",
                        ),
                        CleanupState::Incomplete,
                    ));
                }
            };

            // Process has exited cleanly and threads joined without panic.
            // Check for buffer limit exceedance
            if matches!(stdout_res, ProbeStreamResult::BufferLimitExceeded)
                || matches!(stderr_res, ProbeStreamResult::BufferLimitExceeded)
            {
                return Err(ProbeError::new(
                    CommandError::new(
                        "ENGINE_UNAVAILABLE",
                        "Prerequisite probe output exceeded 64 KiB buffer limit.",
                    ),
                    CleanupState::NoFailureObserved,
                ));
            }

            // Check for reader I/O errors
            if let ProbeStreamResult::IoError(e) = stdout_res {
                return Err(ProbeError::new(
                    CommandError::new(
                        "ENGINE_UNAVAILABLE",
                        format!("Prerequisite probe stdout I/O error: {e}"),
                    ),
                    CleanupState::NoFailureObserved,
                ));
            }
            if let ProbeStreamResult::IoError(e) = stderr_res {
                return Err(ProbeError::new(
                    CommandError::new(
                        "ENGINE_UNAVAILABLE",
                        format!("Prerequisite probe stderr I/O error: {e}"),
                    ),
                    CleanupState::NoFailureObserved,
                ));
            }

            if !process_exit_status.success() {
                return Err(ProbeError::new(
                    CommandError::new(
                        "ENGINE_UNAVAILABLE",
                        "Prerequisite probe failed: cad-copilot or provider dependencies not found in .venv.",
                    ),
                    CleanupState::NoFailureObserved,
                ));
            }
            Ok(())
        }
        Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {
            #[cfg(windows)]
            let force_ok = super::windows::force_terminate_process(owned_handle.raw(), 1).is_ok();
            #[cfg(windows)]
            let wait_ok = matches!(
                super::windows::wait_process_timeout(owned_handle.raw(), 2000),
                Ok(Some(_))
            );
            #[cfg(not(windows))]
            let (force_ok, wait_ok) = (false, false);

            let supervisor_joined = {
                let start = std::time::Instant::now();
                let join_timeout = Duration::from_millis(500);
                let mut finished = false;
                while start.elapsed() < join_timeout {
                    if supervisor_handle.is_finished() {
                        finished = supervisor_handle.join().is_ok();
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(10));
                }
                finished
            };

            let cleanup = if force_ok && wait_ok && supervisor_joined {
                CleanupState::NoFailureObserved
            } else {
                CleanupState::Incomplete
            };

            Err(ProbeError::new(
                CommandError::new(
                    "ENGINE_UNAVAILABLE",
                    "Prerequisite probe timed out after 5 seconds.",
                ),
                cleanup,
            ))
        }
        Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {
            #[cfg(windows)]
            let _ = super::windows::force_terminate_process(owned_handle.raw(), 1);
            #[cfg(windows)]
            let _ = super::windows::wait_process_timeout(owned_handle.raw(), 2000);

            Err(ProbeError::new(
                CommandError::new(
                    "ENGINE_UNAVAILABLE",
                    "Prerequisite probe worker thread disconnected unexpectedly.",
                ),
                CleanupState::Incomplete,
            ))
        }
    }
}

/// Assembles a sanitized environment map for the child engine process.
pub fn prepare_child_env(
    layout: &SourceLayout,
    output_path: &Path,
    session_key: Option<&str>,
    is_prompt: bool,
) -> HashMap<String, String> {
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

    // Path handling: ensure .venv/Scripts is prepended to PATH
    let venv_scripts = layout.python_exe.parent().unwrap_or(&layout.repo_root);
    let original_path = std::env::var("PATH").unwrap_or_default();
    let combined_path = format!("{};{}", venv_scripts.display(), original_path);
    env.insert("PATH".to_string(), combined_path);

    // Slice-owned configuration
    env.insert("PYTHONUNBUFFERED".to_string(), "1".to_string());
    env.insert(
        "CAD_OUTPUT_ROOT".to_string(),
        output_path.to_string_lossy().to_string(),
    );

    if is_prompt {
        if let Some(key) = session_key {
            env.insert("GEMINI_API_KEY".to_string(), key.to_string());
        }
    } else {
        // Explicitly ensure GEMINI_API_KEY is not present
        env.remove("GEMINI_API_KEY");
    }

    // Explicitly ensure Python overrides are absent
    env.remove("PYTHONPATH");
    env.remove("PYTHONHOME");
    env.remove("PYTHONSTARTUP");
    env.remove("PYTHONINSPECT");
    env.remove("PYTHONDEBUG");

    env
}

/// Formats and validates the wire request JSON payload.
pub fn format_request_payload(
    request_id: &str,
    input: &GenerationInput,
) -> Result<Vec<u8>, CommandError> {
    let wire_req = match input {
        GenerationInput::ExamplePlan { example_id } => WireRequest {
            contract_version: "1.0".to_string(),
            request_id: request_id.to_string(),
            kind: "example_plan".to_string(),
            unit: "mm".to_string(),
            example_id: Some(example_id.clone()),
            prompt: None,
        },
        GenerationInput::PromptToCad { prompt } => WireRequest {
            contract_version: "1.0".to_string(),
            request_id: request_id.to_string(),
            kind: "prompt_to_cad".to_string(),
            unit: "mm".to_string(),
            example_id: None,
            prompt: Some(prompt.clone()),
        },
    };

    let mut bytes = serde_json::to_vec(&wire_req).map_err(|e| {
        CommandError::new(
            "INTERNAL_ERROR",
            format!("Failed to serialize request payload: {e}"),
        )
    })?;
    bytes.push(b'\n');

    if bytes.len() > MAX_REQUEST_PAYLOAD_BYTES {
        return Err(CommandError::new(
            "INVALID_INPUT",
            format!(
                "Serialized request payload exceeds maximum permitted limit of {} bytes.",
                MAX_REQUEST_PAYLOAD_BYTES
            ),
        ));
    }

    Ok(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_resolve_source_layout_succeeds() {
        let layout = resolve_source_layout().expect("Failed to resolve repo layout in tests");
        assert!(layout.verify_files());
        assert!(layout.python_exe.ends_with(r".venv\Scripts\python.exe"));
    }

    #[test]
    fn test_format_request_payload_example() {
        let input = GenerationInput::ExamplePlan {
            example_id: "spur_gear".to_string(),
        };
        let payload = format_request_payload("req_test_1", &input).unwrap();
        let s = std::str::from_utf8(&payload).unwrap();
        assert!(s.contains(r#""contract_version":"1.0""#));
        assert!(s.contains(r#""kind":"example_plan""#));
        assert!(s.contains(r#""example_id":"spur_gear""#));
        assert!(s.ends_with('\n'));
    }

    #[test]
    fn test_format_request_payload_prompt() {
        let input = GenerationInput::PromptToCad {
            prompt: "Create a miter gear".to_string(),
        };
        let payload = format_request_payload("req_test_2", &input).unwrap();
        let s = std::str::from_utf8(&payload).unwrap();
        assert!(s.contains(r#""contract_version":"1.0""#));
        assert!(s.contains(r#""kind":"prompt_to_cad""#));
        assert!(s.contains(r#""prompt":"Create a miter gear""#));
        assert!(s.ends_with('\n'));
    }

    #[test]
    fn test_prepare_child_env_strips_sensitive_and_overrides() {
        let layout = resolve_source_layout().unwrap();
        let output_dir = Path::new(r"C:\test_out");

        // Example plan: no GEMINI_API_KEY
        let env_example = prepare_child_env(&layout, output_dir, Some("secret_key"), false);
        assert!(!env_example.contains_key("GEMINI_API_KEY"));
        assert!(!env_example.contains_key("PYTHONPATH"));
        assert_eq!(env_example.get("PYTHONUNBUFFERED").unwrap(), "1");
        assert_eq!(env_example.get("CAD_OUTPUT_ROOT").unwrap(), r"C:\test_out");

        // Prompt mode: injects GEMINI_API_KEY
        let env_prompt = prepare_child_env(&layout, output_dir, Some("secret_key"), true);
        assert_eq!(env_prompt.get("GEMINI_API_KEY").unwrap(), "secret_key");
        assert!(!env_prompt.contains_key("PYTHONPATH"));
    }

    #[test]
    fn test_run_prerequisite_probe_example_plan() {
        let layout = resolve_source_layout().unwrap();
        let input = GenerationInput::ExamplePlan {
            example_id: "spur_gear".to_string(),
        };
        let res = run_prerequisite_probe(&layout, &input);
        assert!(res.is_ok(), "Probe failed: {:?}", res);
    }
}
