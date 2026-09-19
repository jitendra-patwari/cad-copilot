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
        ]),
    ))
    .expect("failed to run tauri-build");
}
