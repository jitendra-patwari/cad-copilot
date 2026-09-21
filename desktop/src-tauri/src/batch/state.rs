use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use crate::shared::path::{get_path_filesystem_identity, FileSystemIdentity};

use super::selection::{
    detect_supported_extension, has_reparse_component, SelectedBatchOutput, SelectedBatchSource,
};
use super::types::{
    BatchEngineStatus, BatchOutputSelection, BatchPhase, BatchResultResponse, BatchRunSnapshot,
    BatchRunState, BatchSelectSourceResponse, BatchSnapshot, BatchStartRequest, CleanupState,
    CloseDecision, CommandError, ManifestState,
};

pub type AppState = Mutex<BatchState>;

#[derive(Debug, Clone)]
pub struct ActiveBatchRunState {
    pub request_id: String,
    pub source_selection_id: String,
    pub output_selection_id: String,
    pub operation: String,
    pub formats: Vec<String>,
    pub continue_on_error: bool,
    pub max_files: u32,
    pub eligible_files: Vec<String>,
    pub source_root: PathBuf,
    pub output_root: PathBuf,
    pub source_root_identity: FileSystemIdentity,
    pub output_root_identity: FileSystemIdentity,
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
    pub close_after_cleanup: bool,
    pub manifest_state: ManifestState,
    pub reason: Option<String>,
    pub cancel_token: Arc<AtomicBool>,
    pub cached_result: Option<BatchResultResponse>,
}

impl ActiveBatchRunState {
    pub fn to_snapshot(&self) -> BatchRunSnapshot {
        BatchRunSnapshot {
            request_id: self.request_id.clone(),
            state: self.state,
            engine_status: self.engine_status,
            phase: self.phase,
            total_files: self.total_files,
            completed_files: self.completed_files,
            current_file: self.current_file.clone(),
            current_format: self.current_format.clone(),
            last_file_status: self.last_file_status.clone(),
            cleanup: self.cleanup,
            close_requested: self.close_requested,
            manifest_state: self.manifest_state,
            reason: self.reason.clone(),
        }
    }
}

pub struct BatchState {
    pub revision: u64,
    pub selected_source: Option<SelectedBatchSource>,
    pub selected_output: Option<SelectedBatchOutput>,
    pub active_run: Option<ActiveBatchRunState>,
    pub terminating: bool,
}

impl Default for BatchState {
    fn default() -> Self {
        Self::new()
    }
}

impl BatchState {
    pub fn new() -> Self {
        Self {
            revision: 1,
            selected_source: None,
            selected_output: None,
            active_run: None,
            terminating: false,
        }
    }

    pub fn to_snapshot(&self) -> BatchSnapshot {
        BatchSnapshot {
            revision: self.revision,
            native_available: true,
            source: self.selected_source.as_ref().map(|s| s.to_summary()),
            output: self.selected_output.as_ref().map(|o| BatchOutputSelection {
                selection_id: o.selection_id.clone(),
                display_path: o.canonical_root.to_string_lossy().to_string(),
            }),
            run: self.active_run.as_ref().map(|r| r.to_snapshot()),
            engine_build: crate::shared::engine::current_engine_build_info(),
        }
    }

    pub fn is_run_active(&self) -> bool {
        if let Some(run) = &self.active_run {
            matches!(
                run.state,
                BatchRunState::Starting | BatchRunState::Running | BatchRunState::Cancelling
            )
        } else {
            false
        }
    }

    pub fn has_incomplete_cleanup(&self) -> bool {
        if let Some(run) = &self.active_run {
            run.cleanup == CleanupState::Incomplete
        } else {
            false
        }
    }

    pub fn set_source(
        &mut self,
        source: SelectedBatchSource,
    ) -> Result<BatchSelectSourceResponse, CommandError> {
        if self.is_run_active() {
            return Err(CommandError::new(
                "RUN_ACTIVE",
                "Cannot change source selection while a batch run is in progress.",
            ));
        }

        let resp = source.to_select_response();
        self.selected_source = Some(source);
        self.revision += 1;
        Ok(resp)
    }

    pub fn set_output(
        &mut self,
        output: SelectedBatchOutput,
    ) -> Result<BatchOutputSelection, CommandError> {
        if self.is_run_active() {
            return Err(CommandError::new(
                "RUN_ACTIVE",
                "Cannot change output selection while a batch run is in progress.",
            ));
        }

        let resp = BatchOutputSelection {
            selection_id: output.selection_id.clone(),
            display_path: output.canonical_root.to_string_lossy().to_string(),
        };
        self.selected_output = Some(output);
        self.revision += 1;
        Ok(resp)
    }

