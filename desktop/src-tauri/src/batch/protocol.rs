use std::collections::{HashMap, HashSet};
use std::path::Path;
use std::sync::OnceLock;

use serde::de::{self, Deserializer, MapAccess, SeqAccess, Visitor};
use serde::{Deserialize, Serialize};

use super::types::{
    BatchArtifactRecord, BatchFileRowRecord, BatchPhase, BatchRowCategory, BatchSummaryRecord,
    CommandError,
};

pub const MAX_REQUEST_PAYLOAD_BYTES: usize = 131_072; // 128 KiB
pub const MAX_STDOUT_BYTES: usize = 10_485_760; // 10 MiB
pub const MAX_STDERR_LINE_BYTES: usize = 4_096; // 4 KiB
pub const MAX_STDERR_AGGREGATE_BYTES: usize = 20_971_520; // 20 MiB
pub const MAX_MANIFEST_BYTES: usize = 10_485_760; // 10 MiB

pub const APPROVED_DIAGNOSTIC_CODES: &[&str] = &[
    "INVALID_SCHEMA",
    "PAYLOAD_TOO_LARGE",
    "UNSUPPORTED_OPERATION",
    "UNSUPPORTED_FORMAT",
    "INPUT_ROOT_NOT_FOUND",
    "INPUT_PATH_NOT_ALLOWED",
    "INPUT_FILE_NOT_FOUND",
    "INPUT_EXTENSION_NOT_ALLOWED",
    "OUTPUT_ROOT_UNAVAILABLE",
    "OUTPUT_TARGET_COLLISION",
    "TARGET_ALREADY_EXISTS",
    "SOLID_EDGE_UNAVAILABLE",
    "SOLID_EDGE_UNHEALTHY",
    "DOCUMENT_OPEN_FAILED",
    "ARTIFACT_EXPORT_FAILED",
    "DOCUMENT_CLOSE_FAILED",
    "SOURCE_INTEGRITY_FAILED",
    "MANIFEST_PUBLICATION_FAILED",
    "VERSION_METADATA_UNAVAILABLE",
    "INTERNAL_ERROR",
    "BATCH_CANCELLED",
];

pub const APPROVED_WARNING_CODES: &[&str] = &["VERSION_METADATA_UNAVAILABLE"];

pub const FATAL_RESPONSE_TOP_LEVEL_CODES: &[&str] = &[
    "SOLID_EDGE_UNHEALTHY",
    "DOCUMENT_CLOSE_FAILED",
    "SOURCE_INTEGRITY_FAILED",
    "INTERNAL_ERROR",
    "MANIFEST_PUBLICATION_FAILED",
];

// Embed canonical schemas from repository contracts
pub const BATCH_REQUEST_SCHEMA_STR: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../contracts/schemas/batch/batch-request.schema.json"
));
pub const BATCH_RESPONSE_SCHEMA_STR: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../contracts/schemas/batch/batch-response.schema.json"
));
pub const BATCH_MANIFEST_SCHEMA_STR: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../contracts/schemas/batch/batch-manifest-v1.schema.json"
));

static REQUEST_VALIDATOR: OnceLock<jsonschema::Validator> = OnceLock::new();
static RESPONSE_VALIDATOR: OnceLock<jsonschema::Validator> = OnceLock::new();
static MANIFEST_VALIDATOR: OnceLock<jsonschema::Validator> = OnceLock::new();

pub fn get_batch_request_validator() -> &'static jsonschema::Validator {
    REQUEST_VALIDATOR.get_or_init(|| {
        let schema_json: serde_json::Value = serde_json::from_str(BATCH_REQUEST_SCHEMA_STR)
            .expect("canonical batch-request.schema.json must be valid JSON");
        jsonschema::options()
            .should_validate_formats(false)
            .build(&schema_json)
            .expect("canonical batch-request.schema.json must compile cleanly")
    })
}

pub fn get_batch_response_validator() -> &'static jsonschema::Validator {
    RESPONSE_VALIDATOR.get_or_init(|| {
        // Rust's regex engine rejects \0 inside character classes; normalize \0 to \x00 in-memory
        let normalized = BATCH_RESPONSE_SCHEMA_STR.replace(r#"[^\\0]"#, r#"[^\\x00]"#);
        let schema_json: serde_json::Value = serde_json::from_str(&normalized)
            .expect("canonical batch-response.schema.json must be valid JSON");
        jsonschema::options()
            .should_validate_formats(false)
            .build(&schema_json)
            .expect("canonical batch-response.schema.json must compile cleanly")
    })
}

pub fn get_batch_manifest_validator() -> &'static jsonschema::Validator {
    MANIFEST_VALIDATOR.get_or_init(|| {
        // Rust's regex engine rejects \0 inside character classes; normalize \0 to \x00 in-memory
        let normalized = BATCH_MANIFEST_SCHEMA_STR.replace(r#":\\0]"#, r#":\\x00]"#);
        let schema_json: serde_json::Value = serde_json::from_str(&normalized)
            .expect("canonical batch-manifest-v1.schema.json must be valid JSON");
        jsonschema::options()
            .should_validate_formats(false)
            .build(&schema_json)
            .expect("canonical batch-manifest-v1.schema.json must compile cleanly")
    })
}

