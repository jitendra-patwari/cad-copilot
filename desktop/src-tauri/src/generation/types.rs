use serde::{Deserialize, Serialize};

pub use crate::shared::engine::EngineBuildInfo;
pub use crate::shared::error::CommandError;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct GenerationSnapshot {
    pub revision: u64,
    pub native_available: bool,
    pub key_configured: bool,
    pub output: Option<GenerationOutputSelection>,
    pub run: Option<GenerationRunSnapshot>,
    pub engine_build: EngineBuildInfo,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct GenerationOutputSelection {
    pub selection_id: String,
    pub display_path: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct GenerationRunSnapshot {
    pub request_id: String,
    pub state: RunState,
    pub phase: Option<RunPhase>,
    pub engine_status: Option<EngineStatus>,
    pub reason: Option<String>,
    pub result_access: ResultAccess,
    pub cleanup: CleanupState,
    pub close_requested: bool,
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RunState {
    Starting,
    Running,
    Cancelling,
    Succeeded,
    Rejected,
    Failed,
    Cancelled,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RunPhase {
    RequestReceived,
    RequestValidated,
    GenerationStarted,
    ResponseReady,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EngineStatus {
    Accepted,
    Rejected,
    Failed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ResultAccess {
    None,
    Checking,
    Ready,
    Unavailable,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CleanupState {
    NotStarted,
    Pending,
    NoFailureObserved,
    Incomplete,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(
    tag = "kind",
    rename_all = "snake_case",
    rename_all_fields = "camelCase"
)]
pub enum GenerationInput {
    ExamplePlan { example_id: String },
    PromptToCad { prompt: String },
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SetKeyRequest {
    pub key: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SetKeyResponse {
    pub configured: bool,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StartGenerationRequest {
    pub selection_id: String,
    pub input: GenerationInput,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CancelGenerationRequest {
    pub request_id: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RunTargetRequest {
    pub request_id: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CloseDecision {
    Stay,
    CancelAndClose,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ResolveCloseRequest {
    pub request_id: String,
    pub decision: CloseDecision,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RevealResponse {
    pub revealed: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct GenerationArtifactRecord {
    pub format: String,
    pub filename: String,
    pub path: String,
    pub size_bytes: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct GenerationResultResponse {
    pub request_id: String,
    pub run_folder: String,
    pub artifacts: Vec<GenerationArtifactRecord>,
    pub has_preview: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub preview_sha256: Option<String>,
    pub manifest_summary: Option<ManifestSummary>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ManifestDiagnosticItem {
    pub severity: String,
    pub code: String,
    pub message: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ManifestSummary {
    pub schema_version: String,
    pub provenance_kind: String,
    pub source_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub engine_version: Option<String>,
    pub cad_runtime_version: Option<String>,
    pub operations_executed: u32,
    pub plan_sha256: String,
    pub prompt_sha256: Option<String>,
    pub warnings: Vec<String>,
    #[serde(default)]
    pub diagnostics: Vec<ManifestDiagnosticItem>,
}
