use std::collections::HashMap;
use std::path::Path;

pub use crate::shared::engine::*;

use super::protocol::{WireRequest, MAX_REQUEST_PAYLOAD_BYTES};
use super::types::{CommandError, GenerationInput};

/// Assembles a sanitized environment map for the child engine process.
pub fn prepare_child_env_for_launch(
    launch: &EngineLaunch,
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

    let support_dir = launch.program.parent().unwrap_or(&launch.cwd);
    let path_val = if launch.is_packaged {
        let sys_root = env
            .get("SYSTEMROOT")
            .cloned()
            .or_else(|| env.get("WINDIR").cloned())
            .unwrap_or_else(|| "C:\\Windows".to_string());
        format!(
            "{};{}\\System32;{};{}\\System32\\Wbem",
            support_dir.display(),
            sys_root,
            sys_root,
            sys_root
        )
    } else {
        let original_path = std::env::var("PATH").unwrap_or_default();
        format!("{};{}", support_dir.display(), original_path)
    };
    env.insert("PATH".to_string(), path_val);

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

pub fn prepare_child_env(
    layout: &SourceLayout,
    output_path: &Path,
    session_key: Option<&str>,
    is_prompt: bool,
) -> HashMap<String, String> {
    let launch = EngineLaunch {
        program: layout.python_exe.clone(),
        args: vec!["-m".to_string(), "ipc".to_string()],
        cwd: layout.repo_root.clone(),
        is_packaged: false,
    };
    prepare_child_env_for_launch(&launch, output_path, session_key, is_prompt)
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
}