    pub fn validate_start_request(
        &self,
        req: &BatchStartRequest,
    ) -> Result<(SelectedBatchSource, SelectedBatchOutput, Vec<String>), CommandError> {
        if self.is_run_active() {
            return Err(CommandError::new(
                "RUN_ACTIVE",
                "A batch operation is already in progress.",
            ));
        }

        if self.has_incomplete_cleanup() {
            return Err(CommandError::new(
                "RUN_ACTIVE",
                "Cannot start a new batch run while prior run cleanup is incomplete.",
            ));
        }

        let source = self.selected_source.as_ref().ok_or_else(|| {
            CommandError::new("NO_SOURCE_SELECTED", "No source files or folder selected.")
        })?;

        if source.selection_id != req.source_selection_id {
            return Err(CommandError::new(
                "SELECTION_NOT_FOUND",
                "The specified source selection ID is invalid or stale.",
            ));
        }

        let output = self.selected_output.as_ref().ok_or_else(|| {
            CommandError::new("NO_OUTPUT_SELECTED", "No output directory selected.")
        })?;

        if output.selection_id != req.output_selection_id {
            return Err(CommandError::new(
                "SELECTION_NOT_FOUND",
                "The specified output selection ID is invalid or stale.",
            ));
        }

        // Recheck root existence, reparse components, and identity
        if has_reparse_component(&source.canonical_root) {
            return Err(CommandError::new(
                "INPUT_PATH_NOT_ALLOWED",
                "Source root directory or an ancestor is a symlink, junction, or reparse point.",
            ));
        }

        let current_source_id =
            get_path_filesystem_identity(&source.canonical_root).map_err(|_| {
                CommandError::new(
                    "INPUT_ROOT_NOT_FOUND",
                    "Source root directory verification failed.",
                )
            })?;
        if current_source_id != source.root_identity {
            return Err(CommandError::new(
                "INPUT_ROOT_NOT_FOUND",
                "Source root directory identity changed since selection.",
            ));
        }

        if has_reparse_component(&output.canonical_root) {
            return Err(CommandError::new(
                "OUTPUT_PATH_NOT_ALLOWED",
                "Output directory or an ancestor is a symlink, junction, or reparse point.",
            ));
        }

        let current_output_id =
            get_path_filesystem_identity(&output.canonical_root).map_err(|_| {
                CommandError::new(
                    "OUTPUT_UNAVAILABLE",
                    "Output directory verification failed.",
                )
            })?;
        if current_output_id != output.root_identity {
            return Err(CommandError::new(
                "OUTPUT_UNAVAILABLE",
                "Output directory identity changed since selection.",
            ));
        }

        // Validate operation and formats
        let is_3d = req.operation == "export_3d";
        let is_drawing = req.operation == "publish_drawing";

        if !is_3d && !is_drawing {
            return Err(CommandError::with_field(
                "INVALID_OPERATION",
                format!("Unsupported operation '{}'. Supported operations are 'export_3d' and 'publish_drawing'.", req.operation),
                "operation",
            ));
        }

        if req.formats.is_empty() {
            return Err(CommandError::with_field(
                "INVALID_FORMATS",
                "At least one output format must be specified.",
                "formats",
            ));
        }

        let mut seen_formats = std::collections::HashSet::new();
        for fmt in &req.formats {
            let lower = fmt.to_lowercase();
            if !seen_formats.insert(lower.clone()) {
                return Err(CommandError::with_field(
                    "INVALID_FORMATS",
                    format!("Duplicate format '{}' specified.", fmt),
                    "formats",
                ));
            }

            if is_3d && !matches!(lower.as_str(), "step" | "stl" | "parasolid") {
                return Err(CommandError::with_field(
                    "INVALID_FORMATS",
                    format!("Unsupported format '{}' for 3D export. Supported formats: step, stl, parasolid.", fmt),
                    "formats",
                ));
            }

            if is_drawing && !matches!(lower.as_str(), "pdf" | "dxf") {
                return Err(CommandError::with_field(
                    "INVALID_FORMATS",
                    format!("Unsupported format '{}' for drawing publication. Supported formats: pdf, dxf.", fmt),
                    "formats",
                ));
            }
        }

        if req.max_files == 0 || req.max_files > 500 {
            return Err(CommandError::with_field(
                "INVALID_MAX_FILES",
                "Max files must be between 1 and 500.",
                "maxFiles",
            ));
        }

        // Filter eligible files for the chosen operation
        let mut eligible_files = Vec::new();
        for rel_file in &source.ordered_relative_files {
            let path = PathBuf::from(rel_file);
            let ext = detect_supported_extension(&path).unwrap_or("");
            let eligible = if is_3d {
                matches!(ext, "par" | "psm" | "asm")
            } else {
                matches!(ext, "dft")
            };
            if eligible {
                eligible_files.push(rel_file.clone());
            }
        }

        if eligible_files.is_empty() {
            return Err(CommandError::new(
                "NO_ELIGIBLE_FILES",
                format!(
                    "No files eligible for operation '{}' in the current selection.",
                    req.operation
                ),
            ));
        }

        if eligible_files.len() as u32 > req.max_files {
            return Err(CommandError::new(
                "TOO_MANY_FILES",
                format!(
                    "Selected {} eligible files, which exceeds the configured max files limit of {}. Please increase max files (up to 500).",
                    eligible_files.len(),
                    req.max_files
                ),
            ));
        }

        // Recheck filesystem identity for all eligible files before reserving run
        for rel_file in &eligible_files {
            let full_path = source.canonical_root.join(rel_file);
            let current_id = get_path_filesystem_identity(&full_path).map_err(|_| {
                CommandError::new("INPUT_FILE_NOT_FOUND", "Source file verification failed.")
            })?;
            if let Some(&stored_id) = source.file_identities.get(rel_file) {
                if current_id != stored_id {
                    return Err(CommandError::new(
                        "INPUT_FILE_NOT_FOUND",
                        "Source file identity changed since selection.",
                    ));
                }
            }
        }

        Ok((source.clone(), output.clone(), eligible_files))
    }

