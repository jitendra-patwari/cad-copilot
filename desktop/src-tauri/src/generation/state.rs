use std::path::PathBuf;
use std::sync::Mutex;

use super::types::{
    CleanupState, CloseDecision, CommandError, EngineStatus, GenerationInput,
    GenerationOutputSelection, GenerationRunSnapshot, GenerationSnapshot, ResultAccess, RunPhase,
    RunState,
};

#[derive(Debug, Clone)]
pub struct ActiveRunState {
    pub request_id: String,
    pub input: GenerationInput,
    pub output_root: PathBuf,
    pub output_root_identity: super::output::FileSystemIdentity,
    pub state: RunState,
    pub phase: Option<RunPhase>,
    pub engine_status: Option<EngineStatus>,
    pub reason: Option<String>,
    pub result_access: ResultAccess,
    pub cleanup: CleanupState,
    pub close_requested: bool,
    pub close_after_cleanup: bool,
    pub warnings: Vec<String>,
    pub cancel_token: std::sync::Arc<std::sync::atomic::AtomicBool>,
    pub response_data: Option<super::protocol::WireResponseData>,
    pub cached_result: Option<super::types::GenerationResultResponse>,
    pub verified_preview_sha256: Option<String>,
    pub verification_cancel_token: Option<std::sync::Arc<std::sync::atomic::AtomicBool>>,
}

impl ActiveRunState {
    pub fn to_snapshot(&self) -> GenerationRunSnapshot {
        GenerationRunSnapshot {
            request_id: self.request_id.clone(),
            state: self.state,
            phase: self.phase,
            engine_status: self.engine_status,
            reason: self.reason.clone(),
            result_access: self.result_access,
            cleanup: self.cleanup,
            close_requested: self.close_requested,
            warnings: self.warnings.clone(),
        }
    }
}

pub struct GenerationState {
    pub revision: u64,
    pub session_key: Option<String>,
    pub selected_output: Option<GenerationOutputSelection>,
    pub selected_output_path: Option<PathBuf>,
    pub active_run: Option<ActiveRunState>,
    pub terminating: bool,
}

impl Default for GenerationState {
    fn default() -> Self {
        Self::new()
    }
}

impl GenerationState {
    pub fn new() -> Self {
        Self {
            revision: 1,
            session_key: None,
            selected_output: None,
            selected_output_path: None,
            active_run: None,
            terminating: false,
        }
    }

    pub fn to_snapshot(&self) -> GenerationSnapshot {
        GenerationSnapshot {
            revision: self.revision,
            native_available: true,
            key_configured: self.session_key.is_some(),
            output: self.selected_output.clone(),
            run: self.active_run.as_ref().map(|r| r.to_snapshot()),
            engine_build: crate::shared::engine::current_engine_build_info(),
        }
    }

    pub fn set_key(&mut self, key: Option<String>) -> Result<bool, CommandError> {
        if self.is_run_active() {
            return Err(CommandError::new(
                "RUN_ACTIVE",
                "Cannot modify session key while a generation run is in progress.",
            ));
        }

        match key {
            Some(raw) => {
                let trimmed = raw.trim();
                if trimmed.is_empty() {
                    self.session_key = None;
                } else {
                    if trimmed.len() > 4096 {
                        return Err(CommandError::with_field(
                            "INVALID_INPUT",
                            "Session API key exceeds maximum permitted length of 4096 bytes.",
                            "key",
                        ));
                    }
                    if trimmed.chars().any(|c| c == '\0' || c == '\n' || c == '\r') {
                        return Err(CommandError::with_field(
                            "INVALID_INPUT",
                            "Session API key must not contain null bytes or line breaks.",
                            "key",
                        ));
                    }
                    self.session_key = Some(trimmed.to_string());
                }
            }
            None => {
                self.session_key = None;
            }
        }

        self.revision += 1;
        Ok(self.session_key.is_some())
    }

