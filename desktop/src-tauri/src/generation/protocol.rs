use serde::{Deserialize, Serialize};

use super::types::{CommandError, EngineStatus, RunPhase};

pub const MAX_REQUEST_PAYLOAD_BYTES: usize = 131_072; // 128 KiB
pub const MAX_STDOUT_BYTES: usize = 1_048_576; // 1 MiB
pub const MAX_STDERR_LINE_BYTES: usize = 1_024; // 1 KiB per line
pub const MAX_STDERR_AGGREGATE_BYTES: usize = 1_048_576; // 1 MiB aggregate
pub const MAX_RETAINED_PROGRESS_EVENTS: usize = 100;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WireRequest {
    pub contract_version: String,
    pub request_id: String,
    pub kind: String,
    pub unit: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub example_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub prompt: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WireProgressEvent {
    #[serde(rename = "type")]
    pub event_type: String,
    pub phase: String,
    pub message: String,
}

impl WireProgressEvent {
    pub fn parse_line(line: &str) -> Option<RunPhase> {
        let trimmed = line.trim();
        if trimmed.is_empty() || !trimmed.starts_with('{') {
            return None;
        }

        let parsed: Result<WireProgressEvent, _> = serde_json::from_str(trimmed);
        match parsed {
            Ok(evt) if evt.event_type == "progress" => match evt.phase.as_str() {
                "request_received" => Some(RunPhase::RequestReceived),
                "request_validated" => Some(RunPhase::RequestValidated),
                "generation_started" => Some(RunPhase::GenerationStarted),
                "response_ready" => Some(RunPhase::ResponseReady),
                _ => None,
            },
            _ => None,
        }
    }
}

pub const KNOWN_ERROR_CODES: &[&str] = &[
    "INVALID_SCHEMA",
    "UNSUPPORTED_REQUEST",
    "PROMPT_INTERPRETATION_FAILED",
    "CAD_PLAN_REJECTED",
    "CAD_EXECUTION_FAILED",
    "ARTIFACT_EXPORT_FAILED",
    "OUTPUT_PATH_NOT_ALLOWED",
    "INTERNAL_ERROR",
    "NATIVE_QA_BLOCKED",
    "PAYLOAD_TOO_LARGE",
    "TARGET_ALREADY_EXISTS",
];

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct WireArtifactItem {
    #[serde(rename = "type")]
    pub artifact_type: String,
    pub format: String,
    pub path: String,
    pub origin: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct WireResponseData {
    pub artifacts: Vec<WireArtifactItem>,
}

fn deserialize_optional_nonempty_field<'de, D>(deserializer: D) -> Result<Option<String>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let s: String = serde::Deserialize::deserialize(deserializer)?;
    if s.is_empty() {
        return Err(serde::de::Error::custom(
            "field must not be empty when present",
        ));
    }
    Ok(Some(s))
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct WireErrorItem {
    pub code: String,
    pub message: String,
    #[serde(
        default,
        deserialize_with = "deserialize_optional_nonempty_field",
        skip_serializing_if = "Option::is_none"
    )]
    pub field: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "status", rename_all = "snake_case", deny_unknown_fields)]
pub enum WireResponse {
    Accepted {
        contract_version: String,
        request_id: String,
        data: WireResponseData,
        warnings: Vec<String>,
    },
    Rejected {
        contract_version: String,
        request_id: String,
        errors: Vec<WireErrorItem>,
        warnings: Vec<String>,
    },
    Failed {
        contract_version: String,
        request_id: String,
        errors: Vec<WireErrorItem>,
        warnings: Vec<String>,
    },
}