    pub fn reserve_run(
        &mut self,
        req: &BatchStartRequest,
        request_id: String,
    ) -> Result<BatchSnapshot, CommandError> {
        let (source, output, eligible_files) = self.validate_start_request(req)?;

        let total_files = eligible_files.len() as u32;
        let cancel_token = Arc::new(AtomicBool::new(false));

        let active_run = ActiveBatchRunState {
            request_id,
            source_selection_id: req.source_selection_id.clone(),
            output_selection_id: req.output_selection_id.clone(),
            operation: req.operation.clone(),
            formats: req.formats.clone(),
            continue_on_error: req.continue_on_error,
            max_files: req.max_files,
            eligible_files,
            source_root: source.canonical_root,
            output_root: output.canonical_root,
            source_root_identity: source.root_identity,
            output_root_identity: output.root_identity,
            state: BatchRunState::Starting,
            engine_status: None,
            phase: None,
            total_files,
            completed_files: 0,
            current_file: None,
            current_format: None,
            last_file_status: None,
            cleanup: CleanupState::NoFailureObserved,
            close_requested: false,
            close_after_cleanup: false,
            manifest_state: ManifestState::NotApplicable,
            reason: None,
            cancel_token,
            cached_result: None,
        };

        self.active_run = Some(active_run);
        self.revision += 1;
        Ok(self.to_snapshot())
    }

    pub fn request_cancel(&mut self, request_id: &str) -> Result<BatchSnapshot, CommandError> {
        let run = self.active_run.as_mut().ok_or_else(|| {
            CommandError::new("RUN_NOT_FOUND", "The requested batch run was not found.")
        })?;

        if run.request_id != request_id {
            return Err(CommandError::new(
                "RUN_NOT_FOUND",
                "The requested batch run was not found.",
            ));
        }

        if run.state == BatchRunState::Cancelling {
            return Ok(self.to_snapshot());
        }

        if matches!(run.state, BatchRunState::Starting | BatchRunState::Running) {
            run.state = BatchRunState::Cancelling;
            run.reason = Some("Cancellation requested by user.".to_string());
            run.cancel_token.store(true, Ordering::SeqCst);
            self.revision += 1;
        }

        Ok(self.to_snapshot())
    }

