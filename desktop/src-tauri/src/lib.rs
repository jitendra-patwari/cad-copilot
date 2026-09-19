pub mod generation;

use tauri::{Emitter, Manager, WindowEvent};

pub fn run() {
    tauri::Builder::default()
        .manage(std::sync::Mutex::new(generation::GenerationState::new()))
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                let app_state = window.state::<generation::AppState>();
                let maybe_snap = {
                    if let Ok(mut guard) = app_state.lock() {
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
