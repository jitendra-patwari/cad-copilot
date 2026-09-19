use std::fs::{self, File};
use std::io::Read;
use std::os::windows::ffi::OsStringExt;
use std::os::windows::fs::{MetadataExt, OpenOptionsExt};
use std::os::windows::io::AsRawHandle;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use windows_sys::Win32::Storage::FileSystem::{
    GetFileInformationByHandle, GetFinalPathNameByHandleW, BY_HANDLE_FILE_INFORMATION,
    FILE_FLAG_BACKUP_SEMANTICS, FILE_NAME_NORMALIZED, FILE_SHARE_READ, FILE_SHARE_WRITE,
    VOLUME_NAME_DOS,
};

use super::types::{
    CommandError, GenerationArtifactRecord, GenerationResultResponse, ManifestDiagnosticItem,
    ManifestSummary, RevealResponse,
};

const RUN_MANIFEST_SCHEMA_STR: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../engine/src/manifests/schemas/run-manifest-v1.schema.json"
));

static MANIFEST_VALIDATOR: std::sync::OnceLock<jsonschema::Validator> = std::sync::OnceLock::new();

fn get_manifest_validator() -> &'static jsonschema::Validator {
    MANIFEST_VALIDATOR.get_or_init(|| {
        let schema_json: serde_json::Value = serde_json::from_str(RUN_MANIFEST_SCHEMA_STR)
            .expect("canonical run-manifest-v1.schema.json must be valid JSON");
        jsonschema::validator_for(&schema_json)
            .expect("canonical run-manifest-v1.schema.json must compile cleanly")
    })
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct FileSystemIdentity {
    pub volume_serial_number: u32,
    pub file_index: u64,
}

/// Retrieves the NTFS volume serial number and 64-bit file index from an open handle.
/// Rejects reparse points and junctions.
pub fn get_handle_filesystem_identity(
    file: &File,
    path_for_err: &Path,
) -> Result<FileSystemIdentity, CommandError> {
    let handle = file.as_raw_handle() as windows_sys::Win32::Foundation::HANDLE;
    let mut info: BY_HANDLE_FILE_INFORMATION = unsafe { std::mem::zeroed() };
    let ok = unsafe { GetFileInformationByHandle(handle, &mut info) };
    if ok == 0 {
        return Err(CommandError::new(
            "OUTPUT_UNAVAILABLE",
            format!(
                "Failed to query filesystem identity for '{}'.",
                path_for_err.display()
            ),
        ));
    }

    if (info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0 {
        return Err(CommandError::new(
            "OUTPUT_PATH_NOT_ALLOWED",
            format!(
                "Path '{}' is a symlink, junction, or reparse point.",
                path_for_err.display()
            ),
        ));
    }

    let file_index = ((info.nFileIndexHigh as u64) << 32) | (info.nFileIndexLow as u64);
    Ok(FileSystemIdentity {
        volume_serial_number: info.dwVolumeSerialNumber,
        file_index,
    })
}

/// Retrieves the NTFS volume serial number and 64-bit file index for a path via an open handle.
/// Opens directories with `FILE_FLAG_BACKUP_SEMANTICS` and rejects reparse points/junctions.
pub fn get_path_filesystem_identity(path: &Path) -> Result<FileSystemIdentity, CommandError> {
    if !path.exists() {
        return Err(CommandError::new(
            "OUTPUT_UNAVAILABLE",
            format!("Path does not exist: {}", path.display()),
        ));
    }

    let file = std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS)
        .open(path)
        .map_err(|e| {
            CommandError::new(
                "OUTPUT_UNAVAILABLE",
                format!(
                    "Failed to open path for identity check '{}': {}",
                    path.display(),
                    e
                ),
            )
        })?;

    get_handle_filesystem_identity(&file, path)
}

