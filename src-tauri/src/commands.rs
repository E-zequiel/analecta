use tauri::AppHandle;
use tauri_plugin_fs::FsExt;

#[tauri::command]
pub fn update_vault_scope(app: AppHandle, vault_path: String) -> Result<(), String> {
    app.fs_scope()
        .allow_directory(&vault_path, true)
        .map_err(|e| e.to_string())
}
