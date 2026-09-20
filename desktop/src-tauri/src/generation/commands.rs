use tauri::ipc::Response;
use tauri::{command, Emitter, State, Window};

use super::state::AppState;
use super::types::{
    CancelGenerationRequest, CommandError, GenerationOutputSelection, GenerationResultResponse,
    GenerationSnapshot, ResolveCloseRequest, RevealResponse, RunTargetRequest, SetKeyRequest,
    SetKeyResponse, StartGenerationRequest,
};

fn verify_main_window(window: &Window) -> Result<(), CommandError> {
    if window.label() != "main" {
        return Err(CommandError::new(
            "UNAUTHORIZED_WINDOW",
            "Only the main application window is authorized to invoke generation commands.",
        ));
    }
    Ok(())
}

#[command]
pub fn generation_snapshot(
    window: Window,
    state: State<'_, AppState>,
) -> Result<GenerationSnapshot, CommandError> {
    verify_main_window(&window)?;
    let guard = state.lock().map_err(|_| {
        CommandError::new(
            "INTERNAL_ERROR",
            "Failed to acquire internal application lock.",
        )
    })?;
    Ok(guard.to_snapshot())
}

#[command]
pub fn generation_set_key(
    window: Window,
    state: State<'_, AppState>,
    request: SetKeyRequest,
) -> Result<SetKeyResponse, CommandError> {
    verify_main_window(&window)?;
    let mut guard = state.lock().map_err(|_| {
        CommandError::new(
            "INTERNAL_ERROR",
            "Failed to acquire internal application lock.",
        )
    })?;
    let configured = guard.set_key(request.key)?;
    Ok(SetKeyResponse { configured })
}

#[command]
pub fn generation_select_output(
    window: Window,
    state: State<'_, AppState>,
) -> Result<GenerationOutputSelection, CommandError> {
    verify_main_window(&window)?;
    {
        let guard = state.lock().map_err(|_| {
            CommandError::new(
                "INTERNAL_ERROR",
                "Failed to acquire internal application lock.",
            )
        })?;
        if guard.is_run_active() {
            return Err(CommandError::new(
                "RUN_ACTIVE",
                "Cannot change output directory while a generation run is in progress.",
            ));
        }
    }

    let picked = rfd::FileDialog::new().pick_folder();
    let folder_path = match picked {
        Some(path) => path,
        None => {
            // User cancelled; return current selection if exists or cancelled error
            let guard = state.lock().map_err(|_| {
                CommandError::new(
                    "INTERNAL_ERROR",
                    "Failed to acquire internal application lock.",
                )
            })?;
            return guard.selected_output.clone().ok_or_else(|| {
                CommandError::new(
                    "SELECTION_CANCELLED",
                    "Output directory selection was cancelled.",
                )
            });
        }
    };

    if !folder_path.is_dir() {
        return Err(CommandError::new(
            "OUTPUT_UNAVAILABLE",
            "Selected path is not a valid directory.",
        ));
    }

    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let selection_id = format!("sel_{:016x}", nanos);
    let display_path = folder_path.to_string_lossy().to_string();
    let selection = GenerationOutputSelection {
        selection_id,
        display_path,
    };

    let mut guard = state.lock().map_err(|_| {
        CommandError::new(
            "INTERNAL_ERROR",
            "Failed to acquire internal application lock.",
        )
    })?;
    guard.set_output(selection.clone(), folder_path)?;

    Ok(selection)
}

#[command]
pub fn generation_start(
    window: Window,
    state: State<'_, AppState>,
    claim_state: State<'_, std::sync::Mutex<crate::run_claim::RunClaimCoordinator>>,
    request: StartGenerationRequest,
) -> Result<GenerationSnapshot, CommandError> {
    verify_main_window(&window)?;
    let snapshot = {
        let mut guard = state.lock().map_err(|_| {
            CommandError::new(
                "INTERNAL_ERROR",
                "Failed to acquire internal application lock.",
            )
        })?;

        let mut claim_guard = claim_state.lock().map_err(|_| {
            CommandError::new("INTERNAL_ERROR", "Failed to acquire run claim lock.")
        })?;
        if claim_guard.is_active() {
            return Err(CommandError::new(
                "RUN_ACTIVE",
                "Another operation is currently in progress.",
            ));
        }

        let snap = guard.reserve_run(&request.selection_id, request.input)?;
        if let Some(run) = &snap.run {
            if let Err(msg) =
                claim_guard.claim(crate::run_claim::RunKind::Generation, &run.request_id)
            {
                guard.active_run = None;
                guard.revision += 1;
                return Err(CommandError::new("RUN_ACTIVE", msg));
            }
        }
        snap
    };

    if let Some(run) = &snapshot.run {
        super::process::spawn_generation_supervisor(window, run.request_id.clone());
    }

    Ok(snapshot)
}

