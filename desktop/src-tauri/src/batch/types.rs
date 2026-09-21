use serde::{Deserialize, Serialize};

pub use crate::shared::engine::EngineBuildInfo;
pub use crate::shared::error::CommandError;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BatchSnapshot {
    pub revision: u64,
    pub native_available: bool,
    pub source: Option<BatchSourceSelection>,
    pub output: Option<BatchOutputSelection>,
    pub run: Option<BatchRunSnapshot>,
    pub engine_build: EngineBuildInfo,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BatchSourceSelection {
    pub selection_id: String,
    pub display_root: String,
    pub par_count: u32,
    pub psm_count: u32,
    pub asm_count: u32,
    pub dft_count: u32,
    pub skipped_count: u32,
    pub preview_files: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BatchOutputSelection {
    pub selection_id: String,
    pub display_path: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BatchRunSnapshot {
    pub request_id: String,
    pub state: BatchRunState,
    pub engine_status: Option<BatchEngineStatus>,
    pub phase: Option<BatchPhase>,
    pub total_files: u32,
    pub completed_files: u32,
    pub current_file: Option<String>,
    pub current_format: Option<String>,
    pub last_file_status: Option<String>,
    pub cleanup: CleanupState,
    pub close_requested: bool,
    pub manifest_state: ManifestState,
    pub reason: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BatchRunState {
    Starting,
    Running,
    Cancelling,
    Terminal,
}

impl BatchRunState {
    pub fn is_terminal(&self) -> bool {
        matches!(self, BatchRunState::Terminal)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BatchEngineStatus {
    Completed,
    Cancelled,
    Rejected,
    Failed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BatchPhase {
    BatchStarted,
    FileStarted,
    FormatStarted,
    FormatFinished,
    FileFinished,
    BatchFinished,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ManifestState {
    NotApplicable,
    Checking,
    Validated,
    Unavailable,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CleanupState {
    NoFailureObserved,
    Incomplete,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SourceSelectMode {
    Files,
    Folder,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CloseDecision {
    Stay,
    CancelAndClose,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SelectSourceRequest {
    pub mode: SourceSelectMode,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BatchSelectSourceResponse {
    pub selection_id: String,
    pub display_root: String,
    pub par_count: u32,
    pub psm_count: u32,
    pub asm_count: u32,
    pub dft_count: u32,
    pub skipped_count: u32,
    pub files: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BatchStartRequest {
    pub source_selection_id: String,
    pub output_selection_id: String,
    pub operation: String,
    pub formats: Vec<String>,
    pub continue_on_error: bool,
    pub max_files: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BatchCancelRequest {
    pub request_id: String,
}

pub type CancelBatchRequest = BatchCancelRequest;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RunTargetRequest {
    pub request_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ResolveBatchCloseRequest {
    pub request_id: String,
    pub decision: CloseDecision,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BatchSummaryRecord {
    pub total: u32,
    pub accepted: u32,
    pub partial: u32,
    pub failed: u32,
    pub cancelled: u32,
    pub unprocessed: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BatchArtifactRecord {
    pub format: String,
    pub relative_path: String,
    pub size_bytes: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BatchRowCategory {
    Succeeded,
    Partial,
    Failed,
    Cancelled,
    Unprocessed,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BatchFileRowRecord {
    pub file: String,
    pub category: BatchRowCategory,
    pub attempted_formats: Vec<String>,
    pub successful_artifacts: Vec<BatchArtifactRecord>,
    pub diagnostic_codes: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BatchResultResponse {
    pub request_id: String,
    pub engine_status: BatchEngineStatus,
    pub operation: String,
    pub summary: BatchSummaryRecord,
    pub rows: Vec<BatchFileRowRecord>,
    pub manifest_state: ManifestState,
    pub manifest_path: Option<String>,
    pub reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub engine_version: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cad_runtime_version: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BatchRevealResponse {
    pub acknowledged: bool,
    pub target: String,
}
