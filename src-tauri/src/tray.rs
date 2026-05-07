use tauri::{
    menu::{Menu, MenuItem},
    tray::{TrayIconBuilder, TrayIconEvent},
    AppHandle, Manager,
};
use tauri_plugin_autostart::ManagerExt;
use tauri_plugin_clipboard_manager::ClipboardExt;
use tauri_plugin_notification::NotificationExt;

use crate::sidecar::SidecarPort;

pub fn setup_tray(app: &AppHandle) -> tauri::Result<()> {
    let menu = Menu::with_items(
        app,
        &[
            &MenuItem::with_id(app, "add-url", "Add URL from clipboard", true, None::<&str>)?,
            &MenuItem::with_id(app, "open", "Open Analecta", true, None::<&str>)?,
            &MenuItem::with_id(
                app,
                "toggle-autostart",
                "Start with system",
                true,
                None::<&str>,
            )?,
            &MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?,
        ],
    )?;

    TrayIconBuilder::new()
        .menu(&menu)
        .show_menu_on_left_click(false)
        .icon(app.default_window_icon().unwrap().clone())
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::DoubleClick { .. } = event {
                show_main_window(tray.app_handle());
            }
        })
        .on_menu_event(|app, event| match event.id.as_ref() {
            "add-url" => {
                let handle = app.clone();
                tauri::async_runtime::spawn(async move {
                    add_url_from_clipboard(handle).await;
                });
            }
            "open" => show_main_window(app),
            "toggle-autostart" => toggle_autostart(app),
            "quit" => app.exit(0),
            _ => {}
        })
        .build(app)?;

    Ok(())
}

async fn add_url_from_clipboard(app: AppHandle) {
    let url = match app.clipboard().read_text() {
        Ok(text) => text.trim().to_string(),
        Err(e) => {
            notify(&app, &format!("Could not read clipboard: {e}"));
            return;
        }
    };

    if !url.starts_with("http://") && !url.starts_with("https://") {
        notify(&app, "Clipboard does not contain a valid URL.");
        return;
    }

    let port = match *app.state::<SidecarPort>().0.lock().unwrap() {
        Some(p) => p,
        None => {
            notify(&app, "Sidecar not ready — try again in a moment.");
            return;
        }
    };

    #[derive(serde::Serialize)]
    struct ExtractBody {
        url: String,
    }

    let client = reqwest::Client::new();
    match client
        .post(format!("http://localhost:{port}/api/v1/extract"))
        .json(&ExtractBody { url })
        .send()
        .await
    {
        Ok(r) if r.status().is_success() => {
            notify(&app, "Entry saved.");
        }
        Ok(r) => {
            let status = r.status();
            let body = r.text().await.unwrap_or_default();
            notify(&app, &format!("Extraction failed ({status}): {body}"));
        }
        Err(e) => {
            notify(&app, &format!("Request failed: {e}"));
        }
    }
}

fn toggle_autostart(app: &AppHandle) {
    let autolaunch = app.autolaunch();
    match autolaunch.is_enabled() {
        Ok(enabled) => {
            let result = if enabled {
                autolaunch.disable()
            } else {
                autolaunch.enable()
            };
            match result {
                Ok(()) => {
                    let msg = if enabled {
                        "Autostart disabled."
                    } else {
                        "Autostart enabled."
                    };
                    notify(app, msg);
                }
                Err(e) => notify(app, &format!("Autostart error: {e}")),
            }
        }
        Err(e) => notify(app, &format!("Autostart error: {e}")),
    }
}

pub fn show_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn notify(app: &AppHandle, body: &str) {
    let _ = app
        .notification()
        .builder()
        .title("Analecta")
        .body(body)
        .show();
}