#[command]
pub fn generation_cancel(
    window: Window,
    state: State<'_, AppState>,
    request: CancelGenerationRequest,
) -> Result<GenerationSnapshot, CommandError> {
    verify_main_window(&window)?;
    let mut guard = state.lock().map_err(|_| {
        CommandError::new(
            "INTERNAL_ERROR",
            "Failed to acquire internal application lock.",
        )
    })?;
    guard.request_cancel(&request.request_id)
}

#[command]
pub async fn generation_result(
    window: Window,
    state: State<'_, AppState>,
    request: RunTargetRequest,
) -> Result<GenerationResultResponse, CommandError> {
    verify_main_window(&window)?;
    let (output_path, output_identity, expected_kind, wire_artifacts, cancel_token) = {
        let mut guard = state.lock().map_err(|_| {
            CommandError::new(
                "INTERNAL_ERROR",
                "Failed to acquire internal application lock.",
            )
        })?;

        let run = guard.active_run.as_mut().ok_or_else(|| {
            CommandError::new(
                "RUN_NOT_FOUND",
                "The requested generation run was not found.",
            )
        })?;

        if run.request_id != request.request_id {
            return Err(CommandError::new(
                "RUN_NOT_FOUND",
                "The requested generation run was not found.",
            ));
        }

        if run.state != super::types::RunState::Succeeded {
            return Err(CommandError::new(
                "RESULT_ACCESS_UNAVAILABLE",
                "Results are only available for successfully completed generation runs.",
            ));
        }

        if run.result_access == super::types::ResultAccess::Checking {
            return Err(CommandError::new(
                "RUN_ACTIVE",
                "Result verification is already in progress for this generation run.",
            ));
        }

        if let Some(cached) = &run.cached_result {
            return Ok(cached.clone());
        }

        let output_path = run.output_root.clone();
        let output_identity = run.output_root_identity;
        let expected_kind = match &run.input {
            super::types::GenerationInput::ExamplePlan { .. } => "example_plan",
            super::types::GenerationInput::PromptToCad { .. } => "prompt_to_cad",
        };
        let wire_artifacts = run.response_data.as_ref().map(|d| d.artifacts.clone());

        let cancel_token = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        run.verification_cancel_token = Some(cancel_token.clone());

        // Transition to Checking and notify UI
        run.result_access = super::types::ResultAccess::Checking;
        guard.revision += 1;
        let snap = guard.to_snapshot();
        let _ = window.emit("generation-state", snap);

        (
            output_path,
            output_identity,
            expected_kind,
            wire_artifacts,
            cancel_token,
        )
    };

    let req_id = request.request_id.clone();
    let res = tauri::async_runtime::spawn_blocking(move || {
        super::output::validate_and_load_result(
            &output_path,
            &req_id,
            Some(&cancel_token),
            Some(expected_kind),
            wire_artifacts.as_deref(),
            Some(output_identity),
        )
    })
    .await
    .map_err(|e| CommandError::new("INTERNAL_ERROR", format!("Verification task failed: {}", e)))?;

    match res {
        Ok((result, preview_sha)) => {
            if let Ok(mut guard) = state.lock() {
                if let Some(run) = guard.active_run.as_mut() {
                    if run.request_id == request.request_id {
                        run.result_access = super::types::ResultAccess::Ready;
                        run.cached_result = Some(result.clone());
                        run.verified_preview_sha256 = preview_sha;
                        run.verification_cancel_token = None;
                        guard.revision += 1;
                        let snap = guard.to_snapshot();
                        let _ = window.emit("generation-state", snap);
                    }
                }
            }
            Ok(result)
        }
        Err(err) => {
            if let Ok(mut guard) = state.lock() {
                if let Some(run) = guard.active_run.as_mut() {
                    if run.request_id == request.request_id {
                        run.result_access = super::types::ResultAccess::Unavailable;
                        run.verification_cancel_token = None;
                        guard.revision += 1;
                        let snap = guard.to_snapshot();
                        let _ = window.emit("generation-state", snap);
                    }
                }
            }
            Err(err)
        }
    }
}