pub const MAX_MANIFEST_BYTES: u64 = 2_097_152; // 2 MiB
const CHUNK_SIZE: usize = 65_536; // 64 KiB streaming buffer
const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0400;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManifestArtifactItem {
    #[serde(rename = "type")]
    pub artifact_type: String,
    pub format: String,
    pub path: String,
    pub size_bytes: u64,
    pub sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ManifestRequestSection {
    pub request_id: String,
    pub contract_version: String,
    pub kind: String,
    pub unit: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ManifestEngineSection {
    pub name: String,
    pub version: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ManifestCadRuntimeSection {
    pub product: String,
    #[serde(default)]
    pub version_build: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ManifestProvenanceSection {
    pub kind: String,
    #[serde(default)]
    pub source_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ManifestFingerprintsSection {
    pub plan_sha256: String,
    #[serde(default)]
    pub prompt_sha256: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ManifestStableIdsSection {
    pub part_id: String,
    pub body_ids: Vec<String>,
    pub boolean_operation_ids: Vec<String>,
    pub feature_ids: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ManifestOperationResultItem {
    pub patch_id: String,
    pub operation: String,
    pub reference_id: String,
    pub reference_kind: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ManifestInspectionSection {
    pub volume_mm3: f64,
    pub mass_kg: f64,
    pub feature_count: u32,
    pub body_count: u32,
    #[serde(default)]
    pub solid_body_count: Option<u32>,
    #[serde(default)]
    pub sheet_body_count: Option<u32>,
    #[serde(default)]
    pub wire_body_count: Option<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ManifestExecutionSection {
    pub operations_executed: u32,
    #[serde(default)]
    pub operation_results: Vec<ManifestOperationResultItem>,
    pub inspection: ManifestInspectionSection,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ManifestDiagnosticRecord {
    pub severity: String,
    pub code: String,
    pub message: String,
    #[serde(default)]
    pub path: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunManifest {
    pub manifest_version: String,
    pub request: ManifestRequestSection,
    pub engine: ManifestEngineSection,
    pub cad_runtime: ManifestCadRuntimeSection,
    pub provenance: ManifestProvenanceSection,
    pub gate_mode: String,
    pub fingerprints: ManifestFingerprintsSection,
    pub feature_plan: serde_json::Value,
    pub stable_ids: ManifestStableIdsSection,
    pub defaults_applied: Vec<serde_json::Value>,
    #[serde(default)]
    pub warnings: Vec<String>,
    #[serde(default)]
    pub diagnostics: Vec<ManifestDiagnosticRecord>,
    pub execution: ManifestExecutionSection,
    pub artifacts: Vec<ManifestArtifactItem>,
}

/// Validates request ID syntax to prevent path traversal, ADS streams, and illegal chars.
pub fn validate_request_id_syntax(request_id: &str) -> Result<(), CommandError> {
    if request_id.is_empty() || request_id.len() > 96 {
        return Err(CommandError::new(
            "INVALID_REQUEST_ID",
            "Request ID must be between 1 and 96 characters.",
        ));
    }
    if request_id.contains('/')
        || request_id.contains('\\')
        || request_id.contains(':')
        || request_id.contains('\0')
        || request_id.contains("..")
    {
        return Err(CommandError::new(
            "INVALID_REQUEST_ID",
            "Request ID contains illegal characters or path traversal elements.",
        ));
    }
    for ch in request_id.chars() {
        if !ch.is_ascii_alphanumeric() && ch != '_' && ch != '-' && ch != '.' {
            return Err(CommandError::new(
                "INVALID_REQUEST_ID",
                "Request ID contains invalid non-alphanumeric characters.",
            ));
        }
    }
    Ok(())
}

/// Holds open directory handles for the validated output root and run directory
/// without `FILE_SHARE_DELETE`, ensuring the directories cannot be renamed,
/// deleted, or replaced while file operations are being performed.
#[derive(Debug)]
pub struct ValidatedOutputBinding {
    pub canonical_run: PathBuf,
    pub canonical_root: PathBuf,
    pub root_identity: FileSystemIdentity,
    pub run_identity: FileSystemIdentity,
    _root_handle: File,
    _run_handle: File,
}

impl ValidatedOutputBinding {
    pub fn verify_file(&self, file: &File) -> Result<(), CommandError> {
        verify_open_file_handle(
            file,
            &self.canonical_run,
            Some(self.root_identity.volume_serial_number),
        )
    }
}

pub const VERIFICATION_DEADLINE_SECS: u64 = 30;

pub fn verify_folder_containment(
    selected_output_root: &Path,
    request_id: &str,
    expected_root_identity: Option<FileSystemIdentity>,
) -> Result<ValidatedOutputBinding, CommandError> {
    validate_request_id_syntax(request_id)?;

    // Reject UNC paths (e.g. \\server\share or \\?\UNC\), device paths (\\.\), and alternate streams (:)
    let root_str = selected_output_root.to_string_lossy();
    if root_str.starts_with(r"\\")
        || root_str.starts_with(r"//")
        || (root_str.contains(':') && root_str.chars().skip(2).any(|c| c == ':'))
    {
        return Err(CommandError::new(
            "OUTPUT_PATH_NOT_ALLOWED",
            "UNC network shares, device paths, and alternate data streams are not permitted.",
        ));
    }

    if !selected_output_root.exists() {
        return Err(CommandError::new(
            "OUTPUT_UNAVAILABLE",
            "Selected output directory does not exist.",
        ));
    }

    let canonical_root = selected_output_root.canonicalize().map_err(|e| {
        CommandError::new(
            "OUTPUT_UNAVAILABLE",
            format!("Failed to canonicalize output root: {}", e),
        )
    })?;

    let canon_root_str = canonical_root.to_string_lossy();
    if canon_root_str.starts_with(r"\\?\UNC\") || canon_root_str.starts_with(r"\\.\") {
        return Err(CommandError::new(
            "OUTPUT_PATH_NOT_ALLOWED",
            "UNC network shares and device paths are not permitted.",
        ));
    }

    // Open root directory handle with FILE_SHARE_READ | FILE_SHARE_WRITE (no FILE_SHARE_DELETE)
    // to hold the directory identity stable and prevent rename/deletion during file operations.
    let root_file = std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .open(&canonical_root)
        .map_err(|e| {
            CommandError::new(
                "OUTPUT_UNAVAILABLE",
                format!(
                    "Failed to open output root directory '{}': {}",
                    canonical_root.display(),
                    e
                ),
            )
        })?;

    let root_identity = get_handle_filesystem_identity(&root_file, &canonical_root)?;
    if let Some(expected_id) = expected_root_identity {
        if root_identity != expected_id {
            return Err(CommandError::new(
                "OUTPUT_PATH_NOT_ALLOWED",
                "Selected output root filesystem identity does not match reserved run identity.",
            ));
        }
    }

    let run_folder = selected_output_root.join(request_id);
    if !run_folder.exists() {
        return Err(CommandError::new(
            "RUN_NOT_FOUND",
            "Run output directory does not exist.",
        ));
    }

    let canonical_run = run_folder.canonicalize().map_err(|e| {
        CommandError::new(
            "OUTPUT_UNAVAILABLE",
            format!("Failed to canonicalize run directory: {}", e),
        )
    })?;

    let canon_run_str = canonical_run.to_string_lossy();
    if canon_run_str.starts_with(r"\\?\UNC\") || canon_run_str.starts_with(r"\\.\") {
        return Err(CommandError::new(
            "OUTPUT_PATH_NOT_ALLOWED",
            "UNC network shares and device paths are not permitted.",
        ));
    }

    // Must be canonically contained within canonical_root
    if !canonical_run.starts_with(&canonical_root) {
        return Err(CommandError::new(
            "OUTPUT_PATH_NOT_ALLOWED",
            "Run directory escapes canonical output root containment.",
        ));
    }

    // Open run directory handle with FILE_SHARE_READ | FILE_SHARE_WRITE (no FILE_SHARE_DELETE)
    let run_file = std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .open(&canonical_run)
        .map_err(|e| {
            CommandError::new(
                "OUTPUT_UNAVAILABLE",
                format!(
                    "Failed to open run directory '{}': {}",
                    canonical_run.display(),
                    e
                ),
            )
        })?;

    let run_identity = get_handle_filesystem_identity(&run_file, &canonical_run)?;
    if run_identity.volume_serial_number != root_identity.volume_serial_number {
        return Err(CommandError::new(
            "OUTPUT_PATH_NOT_ALLOWED",
            "Run directory resides on a different volume than output root.",
        ));
    }

    let meta = run_file.metadata().map_err(|e| {
        CommandError::new(
            "OUTPUT_UNAVAILABLE",
            format!("Failed to read metadata for run directory: {}", e),
        )
    })?;

    if meta.is_symlink() || (meta.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT) != 0 {
        return Err(CommandError::new(
            "OUTPUT_PATH_NOT_ALLOWED",
            "Run directory is a symlink, junction, or reparse point.",
        ));
    }

    if !meta.is_dir() {
        return Err(CommandError::new(
            "OUTPUT_UNAVAILABLE",
            "Run output path is not a directory.",
        ));
    }

    Ok(ValidatedOutputBinding {
        canonical_run,
        canonical_root,
        root_identity,
        run_identity,
        _root_handle: root_file,
        _run_handle: run_file,
    })
}

fn compute_file_sha256(
    mut file: File,
    start_time: std::time::Instant,
    timeout: std::time::Duration,
    cancel_token: Option<&std::sync::atomic::AtomicBool>,
) -> Result<(u64, String), CommandError> {
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; CHUNK_SIZE];
    let mut total_bytes = 0u64;

    loop {
        if start_time.elapsed() >= timeout {
            return Err(CommandError::new(
                "RESULT_ACCESS_UNAVAILABLE",
                "Result verification deadline (30s) exceeded while reading artifact files.",
            ));
        }

        if let Some(token) = cancel_token {
            if token.load(std::sync::atomic::Ordering::SeqCst) {
                return Err(CommandError::new(
                    "RESULT_ACCESS_UNAVAILABLE",
                    "Result verification cancelled by user.",
                ));
            }
        }

        let bytes_read = file.read(&mut buffer).map_err(|e| {
            CommandError::new(
                "RESULT_ACCESS_UNAVAILABLE",
                format!("Failed to read artifact file: {}", e),
            )
        })?;
        if bytes_read == 0 {
            break;
        }
        hasher.update(&buffer[..bytes_read]);
        total_bytes += bytes_read as u64;
    }

    let result = hasher.finalize();
    let mut hex = String::with_capacity(result.len() * 2);
    for b in result {
        use std::fmt::Write;
        let _ = write!(hex, "{:02x}", b);
    }

    Ok((total_bytes, hex))
}

/// Simplifies a canonical Windows path by stripping extended-length `\\?\` or `\\?\UNC\` prefixes
/// for compatibility with external shell tools (such as explorer.exe) and cleaner UI presentation.
pub fn simplify_windows_path(path: &Path) -> PathBuf {
    let s = path.to_string_lossy();
    if let Some(stripped) = s.strip_prefix(r"\\?\UNC\") {
        PathBuf::from(format!(r"\\{}", stripped))
    } else if let Some(stripped) = s.strip_prefix(r"\\?\") {
        PathBuf::from(stripped)
    } else {
        path.to_path_buf()
    }
}

/// Verifies that an open file handle does not reference a reparse point or hardlink,
/// resides on the expected volume (if provided), and that its final resolved path on disk
/// is contained within `canonical_run`.
/// This protects against TOCTOU junction / hardlink replacement attacks between path checks and reads.
pub fn verify_open_file_handle(
    file: &File,
    canonical_run: &Path,
    expected_volume: Option<u32>,
) -> Result<(), CommandError> {
    let handle = file.as_raw_handle() as windows_sys::Win32::Foundation::HANDLE;

    let mut info: BY_HANDLE_FILE_INFORMATION = unsafe { std::mem::zeroed() };
    let ok = unsafe { GetFileInformationByHandle(handle, &mut info) };
    if ok == 0 {
        return Err(CommandError::new(
            "RESULT_ACCESS_UNAVAILABLE",
            "Failed to retrieve file information from open handle.",
        ));
    }

    if let Some(vol) = expected_volume {
        if info.dwVolumeSerialNumber != vol {
            return Err(CommandError::new(
                "OUTPUT_PATH_NOT_ALLOWED",
                "Open file handle resides on a different volume than designated run directory.",
            ));
        }
    }

    if (info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0 {
        return Err(CommandError::new(
            "OUTPUT_PATH_NOT_ALLOWED",
            "Open file handle references a reparse point or symlink.",
        ));
    }

    if info.nNumberOfLinks > 1 {
        return Err(CommandError::new(
            "OUTPUT_PATH_NOT_ALLOWED",
            format!(
                "Open file handle has {} hardlinks; hardlinks are not permitted.",
                info.nNumberOfLinks
            ),
        ));
    }

    let mut buf = vec![0u16; 1024];
    let mut len = unsafe {
        GetFinalPathNameByHandleW(
            handle,
            buf.as_mut_ptr(),
            buf.len() as u32,
            FILE_NAME_NORMALIZED | VOLUME_NAME_DOS,
        )
    };

    if len as usize > buf.len() {
        buf.resize(len as usize + 1, 0);
        len = unsafe {
            GetFinalPathNameByHandleW(
                handle,
                buf.as_mut_ptr(),
                buf.len() as u32,
                FILE_NAME_NORMALIZED | VOLUME_NAME_DOS,
            )
        };
    }

    if len == 0 {
        return Err(CommandError::new(
            "RESULT_ACCESS_UNAVAILABLE",
            "Failed to resolve final path from open file handle.",
        ));
    }

    let raw_os = std::ffi::OsString::from_wide(&buf[..len as usize]);
    let handle_final_path = PathBuf::from(raw_os);

    let clean_handle = simplify_windows_path(&handle_final_path);
    let clean_run = simplify_windows_path(canonical_run);

    let handle_str = clean_handle.to_string_lossy().replace('/', "\\");
    let run_str = clean_run.to_string_lossy().replace('/', "\\");
    let normalized_run_prefix = if run_str.ends_with('\\') {
        run_str
    } else {
        format!("{}\\", run_str)
    };

    if !handle_str
        .to_ascii_lowercase()
        .starts_with(&normalized_run_prefix.to_ascii_lowercase())
    {
        return Err(CommandError::new(
            "OUTPUT_PATH_NOT_ALLOWED",
            "Open file handle resolves outside the designated run directory.",
        ));
    }

    Ok(())
}

pub fn validate_and_load_result(
    selected_output_root: &Path,
    request_id: &str,
    cancel_token: Option<&std::sync::atomic::AtomicBool>,
    expected_kind: Option<&str>,
    expected_wire_artifacts: Option<&[super::protocol::WireArtifactItem]>,
    expected_root_identity: Option<FileSystemIdentity>,
) -> Result<(GenerationResultResponse, Option<String>), CommandError> {
    let start_time = std::time::Instant::now();
    let timeout = std::time::Duration::from_secs(VERIFICATION_DEADLINE_SECS);
    let binding =
        verify_folder_containment(selected_output_root, request_id, expected_root_identity)?;
    let canonical_run = &binding.canonical_run;

    let check_deadline = || -> Result<(), CommandError> {
        if let Some(token) = cancel_token {
            if token.load(std::sync::atomic::Ordering::SeqCst) {
                return Err(CommandError::new(
                    "RESULT_ACCESS_UNAVAILABLE",
                    "Result verification cancelled by user.",
                ));
            }
        }
        if start_time.elapsed() >= timeout {
            return Err(CommandError::new(
                "RESULT_ACCESS_UNAVAILABLE",
                "Result verification deadline (30s) exceeded.",
            ));
        }
        Ok(())
    };

    check_deadline()?;

    let manifest_path = canonical_run.join("run_manifest.json");
    if !manifest_path.exists() {
        return Err(CommandError::new(
            "RESULT_ACCESS_UNAVAILABLE",
            "Published run_manifest.json sidecar was not found.",
        ));
    }

    check_deadline()?;

    let manifest_sym_meta = fs::symlink_metadata(&manifest_path).map_err(|e| {
        CommandError::new(
            "RESULT_ACCESS_UNAVAILABLE",
            format!("Failed to read run_manifest.json metadata: {}", e),
        )
    })?;

    if manifest_sym_meta.is_symlink()
        || (manifest_sym_meta.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT) != 0
    {
        return Err(CommandError::new(
            "OUTPUT_PATH_NOT_ALLOWED",
            "run_manifest.json is a symlink or reparse point.",
        ));
    }

    let manifest_file = File::open(&manifest_path).map_err(|e| {
        CommandError::new(
            "RESULT_ACCESS_UNAVAILABLE",
            format!("Failed to open run_manifest.json: {}", e),
        )
    })?;

    binding.verify_file(&manifest_file)?;

    let manifest_meta = manifest_file.metadata().map_err(|e| {
        CommandError::new(
            "RESULT_ACCESS_UNAVAILABLE",
            format!("Failed to read run_manifest.json metadata: {}", e),
        )
    })?;

    if manifest_meta.is_symlink()
        || (manifest_meta.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT) != 0
    {
        return Err(CommandError::new(
            "OUTPUT_PATH_NOT_ALLOWED",
            "run_manifest.json is a symlink or reparse point.",
        ));
    }

    if !manifest_meta.is_file() {
        return Err(CommandError::new(
            "RESULT_ACCESS_UNAVAILABLE",
            "run_manifest.json is not a regular file.",
        ));
    }

    if manifest_meta.len() > MAX_MANIFEST_BYTES {
        return Err(CommandError::new(
            "RESULT_ACCESS_UNAVAILABLE",
            format!(
                "Run manifest size ({} bytes) exceeds maximum permitted limit of 2 MiB.",
                manifest_meta.len()
            ),
        ));
    }

    let mut manifest_bytes =
        Vec::with_capacity(std::cmp::min(manifest_meta.len(), MAX_MANIFEST_BYTES) as usize);
    manifest_file
        .take(MAX_MANIFEST_BYTES + 1)
        .read_to_end(&mut manifest_bytes)
        .map_err(|e| {
            CommandError::new(
                "RESULT_ACCESS_UNAVAILABLE",
                format!("Failed to read run_manifest.json: {}", e),
            )
        })?;

    if manifest_bytes.len() as u64 > MAX_MANIFEST_BYTES {
        return Err(CommandError::new(
            "RESULT_ACCESS_UNAVAILABLE",
            "Run manifest exceeded maximum permitted limit of 2 MiB during read.",
        ));
    }

    let manifest_val: serde_json::Value = serde_json::from_slice(&manifest_bytes).map_err(|e| {
        CommandError::new(
            "INVALID_ENGINE_OUTPUT",
            format!("run_manifest.json failed JSON parsing: {}", e),
        )
    })?;

    let validator = get_manifest_validator();
    if let Err(err) = validator.validate(&manifest_val) {
        return Err(CommandError::new(
            "INVALID_ENGINE_OUTPUT",
            format!(
                "run_manifest.json failed canonical schema validation at {}: {}",
                err.instance_path, err
            ),
        ));
    }

    let manifest: RunManifest = serde_json::from_value(manifest_val).map_err(|e| {
        CommandError::new(
            "INVALID_ENGINE_OUTPUT",
            format!("run_manifest.json deserialization failed: {}", e),
        )
    })?;

    if manifest.manifest_version != "cad_copilot.run_manifest.v1" {
        return Err(CommandError::new(
            "INVALID_ENGINE_OUTPUT",
            format!(
                "Unsupported manifest schema version '{}'.",
                manifest.manifest_version
            ),
        ));
    }

    if manifest.request.request_id != request_id {
        return Err(CommandError::new(
            "INVALID_ENGINE_OUTPUT",
            format!(
                "Manifest request ID mismatch: expected '{}', found '{}'.",
                request_id, manifest.request.request_id
            ),
        ));
    }

    if manifest.request.contract_version != "1.0" {
        return Err(CommandError::new(
            "INVALID_ENGINE_OUTPUT",
            format!(
                "Unsupported request contract version '{}'.",
                manifest.request.contract_version
            ),
        ));
    }

    if manifest.request.unit != "mm" {
        return Err(CommandError::new(
            "INVALID_ENGINE_OUTPUT",
            format!(
                "Unsupported request unit '{}'; expected 'mm'.",
                manifest.request.unit
            ),
        ));
    }

    if manifest.request.kind != "example_plan" && manifest.request.kind != "prompt_to_cad" {
        return Err(CommandError::new(
            "INVALID_ENGINE_OUTPUT",
            format!(
                "Unsupported request kind '{}'; expected 'example_plan' or 'prompt_to_cad'.",
                manifest.request.kind
            ),
        ));
    }

    if manifest.gate_mode != "capability_first" && manifest.gate_mode != "strict" {
        return Err(CommandError::new(
            "INVALID_ENGINE_OUTPUT",
            format!(
                "Unsupported gate_mode '{}'; expected 'capability_first' or 'strict'.",
                manifest.gate_mode
            ),
        ));
    }

    let plan_obj = manifest.feature_plan.as_object().ok_or_else(|| {
        CommandError::new(
            "INVALID_ENGINE_OUTPUT",
            "feature_plan must be a non-empty object.",
        )
    })?;

    let part_obj = plan_obj
        .get("part")
        .and_then(|p| p.as_object())
        .ok_or_else(|| {
            CommandError::new(
                "INVALID_ENGINE_OUTPUT",
                "feature_plan must contain a valid 'part' object.",
            )
        })?;

    let plan_part_id = part_obj
        .get("part_id")
        .and_then(|id| id.as_str())
        .unwrap_or("");
    if plan_part_id.is_empty() {
        return Err(CommandError::new(
            "INVALID_ENGINE_OUTPUT",
            "feature_plan part_id must not be empty.",
        ));
    }

    if manifest.stable_ids.part_id != plan_part_id {
        return Err(CommandError::new(
            "INVALID_ENGINE_OUTPUT",
            format!(
                "stable_ids.part_id '{}' does not match feature_plan part_id '{}'.",
                manifest.stable_ids.part_id, plan_part_id
            ),
        ));
    }

    if manifest.execution.inspection.volume_mm3 < 0.0 || manifest.execution.inspection.mass_kg < 0.0
    {
        return Err(CommandError::new(
            "INVALID_ENGINE_OUTPUT",
            "Inspection metrics must not be negative.",
        ));
    }

    if let Some(expected_k) = expected_kind {
        if manifest.request.kind != expected_k {
            return Err(CommandError::new(
                "INVALID_ENGINE_OUTPUT",
                format!(
                    "Manifest request kind mismatch: expected '{}', found '{}'.",
                    expected_k, manifest.request.kind
                ),
            ));
        }
    }

    if let Some(wire_artifacts) = expected_wire_artifacts {
        if manifest.artifacts.len() != wire_artifacts.len() {
            return Err(CommandError::new(
                "INVALID_ENGINE_OUTPUT",
                format!(
                    "Artifact count mismatch: manifest specifies {} artifacts, wire response has {}.",
                    manifest.artifacts.len(),
                    wire_artifacts.len()
                ),
            ));
        }

        let mut matched_manifest_indices = std::collections::HashSet::new();

        for wire_art in wire_artifacts {
            let found = manifest.artifacts.iter().enumerate().find(|(idx, a)| {
                !matched_manifest_indices.contains(idx)
                    && a.artifact_type == wire_art.artifact_type
                    && a.format == wire_art.format
            });

            match found {
                Some((idx, a)) => {
                    matched_manifest_indices.insert(idx);
                    let wire_file_name = Path::new(&wire_art.path)
                        .file_name()
                        .and_then(|n| n.to_str())
                        .unwrap_or("");
                    let manifest_file_name = Path::new(&a.path)
                        .file_name()
                        .and_then(|n| n.to_str())
                        .unwrap_or("");

                    if wire_file_name != manifest_file_name {
                        return Err(CommandError::new(
                            "INVALID_ENGINE_OUTPUT",
                            format!(
                                "Artifact filename mismatch: manifest '{}', wire '{}'.",
                                a.path, wire_art.path
                            ),
                        ));
                    }

                    let expected_full_path = canonical_run.join(&a.path);
                    let wire_path = Path::new(&wire_art.path);
                    let canon_expected = expected_full_path.canonicalize().map_err(|e| {
                        CommandError::new(
                            "OUTPUT_PATH_NOT_ALLOWED",
                            format!(
                                "Failed to canonicalize manifest artifact path '{}': {}",
                                expected_full_path.display(),
                                e
                            ),
                        )
                    })?;
                    let canon_wire = wire_path.canonicalize().map_err(|e| {
                        CommandError::new(
                            "OUTPUT_PATH_NOT_ALLOWED",
                            format!(
                                "Failed to canonicalize wire artifact path '{}': {}",
                                wire_art.path, e
                            ),
                        )
                    })?;
                    if canon_expected != canon_wire {
                        return Err(CommandError::new(
                            "OUTPUT_PATH_NOT_ALLOWED",
                            format!(
                                "Wire artifact path '{}' does not match expected run folder path '{}'.",
                                wire_art.path,
                                expected_full_path.display()
                            ),
                        ));
                    }
                }
                None => {
                    return Err(CommandError::new(
                        "INVALID_ENGINE_OUTPUT",
                        format!(
                            "Manifest missing artifact declared in engine response: {} ({})",
                            wire_art.path, wire_art.artifact_type
                        ),
                    ));
                }
            }
        }

        if matched_manifest_indices.len() != manifest.artifacts.len() {
            return Err(CommandError::new(
                "INVALID_ENGINE_OUTPUT",
                "Not all manifest artifacts correspond to distinct wire artifacts.",
            ));
        }
    }

    if manifest.artifacts.len() < 3 || manifest.artifacts.len() > 4 {
        return Err(CommandError::new(
            "INVALID_ENGINE_OUTPUT",
            format!(
                "Manifest specifies {} artifacts; expected between 3 and 4.",
                manifest.artifacts.len()
            ),
        ));
    }

    let mut artifact_records = Vec::with_capacity(manifest.artifacts.len());
    let mut has_native_part = false;
    let mut has_geometry_step = false;
    let mut has_mesh_stl = false;
    let mut has_preview_image = false;
    let mut verified_preview_sha: Option<String> = None;

    for item in &manifest.artifacts {
        check_deadline()?;

        // Validate artifact relative path: must be a bare filename, no slashes or colons
        if item.path.contains('/')
            || item.path.contains('\\')
            || item.path.contains(':')
            || item.path.contains("..")
        {
            return Err(CommandError::new(
                "OUTPUT_PATH_NOT_ALLOWED",
                format!(
                    "Artifact path '{}' contains prohibited directory separators or traversal.",
                    item.path
                ),
            ));
        }

        match item.artifact_type.as_str() {
            "native_part" => {
                if has_native_part {
                    return Err(CommandError::new(
                        "INVALID_ENGINE_OUTPUT",
                        "Duplicate native_part artifact in manifest.",
                    ));
                }
                has_native_part = true;
                if item.format != "par" || !item.path.ends_with(".par") {
                    return Err(CommandError::new(
                        "INVALID_ENGINE_OUTPUT",
                        format!(
                            "Mismatched native_part artifact format or extension: {}",
                            item.path
                        ),
                    ));
                }
            }
            "geometry_step" => {
                if has_geometry_step {
                    return Err(CommandError::new(
                        "INVALID_ENGINE_OUTPUT",
                        "Duplicate geometry_step artifact in manifest.",
                    ));
                }
                has_geometry_step = true;
                if item.format != "step" || !item.path.ends_with(".step") {
                    return Err(CommandError::new(
                        "INVALID_ENGINE_OUTPUT",
                        format!(
                            "Mismatched geometry_step artifact format or extension: {}",
                            item.path
                        ),
                    ));
                }
            }
            "mesh_stl" => {
                if has_mesh_stl {
                    return Err(CommandError::new(
                        "INVALID_ENGINE_OUTPUT",
                        "Duplicate mesh_stl artifact in manifest.",
                    ));
                }
                has_mesh_stl = true;
                if item.format != "stl" || !item.path.ends_with(".stl") {
                    return Err(CommandError::new(
                        "INVALID_ENGINE_OUTPUT",
                        format!(
                            "Mismatched mesh_stl artifact format or extension: {}",
                            item.path
                        ),
                    ));
                }
            }
            "preview_image" => {
                if has_preview_image {
                    return Err(CommandError::new(
                        "INVALID_ENGINE_OUTPUT",
                        "Duplicate preview_image artifact in manifest.",
                    ));
                }
                has_preview_image = true;
                if item.format != "jpg" || !item.path.ends_with(".jpg") {
                    return Err(CommandError::new(
                        "INVALID_ENGINE_OUTPUT",
                        format!(
                            "Mismatched preview_image artifact format or extension: {}",
                            item.path
                        ),
                    ));
                }
                verified_preview_sha = Some(item.sha256.clone());
            }
            unknown => {
                return Err(CommandError::new(
                    "INVALID_ENGINE_OUTPUT",
                    format!("Unknown manifest artifact type '{}'.", unknown),
                ));
            }
        }

        let artifact_path = canonical_run.join(&item.path);

        let art_sym_meta = match fs::symlink_metadata(&artifact_path) {
            Ok(meta) => meta,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                return Err(CommandError::new(
                    "RESULT_ACCESS_UNAVAILABLE",
                    format!("Artifact '{}' not found on disk: {}", item.path, e),
                ));
            }
            Err(e) => {
                return Err(CommandError::new(
                    "RESULT_ACCESS_UNAVAILABLE",
                    format!(
                        "Failed to read metadata for artifact '{}': {}",
                        item.path, e
                    ),
                ));
            }
        };

        if art_sym_meta.is_symlink()
            || (art_sym_meta.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT) != 0
        {
            return Err(CommandError::new(
                "OUTPUT_PATH_NOT_ALLOWED",
                format!("Artifact '{}' is a symlink or reparse point.", item.path),
            ));
        }

        if let Ok(canon_art) = artifact_path.canonicalize() {
            if !canon_art.starts_with(canonical_run) {
                return Err(CommandError::new(
                    "OUTPUT_PATH_NOT_ALLOWED",
                    format!("Artifact '{}' path escapes run directory.", item.path),
                ));
            }
        }

        let art_file = File::open(&artifact_path).map_err(|e| {
            CommandError::new(
                "RESULT_ACCESS_UNAVAILABLE",
                format!("Artifact '{}' not found on disk: {}", item.path, e),
            )
        })?;

        binding.verify_file(&art_file)?;
        let art_meta = art_file.metadata().map_err(|e| {
            CommandError::new(
                "RESULT_ACCESS_UNAVAILABLE",
                format!(
                    "Failed to read metadata for artifact '{}': {}",
                    item.path, e
                ),
            )
        })?;

        if art_meta.is_symlink() || (art_meta.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT) != 0
        {
            return Err(CommandError::new(
                "OUTPUT_PATH_NOT_ALLOWED",
                format!("Artifact '{}' is a symlink or reparse point.", item.path),
            ));
        }

        if !art_meta.is_file() {
            return Err(CommandError::new(
                "RESULT_ACCESS_UNAVAILABLE",
                format!("Artifact '{}' is not a regular file.", item.path),
            ));
        }

        let (file_len, file_sha) =
            compute_file_sha256(art_file, start_time, timeout, cancel_token)?;

        if file_len != item.size_bytes {
            return Err(CommandError::new(
                "RESULT_ACCESS_UNAVAILABLE",
                format!(
                    "Artifact '{}' size mismatch: expected {} bytes, actual {} bytes.",
                    item.path, item.size_bytes, file_len
                ),
            ));
        }

        if !file_sha.eq_ignore_ascii_case(&item.sha256) {
            return Err(CommandError::new(
                "RESULT_ACCESS_UNAVAILABLE",
                format!(
                    "Artifact '{}' SHA-256 digest mismatch against manifest sidecar.",
                    item.path
                ),
            ));
        }

        artifact_records.push(GenerationArtifactRecord {
            format: item.format.clone(),
            filename: item.path.clone(),
            path: simplify_windows_path(&artifact_path)
                .to_string_lossy()
                .to_string(),
            size_bytes: file_len,
        });
    }

    if !has_native_part || !has_geometry_step || !has_mesh_stl {
        return Err(CommandError::new(
            "INVALID_ENGINE_OUTPUT",
            "Manifest missing one or more required artifacts (native_part, geometry_step, mesh_stl).",
        ));
    }

    let summary = ManifestSummary {
        schema_version: manifest.manifest_version,
        provenance_kind: manifest.provenance.kind,
        source_id: manifest.provenance.source_id,
        cad_runtime_version: manifest.cad_runtime.version_build,
        operations_executed: manifest.execution.operations_executed,
        plan_sha256: manifest.fingerprints.plan_sha256,
        prompt_sha256: manifest.fingerprints.prompt_sha256,
        warnings: manifest.warnings,
        diagnostics: manifest
            .diagnostics
            .into_iter()
            .map(|d| ManifestDiagnosticItem {
                severity: d.severity,
                code: d.code,
                message: d.message,
            })
            .collect(),
    };

    let clean_run = simplify_windows_path(canonical_run);

    Ok((
        GenerationResultResponse {
            request_id: request_id.to_string(),
            run_folder: clean_run.to_string_lossy().to_string(),
            artifacts: artifact_records,
            has_preview: has_preview_image,
            preview_sha256: verified_preview_sha.clone(),
            manifest_summary: Some(summary),
        },
        verified_preview_sha,
    ))
}

pub fn reveal_in_explorer(
    selected_output_root: &Path,
    request_id: &str,
    expected_root_identity: Option<FileSystemIdentity>,
) -> Result<RevealResponse, CommandError> {
    let binding =
        verify_folder_containment(selected_output_root, request_id, expected_root_identity)?;
    let clean_run = simplify_windows_path(&binding.canonical_run);

    let explorer_exe = std::env::var_os("SystemRoot")
        .map(|root| PathBuf::from(root).join("explorer.exe"))
        .filter(|p| p.exists())
        .unwrap_or_else(|| PathBuf::from(r"C:\Windows\explorer.exe"));

    std::process::Command::new(explorer_exe)
        .arg(&clean_run)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map_err(|e| {
            CommandError::new(
                "REVEAL_FAILED",
                format!("Failed to spawn Windows Explorer: {}", e),
            )
        })?;

    Ok(RevealResponse { revealed: true })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    struct TestDir(PathBuf);
    impl TestDir {
        fn new(prefix: &str) -> Self {
            let nanos = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let path = std::env::temp_dir().join(format!("cad_test_out_{}_{}", prefix, nanos));
            std::fs::create_dir_all(&path).unwrap();
            Self(path)
        }
        fn path(&self) -> &Path {
            &self.0
        }
    }
    impl Drop for TestDir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn setup_test_workspace(test_name: &str) -> (TestDir, PathBuf, String) {
        let temp = TestDir::new(test_name);
        let request_id = format!("req_{}", test_name);
        let run_dir = temp.path().join(&request_id);
        std::fs::create_dir_all(&run_dir).expect("Failed to create run dir");
        (temp, run_dir, request_id)
    }

    fn build_valid_manifest_json(
        request_id: &str,
        artifacts: &[ManifestArtifactItem],
    ) -> serde_json::Value {
        serde_json::json!({
            "manifest_version": "cad_copilot.run_manifest.v1",
            "request": {
                "request_id": request_id,
                "contract_version": "1.0",
                "kind": "example_plan",
                "unit": "mm"
            },
            "engine": {
                "name": "cad-copilot",
                "version": "0.1.0"
            },
            "cad_runtime": {
                "product": "solid_edge",
                "version_build": "226.00.00.106"
            },
            "provenance": {
                "kind": "example_plan",
                "source_id": "spur_gear"
            },
            "gate_mode": "strict",
            "fingerprints": {
                "plan_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
                "prompt_sha256": null
            },
            "feature_plan": {
                "plan_version": "cad_copilot.single_part_feature_plan.v1",
                "request_id": request_id,
                "units": "mm",
                "part": {
                    "part_id": "p1",
                    "design_intent": "spur gear",
                    "scope": "single_part",
                    "origin": "body_center",
                    "x_axis": "length",
                    "y_axis": "width",
                    "z_axis": "thickness_up"
                },
                "base_body": {
                    "id": "b1",
                    "family": "rectangular_prism",
                    "dimensions_mm": {
                        "length_mm": 100.0,
                        "width_mm": 50.0,
                        "thickness_mm": 20.0
                    },
                    "placement": {
                        "x_mm": 0.0,
                        "y_mm": 0.0,
                        "z_mm": 0.0
                    },
                    "semantic_labels": ["base_plate"]
                },
                "primitive_bodies": [],
                "boolean_operations": [],
                "features": [],
                "lowering_strategy": {
                    "status": "canonical",
                    "preferred_current_target": "cad_copilot_lowering",
                    "runtime_route_change": false
                }
            },
            "stable_ids": {
                "part_id": "p1",
                "body_ids": ["b1"],
                "boolean_operation_ids": [],
                "feature_ids": []
            },
            "defaults_applied": [],
            "diagnostics": [],
            "warnings": ["Test warning"],
            "execution": {
                "operations_executed": 1,
                "operation_results": [],
                "inspection": {
                    "volume_mm3": 1000.0,
                    "mass_kg": 0.05,
                    "feature_count": 1,
                    "body_count": 1,
                    "solid_body_count": 1,
                    "sheet_body_count": 0,
                    "wire_body_count": 0
                }
            },
            "artifacts": artifacts
        })
    }

    fn write_valid_manifest(run_dir: &Path, request_id: &str, artifacts: &[ManifestArtifactItem]) {
        let manifest = build_valid_manifest_json(request_id, artifacts);
        let path = run_dir.join("run_manifest.json");
        let content = serde_json::to_vec_pretty(&manifest).unwrap();
        let mut file = File::create(path).unwrap();
        file.write_all(&content).unwrap();
    }

    fn create_dummy_artifact(dir: &Path, filename: &str, content: &[u8]) -> (u64, String) {
        let path = dir.join(filename);
        let mut file = File::create(&path).unwrap();
        file.write_all(content).unwrap();
        let mut hasher = Sha256::new();
        hasher.update(content);
        let hex = format!("{:02x}", hasher.finalize());
        (content.len() as u64, hex)
    }

    #[test]
    fn test_validate_and_load_result_valid_artifacts() {
        let (temp, run_dir, request_id) = setup_test_workspace("valid_result");

        let (par_len, par_sha) = create_dummy_artifact(&run_dir, "model.par", b"PAR_CONTENT_BYTES");
        let (step_len, step_sha) =
            create_dummy_artifact(&run_dir, "model.step", b"STEP_CONTENT_BYTES");
        let (stl_len, stl_sha) = create_dummy_artifact(&run_dir, "model.stl", b"STL_CONTENT_BYTES");
        let (jpg_len, jpg_sha) =
            create_dummy_artifact(&run_dir, "preview.jpg", b"\xFF\xD8\xFF\xD9");

        let artifacts = vec![
            ManifestArtifactItem {
                artifact_type: "native_part".into(),
                format: "par".into(),
                path: "model.par".into(),
                size_bytes: par_len,
                sha256: par_sha,
            },
            ManifestArtifactItem {
                artifact_type: "geometry_step".into(),
                format: "step".into(),
                path: "model.step".into(),
                size_bytes: step_len,
                sha256: step_sha,
            },
            ManifestArtifactItem {
                artifact_type: "mesh_stl".into(),
                format: "stl".into(),
                path: "model.stl".into(),
                size_bytes: stl_len,
                sha256: stl_sha,
            },
            ManifestArtifactItem {
                artifact_type: "preview_image".into(),
                format: "jpg".into(),
                path: "preview.jpg".into(),
                size_bytes: jpg_len,
                sha256: jpg_sha.clone(),
            },
        ];

        write_valid_manifest(&run_dir, &request_id, &artifacts);

        let (res, preview_sha) = validate_and_load_result(
            temp.path(),
            &request_id,
            None,
            Some("example_plan"),
            None,
            None,
        )
        .expect("Should succeed");
        assert_eq!(res.request_id, request_id);
        assert!(res.has_preview);
        assert_eq!(preview_sha.as_deref(), Some(jpg_sha.as_str()));
        assert_eq!(res.preview_sha256.as_deref(), Some(jpg_sha.as_str()));
        assert_eq!(res.artifacts.len(), 4);
        assert!(!res.artifacts[0].path.is_empty());
        let summary = res.manifest_summary.unwrap();
        assert_eq!(
            summary.cad_runtime_version.as_deref(),
            Some("226.00.00.106")
        );
        assert_eq!(summary.operations_executed, 1);
        assert_eq!(
            summary.plan_sha256,
            "0000000000000000000000000000000000000000000000000000000000000000"
        );
    }

    #[test]
    fn test_validate_and_load_result_hash_mismatch_fails() {
        let (temp, run_dir, request_id) = setup_test_workspace("hash_mismatch");

        let (par_len, _par_sha) = create_dummy_artifact(&run_dir, "model.par", b"PAR_CONTENT");
        let (step_len, step_sha) = create_dummy_artifact(&run_dir, "model.step", b"STEP_CONTENT");
        let (stl_len, stl_sha) = create_dummy_artifact(&run_dir, "model.stl", b"STL_CONTENT");

        let artifacts = vec![
            ManifestArtifactItem {
                artifact_type: "native_part".into(),
                format: "par".into(),
                path: "model.par".into(),
                size_bytes: par_len,
                sha256: "0000000000000000000000000000000000000000000000000000000000000000".into(), // Wrong hash
            },
            ManifestArtifactItem {
                artifact_type: "geometry_step".into(),
                format: "step".into(),
                path: "model.step".into(),
                size_bytes: step_len,
                sha256: step_sha,
            },
            ManifestArtifactItem {
                artifact_type: "mesh_stl".into(),
                format: "stl".into(),
                path: "model.stl".into(),
                size_bytes: stl_len,
                sha256: stl_sha,
            },
        ];

        write_valid_manifest(&run_dir, &request_id, &artifacts);

        let err =
            validate_and_load_result(temp.path(), &request_id, None, None, None, None).unwrap_err();
        assert_eq!(err.code, "RESULT_ACCESS_UNAVAILABLE");
        assert!(err.message.contains("SHA-256 digest mismatch"));
    }

    #[test]
    fn test_path_traversal_request_id_rejected() {
        let temp = TestDir::new("escape");
        let err =
            validate_and_load_result(temp.path(), "../escape", None, None, None, None).unwrap_err();
        assert_eq!(err.code, "INVALID_REQUEST_ID");
    }

    #[test]
    fn test_oversized_manifest_rejected() {
        let (temp, run_dir, request_id) = setup_test_workspace("oversized");
        let manifest_path = run_dir.join("run_manifest.json");
        let mut file = File::create(&manifest_path).unwrap();
        // Write 2 MiB + 1 byte
        let zeros = vec![b' '; (MAX_MANIFEST_BYTES + 1) as usize];
        file.write_all(&zeros).unwrap();

        let err =
            validate_and_load_result(temp.path(), &request_id, None, None, None, None).unwrap_err();
        assert_eq!(err.code, "RESULT_ACCESS_UNAVAILABLE");
        assert!(err.message.contains("exceeds maximum permitted limit"));
    }

    #[test]
    fn test_validate_and_load_result_unc_path_rejected() {
        let unc_root = Path::new(r"\\server\share\runs");
        let err = validate_and_load_result(unc_root, "req_1", None, None, None, None).unwrap_err();
        assert_eq!(err.code, "OUTPUT_PATH_NOT_ALLOWED");
    }

    #[test]
    fn test_validate_and_load_result_invalid_unit_rejected() {
        let (temp, run_dir, request_id) = setup_test_workspace("invalid_unit");
        let (par_len, par_sha) = create_dummy_artifact(&run_dir, "model.par", b"P");
        let (step_len, step_sha) = create_dummy_artifact(&run_dir, "model.step", b"S");
        let (stl_len, stl_sha) = create_dummy_artifact(&run_dir, "model.stl", b"T");

        let mut manifest = build_valid_manifest_json(
            &request_id,
            &[
                ManifestArtifactItem {
                    artifact_type: "native_part".into(),
                    format: "par".into(),
                    path: "model.par".into(),
                    size_bytes: par_len,
                    sha256: par_sha,
                },
                ManifestArtifactItem {
                    artifact_type: "geometry_step".into(),
                    format: "step".into(),
                    path: "model.step".into(),
                    size_bytes: step_len,
                    sha256: step_sha,
                },
                ManifestArtifactItem {
                    artifact_type: "mesh_stl".into(),
                    format: "stl".into(),
                    path: "model.stl".into(),
                    size_bytes: stl_len,
                    sha256: stl_sha,
                },
            ],
        );
        manifest["request"]["unit"] = serde_json::json!("inches");
        let path = run_dir.join("run_manifest.json");
        std::fs::write(path, serde_json::to_vec_pretty(&manifest).unwrap()).unwrap();

        let err =
            validate_and_load_result(temp.path(), &request_id, None, None, None, None).unwrap_err();
        assert_eq!(err.code, "INVALID_ENGINE_OUTPUT");
    }

    #[test]
    fn test_validate_and_load_result_invalid_kind_rejected() {
        let (temp, run_dir, request_id) = setup_test_workspace("invalid_kind");
        let (par_len, par_sha) = create_dummy_artifact(&run_dir, "model.par", b"P");
        let (step_len, step_sha) = create_dummy_artifact(&run_dir, "model.step", b"S");
        let (stl_len, stl_sha) = create_dummy_artifact(&run_dir, "model.stl", b"T");

        let mut manifest = build_valid_manifest_json(
            &request_id,
            &[
                ManifestArtifactItem {
                    artifact_type: "native_part".into(),
                    format: "par".into(),
                    path: "model.par".into(),
                    size_bytes: par_len,
                    sha256: par_sha,
                },
                ManifestArtifactItem {
                    artifact_type: "geometry_step".into(),
                    format: "step".into(),
                    path: "model.step".into(),
                    size_bytes: step_len,
                    sha256: step_sha,
                },
                ManifestArtifactItem {
                    artifact_type: "mesh_stl".into(),
                    format: "stl".into(),
                    path: "model.stl".into(),
                    size_bytes: stl_len,
                    sha256: stl_sha,
                },
            ],
        );
        manifest["request"]["kind"] = serde_json::json!("unsupported_kind");
        let path = run_dir.join("run_manifest.json");
        std::fs::write(path, serde_json::to_vec_pretty(&manifest).unwrap()).unwrap();

        let err =
            validate_and_load_result(temp.path(), &request_id, None, None, None, None).unwrap_err();
        assert_eq!(err.code, "INVALID_ENGINE_OUTPUT");
    }

    #[test]
    fn test_validate_and_load_result_invalid_gate_mode_rejected() {
        let (temp, run_dir, request_id) = setup_test_workspace("invalid_gate");
        let (par_len, par_sha) = create_dummy_artifact(&run_dir, "model.par", b"P");
        let (step_len, step_sha) = create_dummy_artifact(&run_dir, "model.step", b"S");
        let (stl_len, stl_sha) = create_dummy_artifact(&run_dir, "model.stl", b"T");

        let mut manifest = build_valid_manifest_json(
            &request_id,
            &[
                ManifestArtifactItem {
                    artifact_type: "native_part".into(),
                    format: "par".into(),
                    path: "model.par".into(),
                    size_bytes: par_len,
                    sha256: par_sha,
                },
                ManifestArtifactItem {
                    artifact_type: "geometry_step".into(),
                    format: "step".into(),
                    path: "model.step".into(),
                    size_bytes: step_len,
                    sha256: step_sha,
                },
                ManifestArtifactItem {
                    artifact_type: "mesh_stl".into(),
                    format: "stl".into(),
                    path: "model.stl".into(),
                    size_bytes: stl_len,
                    sha256: stl_sha,
                },
            ],
        );
        manifest["gate_mode"] = serde_json::json!("canonical");
        let path = run_dir.join("run_manifest.json");
        std::fs::write(path, serde_json::to_vec_pretty(&manifest).unwrap()).unwrap();

        let err =
            validate_and_load_result(temp.path(), &request_id, None, None, None, None).unwrap_err();
        assert_eq!(err.code, "INVALID_ENGINE_OUTPUT");
    }

    #[test]
    fn test_validate_and_load_result_canonical_schema_violation_rejected() {
        let (temp, run_dir, request_id) = setup_test_workspace("schema_violation");
        let (par_len, par_sha) = create_dummy_artifact(&run_dir, "model.par", b"P");
        let (step_len, step_sha) = create_dummy_artifact(&run_dir, "model.step", b"S");
        let (stl_len, stl_sha) = create_dummy_artifact(&run_dir, "model.stl", b"T");

        let mut manifest = build_valid_manifest_json(
            &request_id,
            &[
                ManifestArtifactItem {
                    artifact_type: "native_part".into(),
                    format: "par".into(),
                    path: "model.par".into(),
                    size_bytes: par_len,
                    sha256: par_sha,
                },
                ManifestArtifactItem {
                    artifact_type: "geometry_step".into(),
                    format: "step".into(),
                    path: "model.step".into(),
                    size_bytes: step_len,
                    sha256: step_sha,
                },
                ManifestArtifactItem {
                    artifact_type: "mesh_stl".into(),
                    format: "stl".into(),
                    path: "model.stl".into(),
                    size_bytes: stl_len,
                    sha256: stl_sha,
                },
            ],
        );
        // Omit required base_body from feature_plan to violate canonical schema
        manifest["feature_plan"]
            .as_object_mut()
            .unwrap()
            .remove("base_body");
        let path = run_dir.join("run_manifest.json");
        std::fs::write(path, serde_json::to_vec_pretty(&manifest).unwrap()).unwrap();

        let err =
            validate_and_load_result(temp.path(), &request_id, None, None, None, None).unwrap_err();
        assert_eq!(err.code, "INVALID_ENGINE_OUTPUT");
        assert!(err.message.contains("canonical schema validation"));
    }

    #[test]
    fn test_verify_folder_containment_identity_mismatch() {
        let (temp, _run_dir, request_id) = setup_test_workspace("identity_mismatch");
        let fake_identity = FileSystemIdentity {
            volume_serial_number: 999999,
            file_index: 888888,
        };
        let err =
            verify_folder_containment(temp.path(), &request_id, Some(fake_identity)).unwrap_err();
        assert_eq!(err.code, "OUTPUT_PATH_NOT_ALLOWED");
        assert!(err.message.contains("filesystem identity"));
    }

    #[test]
    fn test_actual_directory_replacement_at_same_pathname_rejected() {
        let parent_dir = TestDir::new("replacement");
        let output_root = parent_dir.path().join("output_root");
        std::fs::create_dir(&output_root).unwrap();

        // 1. Capture the initial identity of the original directory
        let initial_identity = get_path_filesystem_identity(&output_root).unwrap();

        // 2. Simulate attacker replacing the directory at the exact same pathname:
        // Move original aside and create a brand new directory at the same path
        let original_backup = parent_dir.path().join("output_root_original");
        std::fs::rename(&output_root, &original_backup).unwrap();
        std::fs::create_dir(&output_root).unwrap();

        let request_id = "req-replacement-test";
        let run_dir = output_root.join(request_id);
        std::fs::create_dir(&run_dir).unwrap();
        write_valid_manifest(&run_dir, request_id, &[]);

        // 3. Verify that validate_and_load_result rejects the replaced directory
        // even though it resides at the exact same pathname
        let err = validate_and_load_result(
            &output_root,
            request_id,
            None,
            None,
            None,
            Some(initial_identity),
        )
        .unwrap_err();

        assert_eq!(err.code, "OUTPUT_PATH_NOT_ALLOWED");
        assert!(err.message.contains("filesystem identity"));

        // Also test that verify_folder_containment rejects it directly
        let err2 = verify_folder_containment(&output_root, request_id, Some(initial_identity))
            .unwrap_err();
        assert_eq!(err2.code, "OUTPUT_PATH_NOT_ALLOWED");
        assert!(err2.message.contains("filesystem identity"));
    }

    #[test]
    fn test_directory_binding_prevents_rename_during_access() {
        let parent_dir = TestDir::new("binding_lock");
        let output_root = parent_dir.path().join("output_root");
        std::fs::create_dir(&output_root).unwrap();
        let request_id = "req-binding-lock";
        let run_dir = output_root.join(request_id);
        std::fs::create_dir(&run_dir).unwrap();

        {
            // Acquire binding (which holds open directory handles without FILE_SHARE_DELETE)
            let binding = verify_folder_containment(&output_root, request_id, None).unwrap();

            // Attempting to rename output_root while binding is held MUST fail on Windows
            let rename_root =
                std::fs::rename(&output_root, parent_dir.path().join("output_root_renamed"));
            assert!(
                rename_root.is_err(),
                "Output root rename should be blocked by OS while directory binding is active"
            );

            // Attempting to rename run_dir while binding is held MUST fail on Windows
            let rename_run = std::fs::rename(&run_dir, output_root.join("run_renamed"));
            assert!(
                rename_run.is_err(),
                "Run directory rename should be blocked by OS while directory binding is active"
            );

            assert_eq!(binding.canonical_root, output_root.canonicalize().unwrap());
        }

        // Once binding is dropped, directory rename succeeds
        let rename_after =
            std::fs::rename(&output_root, parent_dir.path().join("output_root_renamed"));
        assert!(rename_after.is_ok());
    }

    #[test]
    fn test_validate_and_load_result_wrong_inventory_duplicate_rejected() {
        let (temp, run_dir, request_id) = setup_test_workspace("dup_inventory");
        let (stl1_len, stl1_sha) = create_dummy_artifact(&run_dir, "model1.stl", b"T1");
        let (stl2_len, stl2_sha) = create_dummy_artifact(&run_dir, "model2.stl", b"T2");
        let (step_len, step_sha) = create_dummy_artifact(&run_dir, "model.step", b"S");

        let artifacts = vec![
            ManifestArtifactItem {
                artifact_type: "mesh_stl".into(),
                format: "stl".into(),
                path: "model1.stl".into(),
                size_bytes: stl1_len,
                sha256: stl1_sha,
            },
            ManifestArtifactItem {
                artifact_type: "mesh_stl".into(),
                format: "stl".into(),
                path: "model2.stl".into(),
                size_bytes: stl2_len,
                sha256: stl2_sha,
            },
            ManifestArtifactItem {
                artifact_type: "geometry_step".into(),
                format: "step".into(),
                path: "model.step".into(),
                size_bytes: step_len,
                sha256: step_sha,
            },
        ];

        write_valid_manifest(&run_dir, &request_id, &artifacts);

        let err =
            validate_and_load_result(temp.path(), &request_id, None, None, None, None).unwrap_err();
        assert_eq!(err.code, "INVALID_ENGINE_OUTPUT");
        assert!(
            err.message.contains("Duplicate mesh_stl")
                || err.message.contains("canonical schema validation")
        );
    }

    #[test]
    fn test_validate_and_load_result_cancellation_aborts() {
        let (temp, run_dir, request_id) = setup_test_workspace("cancelled");
        let (par_len, par_sha) = create_dummy_artifact(&run_dir, "model.par", b"P");
        let (step_len, step_sha) = create_dummy_artifact(&run_dir, "model.step", b"S");
        let (stl_len, stl_sha) = create_dummy_artifact(&run_dir, "model.stl", b"T");

        let artifacts = vec![
            ManifestArtifactItem {
                artifact_type: "native_part".into(),
                format: "par".into(),
                path: "model.par".into(),
                size_bytes: par_len,
                sha256: par_sha,
            },
            ManifestArtifactItem {
                artifact_type: "geometry_step".into(),
                format: "step".into(),
                path: "model.step".into(),
                size_bytes: step_len,
                sha256: step_sha,
            },
            ManifestArtifactItem {
                artifact_type: "mesh_stl".into(),
                format: "stl".into(),
                path: "model.stl".into(),
                size_bytes: stl_len,
                sha256: stl_sha,
            },
        ];

        write_valid_manifest(&run_dir, &request_id, &artifacts);

        let cancel_token = std::sync::atomic::AtomicBool::new(true);
        let err = validate_and_load_result(
            temp.path(),
            &request_id,
            Some(&cancel_token),
            None,
            None,
            None,
        )
        .unwrap_err();
        assert_eq!(err.code, "RESULT_ACCESS_UNAVAILABLE");
        assert!(err.message.contains("cancelled"));
    }

    #[test]
    fn test_validate_and_load_result_missing_file_on_disk_fails() {
        let (temp, run_dir, request_id) = setup_test_workspace("missing_file");
        let (par_len, par_sha) = create_dummy_artifact(&run_dir, "model.par", b"P");
        let (step_len, step_sha) = create_dummy_artifact(&run_dir, "model.step", b"S");
        let stl_len = 128;
        let stl_sha = "0000000000000000000000000000000000000000000000000000000000000000";

        let artifacts = vec![
            ManifestArtifactItem {
                artifact_type: "native_part".into(),
                format: "par".into(),
                path: "model.par".into(),
                size_bytes: par_len,
                sha256: par_sha,
            },
            ManifestArtifactItem {
                artifact_type: "geometry_step".into(),
                format: "step".into(),
                path: "model.step".into(),
                size_bytes: step_len,
                sha256: step_sha,
            },
            ManifestArtifactItem {
                artifact_type: "mesh_stl".into(),
                format: "stl".into(),
                path: "model.stl".into(),
                size_bytes: stl_len,
                sha256: stl_sha.into(),
            },
        ];

        write_valid_manifest(&run_dir, &request_id, &artifacts);

        let err =
            validate_and_load_result(temp.path(), &request_id, None, None, None, None).unwrap_err();
        assert_eq!(err.code, "RESULT_ACCESS_UNAVAILABLE");
        assert!(err.message.contains("not found on disk"));
    }

    #[test]
    fn test_validate_and_load_result_surfaces_safe_diagnostics() {
        let (temp, run_dir, request_id) = setup_test_workspace("safe_diags");
        let (par_len, par_sha) = create_dummy_artifact(&run_dir, "model.par", b"P");
        let (step_len, step_sha) = create_dummy_artifact(&run_dir, "model.step", b"S");
        let (stl_len, stl_sha) = create_dummy_artifact(&run_dir, "model.stl", b"T");

        let mut manifest = build_valid_manifest_json(
            &request_id,
            &[
                ManifestArtifactItem {
                    artifact_type: "native_part".into(),
                    format: "par".into(),
                    path: "model.par".into(),
                    size_bytes: par_len,
                    sha256: par_sha,
                },
                ManifestArtifactItem {
                    artifact_type: "geometry_step".into(),
                    format: "step".into(),
                    path: "model.step".into(),
                    size_bytes: step_len,
                    sha256: step_sha,
                },
                ManifestArtifactItem {
                    artifact_type: "mesh_stl".into(),
                    format: "stl".into(),
                    path: "model.stl".into(),
                    size_bytes: stl_len,
                    sha256: stl_sha,
                },
            ],
        );
        manifest["diagnostics"] = serde_json::json!([
            {
                "severity": "warning",
                "code": "WARN_TOLERANCE",
                "message": "Mesh tolerance relaxed to 0.05mm",
                "path": "/internal/workstation/path/secret.log"
            }
        ]);
        let path = run_dir.join("run_manifest.json");
        std::fs::write(path, serde_json::to_vec_pretty(&manifest).unwrap()).unwrap();

        let (res, _) =
            validate_and_load_result(temp.path(), &request_id, None, None, None, None).unwrap();
        let summary = res.manifest_summary.expect("manifest summary must exist");
        assert_eq!(summary.diagnostics.len(), 1);
        assert_eq!(summary.diagnostics[0].severity, "warning");
        assert_eq!(summary.diagnostics[0].code, "WARN_TOLERANCE");
        assert_eq!(
            summary.diagnostics[0].message,
            "Mesh tolerance relaxed to 0.05mm"
        );
    }

    #[test]
    fn test_validate_and_load_result_mismatched_wire_artifacts_rejected() {
        let (temp, run_dir, request_id) = setup_test_workspace("mismatched_wire");
        let (par_len, par_sha) = create_dummy_artifact(&run_dir, "model.par", b"P");
        let (step_len, step_sha) = create_dummy_artifact(&run_dir, "model.step", b"S");
        let (stl_len, stl_sha) = create_dummy_artifact(&run_dir, "model.stl", b"T");

        let artifacts = vec![
            ManifestArtifactItem {
                artifact_type: "native_part".into(),
                format: "par".into(),
                path: "model.par".into(),
                size_bytes: par_len,
                sha256: par_sha,
            },
            ManifestArtifactItem {
                artifact_type: "geometry_step".into(),
                format: "step".into(),
                path: "model.step".into(),
                size_bytes: step_len,
                sha256: step_sha,
            },
            ManifestArtifactItem {
                artifact_type: "mesh_stl".into(),
                format: "stl".into(),
                path: "model.stl".into(),
                size_bytes: stl_len,
                sha256: stl_sha,
            },
        ];

        write_valid_manifest(&run_dir, &request_id, &artifacts);

        let expected_wire = vec![
            super::super::protocol::WireArtifactItem {
                artifact_type: "native_part".into(),
                format: "par".into(),
                path: "model.par".into(),
                origin: "cad_runtime".into(),
            },
            super::super::protocol::WireArtifactItem {
                artifact_type: "mesh_stl".into(),
                format: "stl".into(),
                path: "other.stl".into(),
                origin: "cad_runtime".into(),
            },
        ];

        let err = validate_and_load_result(
            temp.path(),
            &request_id,
            None,
            Some("example_plan"),
            Some(&expected_wire),
            None,
        )
        .unwrap_err();
        assert_eq!(err.code, "INVALID_ENGINE_OUTPUT");
        assert!(err.message.contains("Artifact count mismatch"));
    }

    #[test]
    fn test_validate_and_load_result_wire_artifacts_path_equality() {
        let (temp, run_dir, request_id) = setup_test_workspace("wire_path_eq");
        let (par_len, par_sha) = create_dummy_artifact(&run_dir, "model.par", b"P");
        let (step_len, step_sha) = create_dummy_artifact(&run_dir, "model.step", b"S");
        let (stl_len, stl_sha) = create_dummy_artifact(&run_dir, "model.stl", b"T");

        let artifacts = vec![
            ManifestArtifactItem {
                artifact_type: "native_part".into(),
                format: "par".into(),
                path: "model.par".into(),
                size_bytes: par_len,
                sha256: par_sha,
            },
            ManifestArtifactItem {
                artifact_type: "geometry_step".into(),
                format: "step".into(),
                path: "model.step".into(),
                size_bytes: step_len,
                sha256: step_sha,
            },
            ManifestArtifactItem {
                artifact_type: "mesh_stl".into(),
                format: "stl".into(),
                path: "model.stl".into(),
                size_bytes: stl_len,
                sha256: stl_sha,
            },
        ];

        write_valid_manifest(&run_dir, &request_id, &artifacts);

        let expected_wire = vec![
            super::super::protocol::WireArtifactItem {
                artifact_type: "native_part".into(),
                format: "par".into(),
                path: run_dir.join("model.par").to_string_lossy().to_string(),
                origin: "cad_copilot".into(),
            },
            super::super::protocol::WireArtifactItem {
                artifact_type: "geometry_step".into(),
                format: "step".into(),
                path: run_dir.join("model.step").to_string_lossy().to_string(),
                origin: "cad_copilot".into(),
            },
            super::super::protocol::WireArtifactItem {
                artifact_type: "mesh_stl".into(),
                format: "stl".into(),
                path: run_dir.join("model.stl").to_string_lossy().to_string(),
                origin: "cad_copilot".into(),
            },
        ];

        let (res, _) = validate_and_load_result(
            temp.path(),
            &request_id,
            None,
            Some("example_plan"),
            Some(&expected_wire),
            None,
        )
        .expect("Should succeed with full wire paths matching bare manifest paths");
        assert_eq!(res.artifacts.len(), 3);
    }

    #[test]
    fn test_simplify_windows_path() {
        assert_eq!(
            simplify_windows_path(Path::new(r"\\?\C:\foo\bar")),
            PathBuf::from(r"C:\foo\bar")
        );
        assert_eq!(
            simplify_windows_path(Path::new(r"\\?\UNC\server\share\foo")),
            PathBuf::from(r"\\server\share\foo")
        );
        assert_eq!(
            simplify_windows_path(Path::new(r"C:\normal\path")),
            PathBuf::from(r"C:\normal\path")
        );
    }
}