    pub fn resolve_close(
        &mut self,
        request_id: &str,
        decision: CloseDecision,
    ) -> Result<BatchSnapshot, CommandError> {
        let run = self.active_run.as_mut().ok_or_else(|| {
            CommandError::new("RUN_NOT_FOUND", "The requested batch run was not found.")
        })?;

        if run.request_id != request_id {
            return Err(CommandError::new(
                "RUN_NOT_FOUND",
                "The requested batch run was not found.",
            ));
        }

        match decision {
            CloseDecision::Stay => {
                run.close_requested = false;
                self.revision += 1;
            }
            CloseDecision::CancelAndClose => {
                run.close_requested = false;
                run.close_after_cleanup = true;
                if matches!(run.state, BatchRunState::Starting | BatchRunState::Running) {
                    run.state = BatchRunState::Cancelling;
                    run.reason = Some("Application close requested by user.".to_string());
                    run.cancel_token.store(true, Ordering::SeqCst);
                }
                self.revision += 1;
            }
        }

        Ok(self.to_snapshot())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs::{self, File};
    use std::path::{Path, PathBuf};

    struct TestDir(PathBuf);
    impl TestDir {
        fn new(prefix: &str) -> Self {
            let nanos = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let path = std::env::temp_dir().join(format!("cad_batch_state_{}_{}", prefix, nanos));
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

    fn create_test_source_and_output() -> (SelectedBatchSource, SelectedBatchOutput, TestDir) {
        let temp_dir = TestDir::new("state_test");
        let src_dir = temp_dir.path().join("source");
        let out_dir = temp_dir.path().join("output");
        fs::create_dir(&src_dir).unwrap();
        fs::create_dir(&out_dir).unwrap();

        let f1 = src_dir.join("p1.par");
        let f2 = src_dir.join("d1.dft");
        File::create(&f1).unwrap();
        File::create(&f2).unwrap();

        let source = super::super::selection::process_picked_files(vec![f1, f2]).unwrap();
        let output = super::super::selection::process_picked_output(out_dir).unwrap();
        (source, output, temp_dir)
    }

    #[test]
    fn test_batch_state_initial_snapshot() {
        let state = BatchState::new();
        let snap = state.to_snapshot();
        assert_eq!(snap.revision, 1);
        assert!(snap.native_available);
        assert_eq!(snap.source, None);
        assert_eq!(snap.output, None);
        assert_eq!(snap.run, None);
    }

    #[test]
    fn test_set_source_and_output() {
        let mut state = BatchState::new();
        let (source, output, _temp) = create_test_source_and_output();

        let src_resp = state.set_source(source).unwrap();
        assert_eq!(src_resp.par_count, 1);
        assert_eq!(src_resp.dft_count, 1);

        let out_resp = state.set_output(output).unwrap();
        assert!(!out_resp.selection_id.is_empty());

        let snap = state.to_snapshot();
        assert!(snap.source.is_some());
        assert!(snap.output.is_some());
        assert_eq!(snap.revision, 3);
    }

    #[test]
    fn test_start_validation_and_reservation() {
        let mut state = BatchState::new();
        let (source, output, _temp) = create_test_source_and_output();
        let src_id = source.selection_id.clone();
        let out_id = output.selection_id.clone();

        state.set_source(source).unwrap();
        state.set_output(output).unwrap();

        let req_3d = BatchStartRequest {
            source_selection_id: src_id.clone(),
            output_selection_id: out_id.clone(),
            operation: "export_3d".to_string(),
            formats: vec!["step".to_string()],
            continue_on_error: true,
            max_files: 100,
        };

        let snap = state
            .reserve_run(&req_3d, "batch-req-001".to_string())
            .unwrap();
        assert!(snap.run.is_some());
        let run = snap.run.unwrap();
        assert_eq!(run.request_id, "batch-req-001");
        assert_eq!(run.state, BatchRunState::Starting);
        assert_eq!(run.total_files, 1); // Only p1.par is eligible

        // Cannot modify selection while active
        let (s2, _, _) = create_test_source_and_output();
        assert!(state.set_source(s2).is_err());
    }

    #[test]
    fn test_cancel_and_close_resolution() {
        let mut state = BatchState::new();
        let (source, output, _temp) = create_test_source_and_output();
        let src_id = source.selection_id.clone();
        let out_id = output.selection_id.clone();

        state.set_source(source).unwrap();
        state.set_output(output).unwrap();

        let req_3d = BatchStartRequest {
            source_selection_id: src_id,
            output_selection_id: out_id,
            operation: "export_3d".to_string(),
            formats: vec!["step".to_string()],
            continue_on_error: true,
            max_files: 100,
        };

        state
            .reserve_run(&req_3d, "batch-req-002".to_string())
            .unwrap();

        // Cancel
        let snap = state.request_cancel("batch-req-002").unwrap();
        assert_eq!(snap.run.as_ref().unwrap().state, BatchRunState::Cancelling);

        // Resolve close - Stay
        let snap2 = state
            .resolve_close("batch-req-002", CloseDecision::Stay)
            .unwrap();
        assert!(!snap2.run.as_ref().unwrap().close_requested);
    }
}