#[command]
pub async fn generation_preview(
    window: Window,
    state: State<'_, AppState>,
    request: RunTargetRequest,
) -> Result<Response, CommandError> {
    verify_main_window(&window)?;
    let (output_path, output_identity, preview_sha) = {
        let guard = state.lock().map_err(|_| {
            CommandError::new(
                "INTERNAL_ERROR",
                "Failed to acquire internal application lock.",
            )
        })?;

        let run = guard.active_run.as_ref().ok_or_else(|| {
            CommandError::new(
                "RUN_NOT_FOUND",
                "The requested generation run was not found.",
            )
        })?;

        if run.request_id != request.request_id {
            return Err(CommandError::new(
                "RUN_NOT_FOUND",
                "The requested generation run was not found.",
            ));
        }

        if run.state != super::types::RunState::Succeeded {
            return Err(CommandError::new(
                "RESULT_ACCESS_UNAVAILABLE",
                "Preview image is only available for successfully completed generation runs.",
            ));
        }

        if run.result_access != super::types::ResultAccess::Ready {
            return Err(CommandError::new(
                "RESULT_ACCESS_UNAVAILABLE",
                "Preview image is only available after result verification has completed successfully.",
            ));
        }

        let preview_sha = run
            .verified_preview_sha256
            .as_ref()
            .ok_or_else(|| {
                CommandError::new(
                    "RESULT_ACCESS_UNAVAILABLE",
                    "No preview image was verified for this run.",
                )
            })?
            .clone();

        (
            run.output_root.clone(),
            run.output_root_identity,
            preview_sha,
        )
    };

    let req_id = request.request_id.clone();
    let bytes = tauri::async_runtime::spawn_blocking(move || {
        super::preview::load_preview_jpeg(
            &output_path,
            &req_id,
            Some(&preview_sha),
            Some(output_identity),
        )
    })
    .await
    .map_err(|e| CommandError::new("INTERNAL_ERROR", format!("Preview task failed: {}", e)))??;

    Ok(Response::new(bytes))
}

#[command]
pub fn generation_reveal(
    window: Window,
    state: State<'_, AppState>,
    request: RunTargetRequest,
) -> Result<RevealResponse, CommandError> {
    verify_main_window(&window)?;
    let (output_path, output_identity) = {
        let guard = state.lock().map_err(|_| {
            CommandError::new(
                "INTERNAL_ERROR",
                "Failed to acquire internal application lock.",
            )
        })?;

        let run = guard.active_run.as_ref().ok_or_else(|| {
            CommandError::new(
                "RUN_NOT_FOUND",
                "The requested generation run was not found.",
            )
        })?;

        if run.request_id != request.request_id {
            return Err(CommandError::new(
                "RUN_NOT_FOUND",
                "The requested generation run was not found.",
            ));
        }

        if run.state != super::types::RunState::Succeeded {
            return Err(CommandError::new(
                "OUTPUT_UNAVAILABLE",
                "Output directory cannot be revealed until generation has successfully completed.",
            ));
        }

        (run.output_root.clone(), run.output_root_identity)
    };

    super::output::reveal_in_explorer(&output_path, &request.request_id, Some(output_identity))
}

#[command]
pub fn generation_resolve_close(
    window: Window,
    state: State<'_, AppState>,
    request: ResolveCloseRequest,
) -> Result<GenerationSnapshot, CommandError> {
    verify_main_window(&window)?;
    let (snap, should_destroy) = {
        let mut guard = state.lock().map_err(|_| {
            CommandError::new(
                "INTERNAL_ERROR",
                "Failed to acquire internal application lock.",
            )
        })?;
        let snap = guard.resolve_close(&request.request_id, request.decision)?;
        let should_destroy = guard.terminating;
        (snap, should_destroy)
    };

    if should_destroy {
        let _ = window.destroy();
    }

    Ok(snap)
}