impl WireResponse {
    pub fn parse_stdout(
        stdout_bytes: &[u8],
        expected_request_id: &str,
    ) -> Result<WireResponse, CommandError> {
        if stdout_bytes.len() > MAX_STDOUT_BYTES {
            return Err(CommandError::new(
                "INVALID_ENGINE_OUTPUT",
                "Engine stdout exceeded maximum permitted buffer limit.",
            ));
        }

        // Canonical framing: exactly one newline-terminated UTF-8 JSON response
        let stripped = if stdout_bytes.ends_with(b"\r\n") {
            &stdout_bytes[..stdout_bytes.len() - 2]
        } else if stdout_bytes.ends_with(b"\n") {
            &stdout_bytes[..stdout_bytes.len() - 1]
        } else {
            return Err(CommandError::new(
                "INVALID_ENGINE_OUTPUT",
                "Engine stdout missing canonical terminating newline.",
            ));
        };

        if stripped.is_empty() {
            return Err(CommandError::new(
                "INVALID_ENGINE_OUTPUT",
                "Engine stdout was empty when a response was expected.",
            ));
        }

        // Strict framing: must start with '{' and must not have leading or trailing whitespace/newlines
        if !stripped.starts_with(b"{")
            || stripped.ends_with(b"\n")
            || stripped.ends_with(b"\r")
            || stripped.ends_with(b" ")
            || stripped.ends_with(b"\t")
        {
            return Err(CommandError::new(
                "INVALID_ENGINE_OUTPUT",
                "Engine stdout contains noncanonical whitespace or invalid framing.",
            ));
        }

        let text = std::str::from_utf8(stripped).map_err(|_| {
            CommandError::new(
                "INVALID_ENGINE_OUTPUT",
                "Engine stdout contained invalid non-UTF-8 bytes.",
            )
        })?;

        let mut de = serde_json::Deserializer::from_str(text);
        let response: WireResponse = serde::Deserialize::deserialize(&mut de).map_err(|_| {
            CommandError::new(
                "INVALID_ENGINE_OUTPUT",
                "Failed to parse canonical engine response JSON.",
            )
        })?;
        de.end().map_err(|_| {
            CommandError::new(
                "INVALID_ENGINE_OUTPUT",
                "Trailing garbage detected after response JSON.",
            )
        })?;

        if response.contract_version() != "1.0" {
            return Err(CommandError::new(
                "INVALID_ENGINE_OUTPUT",
                "Unsupported engine contract version.",
            ));
        }

        if response.request_id() != expected_request_id {
            return Err(CommandError::new(
                "INVALID_ENGINE_OUTPUT",
                "Response request_id does not match expected request.",
            ));
        }

        let req_id = response.request_id();
        if req_id.is_empty()
            || req_id.len() > 96
            || !req_id
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '_' || c == '-')
        {
            return Err(CommandError::new(
                "INVALID_ENGINE_OUTPUT",
                "Response request_id does not conform to canonical schema.",
            ));
        }

        match &response {
            WireResponse::Accepted { data, .. } => {
                if data.artifacts.len() < 3 || data.artifacts.len() > 4 {
                    return Err(CommandError::new(
                        "INVALID_ENGINE_OUTPUT",
                        "Accepted response must contain 3 to 4 artifacts.",
                    ));
                }

                let mut par_count = 0;
                let mut step_count = 0;
                let mut stl_count = 0;
                let mut jpg_count = 0;

                for art in &data.artifacts {
                    if art.origin != "cad_copilot" {
                        return Err(CommandError::new(
                            "INVALID_ENGINE_OUTPUT",
                            "Artifact origin must be 'cad_copilot'.",
                        ));
                    }
                    if art.path.trim().is_empty() {
                        return Err(CommandError::new(
                            "INVALID_ENGINE_OUTPUT",
                            "Artifact path must not be empty.",
                        ));
                    }

                    match (art.artifact_type.as_str(), art.format.as_str()) {
                        ("native_part", "par") => par_count += 1,
                        ("geometry_step", "step") => step_count += 1,
                        ("mesh_stl", "stl") => stl_count += 1,
                        ("preview_image", "jpg") => jpg_count += 1,
                        _ => {
                            return Err(CommandError::new(
                                "INVALID_ENGINE_OUTPUT",
                                "Invalid artifact type or format pairing in engine response.",
                            ));
                        }
                    }
                }

                if par_count != 1 || step_count != 1 || stl_count != 1 || jpg_count > 1 {
                    return Err(CommandError::new(
                        "INVALID_ENGINE_OUTPUT",
                        "Accepted response missing required canonical artifacts (exactly 1 par, 1 step, 1 stl, at most 1 jpg).",
                    ));
                }
            }
            WireResponse::Rejected { errors, .. } | WireResponse::Failed { errors, .. } => {
                if errors.is_empty() {
                    return Err(CommandError::new(
                        "INVALID_ENGINE_OUTPUT",
                        "Rejected/failed response must include at least one error record.",
                    ));
                }
                for err in errors {
                    if !KNOWN_ERROR_CODES.contains(&err.code.as_str()) {
                        return Err(CommandError::new(
                            "INVALID_ENGINE_OUTPUT",
                            "Engine returned unknown error code.",
                        ));
                    }
                    if err.message.trim().is_empty() {
                        return Err(CommandError::new(
                            "INVALID_ENGINE_OUTPUT",
                            "Engine error record message must not be empty.",
                        ));
                    }
                }
            }
        }

