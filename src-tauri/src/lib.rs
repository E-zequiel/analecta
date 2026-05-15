mod commands;
mod sidecar;
mod tray;

use std::sync::Mutex;

use sidecar::{SidecarPort, SidecarState};
use tauri::{Emitter, Manager, WindowEvent};
use tauri_plugin_deep_link::DeepLinkExt;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            tray::show_main_window(app);
            if let Some(url) = argv.iter().find(|a| a.starts_with("analecta://")) {
                let _ = app.emit("deep-link", url.clone());
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .invoke_handler(tauri::generate_handler![
            commands::get_sidecar_port,
            commands::update_vault_scope,
            commands::notify_success,
            commands::notify_error,
        ])
        .manage(SidecarState(Mutex::new(None)))
        .manage(SidecarPort(Mutex::new(None)))
        .setup(|app| {
            sidecar::spawn_sidecar(app.handle())?;
            tray::setup_tray(app.handle())?;

            #[cfg(debug_assertions)]
            app.deep_link().register("analecta")?;

            let handle = app.handle().clone();
            app.deep_link().on_open_url(move |event| {
                tray::show_main_window(&handle);
                for url in event.urls() {
                    if url.scheme() == "analecta" {
                        let _ = handle.emit("deep-link", url.to_string());
                    }
                }
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, WindowEvent::CloseRequested { .. }) {
                if let Some(child) = window.state::<SidecarState>().0.lock().unwrap().take() {
                    let _ = child.kill();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
