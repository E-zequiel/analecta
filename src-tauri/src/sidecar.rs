use std::sync::Mutex;

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_fs::FsExt;
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

pub struct SidecarState(pub Mutex<Option<CommandChild>>);
pub struct SidecarPort(pub Mutex<Option<u16>>);

#[derive(Serialize, Clone)]
struct SidecarReadyPayload {
    port: u16,
}

pub fn spawn_sidecar(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let (mut rx, child) = app.shell().sidecar("analecta-sidecar")?.spawn()?;

    *app.state::<SidecarState>().0.lock().unwrap() = Some(child);

    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    let line = String::from_utf8_lossy(&bytes);
                    log::info!("[sidecar stdout] {line}");
                    if let Some(port_str) = line.strip_prefix("LISTENING_ON_PORT:") {
                        if let Ok(port) = port_str.trim().parse::<u16>() {
                            *handle.state::<SidecarPort>().0.lock().unwrap() = Some(port);
                            let _ = handle.emit("sidecar-ready", SidecarReadyPayload { port });
                        }
                    } else if let Some(path_str) = line.strip_prefix("VAULT_PATH:") {
                        let path = path_str.trim();
                        match handle.fs_scope().allow_directory(path, true) {
                            Ok(()) => log::info!("[sidecar] fs scope granted for {path}"),
                            Err(e) => {
                                log::error!("[sidecar] failed to grant fs scope for {path}: {e}")
                            }
                        }
                    }
                }
                CommandEvent::Stderr(bytes) => {
                    log::warn!("[sidecar stderr] {}", String::from_utf8_lossy(&bytes));
                }
                CommandEvent::Error(e) => {
                    log::error!("[sidecar error] {e}");
                }
                CommandEvent::Terminated(status) => {
                    log::info!(
                        "[sidecar] terminated: code={:?} signal={:?}",
                        status.code,
                        status.signal
                    );
                    break;
                }
                _ => {}
            }
        }
    });

    Ok(())
}
