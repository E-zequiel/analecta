use std::path::PathBuf;
use std::sync::Mutex;

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_fs::FsExt;
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

// Injected by build.rs from Cargo's TARGET variable.
const TARGET: &str = env!("TARGET");

pub struct SidecarState(pub Mutex<Option<CommandChild>>);
pub struct SidecarPort(pub Mutex<Option<u16>>);

#[derive(Serialize, Clone)]
struct SidecarReadyPayload {
    port: u16,
}

fn sidecar_binary(_app: &AppHandle) -> Result<PathBuf, Box<dyn std::error::Error>> {
    let name = format!("analecta-sidecar-{TARGET}");

    // In dev builds, use the onedir directly from the source tree.
    #[cfg(debug_assertions)]
    return Ok(PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("binaries/analecta-sidecar")
        .join(&name));

    // In release builds, the onedir is bundled as a resource.
    // List-form resources preserve the full path from src-tauri/, so the
    // onedir lands at resource_dir/binaries/analecta-sidecar/ with _internal/
    // adjacent to the binary — exactly what PyInstaller's bootloader expects.
    #[cfg(not(debug_assertions))]
    Ok(_app
        .path()
        .resource_dir()?
        .join("binaries/analecta-sidecar")
        .join(name))
}

pub fn spawn_sidecar(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let binary = sidecar_binary(app)?;
    let (mut rx, child) = app.shell().command(binary.to_str().unwrap()).spawn()?;

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
