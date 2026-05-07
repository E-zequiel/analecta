use tauri::{AppHandle, State};
use tauri_plugin_fs::FsExt;
use tauri_plugin_notification::NotificationExt;

use crate::sidecar::SidecarPort;

#[tauri::command]
pub fn get_sidecar_port(port: State<SidecarPort>) -> Option<u16> {
    *port.0.lock().unwrap()
}

#[tauri::command]
pub fn update_vault_scope(app: AppHandle, vault_path: String) -> Result<(), String> {
    app.fs_scope()
        .allow_directory(&vault_path, true)
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub fn notify_success(app: AppHandle, title: String, body: String) {
    notify(&app, title, body);
}

#[tauri::command]
pub fn notify_error(app: AppHandle, title: String, body: String) {
    notify(&app, title, body);
}

fn notify(app: &AppHandle, title: String, body: String) {
    let _ = app.notification().builder().title(title).body(body).show();
}
