pub mod batch;
pub mod generation;
pub mod run_claim;

use std::sync::Mutex;
use tauri::{Emitter, Manager, WindowEvent};

pub fn run() {
    tauri::Builder::default()
        .manage(Mutex::new(generation::GenerationState::new()))
        .manage(Mutex::new(batch::BatchState::new()))
        .manage(Mutex::new(run_claim::RunClaimCoordinator::new()))
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                let close_owner = window
                    .state::<Mutex<run_claim::RunClaimCoordinator>>()
                    .lock()
                    .ok()
                    .and_then(|guard| guard.get_close_owner());

                match close_owner {
                    Some(claim) if claim.kind == run_claim::RunKind::Batch => {
                        let batch_state = window.state::<batch::AppState>();
                        let maybe_snap = {
                            if let Ok(mut guard) = batch_state.lock() {
                                if guard.terminating {
                                    return;
                                }
                                if guard.is_run_active() || guard.has_incomplete_cleanup() {
                                    api.prevent_close();
                                    if let Some(run) = &mut guard.active_run {
                                        run.close_requested = true;
                                    }
                                    guard.revision += 1;
                                    Some(guard.to_snapshot())
                                } else {
                                    None
                                }
                            } else {
                                None
                            }
                        };
                        if let Some(snap) = maybe_snap {
                            let _ = window.emit("batch-state", &snap);
                        }
                    }
                    Some(claim) if claim.kind == run_claim::RunKind::Generation => {
                        let gen_state = window.state::<generation::AppState>();
                        let maybe_snap = {
                            if let Ok(mut guard) = gen_state.lock() {
                                if guard.terminating {
                                    return;
                                }
                                if guard.is_run_active() || guard.has_incomplete_cleanup() {
                                    api.prevent_close();
                                    if let Some(run) = &mut guard.active_run {
                                        run.close_requested = true;
                                    }
                                    guard.revision += 1;
                                    Some(guard.to_snapshot())
                                } else {
                                    None
                                }
                            } else {
                                None
                            }
                        };
                        if let Some(snap) = maybe_snap {
                            let _ = window.emit("generation-state", &snap);
                        }
                    }
                    _ => {
                        // Fallback check if coordinator had no active claim but one of the states is active
                        let gen_state = window.state::<generation::AppState>();
                        let gen_snap = if let Ok(mut guard) = gen_state.lock() {
                            if guard.terminating {
                                return;
                            }
                            if guard.is_run_active() || guard.has_incomplete_cleanup() {
                                api.prevent_close();
                                if let Some(run) = &mut guard.active_run {
                                    run.close_requested = true;
                                }
                                guard.revision += 1;
                                Some(guard.to_snapshot())
                            } else {
                                None
                            }
                        } else {
                            None
                        };
                        if let Some(snap) = gen_snap {
                            let _ = window.emit("generation-state", &snap);
                            return;
                        }

                        let batch_state = window.state::<batch::AppState>();
                        let batch_snap = if let Ok(mut guard) = batch_state.lock() {
                            if guard.terminating {
                                return;
                            }
                            if guard.is_run_active() || guard.has_incomplete_cleanup() {
                                api.prevent_close();
                                if let Some(run) = &mut guard.active_run {
                                    run.close_requested = true;
                                }
                                guard.revision += 1;
                                Some(guard.to_snapshot())
                            } else {
                                None
                            }
                        } else {
                            None
                        };
                        if let Some(snap) = batch_snap {
                            let _ = window.emit("batch-state", &snap);
                        }
                    }
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            generation::commands::generation_snapshot,
            generation::commands::generation_select_output,
            generation::commands::generation_set_key,
            generation::commands::generation_start,
            generation::commands::generation_cancel,
            generation::commands::generation_result,
            generation::commands::generation_preview,
            generation::commands::generation_reveal,
            generation::commands::generation_resolve_close,
            batch::commands::batch_snapshot,
            batch::commands::batch_select_source,
            batch::commands::batch_select_output,
            batch::commands::batch_start,
            batch::commands::batch_cancel,
            batch::commands::batch_result,
            batch::commands::batch_reveal,
            batch::commands::batch_resolve_close,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    #[test]
    fn test_tauri_context_generates() {
        let _context: tauri::Context<tauri::Wry> = tauri::generate_context!();
    }
}
