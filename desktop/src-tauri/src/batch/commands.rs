use std::sync::Mutex;
use tauri::{command, State, Window};

use crate::run_claim::{RunClaimCoordinator, RunKind};

use super::selection::{process_picked_files, process_picked_output, scan_folder_bounded};
use super::state::AppState;
use super::types::{
    BatchCancelRequest, BatchOutputSelection, BatchResultResponse, BatchRevealResponse,
    BatchSelectSourceResponse, BatchSnapshot, BatchStartRequest, CommandError,
    ResolveBatchCloseRequest, RunTargetRequest, SelectSourceRequest, SourceSelectMode,
};

fn verify_main_window(window: &Window) -> Result<(), CommandError> {
    if window.label() != "main" {
        return Err(CommandError::new(
            "UNAUTHORIZED_WINDOW",
            "Only the main application window is authorized to invoke batch commands.",
        ));
    }
    Ok(())
}

pub fn generate_batch_request_id() -> String {
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};
    static COUNTER: AtomicU64 = AtomicU64::new(1);
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    let seq = COUNTER.fetch_add(1, Ordering::Relaxed);
    format!("batch-{}-{}", millis, seq)
}

#[command]
pub fn batch_snapshot(
    window: Window,
    state: State<'_, AppState>,
) -> Result<BatchSnapshot, CommandError> {
    verify_main_window(&window)?;
    let guard = state
        .lock()
        .map_err(|_| CommandError::new("INTERNAL_ERROR", "Failed to acquire batch state lock."))?;
    Ok(guard.to_snapshot())
}

#[command]
pub async fn batch_select_source(
    window: Window,
    state: State<'_, AppState>,
    request: SelectSourceRequest,
) -> Result<BatchSelectSourceResponse, CommandError> {
    verify_main_window(&window)?;
    {
        let guard = state.lock().map_err(|_| {
            CommandError::new("INTERNAL_ERROR", "Failed to acquire batch state lock.")
        })?;
        if guard.is_run_active() {
            return Err(CommandError::new(
                "RUN_ACTIVE",
                "Cannot change source selection while a batch run is in progress.",
            ));
        }
    }

    let selected_source = tauri::async_runtime::spawn_blocking(move || match request.mode {
        SourceSelectMode::Files => {
            let picked = rfd::FileDialog::new()
                .add_filter("CAD Files", &["par", "psm", "asm", "dft"])
                .pick_files();
            match picked {
                Some(files) => process_picked_files(files),
                None => Err(CommandError::new(
                    "SELECTION_CANCELLED",
                    "Source file selection was cancelled.",
                )),
            }
        }
        SourceSelectMode::Folder => {
            let picked = rfd::FileDialog::new().pick_folder();
            match picked {
                Some(folder) => scan_folder_bounded(folder),
                None => Err(CommandError::new(
                    "SELECTION_CANCELLED",
                    "Source folder selection was cancelled.",
                )),
            }
        }
    })
    .await
    .map_err(|e| CommandError::new("INTERNAL_ERROR", format!("Scan task join error: {}", e)))??;

    let mut guard = state
        .lock()
        .map_err(|_| CommandError::new("INTERNAL_ERROR", "Failed to acquire batch state lock."))?;
    guard.set_source(selected_source)
}

#[command]
pub async fn batch_select_output(
    window: Window,
    state: State<'_, AppState>,
) -> Result<BatchOutputSelection, CommandError> {
    verify_main_window(&window)?;
    {
        let guard = state.lock().map_err(|_| {
            CommandError::new("INTERNAL_ERROR", "Failed to acquire batch state lock.")
        })?;
        if guard.is_run_active() {
            return Err(CommandError::new(
                "RUN_ACTIVE",
                "Cannot change output directory while a batch run is in progress.",
            ));
        }
    }

    let selected_output = tauri::async_runtime::spawn_blocking(move || {
        let picked = rfd::FileDialog::new().pick_folder();
        match picked {
            Some(folder) => process_picked_output(folder),
            None => Err(CommandError::new(
                "SELECTION_CANCELLED",
                "Output directory selection was cancelled.",
            )),
        }
    })
    .await
    .map_err(|e| {
        CommandError::new("INTERNAL_ERROR", format!("Output picker join error: {}", e))
    })??;

    let mut guard = state
        .lock()
        .map_err(|_| CommandError::new("INTERNAL_ERROR", "Failed to acquire batch state lock."))?;
    guard.set_output(selected_output)
}