    pub fn set_output(
        &mut self,
        selection: GenerationOutputSelection,
        path: PathBuf,
    ) -> Result<(), CommandError> {
        if self.is_run_active() {
            return Err(CommandError::new(
                "RUN_ACTIVE",
                "Cannot change output directory while a generation run is in progress.",
            ));
        }

        self.selected_output = Some(selection);
        self.selected_output_path = Some(path);
        // Clear inactive run so selecting a new directory allows a clean start
        if !self.is_run_active() {
            self.active_run = None;
        }
        self.revision += 1;
        Ok(())
    }

    pub fn is_run_active(&self) -> bool {
        if let Some(run) = &self.active_run {
            matches!(
                run.state,
                RunState::Starting | RunState::Running | RunState::Cancelling
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

    pub fn reserve_run(
        &mut self,
        selection_id: &str,
        input: GenerationInput,
    ) -> Result<GenerationSnapshot, CommandError> {
        if self.terminating {
            return Err(CommandError::new(
                "TERMINATING",
                "Application is terminating; cannot start a new generation run.",
            ));
        }

        if self.is_run_active() {
            return Err(CommandError::new(
                "RUN_ACTIVE",
                "A generation run is already in progress.",
            ));
        }

        if let Some(run) = &self.active_run {
            if run.cleanup == CleanupState::Incomplete {
                return Err(CommandError::new(
                    "INCOMPLETE_CLEANUP",
                    "Previous generation run left incomplete cleanup. Please re-select the output folder or restart the application.",
                ));
            }
            if run.result_access == ResultAccess::Checking {
                return Err(CommandError::new(
                    "RUN_ACTIVE",
                    "Previous generation run results are still being verified.",
                ));
            }
        }

        let current_selection = self.selected_output.as_ref().ok_or_else(|| {
            CommandError::new(
                "OUTPUT_SELECTION_REQUIRED",
                "An output directory must be selected before starting generation.",
            )
        })?;

        if current_selection.selection_id != selection_id {
            return Err(CommandError::new(
                "OUTPUT_UNAVAILABLE",
                "The selected output directory is no longer current.",
            ));
        }

        match &input {
            GenerationInput::PromptToCad { prompt } => {
                let trimmed = prompt.trim();
                if trimmed.is_empty() {
                    return Err(CommandError::with_field(
                        "INVALID_INPUT",
                        "Prompt must not be empty.",
                        "prompt",
                    ));
                }
                if trimmed.chars().count() > 8000 {
                    return Err(CommandError::with_field(
                        "INVALID_INPUT",
                        "Prompt exceeds maximum permitted length of 8000 characters.",
                        "prompt",
                    ));
                }
                if self.session_key.is_none() {
                    return Err(CommandError::new(
                        "KEY_REQUIRED",
                        "Gemini API key must be configured for prompt-to-CAD generation.",
                    ));
                }
            }
            GenerationInput::ExamplePlan { example_id } => {
                if example_id != "spur_gear" {
                    return Err(CommandError::with_field(
                        "INVALID_INPUT",
                        "Only the canonical 'spur_gear' example is supported.",
                        "exampleId",
                    ));
                }
            }
        }

        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        let request_id = format!("gen_{:016x}", nanos);
        let output_root = self.selected_output_path.clone().ok_or_else(|| {
            CommandError::new(
                "OUTPUT_UNAVAILABLE",
                "The selected output directory path is unavailable.",
            )
        })?;
        let output_root_identity = super::output::get_path_filesystem_identity(&output_root)?;
        let cancel_token = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        self.active_run = Some(ActiveRunState {
            request_id,
            input,
            output_root,
            output_root_identity,
            state: RunState::Starting,
            phase: None,
            engine_status: None,
            reason: None,
            result_access: ResultAccess::None,
            cleanup: CleanupState::NotStarted,
            close_requested: false,
            close_after_cleanup: false,
            warnings: Vec::new(),
            cancel_token,
            response_data: None,
            cached_result: None,
            verified_preview_sha256: None,
            verification_cancel_token: None,
        });

        self.revision += 1;
        Ok(self.to_snapshot())
    }

    pub fn request_cancel(&mut self, request_id: &str) -> Result<GenerationSnapshot, CommandError> {
        let run = self.active_run.as_mut().ok_or_else(|| {
            CommandError::new(
                "RUN_NOT_FOUND",
                "The requested generation run was not found or is no longer active.",
            )
        })?;

        if run.request_id != request_id {
            return Err(CommandError::new(
                "RUN_NOT_FOUND",
                "The requested generation run was not found or is no longer active.",
            ));
        }

        if let Some(token) = &run.verification_cancel_token {
            token.store(true, std::sync::atomic::Ordering::SeqCst);
        }

        let is_active = matches!(
            run.state,
            RunState::Starting | RunState::Running | RunState::Cancelling
        );
        if !is_active {
            return Ok(self.to_snapshot());
        }

        if run.state != RunState::Cancelling {
            run.state = RunState::Cancelling;
            run.cleanup = CleanupState::Pending;
            run.reason = Some("Cancellation requested by user.".to_string());
            run.cancel_token
                .store(true, std::sync::atomic::Ordering::SeqCst);
            self.revision += 1;
        }

        Ok(self.to_snapshot())
    }

    pub fn resolve_close(
        &mut self,
        request_id: &str,
        decision: CloseDecision,
    ) -> Result<GenerationSnapshot, CommandError> {
        let run = self.active_run.as_mut().ok_or_else(|| {
            CommandError::new(
                "RUN_NOT_FOUND",
                "The requested generation run was not found.",
            )
        })?;

        if run.request_id != request_id {
            return Err(CommandError::new(
                "RUN_NOT_FOUND",
                "The requested generation run was not found.",
            ));
        }

        match decision {
            CloseDecision::Stay => {
                run.close_requested = false;
                self.revision += 1;
                Ok(self.to_snapshot())
            }
            CloseDecision::CancelAndClose => {
                run.close_after_cleanup = true;
                if let Some(token) = &run.verification_cancel_token {
                    token.store(true, std::sync::atomic::Ordering::SeqCst);
                }
                let is_active = matches!(
                    run.state,
                    RunState::Starting | RunState::Running | RunState::Cancelling
                );
                if is_active && run.state != RunState::Cancelling {
                    run.state = RunState::Cancelling;
                    run.cleanup = CleanupState::Pending;
                    run.reason = Some("Window close requested; cancelling active run.".to_string());
                    run.cancel_token
                        .store(true, std::sync::atomic::Ordering::SeqCst);
                    self.revision += 1;
                } else if !is_active {
                    self.terminating = true;
                    self.revision += 1;
                }
                Ok(self.to_snapshot())
            }
        }
    }
}

pub fn is_valid_phase_transition(current: Option<RunPhase>, next: RunPhase) -> bool {
    matches!(
        (current, next),
        (None, RunPhase::RequestReceived)
            | (Some(RunPhase::RequestReceived), RunPhase::RequestValidated)
            | (Some(RunPhase::RequestReceived), RunPhase::ResponseReady)
            | (
                Some(RunPhase::RequestValidated),
                RunPhase::GenerationStarted
            )
            | (Some(RunPhase::GenerationStarted), RunPhase::ResponseReady)
    )
}

pub type AppState = Mutex<GenerationState>;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_initial_state_snapshot() {
        let state = GenerationState::new();
        let snapshot = state.to_snapshot();

        assert_eq!(snapshot.revision, 1);
        assert!(snapshot.native_available);
        assert!(!snapshot.key_configured);
        assert_eq!(snapshot.output, None);
        assert_eq!(snapshot.run, None);
    }

    #[test]
    fn test_set_and_clear_key() {
        let mut state = GenerationState::new();

        let res = state.set_key(Some("valid_api_key_123".to_string()));
        assert!(res.unwrap());
        assert_eq!(state.revision, 2);
        assert!(state.to_snapshot().key_configured);

        // Key clear
        let res_clear = state.set_key(None);
        assert!(!res_clear.unwrap());
        assert_eq!(state.revision, 3);
        assert!(!state.to_snapshot().key_configured);
    }

    #[test]
    fn test_set_key_invalid_chars() {
        let mut state = GenerationState::new();

        let res = state.set_key(Some("invalid\nkey".to_string()));
        assert!(res.is_err());
        assert_eq!(res.unwrap_err().code, "INVALID_INPUT");

        let res_nul = state.set_key(Some("invalid\0key".to_string()));
        assert!(res_nul.is_err());
        assert_eq!(res_nul.unwrap_err().code, "INVALID_INPUT");
    }

    #[test]
    fn test_set_output() {
        let mut state = GenerationState::new();
        let selection = GenerationOutputSelection {
            selection_id: "sel_1".to_string(),
            display_path: "C:\\outputs".to_string(),
        };

        state
            .set_output(selection.clone(), PathBuf::from("C:\\outputs"))
            .unwrap();
        assert_eq!(state.revision, 2);
        assert_eq!(state.to_snapshot().output, Some(selection));
    }

    #[test]
    fn test_reserve_run_requires_output() {
        let mut state = GenerationState::new();
        let err = state
            .reserve_run(
                "sel_1",
                GenerationInput::ExamplePlan {
                    example_id: "spur_gear".to_string(),
                },
            )
            .unwrap_err();
        assert_eq!(err.code, "OUTPUT_SELECTION_REQUIRED");
    }

    fn create_test_output_dir(prefix: &str) -> PathBuf {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("cad_test_state_{}_{}", prefix, nanos));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn test_reserve_run_prompt_requires_key() {
        let mut state = GenerationState::new();
        let test_dir = create_test_output_dir("prompt_req_key");
        state
            .set_output(
                GenerationOutputSelection {
                    selection_id: "sel_1".to_string(),
                    display_path: test_dir.to_string_lossy().to_string(),
                },
                test_dir,
            )
            .unwrap();

        let err = state
            .reserve_run(
                "sel_1",
                GenerationInput::PromptToCad {
                    prompt: "create a gear".to_string(),
                },
            )
            .unwrap_err();
        assert_eq!(err.code, "KEY_REQUIRED");
    }

    #[test]
    fn test_reserve_run_example_spur_gear_success_and_active_guard() {
        let mut state = GenerationState::new();
        let test_dir = create_test_output_dir("spur_gear_success");
        state
            .set_output(
                GenerationOutputSelection {
                    selection_id: "sel_1".to_string(),
                    display_path: test_dir.to_string_lossy().to_string(),
                },
                test_dir,
            )
            .unwrap();

        let snapshot = state
            .reserve_run(
                "sel_1",
                GenerationInput::ExamplePlan {
                    example_id: "spur_gear".to_string(),
                },
            )
            .unwrap();

        assert!(snapshot.run.is_some());
        let run = snapshot.run.unwrap();
        assert_eq!(run.state, RunState::Starting);
        assert!(run.request_id.starts_with("gen_"));

        // Second reserve while active is rejected with RUN_ACTIVE
        let err2 = state
            .reserve_run(
                "sel_1",
                GenerationInput::ExamplePlan {
                    example_id: "spur_gear".to_string(),
                },
            )
            .unwrap_err();
        assert_eq!(err2.code, "RUN_ACTIVE");

        // Changing key or output while active is rejected with RUN_ACTIVE
        assert_eq!(
            state.set_key(Some("test".to_string())).unwrap_err().code,
            "RUN_ACTIVE"
        );
        assert_eq!(
            state
                .set_output(
                    GenerationOutputSelection {
                        selection_id: "sel_2".to_string(),
                        display_path: "C:\\other".to_string(),
                    },
                    PathBuf::from("C:\\other"),
                )
                .unwrap_err()
                .code,
            "RUN_ACTIVE"
        );
    }

    #[test]
    fn test_request_cancel_transitions_and_signals() {
        let mut state = GenerationState::new();
        let test_dir = create_test_output_dir("cancel_transitions");
        state
            .set_output(
                GenerationOutputSelection {
                    selection_id: "sel_1".to_string(),
                    display_path: test_dir.to_string_lossy().to_string(),
                },
                test_dir,
            )
            .unwrap();

        let snap = state
            .reserve_run(
                "sel_1",
                GenerationInput::ExamplePlan {
                    example_id: "spur_gear".to_string(),
                },
            )
            .unwrap();
        let req_id = snap.run.unwrap().request_id;

        // Cancel with wrong ID returns RUN_NOT_FOUND
        assert_eq!(
            state.request_cancel("wrong_id").unwrap_err().code,
            "RUN_NOT_FOUND"
        );

        // Cancel with valid ID transitions to Cancelling
        let cancelled_snap = state.request_cancel(&req_id).unwrap();
        let run = cancelled_snap.run.unwrap();
        assert_eq!(run.state, RunState::Cancelling);
        assert_eq!(run.cleanup, CleanupState::Pending);
        assert!(state
            .active_run
            .as_ref()
            .unwrap()
            .cancel_token
            .load(std::sync::atomic::Ordering::SeqCst));

        // Repeat cancel is idempotent
        let repeat_snap = state.request_cancel(&req_id).unwrap();
        assert_eq!(repeat_snap.run.unwrap().state, RunState::Cancelling);
    }

    #[test]
    fn test_resolve_close_decisions() {
        let mut state = GenerationState::new();
        let test_dir = create_test_output_dir("close_decisions");
        state
            .set_output(
                GenerationOutputSelection {
                    selection_id: "sel_1".to_string(),
                    display_path: test_dir.to_string_lossy().to_string(),
                },
                test_dir,
            )
            .unwrap();

        let snap = state
            .reserve_run(
                "sel_1",
                GenerationInput::ExamplePlan {
                    example_id: "spur_gear".to_string(),
                },
            )
            .unwrap();
        let req_id = snap.run.unwrap().request_id;

        // Resolve stay
        state.active_run.as_mut().unwrap().close_requested = true;
        let snap_stay = state.resolve_close(&req_id, CloseDecision::Stay).unwrap();
        assert!(!snap_stay.run.unwrap().close_requested);

        // Resolve cancel_and_close
        let snap_close = state
            .resolve_close(&req_id, CloseDecision::CancelAndClose)
            .unwrap();
        assert_eq!(snap_close.run.as_ref().unwrap().state, RunState::Cancelling);
        assert!(state.active_run.as_ref().unwrap().close_after_cleanup);
    }

    #[test]
    fn test_phase_transition_ordering() {
        // Initial phase MUST be RequestReceived
        assert!(is_valid_phase_transition(None, RunPhase::RequestReceived));
        assert!(!is_valid_phase_transition(None, RunPhase::RequestValidated));
        assert!(!is_valid_phase_transition(
            None,
            RunPhase::GenerationStarted
        ));
        assert!(!is_valid_phase_transition(None, RunPhase::ResponseReady));

        // Strict sequential progression
        assert!(is_valid_phase_transition(
            Some(RunPhase::RequestReceived),
            RunPhase::RequestValidated
        ));
        assert!(is_valid_phase_transition(
            Some(RunPhase::RequestValidated),
            RunPhase::GenerationStarted
        ));
        assert!(is_valid_phase_transition(
            Some(RunPhase::GenerationStarted),
            RunPhase::ResponseReady
        ));

        // Canonical early rejection path is valid
        assert!(is_valid_phase_transition(
            Some(RunPhase::RequestReceived),
            RunPhase::ResponseReady
        ));

        // Unsupported skips remain invalid
        assert!(!is_valid_phase_transition(
            Some(RunPhase::RequestReceived),
            RunPhase::GenerationStarted
        ));
        assert!(!is_valid_phase_transition(
            Some(RunPhase::RequestValidated),
            RunPhase::ResponseReady
        ));

        // Backwards transition is invalid
        assert!(!is_valid_phase_transition(
            Some(RunPhase::GenerationStarted),
            RunPhase::RequestReceived
        ));
        assert!(!is_valid_phase_transition(
            Some(RunPhase::ResponseReady),
            RunPhase::GenerationStarted
        ));

        // Repetition is invalid
        assert!(!is_valid_phase_transition(
            Some(RunPhase::RequestReceived),
            RunPhase::RequestReceived
        ));
    }
}
