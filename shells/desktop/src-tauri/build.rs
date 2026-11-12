// The app's own commands are named here, so each needs a permission, and capabilities/ grants exactly these two.
fn main() {
    let manifest = tauri_build::AppManifest::new().commands(&["core_listen", "core_send"]);
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(manifest)).expect("the Tauri build step failed");
}