#[command]
pub fn batch_start(
    window: Window,
    batch_state: State<'_, AppState>,
    claim_state: State<'_, Mutex<RunClaimCoordinator>>,
    request: BatchStartRequest,
) -> Result<BatchSnapshot, CommandError> {
    verify_main_window(&window)?;

    let request_id = generate_batch_request_id();

    // 1. Claim slot under RunClaimCoordinator
    {
        let mut claim_guard = claim_state.lock().map_err(|_| {
            CommandError::new("INTERNAL_ERROR", "Failed to acquire run claim lock.")
        })?;
        claim_guard
            .claim(RunKind::Batch, &request_id)
            .map_err(|msg| CommandError::new("RUN_ACTIVE", msg))?;
    }

    // 2. Reserve run in BatchState
    let snapshot = {
        let mut guard = batch_state.lock().map_err(|_| {
            CommandError::new("INTERNAL_ERROR", "Failed to acquire batch state lock.")
        })?;
        match guard.reserve_run(&request, request_id.clone()) {
            Ok(snap) => snap,
            Err(e) => {
                if let Ok(mut claim_guard) = claim_state.lock() {
                    claim_guard.release(RunKind::Batch, &request_id, false);
                }
                return Err(e);
            }
        }
    };

    // 3. Spawn supervisor thread
    super::process::spawn_batch_supervisor(window, request_id);

    Ok(snapshot)
}

#[command]
pub fn batch_cancel(
    window: Window,
    state: State<'_, AppState>,
    request: BatchCancelRequest,
) -> Result<BatchSnapshot, CommandError> {
    verify_main_window(&window)?;
    let mut guard = state
        .lock()
        .map_err(|_| CommandError::new("INTERNAL_ERROR", "Failed to acquire batch state lock."))?;
    guard.request_cancel(&request.request_id)
}

#[command]
pub fn batch_result(
    window: Window,
    state: State<'_, AppState>,
    request: RunTargetRequest,
) -> Result<BatchResultResponse, CommandError> {
    verify_main_window(&window)?;
    let guard = state
        .lock()
        .map_err(|_| CommandError::new("INTERNAL_ERROR", "Failed to acquire batch state lock."))?;

    let run = guard.active_run.as_ref().ok_or_else(|| {
        CommandError::new("RUN_NOT_FOUND", "The requested batch run was not found.")
    })?;

    if run.request_id != request.request_id {
        return Err(CommandError::new(
            "RUN_NOT_FOUND",
            "The requested batch run was not found.",
        ));
    }

    if let Some(cached) = &run.cached_result {
        Ok(cached.clone())
    } else {
        Err(CommandError::new(
            "RESULT_ACCESS_UNAVAILABLE",
            "Results are not yet available for this batch run.",
        ))
    }
}

#[command]
pub fn batch_reveal(
    window: Window,
    state: State<'_, AppState>,
    request: RunTargetRequest,
) -> Result<BatchRevealResponse, CommandError> {
    verify_main_window(&window)?;
    let (output_root, output_identity) = {
        let guard = state.lock().map_err(|_| {
            CommandError::new("INTERNAL_ERROR", "Failed to acquire batch state lock.")
        })?;

        let run = guard.active_run.as_ref().ok_or_else(|| {
            CommandError::new("RUN_NOT_FOUND", "The requested batch run was not found.")
        })?;

        if run.request_id != request.request_id {
            return Err(CommandError::new(
                "RUN_NOT_FOUND",
                "The requested batch run was not found.",
            ));
        }

        if !run.state.is_terminal() {
            return Err(CommandError::new(
                "RUN_NOT_TERMINAL",
                "Cannot reveal output while batch run is active.",
            ));
        }

        (run.output_root.clone(), run.output_root_identity)
    };

    super::result::reveal_batch_output(&output_root, output_identity)
}

#[command]
pub fn batch_resolve_close(
    window: Window,
    state: State<'_, AppState>,
    request: ResolveBatchCloseRequest,
) -> Result<BatchSnapshot, CommandError> {
    verify_main_window(&window)?;
    let mut guard = state
        .lock()
        .map_err(|_| CommandError::new("INTERNAL_ERROR", "Failed to acquire batch state lock."))?;
    guard.resolve_close(&request.request_id, request.decision)
}
