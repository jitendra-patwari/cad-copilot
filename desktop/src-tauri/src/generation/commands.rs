use tauri::{command, State, Window};

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
        guard.reserve_run(&request.selection_id, request.input)?
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
pub fn generation_result(
    window: Window,
    state: State<'_, AppState>,
    request: RunTargetRequest,
) -> Result<GenerationResultResponse, CommandError> {
    verify_main_window(&window)?;
    let guard = state.lock().map_err(|_| {
        CommandError::new(
            "INTERNAL_ERROR",
            "Failed to acquire internal application lock.",
        )
    })?;

    if let Some(run) = &guard.active_run {
        if run.request_id == request.request_id {
            return Err(CommandError::new(
                "RESULT_ACCESS_UNAVAILABLE",
                "Results are not yet available for this generation run.",
            ));
        }
    }

    Err(CommandError::new(
        "RUN_NOT_FOUND",
        "The requested generation run was not found.",
    ))
}

#[command]
pub fn generation_preview(
    window: Window,
    state: State<'_, AppState>,
    request: RunTargetRequest,
) -> Result<Vec<u8>, CommandError> {
    verify_main_window(&window)?;
    let guard = state.lock().map_err(|_| {
        CommandError::new(
            "INTERNAL_ERROR",
            "Failed to acquire internal application lock.",
        )
    })?;

    if let Some(run) = &guard.active_run {
        if run.request_id == request.request_id {
            return Err(CommandError::new(
                "RESULT_ACCESS_UNAVAILABLE",
                "Preview image is not available for this run.",
            ));
        }
    }

    Err(CommandError::new(
        "RUN_NOT_FOUND",
        "The requested generation run was not found.",
    ))
}

#[command]
pub fn generation_reveal(
    window: Window,
    state: State<'_, AppState>,
    request: RunTargetRequest,
) -> Result<RevealResponse, CommandError> {
    verify_main_window(&window)?;
    let guard = state.lock().map_err(|_| {
        CommandError::new(
            "INTERNAL_ERROR",
            "Failed to acquire internal application lock.",
        )
    })?;

    if let Some(run) = &guard.active_run {
        if run.request_id == request.request_id {
            return Err(CommandError::new(
                "OUTPUT_UNAVAILABLE",
                "Output directory cannot be revealed until generation has completed.",
            ));
        }
    }

    Err(CommandError::new(
        "RUN_NOT_FOUND",
        "The requested generation run was not found.",
    ))
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