        Ok(response)
    }

    pub fn contract_version(&self) -> &str {
        match self {
            Self::Accepted {
                contract_version, ..
            }
            | Self::Rejected {
                contract_version, ..
            }
            | Self::Failed {
                contract_version, ..
            } => contract_version,
        }
    }

    pub fn request_id(&self) -> &str {
        match self {
            Self::Accepted { request_id, .. }
            | Self::Rejected { request_id, .. }
            | Self::Failed { request_id, .. } => request_id,
        }
    }

    pub fn engine_status(&self) -> EngineStatus {
        match self {
            Self::Accepted { .. } => EngineStatus::Accepted,
            Self::Rejected { .. } => EngineStatus::Rejected,
            Self::Failed { .. } => EngineStatus::Failed,
        }
    }

    pub fn warnings(&self) -> &[String] {
        match self {
            Self::Accepted { warnings, .. }
            | Self::Rejected { warnings, .. }
            | Self::Failed { warnings, .. } => warnings,
        }
    }

    pub fn errors(&self) -> &[WireErrorItem] {
        match self {
            Self::Accepted { .. } => &[],
            Self::Rejected { errors, .. } | Self::Failed { errors, .. } => errors,
        }
    }

    pub fn data(&self) -> Option<&WireResponseData> {
        match self {
            Self::Accepted { data, .. } => Some(data),
            Self::Rejected { .. } | Self::Failed { .. } => None,
        }
    }

    pub fn data_cloned(&self) -> Option<WireResponseData> {
        match self {
            Self::Accepted { data, .. } => Some(data.clone()),
            Self::Rejected { .. } | Self::Failed { .. } => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_progress_event_lines() {
        let line1 =
            r#"{"type":"progress","phase":"request_received","message":"Request received."}"#;
        assert_eq!(
            WireProgressEvent::parse_line(line1),
            Some(RunPhase::RequestReceived)
        );

        let line2 = r#"{"type":"progress","phase":"request_validated","message":"Validated."}"#;
        assert_eq!(
            WireProgressEvent::parse_line(line2),
            Some(RunPhase::RequestValidated)
        );

        let line3 = r#"{"type":"progress","phase":"generation_started","message":"Started."}"#;
        assert_eq!(
            WireProgressEvent::parse_line(line3),
            Some(RunPhase::GenerationStarted)
        );

        let line4 = r#"{"type":"progress","phase":"response_ready","message":"Ready."}"#;
        assert_eq!(
            WireProgressEvent::parse_line(line4),
            Some(RunPhase::ResponseReady)
        );

        // Invalid or diagnostic lines return None
        assert_eq!(WireProgressEvent::parse_line(""), None);
        assert_eq!(WireProgressEvent::parse_line("not json"), None);
        assert_eq!(
            WireProgressEvent::parse_line(r#"{"type":"diagnostic","phase":"fatal"}"#),
            None
        );
    }

    #[test]
    fn test_parse_wire_response_success() {
        let json_bytes = b"{\"contract_version\":\"1.0\",\"request_id\":\"req_1\",\"status\":\"accepted\",\"data\":{\"artifacts\":[{\"type\":\"native_part\",\"format\":\"par\",\"path\":\"C:\\\\out\\\\model.par\",\"origin\":\"cad_copilot\"},{\"type\":\"geometry_step\",\"format\":\"step\",\"path\":\"C:\\\\out\\\\model.step\",\"origin\":\"cad_copilot\"},{\"type\":\"mesh_stl\",\"format\":\"stl\",\"path\":\"C:\\\\out\\\\model.stl\",\"origin\":\"cad_copilot\"}]},\"warnings\":[\"Minor warning\"]}\n";
        let res = WireResponse::parse_stdout(json_bytes, "req_1").unwrap();
        assert_eq!(res.request_id(), "req_1");
        assert_eq!(res.engine_status(), EngineStatus::Accepted);
        assert_eq!(res.data().unwrap().artifacts.len(), 3);
        assert_eq!(res.warnings().len(), 1);
    }

    #[test]
    fn test_parse_wire_response_missing_newline_rejected() {
        let json_bytes = b"{\"contract_version\":\"1.0\",\"request_id\":\"req_1\",\"status\":\"accepted\",\"data\":{\"artifacts\":[{\"type\":\"native_part\",\"format\":\"par\",\"path\":\"a\",\"origin\":\"cad_copilot\"},{\"type\":\"geometry_step\",\"format\":\"step\",\"path\":\"b\",\"origin\":\"cad_copilot\"},{\"type\":\"mesh_stl\",\"format\":\"stl\",\"path\":\"c\",\"origin\":\"cad_copilot\"}]},\"warnings\":[]}";
        let err = WireResponse::parse_stdout(json_bytes, "req_1").unwrap_err();
        assert_eq!(err.code, "INVALID_ENGINE_OUTPUT");
        assert!(err
            .message
            .contains("missing canonical terminating newline"));
    }

    #[test]
    fn test_parse_wire_response_noncanonical_whitespace_rejected() {
        let json_bytes = b"  {\"contract_version\":\"1.0\",\"request_id\":\"req_1\",\"status\":\"accepted\",\"data\":{\"artifacts\":[{\"type\":\"native_part\",\"format\":\"par\",\"path\":\"a\",\"origin\":\"cad_copilot\"},{\"type\":\"geometry_step\",\"format\":\"step\",\"path\":\"b\",\"origin\":\"cad_copilot\"},{\"type\":\"mesh_stl\",\"format\":\"stl\",\"path\":\"c\",\"origin\":\"cad_copilot\"}]},\"warnings\":[]}\n";
        let err = WireResponse::parse_stdout(json_bytes, "req_1").unwrap_err();
        assert_eq!(err.code, "INVALID_ENGINE_OUTPUT");
        assert!(err.message.contains("noncanonical whitespace"));
    }

    #[test]
    fn test_parse_wire_response_unknown_field_rejected() {
        let json_bytes = b"{\"contract_version\":\"1.0\",\"request_id\":\"req_1\",\"status\":\"accepted\",\"extra_field\":\"bad\",\"data\":{\"artifacts\":[{\"type\":\"native_part\",\"format\":\"par\",\"path\":\"a\",\"origin\":\"cad_copilot\"},{\"type\":\"geometry_step\",\"format\":\"step\",\"path\":\"b\",\"origin\":\"cad_copilot\"},{\"type\":\"mesh_stl\",\"format\":\"stl\",\"path\":\"c\",\"origin\":\"cad_copilot\"}]},\"warnings\":[]}\n";
        let err = WireResponse::parse_stdout(json_bytes, "req_1").unwrap_err();
        assert_eq!(err.code, "INVALID_ENGINE_OUTPUT");
        assert!(err
            .message
            .contains("Failed to parse canonical engine response JSON"));
    }

    #[test]
    fn test_parse_wire_response_invalid_artifact_type_rejected() {
        let json_bytes = b"{\"contract_version\":\"1.0\",\"request_id\":\"req_1\",\"status\":\"accepted\",\"data\":{\"artifacts\":[{\"type\":\"cad_part\",\"format\":\"par\",\"path\":\"a\",\"origin\":\"cad_copilot\"},{\"type\":\"geometry_step\",\"format\":\"step\",\"path\":\"b\",\"origin\":\"cad_copilot\"},{\"type\":\"mesh_stl\",\"format\":\"stl\",\"path\":\"c\",\"origin\":\"cad_copilot\"}]},\"warnings\":[]}\n";
        let err = WireResponse::parse_stdout(json_bytes, "req_1").unwrap_err();
        assert_eq!(err.code, "INVALID_ENGINE_OUTPUT");
        assert!(err
            .message
            .contains("Invalid artifact type or format pairing"));
    }

    #[test]
    fn test_parse_wire_response_rejection_valid() {
        let json_bytes = b"{\"contract_version\":\"1.0\",\"request_id\":\"req_2\",\"status\":\"rejected\",\"errors\":[{\"code\":\"INVALID_SCHEMA\",\"message\":\"Bad input\",\"field\":\"prompt\"}],\"warnings\":[]}\n";
        let res = WireResponse::parse_stdout(json_bytes, "req_2").unwrap();
        assert_eq!(res.engine_status(), EngineStatus::Rejected);
        assert_eq!(res.errors()[0].code, "INVALID_SCHEMA");
        assert_eq!(res.errors()[0].field.as_deref(), Some("prompt"));
    }

    #[test]
    fn test_parse_wire_response_null_field_rejected() {
        let json_bytes = b"{\"contract_version\":\"1.0\",\"request_id\":\"req_2\",\"status\":\"rejected\",\"errors\":[{\"code\":\"INVALID_SCHEMA\",\"message\":\"Bad input\",\"field\":null}],\"warnings\":[]}\n";
        let err = WireResponse::parse_stdout(json_bytes, "req_2").unwrap_err();
        assert_eq!(err.code, "INVALID_ENGINE_OUTPUT");
    }

    #[test]
    fn test_parse_wire_response_empty_field_rejected() {
        let json_bytes = b"{\"contract_version\":\"1.0\",\"request_id\":\"req_2\",\"status\":\"rejected\",\"errors\":[{\"code\":\"INVALID_SCHEMA\",\"message\":\"Bad input\",\"field\":\"\"}],\"warnings\":[]}\n";
        let err = WireResponse::parse_stdout(json_bytes, "req_2").unwrap_err();
        assert_eq!(err.code, "INVALID_ENGINE_OUTPUT");
    }

    #[test]
    fn test_parse_wire_response_request_id_mismatch_fails() {
        let json_bytes = b"{\"contract_version\":\"1.0\",\"request_id\":\"req_wrong\",\"status\":\"rejected\",\"errors\":[{\"code\":\"INVALID_SCHEMA\",\"message\":\"Bad\"}],\"warnings\":[]}\n";
        let err = WireResponse::parse_stdout(json_bytes, "req_expected").unwrap_err();
        assert_eq!(err.code, "INVALID_ENGINE_OUTPUT");
        assert!(err.message.contains("does not match expected"));
    }

    #[test]
    fn test_parse_wire_response_trailing_garbage_fails() {
        let json_bytes = b"{\"contract_version\":\"1.0\",\"request_id\":\"req_1\",\"status\":\"accepted\",\"data\":{\"artifacts\":[{\"type\":\"native_part\",\"format\":\"par\",\"path\":\"a\",\"origin\":\"cad_copilot\"},{\"type\":\"geometry_step\",\"format\":\"step\",\"path\":\"b\",\"origin\":\"cad_copilot\"},{\"type\":\"mesh_stl\",\"format\":\"stl\",\"path\":\"c\",\"origin\":\"cad_copilot\"}]},\"warnings\":[]} EXTRA_GARBAGE\n";
        let err = WireResponse::parse_stdout(json_bytes, "req_1").unwrap_err();
        assert_eq!(err.code, "INVALID_ENGINE_OUTPUT");
        assert!(err.message.contains("Trailing garbage"));
    }

    #[test]
    fn test_parse_wire_response_empty_or_oversize_fails() {
        assert_eq!(
            WireResponse::parse_stdout(b"", "req_1").unwrap_err().code,
            "INVALID_ENGINE_OUTPUT"
        );

        let huge = vec![b' '; MAX_STDOUT_BYTES + 1];
        assert_eq!(
            WireResponse::parse_stdout(&huge, "req_1").unwrap_err().code,
            "INVALID_ENGINE_OUTPUT"
        );
    }
}
