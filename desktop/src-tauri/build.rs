fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "generation_snapshot",
            "generation_select_output",
            "generation_set_key",
            "generation_start",
            "generation_cancel",
            "generation_result",
            "generation_preview",
            "generation_reveal",
            "generation_resolve_close",
            "batch_snapshot",
            "batch_select_source",
            "batch_select_output",
            "batch_start",
            "batch_cancel",
            "batch_result",
            "batch_reveal",
            "batch_resolve_close",
        ]),
    ))
    .expect("failed to run tauri-build");
}
