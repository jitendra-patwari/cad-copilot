use std::collections::HashMap;
use std::fs::File;
use std::io::Read;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::shared::path::{
    get_path_filesystem_identity, simplify_windows_path, verify_open_file_handle,
    FileSystemIdentity,
};

use super::protocol::{
    get_batch_manifest_validator, map_result_accounting, WireBatchResponse, MAX_MANIFEST_BYTES,
};
use super::selection::has_reparse_component;
use super::types::{
    BatchEngineStatus, BatchResultResponse, BatchRevealResponse, CommandError, ManifestState,
};

fn normalize_path_str(p: &Path) -> String {
    simplify_windows_path(p)
        .to_string_lossy()
        .replace('/', "\\")
        .to_ascii_lowercase()
}

pub fn sanitize_unverified_relative_path(path_str: &str, output_root: &Path) -> String {
    let p = Path::new(path_str);

    // 1. Direct component-aware prefix check
    if let Ok(stripped) = p.strip_prefix(output_root) {
        let mut parts = Vec::new();
        let mut valid = true;
        for c in stripped.components() {
            if let std::path::Component::Normal(s) = c {
                parts.push(s.to_string_lossy().to_string());
            } else {
                valid = false;
                break;
            }
        }
        if valid && !parts.is_empty() {
            return parts.join("/");
        }
    }

    // 2. Normalized Windows prefix check (handles drive letter casing and forward/back slashes)
    let p_clean = simplify_windows_path(p);
    let root_clean = simplify_windows_path(output_root);
    if let Ok(stripped) = p_clean.strip_prefix(&root_clean) {
        let mut parts = Vec::new();
        let mut valid = true;
        for c in stripped.components() {
            if let std::path::Component::Normal(s) = c {
                parts.push(s.to_string_lossy().to_string());
            } else {
                valid = false;
                break;
            }
        }
        if valid && !parts.is_empty() {
            return parts.join("/");
        }
    }

    // 3. If relative path, ensure it contains only Normal components (no '..', no leading slash)
    if p.is_relative() {
        let mut parts = Vec::new();
        let mut valid = true;
        for c in p.components() {
            if let std::path::Component::Normal(s) = c {
                parts.push(s.to_string_lossy().to_string());
            } else {
                valid = false;
                break;
            }
        }
        if valid && !parts.is_empty() {
            return parts.join("/");
        }
    }

    // 4. If the path cannot be bound to the output root, show no claimed relative location
    String::new()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ManifestArtifactRecord {
    pub format: String,
    pub relative_path: String,
    pub size_bytes: u64,
    pub sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ManifestFileResult {
    pub input: String,
    pub status: String,
    #[serde(default)]
    pub artifacts: Vec<ManifestArtifactRecord>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ManifestOperation {
    #[serde(rename = "type")]
    pub operation_type: String,
    pub formats: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ManifestSummary {
    pub total: u32,
    pub accepted: u32,
    pub partial: u32,
    pub failed: u32,
    pub unprocessed: Option<u32>,
    pub cancelled: Option<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WireBatchManifest {
    pub manifest_version: String,
    pub contract_version: String,
    pub request_id: String,
    pub status: String,
    pub operation: ManifestOperation,
    pub summary: ManifestSummary,
    #[serde(default)]
    pub results: Vec<ManifestFileResult>,
    #[serde(default)]
    pub unprocessed_files: Vec<String>,
    #[serde(default)]
    pub cancelled_files: Vec<String>,
    pub engine_version: String,
    #[serde(default)]
    pub cad_runtime_version_build: Option<String>,
}

pub fn curate_diagnostic_message(code: &str, format: Option<&str>) -> String {
    let desc = match code {
        "INVALID_SCHEMA" => "Request or manifest failed canonical schema validation.",
        "PAYLOAD_TOO_LARGE" => "Payload exceeded maximum allowed size.",
        "UNSUPPORTED_OPERATION" => "Requested batch operation is unsupported.",
        "UNSUPPORTED_FORMAT" => {
            if let Some(fmt) = format {
                return format!(
                    "UNSUPPORTED_FORMAT: Output format '{}' is not supported.",
                    fmt
                );
            }
            "Requested output format is not supported."
        }
        "INPUT_ROOT_NOT_FOUND" => "Source input root directory was not found.",
        "INPUT_PATH_NOT_ALLOWED" => "Source input path violates safety policies.",
        "INPUT_FILE_NOT_FOUND" => "Source input file was not found on disk.",
        "INPUT_EXTENSION_NOT_ALLOWED" => {
            "File extension is not permitted for the selected operation."
        }
        "OUTPUT_ROOT_UNAVAILABLE" => "Destination output directory is unavailable.",
        "OUTPUT_TARGET_COLLISION" => "Output target collided with another requested artifact.",
        "TARGET_ALREADY_EXISTS" => "One or more target output files already exist.",
        "SOLID_EDGE_UNAVAILABLE" => "Solid Edge application is unavailable or not running.",
        "SOLID_EDGE_UNHEALTHY" => "Solid Edge CAD session became unresponsive or unhealthy.",
        "DOCUMENT_OPEN_FAILED" => "Solid Edge failed to open source document.",
        "ARTIFACT_EXPORT_FAILED" => {
            if let Some(fmt) = format {
                return format!(
                    "ARTIFACT_EXPORT_FAILED: Failed to export artifact for format '{}'.",
                    fmt
                );
            }
            "Failed to export artifact for target format."
        }
        "DOCUMENT_CLOSE_FAILED" => "Failed to cleanly close document in Solid Edge.",
        "SOURCE_INTEGRITY_FAILED" => {
            "Source file integrity check failed (unexpected modification)."
        }
        "MANIFEST_PUBLICATION_FAILED" => "Failed to publish batch summary manifest.",
        "VERSION_METADATA_UNAVAILABLE" => "CAD runtime version metadata could not be queried.",
        "INTERNAL_ERROR" => "Internal batch engine error occurred.",
        "BATCH_CANCELLED" => "Batch operation was cancelled.",
        _ => "An unexpected engine diagnostic was reported.",
    };
    format!("{}: {}", code, desc)
}

/// Validates terminal result, loads and validates output manifest, and verifies artifact relations.
pub fn validate_batch_result(
    output_root: &Path,
    expected_root_identity: FileSystemIdentity,
    request_id: &str,
    operation_type: &str,
    operation_formats: &[String],
    terminal_response: &WireBatchResponse,
    requested_files: &[String],
) -> Result<BatchResultResponse, CommandError> {
    // 1. Map base accounting from terminal response first to preserve accounting if output root is lost
    let (summary, mut rows) =
        map_result_accounting(terminal_response, requested_files, operation_formats);

    let engine_status = match terminal_response.status.as_str() {
        "completed" => BatchEngineStatus::Completed,
        "cancelled" => BatchEngineStatus::Cancelled,
        "rejected" => BatchEngineStatus::Rejected,
        _ => BatchEngineStatus::Failed,
    };

    let reason = terminal_response
        .errors
        .first()
        .map(|e| curate_diagnostic_message(&e.code, e.format.as_deref()));

    // 2. Verify output root existence, directory nature, and NTFS identity
    let root_is_valid =
        if !output_root.exists() || !output_root.is_dir() || has_reparse_component(output_root) {
            false
        } else if let Ok(current_root_id) = get_path_filesystem_identity(output_root) {
            current_root_id == expected_root_identity
        } else {
            false
        };

    if !root_is_valid {
        for row in &mut rows {
            for art in &mut row.successful_artifacts {
                art.relative_path =
                    sanitize_unverified_relative_path(&art.relative_path, output_root);
            }
        }
        return Ok(BatchResultResponse {
            request_id: request_id.to_string(),
            engine_status,
            operation: operation_type.to_string(),
            summary,
            rows,
            manifest_state: if terminal_response.manifest.is_some() {
                ManifestState::Unavailable
            } else {
                ManifestState::NotApplicable
            },
            manifest_path: None,
            reason,
            engine_version: None,
            cad_runtime_version: None,
        });
    }

    let canonical_output_root = output_root
        .canonicalize()
        .unwrap_or_else(|_| output_root.to_path_buf());

    // 3. Check for manifest presence and validation
    let derived_manifest_name = format!("{}.batch_manifest.json", request_id);
    let manifest_file_path = output_root.join(&derived_manifest_name);

    let mut manifest_state = ManifestState::NotApplicable;
    let mut manifest_path = None;
    let mut engine_version = None;
    let mut cad_runtime_version = None;

    if terminal_response.manifest.is_some() {
        manifest_state = ManifestState::Unavailable;

        if manifest_file_path.is_file() {
            match validate_and_read_manifest(
                &manifest_file_path,
                &canonical_output_root,
                expected_root_identity,
                request_id,
                operation_type,
                operation_formats,
                terminal_response,
            ) {
                Ok(manifest) => {
                    manifest_state = ManifestState::Validated;
                    manifest_path = Some(
                        simplify_windows_path(&manifest_file_path)
                            .to_string_lossy()
                            .to_string(),
                    );
                    engine_version = Some(manifest.engine_version);
                    cad_runtime_version = manifest.cad_runtime_version_build;

                    // Enrich rows with actual verified artifact sizes and relative paths from manifest
                    let mut artifact_info: HashMap<(String, String), (u64, String)> =
                        HashMap::new();
                    for r in &manifest.results {
                        for a in &r.artifacts {
                            artifact_info.insert(
                                (r.input.to_lowercase(), a.format.to_lowercase()),
                                (a.size_bytes, a.relative_path.clone()),
                            );
                        }
                    }

                    for row in &mut rows {
                        let file_key = row.file.to_lowercase();
                        for art in &mut row.successful_artifacts {
                            if let Some((size, rel_path)) =
                                artifact_info.get(&(file_key.clone(), art.format.to_lowercase()))
                            {
                                art.size_bytes = *size;
                                art.relative_path = rel_path.clone();
                            }
                        }
                    }
                }
                Err(e) => {
                    eprintln!("Manifest validation failed: {}", e.code);
                    manifest_state = ManifestState::Unavailable;
                }
            }
        }
    }

    if manifest_state != ManifestState::Validated {
        for row in &mut rows {
            for art in &mut row.successful_artifacts {
                art.relative_path =
                    sanitize_unverified_relative_path(&art.relative_path, output_root);
            }
        }
    }

    Ok(BatchResultResponse {
        request_id: request_id.to_string(),
        engine_status,
        operation: operation_type.to_string(),
        summary,
        rows,
        manifest_state,
        manifest_path,
        reason,
        engine_version,
        cad_runtime_version,
    })
}

fn validate_and_read_manifest(
    manifest_path: &Path,
    output_root: &Path,
    expected_root_identity: FileSystemIdentity,
    expected_request_id: &str,
    expected_op_type: &str,
    expected_formats: &[String],
    terminal_response: &WireBatchResponse,
) -> Result<WireBatchManifest, CommandError> {
    let derived_manifest_name = format!("{}.batch_manifest.json", expected_request_id);

    // 1. Verify terminal_response.manifest.path matches derived manifest path or name
    if let Some(man_ref) = &terminal_response.manifest {
        let expected_full = normalize_path_str(manifest_path);
        let resp_man_path = normalize_path_str(Path::new(&man_ref.path));
        let derived_rel = normalize_path_str(Path::new(&derived_manifest_name));
        let path_matches = resp_man_path == expected_full || resp_man_path == derived_rel;
        if !path_matches {
            return Err(CommandError::new(
                "INVALID_ENGINE_OUTPUT",
                format!(
                    "Manifest path '{}' in terminal response does not match expected output manifest '{}'.",
                    man_ref.path,
                    manifest_path.display()
                ),
            ));
        }
    }

    // 2. Open manifest file and verify handle
    if has_reparse_component(manifest_path) {
        return Err(CommandError::new(
            "RESULT_ACCESS_UNAVAILABLE",
            "Manifest file or an ancestor directory is a symlink or reparse point.",
        ));
    }

    let file = File::open(manifest_path).map_err(|e| {
        CommandError::new(
            "RESULT_ACCESS_UNAVAILABLE",
            format!("Failed to open manifest file: {}", e),
        )
    })?;

    verify_open_file_handle(
        &file,
        output_root,
        Some(expected_root_identity.volume_serial_number),
    )?;

    let mut bytes = Vec::new();
    let mut take_reader = file.take((MAX_MANIFEST_BYTES + 1) as u64);
    take_reader.read_to_end(&mut bytes).map_err(|e| {
        CommandError::new(
            "RESULT_ACCESS_UNAVAILABLE",
            format!("Failed to read manifest file: {}", e),
        )
    })?;

    if bytes.len() > MAX_MANIFEST_BYTES {
        return Err(CommandError::new(
            "PAYLOAD_TOO_LARGE",
            format!(
                "Manifest file size exceeds {} bytes limit.",
                MAX_MANIFEST_BYTES
            ),
        ));
    }

    let val: serde_json::Value = serde_json::from_slice(&bytes).map_err(|e| {
        CommandError::new(
            "INVALID_ENGINE_OUTPUT",
            format!("Manifest JSON parsing failed: {}", e),
        )
    })?;

    let validator = get_batch_manifest_validator();
    if let Err(err) = validator.validate(&val) {
        return Err(CommandError::new(
            "INVALID_ENGINE_OUTPUT",
            format!(
                "Manifest failed canonical schema validation at {}: {}",
                err.instance_path, err
            ),
        ));
    }

    let manifest: WireBatchManifest = serde_json::from_value(val).map_err(|e| {
        CommandError::new(
            "INVALID_ENGINE_OUTPUT",
            format!("Manifest deserialization failed: {}", e),
        )
    })?;

    if manifest.manifest_version != "1.0" || manifest.contract_version != "1.0" {
        return Err(CommandError::new(
            "INVALID_ENGINE_OUTPUT",
            "Unsupported manifest version.",
        ));
    }

    if manifest.request_id != expected_request_id {
        return Err(CommandError::new(
            "INVALID_ENGINE_OUTPUT",
            "Manifest request ID mismatch against response.",
        ));
    }

    if manifest.status != terminal_response.status {
        return Err(CommandError::new(
            "INVALID_ENGINE_OUTPUT",
            "Manifest status does not match response status.",
        ));
    }

    if manifest.operation.operation_type != expected_op_type {
        return Err(CommandError::new(
            "INVALID_ENGINE_OUTPUT",
            "Manifest operation type does not match expected operation.",
        ));
    }

    if manifest.operation.formats != expected_formats {
        return Err(CommandError::new(
            "INVALID_ENGINE_OUTPUT",
            "Manifest operation formats mismatch.",
        ));
    }

    // Cross-check summary counts with terminal response
    if let Some(resp_sum) = &terminal_response.summary {
        if manifest.summary.total != resp_sum.total
            || manifest.summary.accepted != resp_sum.accepted
            || manifest.summary.partial != resp_sum.partial
            || manifest.summary.failed != resp_sum.failed
            || manifest.summary.unprocessed.unwrap_or(0) != resp_sum.unprocessed
            || manifest.summary.cancelled.unwrap_or(0) != resp_sum.cancelled
        {
            return Err(CommandError::new(
                "INVALID_ENGINE_OUTPUT",
                "Manifest summary counts do not match terminal response summary.",
            ));
        }
    }

    // Cross-check file results count, inputs, statuses, and artifact items
    if manifest.results.len() != terminal_response.results.len() {
        return Err(CommandError::new(
            "INVALID_ENGINE_OUTPUT",
            "Manifest results count does not match response results count.",
        ));
    }

    for (m_res, r_res) in manifest
        .results
        .iter()
        .zip(terminal_response.results.iter())
    {
        if m_res.input != r_res.input {
            return Err(CommandError::new(
                "INVALID_ENGINE_OUTPUT",
                "Manifest file result input mismatch.",
            ));
        }
        if m_res.status != r_res.status {
            return Err(CommandError::new(
                "INVALID_ENGINE_OUTPUT",
                "Manifest file result status mismatch against response.",
            ));
        }
        if m_res.artifacts.len() != r_res.artifacts.len() {
            return Err(CommandError::new(
                "INVALID_ENGINE_OUTPUT",
                "Manifest artifact count mismatch against response.",
            ));
        }
        for (m_art, r_art) in m_res.artifacts.iter().zip(r_res.artifacts.iter()) {
            if !m_art.format.eq_ignore_ascii_case(&r_art.format) {
                return Err(CommandError::new(
                    "INVALID_ENGINE_OUTPUT",
                    "Manifest artifact format mismatch against response.",
                ));
            }
            let expected_full = normalize_path_str(&output_root.join(&m_art.relative_path));
            let r_path = normalize_path_str(Path::new(&r_art.path));
            let m_rel = normalize_path_str(Path::new(&m_art.relative_path));
            let path_matches = (r_path == expected_full) || (r_path == m_rel);
            if !path_matches {
                return Err(CommandError::new(
                    "INVALID_ENGINE_OUTPUT",
                    "Manifest artifact path mismatch against response.",
                ));
            }
        }
    }

    // Cross-check cancelled_files and unprocessed_files lists
    if manifest.cancelled_files != terminal_response.cancelled_files {
        return Err(CommandError::new(
            "INVALID_ENGINE_OUTPUT",
            "Manifest cancelled_files list does not match terminal response.",
        ));
    }

    if manifest.unprocessed_files != terminal_response.unprocessed_files {
        return Err(CommandError::new(
            "INVALID_ENGINE_OUTPUT",
            "Manifest unprocessed_files list does not match terminal response.",
        ));
    }

    // Verify artifact files on disk via open handles
    for r in &manifest.results {
        for a in &r.artifacts {
            // Relative path security
            if a.relative_path.contains("..")
                || a.relative_path.starts_with('/')
                || a.relative_path.starts_with('\\')
                || a.relative_path.contains(':')
            {
                return Err(CommandError::new(
                    "OUTPUT_PATH_NOT_ALLOWED",
                    "Manifest artifact relative path contains prohibited components.",
                ));
            }

            let full_art_path = output_root.join(&a.relative_path);
            let art_file = File::open(&full_art_path).map_err(|_| {
                CommandError::new(
                    "RESULT_ACCESS_UNAVAILABLE",
                    "Failed to open artifact declared in manifest.",
                )
            })?;

            verify_open_file_handle(
                &art_file,
                output_root,
                Some(expected_root_identity.volume_serial_number),
            )?;

            let art_meta = art_file.metadata().map_err(|_| {
                CommandError::new(
                    "RESULT_ACCESS_UNAVAILABLE",
                    "Failed to read artifact metadata.",
                )
            })?;

            if art_meta.len() != a.size_bytes {
                return Err(CommandError::new(
                    "RESULT_ACCESS_UNAVAILABLE",
                    "Manifest artifact size mismatch against file on disk.",
                ));
            }
        }
    }

    Ok(manifest)
}

/// Reveals the bound output directory in Windows Explorer after rechecking directory identity.
pub fn reveal_batch_output(
    output_root: &Path,
    expected_root_identity: FileSystemIdentity,
) -> Result<BatchRevealResponse, CommandError> {
    if !output_root.exists() || !output_root.is_dir() {
        return Err(CommandError::new(
            "OUTPUT_UNAVAILABLE",
            "Output directory does not exist or is not a directory.",
        ));
    }

    if has_reparse_component(output_root) {
        return Err(CommandError::new(
            "OUTPUT_PATH_NOT_ALLOWED",
            "Output directory or an ancestor is a symlink, junction, or reparse point.",
        ));
    }

    let current_id = get_path_filesystem_identity(output_root).map_err(|_| {
        CommandError::new(
            "OUTPUT_UNAVAILABLE",
            "Output directory identity check failed.",
        )
    })?;

    if current_id != expected_root_identity {
        return Err(CommandError::new(
            "OUTPUT_UNAVAILABLE",
            "Output directory identity changed since selection.",
        ));
    }

    let clean_root = simplify_windows_path(output_root);
    let explorer_exe = std::env::var_os("SystemRoot")
        .map(|root| PathBuf::from(root).join("explorer.exe"))
        .filter(|p| p.exists())
        .unwrap_or_else(|| PathBuf::from(r"C:\Windows\explorer.exe"));

    std::process::Command::new(explorer_exe)
        .arg(&clean_root)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map_err(|_| {
            CommandError::new(
                "REVEAL_FAILED",
                "Failed to open destination in Windows Explorer.",
            )
        })?;

    Ok(BatchRevealResponse {
        acknowledged: true,
        target: clean_root.to_string_lossy().to_string(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::batch::protocol;
    use std::fs;
    use std::io::Write;

    struct TestDir(PathBuf);
    impl TestDir {
        fn new(prefix: &str) -> Self {
            let nanos = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let p = std::env::temp_dir().join(format!("batch_res_test_{}_{}", prefix, nanos));
            fs::create_dir_all(&p).unwrap();
            Self(p)
        }
        fn path(&self) -> &Path {
            &self.0
        }
    }
    impl Drop for TestDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn test_validate_batch_result_completed_with_manifest() {
        let temp = TestDir::new("completed");
        let root = simplify_windows_path(&fs::canonicalize(temp.path()).unwrap());
        let root_id = get_path_filesystem_identity(&root).unwrap();

        // Create dummy artifact files
        let art1 = root.join("part1.step");
        let mut f1 = File::create(&art1).unwrap();
        f1.write_all(&vec![0u8; 1024]).unwrap();

        let art2 = root.join("part1.stl");
        let mut f2 = File::create(&art2).unwrap();
        f2.write_all(&vec![0u8; 2048]).unwrap();

        let art3 = root.join("part1.x_t");
        let mut f3 = File::create(&art3).unwrap();
        f3.write_all(&vec![0u8; 512]).unwrap();

        let sub = root.join("subfolder");
        fs::create_dir_all(&sub).unwrap();
        let art4 = sub.join("part2.step");
        let mut f4 = File::create(&art4).unwrap();
        f4.write_all(&vec![0u8; 1536]).unwrap();

        let art5 = sub.join("part2.stl");
        let mut f5 = File::create(&art5).unwrap();
        f5.write_all(&vec![0u8; 3072]).unwrap();

        let art6 = sub.join("part2.x_t");
        let mut f6 = File::create(&art6).unwrap();
        f6.write_all(&vec![0u8; 768]).unwrap();

        // Write completed manifest fixture
        let manifest_content = include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../contracts/schemas/batch/fixtures/completed.batch_manifest.json"
        ));
        let manifest_path = root.join("batch-001.batch_manifest.json");
        let mut mf = File::create(&manifest_path).unwrap();
        mf.write_all(manifest_content.as_bytes()).unwrap();

        // Parse completed response fixture with paths bound to test root
        let response_content = include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../contracts/schemas/batch/fixtures/completed.response.json"
        ))
        .replace(
            "C:/my_cad_parts/output",
            &root.to_string_lossy().replace('\\', "/"),
        );
        let requested_files = vec!["part1.par".to_string(), "subfolder/part2.par".to_string()];
        let resp = super::super::protocol::parse_terminal_response(
            response_content.as_bytes(),
            "batch-001",
            &requested_files,
        )
        .unwrap();

        let op_formats = vec![
            "step".to_string(),
            "stl".to_string(),
            "parasolid".to_string(),
        ];
        let result_res = validate_batch_result(
            &root,
            root_id,
            "batch-001",
            "export_3d",
            &op_formats,
            &resp,
            &requested_files,
        )
        .unwrap();

        assert_eq!(result_res.engine_status, BatchEngineStatus::Completed);
        assert_eq!(result_res.manifest_state, ManifestState::Validated);
        assert!(result_res.manifest_path.is_some());
        assert_eq!(result_res.summary.total, 2);
        assert_eq!(result_res.summary.accepted, 2);
        assert_eq!(result_res.rows.len(), 2);
        assert_eq!(result_res.rows[0].successful_artifacts.len(), 3);
        assert_eq!(result_res.rows[0].successful_artifacts[0].size_bytes, 1024);
        assert_eq!(
            result_res.rows[0].successful_artifacts[0].relative_path,
            "part1.step"
        );
    }

    #[test]
    fn test_validate_batch_result_missing_manifest_leaves_accounting_visible() {
        let temp = TestDir::new("missing_manifest");
        let root = temp.path();
        let root_id = get_path_filesystem_identity(root).unwrap();

        // Parse completed response fixture without creating manifest file on disk
        let response_content = include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../contracts/schemas/batch/fixtures/completed.response.json"
        ))
        .replace(
            "C:/my_cad_parts/output",
            &root.to_string_lossy().replace('\\', "/"),
        );
        let requested_files = vec!["part1.par".to_string(), "subfolder/part2.par".to_string()];
        let resp = super::super::protocol::parse_terminal_response(
            response_content.as_bytes(),
            "batch-001",
            &requested_files,
        )
        .unwrap();

        let op_formats = vec![
            "step".to_string(),
            "stl".to_string(),
            "parasolid".to_string(),
        ];
        let result_res = validate_batch_result(
            root,
            root_id,
            "batch-001",
            "export_3d",
            &op_formats,
            &resp,
            &requested_files,
        )
        .unwrap();

        assert_eq!(result_res.engine_status, BatchEngineStatus::Completed);
        // Manifest is missing, so state is Unavailable
        assert_eq!(result_res.manifest_state, ManifestState::Unavailable);
        assert!(result_res.manifest_path.is_none());
        // But response accounting is still completely visible!
        assert_eq!(result_res.summary.total, 2);
        assert_eq!(result_res.rows.len(), 2);
    }

    #[test]
    fn test_validate_batch_result_missing_output_root_preserves_accounting() {
        let temp = TestDir::new("missing_root");
        let missing_root = temp.path().join("nonexistent_subdir");
        let fake_root_id = FileSystemIdentity {
            volume_serial_number: 12345,
            file_index: 67890,
        };

        let response_content = include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../contracts/schemas/batch/fixtures/completed.response.json"
        ));
        let requested_files = vec!["part1.par".to_string(), "subfolder/part2.par".to_string()];
        let resp = super::super::protocol::parse_terminal_response(
            response_content.as_bytes(),
            "batch-001",
            &requested_files,
        )
        .unwrap();

        let op_formats = vec![
            "step".to_string(),
            "stl".to_string(),
            "parasolid".to_string(),
        ];
        let result_res = validate_batch_result(
            &missing_root,
            fake_root_id,
            "batch-001",
            "export_3d",
            &op_formats,
            &resp,
            &requested_files,
        )
        .unwrap();

        assert_eq!(result_res.engine_status, BatchEngineStatus::Completed);
        assert_eq!(result_res.manifest_state, ManifestState::Unavailable);
        assert!(result_res.manifest_path.is_none());
        assert_eq!(result_res.summary.total, 2);
        assert_eq!(result_res.rows.len(), 2);
    }

    #[test]
    fn test_validate_batch_result_unverified_artifact_path_sanitized() {
        let temp = TestDir::new("sanitize_test");
        let output_root = temp.path().join("out");
        std::fs::create_dir_all(&output_root).unwrap();
        let root_id = get_path_filesystem_identity(&output_root).unwrap();

        let resp = WireBatchResponse {
            contract_version: "1.0".to_string(),
            request_id: "batch-hostile-001".to_string(),
            status: "completed".to_string(),
            summary: Some(protocol::WireSummary {
                total: 2,
                accepted: 2,
                partial: 0,
                failed: 0,
                unprocessed: 0,
                cancelled: 0,
            }),
            results: vec![
                protocol::WireFileResult {
                    input: "part1.par".to_string(),
                    status: "accepted".to_string(),
                    artifacts: vec![protocol::WireArtifact {
                        format: "step".to_string(),
                        path: r"C:\secret\system32\part1.step".to_string(),
                    }],
                    errors: vec![],
                    warnings: vec![],
                },
                protocol::WireFileResult {
                    input: "part2.par".to_string(),
                    status: "accepted".to_string(),
                    artifacts: vec![protocol::WireArtifact {
                        format: "step".to_string(),
                        path: r"..\..\dangerous\part2.step".to_string(),
                    }],
                    errors: vec![],
                    warnings: vec![],
                },
            ],
            manifest: None,
            unprocessed_files: vec![],
            cancelled_files: vec![],
            errors: vec![],
            warnings: vec![],
        };

        let requested_files = vec!["part1.par".to_string(), "part2.par".to_string()];
        let op_formats = vec!["step".to_string()];

        let res = validate_batch_result(
            &output_root,
            root_id,
            "batch-hostile-001",
            "export_3d",
            &op_formats,
            &resp,
            &requested_files,
        )
        .unwrap();

        assert_eq!(res.manifest_state, ManifestState::NotApplicable);
        // Neither artifact path is contained in output_root, so both show no claimed relative location
        assert_eq!(res.rows[0].successful_artifacts[0].relative_path, "");
        assert_eq!(res.rows[1].successful_artifacts[0].relative_path, "");
    }

    #[test]
    fn test_sanitize_unverified_relative_path_prefix_collision() {
        let output_root = Path::new(r"C:\my_workspace\out");

        // Similar prefix C:\my_workspace\out2\part.step must NOT match C:\my_workspace\out
        let false_match = r"C:\my_workspace\out2\part.step";
        assert_eq!(
            sanitize_unverified_relative_path(false_match, output_root),
            ""
        );

        // Genuine descendant
        let genuine = r"C:\my_workspace\out\sub\part.step";
        assert_eq!(
            sanitize_unverified_relative_path(genuine, output_root),
            "sub/part.step"
        );

        // Relative path with parent traversal must NOT be accepted
        let traversal = r"..\..\part.step";
        assert_eq!(
            sanitize_unverified_relative_path(traversal, output_root),
            ""
        );

        // Clean relative path
        let clean_rel = "part.step";
        assert_eq!(
            sanitize_unverified_relative_path(clean_rel, output_root),
            "part.step"
        );
    }

    #[test]
    fn test_validate_batch_result_hostile_manifest_outside_root() {
        let temp = TestDir::new("hostile_manifest");
        let root = simplify_windows_path(&fs::canonicalize(temp.path()).unwrap());
        let root_id = get_path_filesystem_identity(&root).unwrap();

        // Write hostile manifest containing path traversal "../evil.step"
        let manifest_content = include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../contracts/schemas/batch/fixtures/completed.batch_manifest.json"
        ));
        let hostile_manifest = manifest_content.replace("part1.step", "../evil.step");

        let manifest_file = root.join("batch-001.batch_manifest.json");
        let mut mf = File::create(&manifest_file).unwrap();
        mf.write_all(hostile_manifest.as_bytes()).unwrap();

        let response_content = include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../contracts/schemas/batch/fixtures/completed.response.json"
        ))
        .replace(
            "C:/my_cad_parts/output",
            &root.to_string_lossy().replace('\\', "/"),
        );
        let requested_files = vec!["part1.par".to_string(), "subfolder/part2.par".to_string()];
        let resp = protocol::parse_terminal_response(
            response_content.as_bytes(),
            "batch-001",
            &requested_files,
        )
        .unwrap();

        let op_formats = vec![
            "step".to_string(),
            "stl".to_string(),
            "parasolid".to_string(),
        ];
        let manifest_error = validate_and_read_manifest(
            &manifest_file,
            &root,
            root_id,
            "batch-001",
            "export_3d",
            &op_formats,
            &resp,
        )
        .unwrap_err();
        assert_eq!(manifest_error.code, "INVALID_ENGINE_OUTPUT");
        assert!(manifest_error
            .message
            .starts_with("Manifest failed canonical schema validation"));

        let result_res = validate_batch_result(
            &root,
            root_id,
            "batch-001",
            "export_3d",
            &op_formats,
            &resp,
            &requested_files,
        )
        .unwrap();

        // Hostile manifest must fail validation and be marked Unavailable
        assert_eq!(result_res.manifest_state, ManifestState::Unavailable);
        assert!(result_res.manifest_path.is_none());
        // Response accounting remains visible
        assert_eq!(result_res.summary.total, 2);
    }

    #[test]
    fn test_reveal_batch_output_nonexistent() {
        let temp = TestDir::new("reveal_test");
        let missing = temp.path().join("does_not_exist");
        let fake_id = FileSystemIdentity {
            volume_serial_number: 1,
            file_index: 2,
        };

        let res = reveal_batch_output(&missing, fake_id);
        assert!(res.is_err());
        assert_eq!(res.unwrap_err().code, "OUTPUT_UNAVAILABLE");
    }

    #[test]
    fn test_validate_batch_result_substituted_junction() {
        let temp = TestDir::new("junction_subst");
        let real_out = temp.path().join("real_out");
        fs::create_dir(&real_out).unwrap();
        let real_id = match get_path_filesystem_identity(&real_out) {
            Ok(id) => id,
            Err(_) => return,
        };

        let junction_out = temp.path().join("junction_out");
        let status = std::process::Command::new("cmd")
            .args([
                "/C",
                "mklink",
                "/J",
                junction_out.to_str().unwrap(),
                real_out.to_str().unwrap(),
            ])
            .status();

        if let Ok(st) = status {
            if !st.success() {
                return;
            }
        } else {
            return;
        }

        struct JunctionGuard<'a>(&'a Path);
        impl<'a> Drop for JunctionGuard<'a> {
            fn drop(&mut self) {
                let _ = std::process::Command::new("cmd")
                    .args(["/C", "rmdir", self.0.to_str().unwrap()])
                    .status();
            }
        }
        let _guard = JunctionGuard(&junction_out);

        // 1. Verify junction itself is recognized as a reparse component
        assert!(has_reparse_component(&junction_out));

        // 2. reveal_batch_output rejects junction root
        let reveal_err = reveal_batch_output(&junction_out, real_id).unwrap_err();
        assert_eq!(reveal_err.code, "OUTPUT_PATH_NOT_ALLOWED");

        // 3. validate_batch_result on junction root refuses to bind artifacts or load manifest
        let response_content = include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../contracts/schemas/batch/fixtures/completed.response.json"
        ));
        let requested_files = vec!["part1.par".to_string(), "subfolder/part2.par".to_string()];
        let resp = protocol::parse_terminal_response(
            response_content.as_bytes(),
            "batch-001",
            &requested_files,
        )
        .unwrap();

        let op_formats = vec!["step".to_string()];
        let res = validate_batch_result(
            &junction_out,
            real_id,
            "batch-001",
            "export_3d",
            &op_formats,
            &resp,
            &requested_files,
        )
        .unwrap();

        // Artifact paths must be sanitized to empty (unverified)
        assert_eq!(res.rows[0].successful_artifacts[0].relative_path, "");
        assert_eq!(res.rows[1].successful_artifacts[0].relative_path, "");
        assert_eq!(res.manifest_state, ManifestState::Unavailable);

        // 4. validate_and_read_manifest directly on manifest inside junction fails
        let mut resp_for_manifest = resp.clone();
        resp_for_manifest.manifest = Some(crate::batch::protocol::WireManifestRef {
            path: "batch-001.batch_manifest.json".to_string(),
        });
        let manifest_path = junction_out.join("batch-001.batch_manifest.json");
        let man_res = validate_and_read_manifest(
            &manifest_path,
            &junction_out,
            real_id,
            "batch-001",
            "export_3d",
            &op_formats,
            &resp_for_manifest,
        );
        assert!(man_res.is_err());
        assert_eq!(man_res.unwrap_err().code, "RESULT_ACCESS_UNAVAILABLE");
    }
}