// Wire request structures
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BatchWireInput {
    pub root: String,
    pub files: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BatchWireOperation {
    #[serde(rename = "type")]
    pub operation_type: String,
    pub formats: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BatchWireOptions {
    pub continue_on_error: bool,
    pub max_files: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BatchWireMetadata {
    pub source: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BatchWireRequest {
    pub contract_version: String,
    pub request_id: String,
    pub kind: String,
    pub input: BatchWireInput,
    pub output_root: String,
    pub operation: BatchWireOperation,
    pub options: BatchWireOptions,
    pub metadata: BatchWireMetadata,
}

/// Builds and validates the JSON wire request payload, terminating with LF.
#[allow(clippy::too_many_arguments)]
pub fn build_and_serialize_request(
    request_id: &str,
    source_root: &Path,
    files: &[String],
    output_root: &Path,
    operation: &str,
    formats: &[String],
    continue_on_error: bool,
    max_files: u32,
) -> Result<Vec<u8>, CommandError> {
    if files.is_empty() || files.len() > 500 {
        return Err(CommandError::new(
            "INVALID_SCHEMA",
            "Batch request input.files must contain between 1 and 500 files.",
        ));
    }

    // Portable forward slash normalized paths
    let root_str = source_root.to_string_lossy().replace('\\', "/");
    let out_str = output_root.to_string_lossy().replace('\\', "/");

    let wire_req = BatchWireRequest {
        contract_version: "1.0".to_string(),
        request_id: request_id.to_string(),
        kind: "batch_operation".to_string(),
        input: BatchWireInput {
            root: root_str,
            files: files.to_vec(),
        },
        output_root: out_str,
        operation: BatchWireOperation {
            operation_type: operation.to_string(),
            formats: formats.to_vec(),
        },
        options: BatchWireOptions {
            continue_on_error,
            max_files,
        },
        metadata: BatchWireMetadata {
            source: "desktop_app".to_string(),
        },
    };

    let serialized_val = serde_json::to_value(&wire_req).map_err(|e| {
        CommandError::new(
            "INTERNAL_ERROR",
            format!("Failed to serialize wire request to JSON value: {}", e),
        )
    })?;

    let validator = get_batch_request_validator();
    if let Err(err) = validator.validate(&serialized_val) {
        return Err(CommandError::new(
            "INVALID_SCHEMA",
            format!(
                "Batch wire request failed schema validation at {}: {}",
                err.instance_path, err
            ),
        ));
    }

    let mut json_bytes = serde_json::to_vec(&wire_req).map_err(|e| {
        CommandError::new(
            "INTERNAL_ERROR",
            format!("Failed to serialize wire request to JSON: {}", e),
        )
    })?;
    json_bytes.push(b'\n');

    if json_bytes.len() > MAX_REQUEST_PAYLOAD_BYTES {
        return Err(CommandError::new(
            "PAYLOAD_TOO_LARGE",
            format!(
                "Serialized batch request payload size ({} bytes) exceeds limit of {} bytes.",
                json_bytes.len(),
                MAX_REQUEST_PAYLOAD_BYTES
            ),
        ));
    }

    Ok(json_bytes)
}

// Stderr progress event structures
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BatchStderrItem {
    Progress(BatchProgressEvent),
    FatalDiagnostic(String),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BatchProgressEvent {
    pub request_id: String,
    pub phase: BatchPhase,
    pub total_files: u32,
    pub completed_files: u32,
    pub current_file: Option<String>,
    pub current_format: Option<String>,
    pub file_status: Option<String>,
}

struct NoDuplicateKeys;

impl<'de> Visitor<'de> for NoDuplicateKeys {
    type Value = ();

    fn expecting(&self, formatter: &mut std::fmt::Formatter) -> std::fmt::Result {
        formatter.write_str("any valid JSON value")
    }

    fn visit_bool<E>(self, _v: bool) -> Result<(), E> {
        Ok(())
    }

    fn visit_i64<E>(self, _v: i64) -> Result<(), E> {
        Ok(())
    }

    fn visit_u64<E>(self, _v: u64) -> Result<(), E> {
        Ok(())
    }

    fn visit_f64<E>(self, _v: f64) -> Result<(), E> {
        Ok(())
    }

    fn visit_str<E>(self, _v: &str) -> Result<(), E> {
        Ok(())
    }

    fn visit_none<E>(self) -> Result<(), E> {
        Ok(())
    }

    fn visit_some<D>(self, deserializer: D) -> Result<(), D::Error>
    where
        D: Deserializer<'de>,
    {
        deserializer.deserialize_any(NoDuplicateKeys)
    }

    fn visit_unit<E>(self) -> Result<(), E> {
        Ok(())
    }

    fn visit_seq<A>(self, mut seq: A) -> Result<(), A::Error>
    where
        A: SeqAccess<'de>,
    {
        while let Some(()) = seq.next_element_seed(NoDuplicateKeysSeed)? {
            // verified element
        }
        Ok(())
    }

    fn visit_map<A>(self, mut map: A) -> Result<(), A::Error>
    where
        A: MapAccess<'de>,
    {
        let mut seen = HashSet::new();
        while let Some(key) = map.next_key::<String>()? {
            if !seen.insert(key.clone()) {
                return Err(de::Error::custom(format!(
                    "Duplicate key '{}' in JSON object.",
                    key
                )));
            }
            let () = map.next_value_seed(NoDuplicateKeysSeed)?;
        }
        Ok(())
    }
}

struct NoDuplicateKeysSeed;

impl<'de> de::DeserializeSeed<'de> for NoDuplicateKeysSeed {
    type Value = ();

    fn deserialize<D>(self, deserializer: D) -> Result<(), D::Error>
    where
        D: Deserializer<'de>,
    {
        deserializer.deserialize_any(NoDuplicateKeys)
    }
}

pub fn check_no_duplicate_keys(json_str: &str) -> Result<(), CommandError> {
    let mut deserializer = serde_json::Deserializer::from_str(json_str);
    deserializer.deserialize_any(NoDuplicateKeys).map_err(|e| {
        CommandError::new(
            "PROTOCOL_VIOLATION",
            format!("Duplicate key or malformed JSON: {}", e),
        )
    })?;
    deserializer.end().map_err(|e| {
        CommandError::new(
            "PROTOCOL_VIOLATION",
            format!("Trailing data in JSON: {}", e),
        )
    })?;
    Ok(())
}

/// Parses a single stderr line into a validated BatchStderrItem.
pub fn parse_progress_line(
    line: &str,
    expected_request_id: &str,
    expected_formats: &[String],
    expected_files: &[String],
) -> Result<BatchStderrItem, CommandError> {
    let trimmed = line.trim_end_matches(['\r', '\n']);
    if trimmed.is_empty() {
        return Err(CommandError::new(
            "PROTOCOL_VIOLATION",
            "Empty line in progress stream.",
        ));
    }
    if trimmed.len() > MAX_STDERR_LINE_BYTES {
        return Err(CommandError::new(
            "PROTOCOL_VIOLATION",
            format!(
                "Progress line length ({} bytes) exceeds 4 KiB limit.",
                trimmed.len()
            ),
        ));
    }

    check_no_duplicate_keys(trimmed)?;

    let val: serde_json::Value = serde_json::from_str(trimmed).map_err(|e| {
        CommandError::new(
            "PROTOCOL_VIOLATION",
            format!("Failed to parse progress JSON: {}", e),
        )
    })?;

    let obj = val.as_object().ok_or_else(|| {
        CommandError::new("PROTOCOL_VIOLATION", "Progress JSON is not an object.")
    })?;

    let record_type = obj.get("type").and_then(|v| v.as_str()).ok_or_else(|| {
        CommandError::new("PROTOCOL_VIOLATION", "Missing 'type' in progress line.")
    })?;

    if record_type == "diagnostic" {
        for key in obj.keys() {
            if !["type", "phase", "message"].contains(&key.as_str()) {
                return Err(CommandError::new(
                    "PROTOCOL_VIOLATION",
                    format!("Unexpected key '{}' in diagnostic line.", key),
                ));
            }
        }
        let phase = obj
            .get("phase")
            .and_then(|v| v.as_str())
            .unwrap_or_default();
        let message = obj
            .get("message")
            .and_then(|v| v.as_str())
            .unwrap_or("Batch process failed before a contract response could be produced.");
        if phase == "fatal" {
            return Ok(BatchStderrItem::FatalDiagnostic(message.to_string()));
        }
        return Err(CommandError::new(
            "PROTOCOL_VIOLATION",
            format!("Unknown diagnostic phase: {}", phase),
        ));
    }

    if record_type != "progress" {
        return Err(CommandError::new(
            "PROTOCOL_VIOLATION",
            format!("Unknown record type: {}", record_type),
        ));
    }

    let req_id = obj
        .get("request_id")
        .and_then(|v| v.as_str())
        .ok_or_else(|| CommandError::new("PROTOCOL_VIOLATION", "Missing request_id."))?;
    if req_id != expected_request_id {
        return Err(CommandError::new(
            "PROTOCOL_VIOLATION",
            format!(
                "Progress request_id '{}' mismatch against expected '{}'.",
                req_id, expected_request_id
            ),
        ));
    }

    let phase_str = obj
        .get("phase")
        .and_then(|v| v.as_str())
        .ok_or_else(|| CommandError::new("PROTOCOL_VIOLATION", "Missing phase."))?;

    let allowed_keys: &[&str] = match phase_str {
        "batch_started" => &[
            "type",
            "request_id",
            "phase",
            "total_files",
            "completed_files",
        ],
        "file_started" => &[
            "type",
            "request_id",
            "phase",
            "total_files",
            "completed_files",
            "current_file",
        ],
        "format_started" => &[
            "type",
            "request_id",
            "phase",
            "total_files",
            "completed_files",
            "current_file",
            "current_format",
        ],
        "format_finished" => &[
            "type",
            "request_id",
            "phase",
            "total_files",
            "completed_files",
            "current_file",
            "current_format",
        ],
        "file_finished" => &[
            "type",
            "request_id",
            "phase",
            "total_files",
            "completed_files",
            "current_file",
            "file_status",
        ],
        "batch_finished" => &[
            "type",
            "request_id",
            "phase",
            "total_files",
            "completed_files",
        ],
        _ => &[],
    };

    for key in obj.keys() {
        if !allowed_keys.contains(&key.as_str()) {
            return Err(CommandError::new(
                "PROTOCOL_VIOLATION",
                format!(
                    "Unexpected key '{}' in progress line for phase '{}'.",
                    key, phase_str
                ),
            ));
        }
    }

    let total_u64 = obj
        .get("total_files")
        .and_then(|v| v.as_u64())
        .ok_or_else(|| {
            CommandError::new("PROTOCOL_VIOLATION", "Missing or invalid total_files.")
        })?;
    if total_u64 > u32::MAX as u64 {
        return Err(CommandError::new(
            "PROTOCOL_VIOLATION",
            "total_files exceeds u32 limit.",
        ));
    }
    let total_files = total_u64 as u32;
    if total_files != expected_files.len() as u32 {
        return Err(CommandError::new(
            "PROTOCOL_VIOLATION",
            format!(
                "total_files ({}) does not match expected request file count ({}).",
                total_files,
                expected_files.len()
            ),
        ));
    }

    let completed_u64 = obj
        .get("completed_files")
        .and_then(|v| v.as_u64())
        .ok_or_else(|| {
            CommandError::new("PROTOCOL_VIOLATION", "Missing or invalid completed_files.")
        })?;
    if completed_u64 > u32::MAX as u64 {
        return Err(CommandError::new(
            "PROTOCOL_VIOLATION",
            "completed_files exceeds u32 limit.",
        ));
    }
    let completed_files = completed_u64 as u32;

    if completed_files > total_files {
        return Err(CommandError::new(
            "PROTOCOL_VIOLATION",
            format!(
                "completed_files ({}) exceeds total_files ({}).",
                completed_files, total_files
            ),
        ));
    }

    let current_file = obj
        .get("current_file")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());
    let current_format = obj
        .get("current_format")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());
    let file_status = obj
        .get("file_status")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());

    if let Some(f) = &current_file {
        if !expected_files.iter().any(|ef| ef == f) {
            return Err(CommandError::new(
                "PROTOCOL_VIOLATION",
                format!("current_file '{}' was not in the requested files.", f),
            ));
        }
    }

    if let Some(fmt) = &current_format {
        if !expected_formats
            .iter()
            .any(|ef| ef.eq_ignore_ascii_case(fmt))
        {
            return Err(CommandError::new(
                "PROTOCOL_VIOLATION",
                format!("current_format '{}' was not in the requested formats.", fmt),
            ));
        }
    }

    let phase = match phase_str {
        "batch_started" => {
            if completed_files != 0 {
                return Err(CommandError::new(
                    "PROTOCOL_VIOLATION",
                    "batch_started must have completed_files == 0.",
                ));
            }
            if current_file.is_some() || current_format.is_some() || file_status.is_some() {
                return Err(CommandError::new(
                    "PROTOCOL_VIOLATION",
                    "batch_started must not contain current_file, current_format, or file_status.",
                ));
            }
            BatchPhase::BatchStarted
        }
        "file_started" => {
            if current_file.is_none() {
                return Err(CommandError::new(
                    "PROTOCOL_VIOLATION",
                    "file_started requires current_file.",
                ));
            }
            if current_format.is_some() || file_status.is_some() {
                return Err(CommandError::new(
                    "PROTOCOL_VIOLATION",
                    "file_started must not contain current_format or file_status.",
                ));
            }
            BatchPhase::FileStarted
        }
        "format_started" => {
            if current_file.is_none() || current_format.is_none() {
                return Err(CommandError::new(
                    "PROTOCOL_VIOLATION",
                    "format_started requires current_file and current_format.",
                ));
            }
            if file_status.is_some() {
                return Err(CommandError::new(
                    "PROTOCOL_VIOLATION",
                    "format_started must not contain file_status.",
                ));
            }
            BatchPhase::FormatStarted
        }
        "format_finished" => {
            if current_file.is_none() || current_format.is_none() {
                return Err(CommandError::new(
                    "PROTOCOL_VIOLATION",
                    "format_finished requires current_file and current_format.",
                ));
            }
            if file_status.is_some() {
                return Err(CommandError::new(
                    "PROTOCOL_VIOLATION",
                    "format_finished must not contain file_status.",
                ));
            }
            BatchPhase::FormatFinished
        }
        "file_finished" => {
            if current_file.is_none() || file_status.is_none() {
                return Err(CommandError::new(
                    "PROTOCOL_VIOLATION",
                    "file_finished requires current_file and file_status.",
                ));
            }
            let st = file_status.as_deref().unwrap();
            if !matches!(st, "accepted" | "partial" | "failed") {
                return Err(CommandError::new(
                    "PROTOCOL_VIOLATION",
                    format!("Invalid file_status '{}' in file_finished.", st),
                ));
            }
            if current_format.is_some() {
                return Err(CommandError::new(
                    "PROTOCOL_VIOLATION",
                    "file_finished must not contain current_format.",
                ));
            }
            BatchPhase::FileFinished
        }
        "batch_finished" => {
            if current_file.is_some() || current_format.is_some() || file_status.is_some() {
                return Err(CommandError::new(
                    "PROTOCOL_VIOLATION",
                    "batch_finished must not contain current_file, current_format, or file_status.",
                ));
            }
            BatchPhase::BatchFinished
        }
        _ => {
            return Err(CommandError::new(
                "PROTOCOL_VIOLATION",
                format!("Unknown progress phase '{}'.", phase_str),
            ));
        }
    };

    Ok(BatchStderrItem::Progress(BatchProgressEvent {
        request_id: req_id.to_string(),
        phase,
        total_files,
        completed_files,
        current_file,
        current_format,
        file_status,
    }))
}

// Wire response structures
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WireArtifact {
    pub format: String,
    pub path: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WireDiagnostic {
    pub code: String,
    pub message: String,
    pub format: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WireFileResult {
    pub input: String,
    pub status: String,
    #[serde(default)]
    pub artifacts: Vec<WireArtifact>,
    #[serde(default)]
    pub errors: Vec<WireDiagnostic>,
    #[serde(default)]
    pub warnings: Vec<WireDiagnostic>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WireManifestRef {
    pub path: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WireSummary {
    pub total: u32,
    pub accepted: u32,
    pub partial: u32,
    pub failed: u32,
    pub unprocessed: u32,
    pub cancelled: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WireBatchResponse {
    pub contract_version: String,
    pub request_id: String,
    pub status: String,
    pub summary: Option<WireSummary>,
    #[serde(default)]
    pub results: Vec<WireFileResult>,
    pub manifest: Option<WireManifestRef>,
    #[serde(default)]
    pub unprocessed_files: Vec<String>,
    #[serde(default)]
    pub cancelled_files: Vec<String>,
    #[serde(default)]
    pub errors: Vec<WireDiagnostic>,
    #[serde(default)]
    pub warnings: Vec<WireDiagnostic>,
}

fn validate_diagnostic_items(
    diags: &[WireDiagnostic],
    is_warning: bool,
) -> Result<(), CommandError> {
    for d in diags {
        if !APPROVED_DIAGNOSTIC_CODES.iter().any(|&c| c == d.code) {
            return Err(CommandError::new(
                "INVALID_SCHEMA",
                format!(
                    "Diagnostic code '{}' is not an approved canonical code.",
                    d.code
                ),
            ));
        }
        if is_warning {
            if !APPROVED_WARNING_CODES.iter().any(|&c| c == d.code) {
                return Err(CommandError::new(
                    "INVALID_SCHEMA",
                    format!(
                        "Diagnostic code '{}' is not permitted as a warning.",
                        d.code
                    ),
                ));
            }
        } else if APPROVED_WARNING_CODES.iter().any(|&c| c == d.code) {
            return Err(CommandError::new(
                "INVALID_SCHEMA",
                format!(
                    "Diagnostic code '{}' is a warning code and not permitted in errors.",
                    d.code
                ),
            ));
        }
        if d.message.is_empty() || d.message.len() > 512 {
            return Err(CommandError::new(
                "INVALID_SCHEMA",
                "Diagnostic message length must be between 1 and 512 characters.",
            ));
        }
        if let Some(fmt) = &d.format {
            if fmt.is_empty() || fmt.len() > 32 {
                return Err(CommandError::new(
                    "INVALID_SCHEMA",
                    "Diagnostic format length must be between 1 and 32 characters.",
                ));
            }
        }
    }
    Ok(())
}

/// Parses and semantically validates a terminal BatchResponse from stdout.
pub fn parse_terminal_response(
    raw_stdout: &[u8],
    expected_request_id: &str,
    requested_files: &[String],
) -> Result<WireBatchResponse, CommandError> {
    if raw_stdout.is_empty() {
        return Err(CommandError::new(
            "TRANSPORT_ERROR",
            "Child process produced empty stdout on exit.",
        ));
    }

    if raw_stdout.len() > MAX_STDOUT_BYTES {
        return Err(CommandError::new(
            "PAYLOAD_TOO_LARGE",
            format!(
                "Stdout response size ({} bytes) exceeds 10 MiB limit.",
                raw_stdout.len()
            ),
        ));
    }

    if !raw_stdout.ends_with(b"\n") {
        return Err(CommandError::new(
            "PROTOCOL_VIOLATION",
            "Terminal response stdout must end with a newline delimiter.",
        ));
    }

    // Check UTF-8 without BOM
    let text = std::str::from_utf8(raw_stdout).map_err(|e| {
        CommandError::new(
            "PROTOCOL_VIOLATION",
            format!("Stdout response contains invalid UTF-8: {}", e),
        )
    })?;

    if text.starts_with('\u{feff}') {
        return Err(CommandError::new(
            "PROTOCOL_VIOLATION",
            "Stdout response must not contain a Byte Order Mark (BOM).",
        ));
    }

    let trimmed = if let Some(stripped) = text.strip_suffix("\r\n") {
        stripped
    } else if let Some(stripped) = text.strip_suffix('\n') {
        stripped
    } else {
        return Err(CommandError::new(
            "PROTOCOL_VIOLATION",
            "Terminal response stdout must end with a newline delimiter.",
        ));
    };

    if trimmed.ends_with('\n') || trimmed.ends_with('\r') {
        return Err(CommandError::new(
            "PROTOCOL_VIOLATION",
            "Terminal response stdout contains extra trailing newline or blank lines.",
        ));
    }

    check_no_duplicate_keys(trimmed)?;

    // Single JSON object check: ensure no multiple JSON documents or trailing lines
    let mut de = serde_json::Deserializer::from_str(trimmed);
    let val: serde_json::Value = serde::Deserialize::deserialize(&mut de).map_err(|e| {
        CommandError::new(
            "PROTOCOL_VIOLATION",
            format!("Stdout response failed JSON parsing: {}", e),
        )
    })?;
    de.end().map_err(|e| {
        CommandError::new(
            "PROTOCOL_VIOLATION",
            format!(
                "Stdout response contains trailing data after JSON object: {}",
                e
            ),
        )
    })?;

    // Validate against canonical batch-response.schema.json
    let validator = get_batch_response_validator();
    if let Err(err) = validator.validate(&val) {
        return Err(CommandError::new(
            "INVALID_SCHEMA",
            format!(
                "Batch response failed canonical schema validation at {}: {}",
                err.instance_path, err
            ),
        ));
    }

    let resp: WireBatchResponse = serde_json::from_value(val).map_err(|e| {
        CommandError::new(
            "INVALID_SCHEMA",
            format!("Failed to deserialize BatchResponse: {}", e),
        )
    })?;

    if resp.contract_version != "1.0" {
        return Err(CommandError::new(
            "INVALID_SCHEMA",
            format!(
                "Unsupported contract_version '{}'; expected '1.0'.",
                resp.contract_version
            ),
        ));
    }

    if resp.request_id != expected_request_id {
        return Err(CommandError::new(
            "INVALID_SCHEMA",
            format!(
                "Response request_id mismatch: expected '{}', found '{}'.",
                expected_request_id, resp.request_id
            ),
        ));
    }

    // Validate top-level diagnostics
    validate_diagnostic_items(&resp.warnings, true)?;
    validate_diagnostic_items(&resp.errors, false)?;

    // BATCH_CANCELLED forbidden as top-level error
    for e in &resp.errors {
        if e.code == "BATCH_CANCELLED" {
            return Err(CommandError::new(
                "INVALID_SCHEMA",
                "BATCH_CANCELLED is not permitted as a top-level error.",
            ));
        }
    }

    // Validate per-file diagnostics
    for r in &resp.results {
        validate_diagnostic_items(&r.warnings, true)?;
        validate_diagnostic_items(&r.errors, false)?;
    }

    // Check BATCH_CANCELLED occurrences across file results
    let mut cancel_errors = Vec::new();
    for r in &resp.results {
        for e in &r.errors {
            if e.code == "BATCH_CANCELLED" {
                cancel_errors.push((r, e));
            }
        }
    }

    if cancel_errors.len() > 1 {
        return Err(CommandError::new(
            "INVALID_SCHEMA",
            "Only one BATCH_CANCELLED error marker is permitted across all file results.",
        ));
    }

    if let Some((canc_file, canc_diag)) = cancel_errors.first() {
        let diag_fmt = canc_diag.format.as_deref().ok_or_else(|| {
            CommandError::new(
                "INVALID_SCHEMA",
                "BATCH_CANCELLED error must specify a format.",
            )
        })?;

        if let Some(last_result) = resp.results.last() {
            if last_result.input != canc_file.input {
                return Err(CommandError::new(
                    "INVALID_SCHEMA",
                    "BATCH_CANCELLED must be on the final attempted file result.",
                ));
            }
        }

        if canc_file.artifacts.iter().any(|a| a.format == diag_fmt) {
            return Err(CommandError::new(
                "INVALID_SCHEMA",
                format!(
                    "Contradictory result: format '{}' has both an artifact and BATCH_CANCELLED.",
                    diag_fmt
                ),
            ));
        }

        if canc_file
            .errors
            .iter()
            .any(|e| e != *canc_diag && e.format.as_deref() == Some(diag_fmt))
        {
            return Err(CommandError::new(
                "INVALID_SCHEMA",
                format!(
                    "Contradictory result: format '{}' has both an execution error and BATCH_CANCELLED.",
                    diag_fmt
                ),
            ));
        }
    }

    match resp.status.as_str() {
        "completed" => {
            let summary = resp.summary.as_ref().ok_or_else(|| {
                CommandError::new("INVALID_SCHEMA", "Completed response requires a summary.")
            })?;
            if !resp.errors.is_empty() {
                return Err(CommandError::new(
                    "INVALID_SCHEMA",
                    "Completed response must not have top-level errors.",
                ));
            }
            if !cancel_errors.is_empty() {
                return Err(CommandError::new(
                    "INVALID_SCHEMA",
                    "Completed response must not contain BATCH_CANCELLED errors.",
                ));
            }
            if summary.cancelled != 0 || !resp.cancelled_files.is_empty() {
                return Err(CommandError::new(
                    "INVALID_SCHEMA",
                    "Completed response must have zero cancelled files.",
                ));
            }
            if resp.manifest.is_none() {
                return Err(CommandError::new(
                    "INVALID_SCHEMA",
                    "Completed response requires a manifest reference.",
                ));
            }
            validate_summary_accounting(summary, &resp, requested_files)?;
        }
        "cancelled" => {
            let summary = resp.summary.as_ref().ok_or_else(|| {
                CommandError::new("INVALID_SCHEMA", "Cancelled response requires a summary.")
            })?;
            if !resp.errors.is_empty() {
                return Err(CommandError::new(
                    "INVALID_SCHEMA",
                    "Cancelled response must not have top-level errors.",
                ));
            }
            let has_cancelled_files = !resp.cancelled_files.is_empty();
            let has_batch_cancelled = !cancel_errors.is_empty();
            if !has_cancelled_files && !has_batch_cancelled {
                return Err(CommandError::new(
                    "INVALID_SCHEMA",
                    "Cancelled response requires at least one cancelled file or BATCH_CANCELLED per-file diagnostic.",
                ));
            }
            if resp.manifest.is_none() {
                return Err(CommandError::new(
                    "INVALID_SCHEMA",
                    "Cancelled response requires a manifest reference.",
                ));
            }
            validate_summary_accounting(summary, &resp, requested_files)?;
        }
        "rejected" => {
            if resp.summary.is_some() {
                return Err(CommandError::new(
                    "INVALID_SCHEMA",
                    "Rejected response must not include summary.",
                ));
            }
            if !resp.results.is_empty() {
                return Err(CommandError::new(
                    "INVALID_SCHEMA",
                    "Rejected response must not include results.",
                ));
            }
            if !resp.unprocessed_files.is_empty() || !resp.cancelled_files.is_empty() {
                return Err(CommandError::new(
                    "INVALID_SCHEMA",
                    "Rejected response must not include unprocessed or cancelled files.",
                ));
            }
            if !resp.warnings.is_empty() {
                return Err(CommandError::new(
                    "INVALID_SCHEMA",
                    "Rejected response must not include warnings.",
                ));
            }
            if resp.errors.is_empty() {
                return Err(CommandError::new(
                    "INVALID_SCHEMA",
                    "Rejected response requires at least one top-level error.",
                ));
            }
            if resp.manifest.is_some() {
                return Err(CommandError::new(
                    "INVALID_SCHEMA",
                    "Rejected response must not include manifest reference.",
                ));
            }
        }
        "failed" => {
            if resp.errors.is_empty() {
                return Err(CommandError::new(
                    "INVALID_SCHEMA",
                    "Failed response requires at least one top-level error.",
                ));
            }
            if let Some(summary) = &resp.summary {
                if summary.cancelled != 0 || !resp.cancelled_files.is_empty() {
                    return Err(CommandError::new(
                        "INVALID_SCHEMA",
                        "Failed response must have zero cancelled files.",
                    ));
                }
                if !cancel_errors.is_empty() {
                    let has_fatal = resp
                        .errors
                        .iter()
                        .any(|e| FATAL_RESPONSE_TOP_LEVEL_CODES.contains(&e.code.as_str()));
                    if !has_fatal {
                        return Err(CommandError::new(
                            "INVALID_SCHEMA",
                            "BATCH_CANCELLED in failed response requires a fatal top-level error.",
                        ));
                    }
                }
                validate_summary_accounting(summary, &resp, requested_files)?;
            } else {
                // Early failure before processing began
                if !resp.results.is_empty()
                    || !resp.unprocessed_files.is_empty()
                    || !resp.cancelled_files.is_empty()
                {
                    return Err(CommandError::new(
                        "INVALID_SCHEMA",
                        "Early failed response must not include results or skipped files.",
                    ));
                }
                if resp.manifest.is_some() {
                    return Err(CommandError::new(
                        "INVALID_SCHEMA",
                        "Early failed response must not include manifest reference.",
                    ));
                }
            }
        }
        other => {
            return Err(CommandError::new(
                "INVALID_SCHEMA",
                format!("Unsupported response status '{}'.", other),
            ));
        }
    }

    Ok(resp)
}

fn validate_summary_accounting(
    summary: &WireSummary,
    resp: &WireBatchResponse,
    requested_files: &[String],
) -> Result<(), CommandError> {
    let expected_total = summary.accepted
        + summary.partial
        + summary.failed
        + summary.unprocessed
        + summary.cancelled;
    if summary.total != expected_total {
        return Err(CommandError::new(
            "INVALID_SCHEMA",
            format!(
                "Summary total ({}) does not match sum of category counts ({}).",
                summary.total, expected_total
            ),
        ));
    }

    let expected_results_len = summary.accepted + summary.partial + summary.failed;
    if resp.results.len() as u32 != expected_results_len {
        return Err(CommandError::new(
            "INVALID_SCHEMA",
            format!(
                "Results length ({}) does not match summary attempted count ({}).",
                resp.results.len(),
                expected_results_len
            ),
        ));
    }

    if resp.unprocessed_files.len() as u32 != summary.unprocessed {
        return Err(CommandError::new(
            "INVALID_SCHEMA",
            format!(
                "Unprocessed files count ({}) does not match summary.unprocessed ({}).",
                resp.unprocessed_files.len(),
                summary.unprocessed
            ),
        ));
    }

    if resp.cancelled_files.len() as u32 != summary.cancelled {
        return Err(CommandError::new(
            "INVALID_SCHEMA",
            format!(
                "Cancelled files count ({}) does not match summary.cancelled ({}).",
                resp.cancelled_files.len(),
                summary.cancelled
            ),
        ));
    }

    let acc_count = resp
        .results
        .iter()
        .filter(|r| r.status == "accepted")
        .count() as u32;
    let part_count = resp
        .results
        .iter()
        .filter(|r| r.status == "partial")
        .count() as u32;
    let fail_count = resp.results.iter().filter(|r| r.status == "failed").count() as u32;

    if acc_count != summary.accepted {
        return Err(CommandError::new(
            "INVALID_SCHEMA",
            format!(
                "Results accepted count ({}) does not match summary.accepted ({}).",
                acc_count, summary.accepted
            ),
        ));
    }
    if part_count != summary.partial {
        return Err(CommandError::new(
            "INVALID_SCHEMA",
            format!(
                "Results partial count ({}) does not match summary.partial ({}).",
                part_count, summary.partial
            ),
        ));
    }
    if fail_count != summary.failed {
        return Err(CommandError::new(
            "INVALID_SCHEMA",
            format!(
                "Results failed count ({}) does not match summary.failed ({}).",
                fail_count, summary.failed
            ),
        ));
    }

    // Partition verification: results + unprocessed + cancelled must partition requested files
    let mut seen_all: HashSet<String> = HashSet::new();
    for r in &resp.results {
        let key = r.input.to_lowercase();
        if !seen_all.insert(key) {
            return Err(CommandError::new(
                "INVALID_SCHEMA",
                format!("Duplicate input in results: '{}'.", r.input),
            ));
        }
    }

    for u in &resp.unprocessed_files {
        let key = u.to_lowercase();
        if !seen_all.insert(key) {
            return Err(CommandError::new(
                "INVALID_SCHEMA",
                format!("Overlapping or duplicate unprocessed file: '{}'.", u),
            ));
        }
    }

    for c in &resp.cancelled_files {
        let key = c.to_lowercase();
        if !seen_all.insert(key) {
            return Err(CommandError::new(
                "INVALID_SCHEMA",
                format!("Overlapping or duplicate cancelled file: '{}'.", c),
            ));
        }
    }

    if seen_all.len() as u32 != summary.total {
        return Err(CommandError::new(
            "INVALID_SCHEMA",
            format!(
                "Total unique files ({}) does not equal summary.total ({}).",
                seen_all.len(),
                summary.total
            ),
        ));
    }

    // Verify all requested files are accounted for
    for req_f in requested_files {
        let key = req_f.to_lowercase();
        if !seen_all.contains(&key) {
            return Err(CommandError::new(
                "INVALID_SCHEMA",
                format!(
                    "Requested file '{}' was missing from terminal accounting partition.",
                    req_f
                ),
            ));
        }
    }

    Ok(())
}

/// Maps a validated terminal response into canonical summary record and file row records in requested order.
pub fn map_result_accounting(
    response: &WireBatchResponse,
    requested_files: &[String],
    operation_formats: &[String],
) -> (BatchSummaryRecord, Vec<BatchFileRowRecord>) {
    let summary = if let Some(s) = &response.summary {
        BatchSummaryRecord {
            total: s.total,
            accepted: s.accepted,
            partial: s.partial,
            failed: s.failed,
            cancelled: s.cancelled,
            unprocessed: s.unprocessed,
        }
    } else {
        BatchSummaryRecord {
            total: 0,
            accepted: 0,
            partial: 0,
            failed: 0,
            cancelled: 0,
            unprocessed: 0,
        }
    };

    let mut result_by_file: HashMap<String, &WireFileResult> = HashMap::new();
    for r in &response.results {
        result_by_file.insert(r.input.to_lowercase(), r);
    }

    let cancelled_set: HashSet<String> = response
        .cancelled_files
        .iter()
        .map(|f| f.to_lowercase())
        .collect();
    let unprocessed_set: HashSet<String> = response
        .unprocessed_files
        .iter()
        .map(|f| f.to_lowercase())
        .collect();

    let mut rows = Vec::with_capacity(requested_files.len());
    for file in requested_files {
        let key = file.to_lowercase();

        if let Some(r) = result_by_file.get(&key) {
            let category = match r.status.as_str() {
                "accepted" => BatchRowCategory::Succeeded,
                "partial" => BatchRowCategory::Partial,
                _ => BatchRowCategory::Failed,
            };

            let successful_artifacts = r
                .artifacts
                .iter()
                .map(|a| BatchArtifactRecord {
                    format: a.format.clone(),
                    relative_path: a.path.clone(),
                    size_bytes: 0, // Enriched during manifest verification
                })
                .collect();

            let mut diagnostic_codes = Vec::new();
            for e in &r.errors {
                if !diagnostic_codes.contains(&e.code) {
                    diagnostic_codes.push(e.code.clone());
                }
            }
            for w in &r.warnings {
                if !diagnostic_codes.contains(&w.code) {
                    diagnostic_codes.push(w.code.clone());
                }
            }

            rows.push(BatchFileRowRecord {
                file: file.clone(),
                category,
                attempted_formats: operation_formats.to_vec(),
                successful_artifacts,
                diagnostic_codes,
            });
        } else if cancelled_set.contains(&key) {
            rows.push(BatchFileRowRecord {
                file: file.clone(),
                category: BatchRowCategory::Cancelled,
                attempted_formats: operation_formats.to_vec(),
                successful_artifacts: Vec::new(),
                diagnostic_codes: vec!["BATCH_CANCELLED".to_string()],
            });
        } else if unprocessed_set.contains(&key) {
            rows.push(BatchFileRowRecord {
                file: file.clone(),
                category: BatchRowCategory::Unprocessed,
                attempted_formats: operation_formats.to_vec(),
                successful_artifacts: Vec::new(),
                diagnostic_codes: Vec::new(),
            });
        }
    }

    (summary, rows)
}

/// Tracks and enforces strict progress stream sequencing and monotonic completed counts.
#[derive(Debug, Clone, Default)]
pub struct BatchProgressTracker {
    pub last_phase: Option<BatchPhase>,
    pub last_completed_files: u32,
    pub current_file: Option<String>,
    pub current_format: Option<String>,
    pub completed_files_set: HashSet<String>,
    pub expected_total_files: u32,
}

impl BatchProgressTracker {
    pub fn new(expected_total_files: u32) -> Self {
        Self {
            last_phase: None,
            last_completed_files: 0,
            current_file: None,
            current_format: None,
            completed_files_set: HashSet::new(),
            expected_total_files,
        }
    }

    pub fn update(&mut self, evt: &BatchProgressEvent) -> Result<(), CommandError> {
        if evt.total_files != self.expected_total_files {
            return Err(CommandError::new(
                "PROTOCOL_VIOLATION",
                format!(
                    "total_files ({}) does not match expected ({}) in progress event.",
                    evt.total_files, self.expected_total_files
                ),
            ));
        }

        // Monotonic completed_files invariant
        if evt.completed_files < self.last_completed_files {
            return Err(CommandError::new(
                "PROTOCOL_VIOLATION",
                format!(
                    "completed_files ({}) decreased from previous ({}).",
                    evt.completed_files, self.last_completed_files
                ),
            ));
        }

        if evt.completed_files > self.expected_total_files {
            return Err(CommandError::new(
                "PROTOCOL_VIOLATION",
                format!(
                    "completed_files ({}) exceeds expected total ({}).",
                    evt.completed_files, self.expected_total_files
                ),
            ));
        }

        // Sequence state transition invariants
        match evt.phase {
            BatchPhase::BatchStarted => {
                if self.last_phase.is_some() {
                    return Err(CommandError::new(
                        "PROTOCOL_VIOLATION",
                        "batch_started must be the first progress event in the stream.",
                    ));
                }
            }
            BatchPhase::FileStarted => {
                match self.last_phase {
                    Some(BatchPhase::BatchStarted) | Some(BatchPhase::FileFinished) => {}
                    _ => {
                        return Err(CommandError::new(
                            "PROTOCOL_VIOLATION",
                            "file_started must follow batch_started or file_finished.",
                        ));
                    }
                }
                self.current_file = evt.current_file.clone();
                self.current_format = None;
            }
            BatchPhase::FormatStarted => {
                match self.last_phase {
                    Some(BatchPhase::FileStarted) | Some(BatchPhase::FormatFinished) => {}
                    _ => {
                        return Err(CommandError::new(
                            "PROTOCOL_VIOLATION",
                            "format_started must follow file_started or format_finished.",
                        ));
                    }
                }
                if evt.current_file != self.current_file {
                    return Err(CommandError::new(
                        "PROTOCOL_VIOLATION",
                        "format_started current_file must match active file_started.",
                    ));
                }
                self.current_format = evt.current_format.clone();
            }
            BatchPhase::FormatFinished => {
                if self.last_phase != Some(BatchPhase::FormatStarted) {
                    return Err(CommandError::new(
                        "PROTOCOL_VIOLATION",
                        "format_finished must follow format_started.",
                    ));
                }
                if evt.current_file != self.current_file
                    || evt.current_format != self.current_format
                {
                    return Err(CommandError::new(
                        "PROTOCOL_VIOLATION",
                        "format_finished current_file/format must match active format_started.",
                    ));
                }
                self.current_format = None;
            }
            BatchPhase::FileFinished => {
                match self.last_phase {
                    Some(BatchPhase::FileStarted) | Some(BatchPhase::FormatFinished) => {}
                    _ => {
                        return Err(CommandError::new(
                            "PROTOCOL_VIOLATION",
                            "file_finished must follow file_started or format_finished.",
                        ));
                    }
                }
                if evt.current_file != self.current_file {
                    return Err(CommandError::new(
                        "PROTOCOL_VIOLATION",
                        "file_finished current_file must match active file_started.",
                    ));
                }
                if evt.completed_files <= self.last_completed_files {
                    return Err(CommandError::new(
                        "PROTOCOL_VIOLATION",
                        "file_finished must increment completed_files.",
                    ));
                }
                if let Some(f) = &evt.current_file {
                    if !self.completed_files_set.insert(f.clone()) {
                        return Err(CommandError::new(
                            "PROTOCOL_VIOLATION",
                            format!("Duplicate completion reported for file '{}'.", f),
                        ));
                    }
                }
                if self.completed_files_set.len() as u32 != evt.completed_files {
                    return Err(CommandError::new(
                        "PROTOCOL_VIOLATION",
                        format!(
                            "completed_files count ({}) does not match number of distinct completed files ({}).",
                            evt.completed_files,
                            self.completed_files_set.len()
                        ),
                    ));
                }
                self.current_file = None;
                self.current_format = None;
            }
            BatchPhase::BatchFinished => match self.last_phase {
                Some(BatchPhase::BatchStarted) | Some(BatchPhase::FileFinished) => {}
                _ => {
                    return Err(CommandError::new(
                        "PROTOCOL_VIOLATION",
                        "batch_finished must follow batch_started or file_finished.",
                    ));
                }
            },
        }

        self.last_phase = Some(evt.phase);
        self.last_completed_files = evt.completed_files;
        Ok(())
    }
}

pub fn verify_progress_tracker_terminal_accounting(
    tracker: &BatchProgressTracker,
    response: &WireBatchResponse,
) -> Result<(), CommandError> {
    if tracker.last_phase == Some(BatchPhase::BatchFinished) {
        if let Some(summary) = &response.summary {
            let terminal_completed = summary.accepted + summary.partial + summary.failed;
            if tracker.last_completed_files != terminal_completed {
                return Err(CommandError::new(
                    "PROTOCOL_VIOLATION",
                    format!(
                        "Progress tracker completed count ({}) does not match terminal response completed count ({}).",
                        tracker.last_completed_files, terminal_completed
                    ),
                ));
            }
            if tracker.completed_files_set.len() as u32 != terminal_completed {
                return Err(CommandError::new(
                    "PROTOCOL_VIOLATION",
                    format!(
                        "Progress tracker distinct completed files ({}) does not match terminal response completed count ({}).",
                        tracker.completed_files_set.len(), terminal_completed
                    ),
                ));
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_canonical_schemas_compile() {
        let _req = get_batch_request_validator();
        let _resp = get_batch_response_validator();
        let _man = get_batch_manifest_validator();
    }

    #[test]
    fn test_build_and_serialize_request_valid() {
        let files = vec!["part1.par".to_string(), "subfolder/part2.par".to_string()];
        let formats = vec![
            "step".to_string(),
            "stl".to_string(),
            "parasolid".to_string(),
        ];
        let bytes = build_and_serialize_request(
            "batch-001",
            Path::new(r"C:\my_cad_parts"),
            &files,
            Path::new(r"C:\my_cad_parts\output"),
            "export_3d",
            &formats,
            true,
            100,
        )
        .unwrap();

        assert!(bytes.ends_with(b"\n"));
        assert!(bytes.len() <= MAX_REQUEST_PAYLOAD_BYTES);

        let s = std::str::from_utf8(&bytes).unwrap();
        assert!(s.contains("\"request_id\":\"batch-001\""));
        assert!(s.contains("\"type\":\"export_3d\""));
    }

    #[test]
    fn test_parse_progress_line_all_phases() {
        let expected_files = ["part1.par".to_string()];
        let expected_formats = ["step".to_string(), "stl".to_string()];

        // 1. batch_started
        let l1 = r#"{"type":"progress","request_id":"b1","phase":"batch_started","total_files":1,"completed_files":0}"#;
        let item1 = parse_progress_line(l1, "b1", &expected_formats, &expected_files).unwrap();
        assert_eq!(
            item1,
            BatchStderrItem::Progress(BatchProgressEvent {
                request_id: "b1".to_string(),
                phase: BatchPhase::BatchStarted,
                total_files: 1,
                completed_files: 0,
                current_file: None,
                current_format: None,
                file_status: None,
            })
        );

        // 2. file_started
        let l2 = r#"{"type":"progress","request_id":"b1","phase":"file_started","total_files":1,"completed_files":0,"current_file":"part1.par"}"#;
        let item2 = parse_progress_line(l2, "b1", &expected_formats, &expected_files).unwrap();
        assert_eq!(
            item2,
            BatchStderrItem::Progress(BatchProgressEvent {
                request_id: "b1".to_string(),
                phase: BatchPhase::FileStarted,
                total_files: 1,
                completed_files: 0,
                current_file: Some("part1.par".to_string()),
                current_format: None,
                file_status: None,
            })
        );

        // 3. format_started
        let l3 = r#"{"type":"progress","request_id":"b1","phase":"format_started","total_files":1,"completed_files":0,"current_file":"part1.par","current_format":"step"}"#;
        let item3 = parse_progress_line(l3, "b1", &expected_formats, &expected_files).unwrap();
        assert_eq!(
            item3,
            BatchStderrItem::Progress(BatchProgressEvent {
                request_id: "b1".to_string(),
                phase: BatchPhase::FormatStarted,
                total_files: 1,
                completed_files: 0,
                current_file: Some("part1.par".to_string()),
                current_format: Some("step".to_string()),
                file_status: None,
            })
        );

        // 4. format_finished
        let l4 = r#"{"type":"progress","request_id":"b1","phase":"format_finished","total_files":1,"completed_files":0,"current_file":"part1.par","current_format":"step"}"#;
        let item4 = parse_progress_line(l4, "b1", &expected_formats, &expected_files).unwrap();
        assert_eq!(
            item4,
            BatchStderrItem::Progress(BatchProgressEvent {
                request_id: "b1".to_string(),
                phase: BatchPhase::FormatFinished,
                total_files: 1,
                completed_files: 0,
                current_file: Some("part1.par".to_string()),
                current_format: Some("step".to_string()),
                file_status: None,
            })
        );

        // 5. file_finished
        let l5 = r#"{"type":"progress","request_id":"b1","phase":"file_finished","total_files":1,"completed_files":1,"current_file":"part1.par","file_status":"accepted"}"#;
        let item5 = parse_progress_line(l5, "b1", &expected_formats, &expected_files).unwrap();
        assert_eq!(
            item5,
            BatchStderrItem::Progress(BatchProgressEvent {
                request_id: "b1".to_string(),
                phase: BatchPhase::FileFinished,
                total_files: 1,
                completed_files: 1,
                current_file: Some("part1.par".to_string()),
                current_format: None,
                file_status: Some("accepted".to_string()),
            })
        );

        // 6. batch_finished
        let l6 = r#"{"type":"progress","request_id":"b1","phase":"batch_finished","total_files":1,"completed_files":1}"#;
        let item6 = parse_progress_line(l6, "b1", &expected_formats, &expected_files).unwrap();
        assert_eq!(
            item6,
            BatchStderrItem::Progress(BatchProgressEvent {
                request_id: "b1".to_string(),
                phase: BatchPhase::BatchFinished,
                total_files: 1,
                completed_files: 1,
                current_file: None,
                current_format: None,
                file_status: None,
            })
        );

        // Fatal diagnostic
        let lf = r#"{"type":"diagnostic","phase":"fatal","message":"Fatal batch error"}"#;
        let itemf = parse_progress_line(lf, "b1", &expected_formats, &expected_files).unwrap();
        assert_eq!(
            itemf,
            BatchStderrItem::FatalDiagnostic("Fatal batch error".to_string())
        );
    }

    #[test]
    fn test_parse_progress_line_rejections() {
        let expected_files = ["part1.par".to_string()];
        let expected_formats = ["step".to_string()];

        // Mismatched request_id
        let l = r#"{"type":"progress","request_id":"wrong","phase":"batch_started","total_files":1,"completed_files":0}"#;
        assert!(parse_progress_line(l, "b1", &expected_formats, &expected_files).is_err());

        // Unexpected current_format in batch_started
        let l_bad_started = r#"{"type":"progress","request_id":"b1","phase":"batch_started","total_files":1,"completed_files":0,"current_format":"step"}"#;
        assert!(
            parse_progress_line(l_bad_started, "b1", &expected_formats, &expected_files).is_err()
        );

        // File not in expected_files
        let l_bad_file = r#"{"type":"progress","request_id":"b1","phase":"file_started","total_files":1,"completed_files":0,"current_file":"unknown.par"}"#;
        assert!(parse_progress_line(l_bad_file, "b1", &expected_formats, &expected_files).is_err());
    }

    #[test]
    fn test_parse_terminal_response_completed_fixture() {
        let fixture_str = include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../contracts/schemas/batch/fixtures/completed.response.json"
        ));
        let requested_files = vec!["part1.par".to_string(), "subfolder/part2.par".to_string()];
        let resp =
            parse_terminal_response(fixture_str.as_bytes(), "batch-001", &requested_files).unwrap();

        assert_eq!(resp.status, "completed");
        let summary = resp.summary.as_ref().unwrap();
        assert_eq!(summary.total, 2);
        assert_eq!(summary.accepted, 2);

        let (sum_rec, rows) = map_result_accounting(
            &resp,
            &requested_files,
            &[
                "step".to_string(),
                "stl".to_string(),
                "parasolid".to_string(),
            ],
        );
        assert_eq!(sum_rec.total, 2);
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0].category, BatchRowCategory::Succeeded);
        assert_eq!(rows[1].category, BatchRowCategory::Succeeded);
    }

    #[test]
    fn test_parse_terminal_response_cancelled_fixture() {
        let fixture_str = include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../contracts/schemas/batch/fixtures/cancelled_batch.response.json"
        ));
        let requested_files = vec![
            "part1.par".to_string(),
            "part2.par".to_string(),
            "part3.par".to_string(),
            "part4.par".to_string(),
        ];
        // Ensure newline-terminated fixture bytes are parsed
        let mut bytes = fixture_str.as_bytes().to_vec();
        if !bytes.ends_with(b"\n") {
            bytes.push(b'\n');
        }
        let resp =
            parse_terminal_response(&bytes, "batch-cancelled-001", &requested_files).unwrap();

        assert_eq!(resp.status, "cancelled");
        let summary = resp.summary.as_ref().unwrap();
        assert_eq!(summary.total, 4);
        assert_eq!(summary.accepted, 1);
        assert_eq!(summary.unprocessed, 1);
        assert_eq!(summary.cancelled, 2);

        let (sum_rec, rows) = map_result_accounting(
            &resp,
            &requested_files,
            &["step".to_string(), "stl".to_string()],
        );
        assert_eq!(sum_rec.total, 4);
        assert_eq!(rows.len(), 4);
        assert_eq!(rows[0].category, BatchRowCategory::Succeeded);
        assert_eq!(rows[1].category, BatchRowCategory::Unprocessed);
        assert_eq!(rows[2].category, BatchRowCategory::Cancelled);
        assert_eq!(rows[3].category, BatchRowCategory::Cancelled);
    }

    #[test]
    fn test_parse_terminal_response_rejected_fixture() {
        let fixture_str = include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../contracts/schemas/batch/fixtures/rejected_no_files.response.json"
        ));
        let requested_files = Vec::new();
        let resp =
            parse_terminal_response(fixture_str.as_bytes(), "batch-002", &requested_files).unwrap();

        assert_eq!(resp.status, "rejected");
        assert!(resp.summary.is_none());
        assert_eq!(resp.errors.len(), 1);
        assert_eq!(resp.errors[0].code, "INVALID_SCHEMA");
    }

    #[test]
    fn test_parse_terminal_response_failed_early_fixture() {
        let fixture_str = include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../contracts/schemas/batch/fixtures/failed_se_unavailable.response.json"
        ));
        let requested_files = vec!["part1.par".to_string()];
        let resp =
            parse_terminal_response(fixture_str.as_bytes(), "batch-004", &requested_files).unwrap();

        assert_eq!(resp.status, "failed");
        assert!(resp.summary.is_none());
        assert_eq!(resp.errors[0].code, "SOLID_EDGE_UNAVAILABLE");
    }

    #[test]
    fn test_parse_terminal_response_failed_progressed_fixture() {
        let fixture_str = include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../contracts/schemas/batch/fixtures/failed_after_progress.response.json"
        ));
        let requested_files = vec![
            "part1.par".to_string(),
            "part2.par".to_string(),
            "part3.par".to_string(),
        ];
        let resp = parse_terminal_response(
            fixture_str.as_bytes(),
            "batch-failed-prog-001",
            &requested_files,
        )
        .unwrap();

        assert_eq!(resp.status, "failed");
        assert!(resp.summary.is_some());
        let summary = resp.summary.as_ref().unwrap();
        assert_eq!(summary.total, 3);
        assert_eq!(summary.accepted, 1);
        assert_eq!(summary.unprocessed, 2);
    }

    #[test]
    fn test_check_no_duplicate_keys() {
        // Valid flat and nested JSON
        let valid_nested = r#"{"contract_version":"1.0","summary":{"total":1,"accepted":1},"results":[{"input":"p1.par","artifacts":[{"format":"step","path":"p1.step"}]}]}"#;
        assert!(check_no_duplicate_keys(valid_nested).is_ok());

        // Duplicate key in root
        let dup_root = r#"{"a":1,"b":2,"a":3}"#;
        assert!(check_no_duplicate_keys(dup_root).is_err());

        // Duplicate key in nested object
        let dup_nested = r#"{"root":{"sub":1,"sub":2}}"#;
        assert!(check_no_duplicate_keys(dup_nested).is_err());
    }

    #[test]
    fn test_batch_progress_tracker_invariants() {
        let mut tracker = BatchProgressTracker::new(2);

        // batch_started
        let ev1 = BatchProgressEvent {
            request_id: "b1".to_string(),
            phase: BatchPhase::BatchStarted,
            total_files: 2,
            completed_files: 0,
            current_file: None,
            current_format: None,
            file_status: None,
        };
        assert!(tracker.update(&ev1).is_ok());

        // file 1 started
        let ev2 = BatchProgressEvent {
            request_id: "b1".to_string(),
            phase: BatchPhase::FileStarted,
            total_files: 2,
            completed_files: 0,
            current_file: Some("f1.par".to_string()),
            current_format: None,
            file_status: None,
        };
        assert!(tracker.update(&ev2).is_ok());

        // file 1 finished
        let ev3 = BatchProgressEvent {
            request_id: "b1".to_string(),
            phase: BatchPhase::FileFinished,
            total_files: 2,
            completed_files: 1,
            current_file: Some("f1.par".to_string()),
            current_format: None,
            file_status: Some("accepted".to_string()),
        };
        assert!(tracker.update(&ev3).is_ok());

        // duplicate completion for file 1 should fail
        let ev_dup_start = BatchProgressEvent {
            request_id: "b1".to_string(),
            phase: BatchPhase::FileStarted,
            total_files: 2,
            completed_files: 1,
            current_file: Some("f1.par".to_string()),
            current_format: None,
            file_status: None,
        };
        assert!(tracker.update(&ev_dup_start).is_ok());

        let ev_dup_finish = BatchProgressEvent {
            request_id: "b1".to_string(),
            phase: BatchPhase::FileFinished,
            total_files: 2,
            completed_files: 2,
            current_file: Some("f1.par".to_string()),
            current_format: None,
            file_status: Some("accepted".to_string()),
        };
        assert!(tracker.update(&ev_dup_finish).is_err());
    }

    #[test]
    fn test_parse_terminal_response_requires_newline() {
        let fixture_str = include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../contracts/schemas/batch/fixtures/completed.response.json"
        ));
        let trimmed_bytes = fixture_str.trim_end_matches(['\r', '\n']).as_bytes();
        let requested_files = vec!["part1.par".to_string(), "subfolder/part2.par".to_string()];
        // Lacking trailing newline must fail
        assert!(parse_terminal_response(trimmed_bytes, "batch-001", &requested_files).is_err());
    }

    #[test]
    fn test_check_no_duplicate_keys_unicode_escapes() {
        // Escaped unicode "a" vs raw "a"
        let dup_unicode = r#"{"a": 1, "\u0061": 2}"#;
        assert!(check_no_duplicate_keys(dup_unicode).is_err());

        // Escaped unicode "b" vs raw "a"
        let distinct_unicode = r#"{"a": 1, "\u0062": 2}"#;
        assert!(check_no_duplicate_keys(distinct_unicode).is_ok());
    }

    #[test]
    fn test_parse_terminal_response_multiple_trailing_newlines_rejected() {
        let fixture_str = include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../contracts/schemas/batch/fixtures/completed.response.json"
        ));
        let requested_files = vec!["part1.par".to_string(), "subfolder/part2.par".to_string()];

        let mut multi_newline = fixture_str.trim_end_matches(['\r', '\n']).to_string();
        multi_newline.push_str("\n\n");
        let err = parse_terminal_response(multi_newline.as_bytes(), "batch-001", &requested_files)
            .unwrap_err();
        assert_eq!(err.code, "PROTOCOL_VIOLATION");
    }

    #[test]
    fn test_verify_progress_tracker_terminal_accounting() {
        let mut tracker = BatchProgressTracker::new(2);
        tracker.last_phase = Some(BatchPhase::BatchFinished);
        tracker.last_completed_files = 2;
        tracker.completed_files_set.insert("f1.par".to_string());
        tracker.completed_files_set.insert("f2.par".to_string());

        let mut resp = WireBatchResponse {
            contract_version: "1.0".to_string(),
            request_id: "req-1".to_string(),
            status: "completed".to_string(),
            summary: Some(WireSummary {
                total: 2,
                accepted: 2,
                partial: 0,
                failed: 0,
                unprocessed: 0,
                cancelled: 0,
            }),
            results: vec![],
            manifest: None,
            unprocessed_files: vec![],
            cancelled_files: vec![],
            errors: vec![],
            warnings: vec![],
        };

        // Matching accounting
        assert!(verify_progress_tracker_terminal_accounting(&tracker, &resp).is_ok());

        // Mismatched accounting: summary says 1, tracker says 2
        resp.summary = Some(WireSummary {
            total: 2,
            accepted: 1,
            partial: 0,
            failed: 0,
            unprocessed: 1,
            cancelled: 0,
        });
        assert!(verify_progress_tracker_terminal_accounting(&tracker, &resp).is_err());
    }
}
