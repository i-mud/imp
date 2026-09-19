mod tunnel;
mod tunnel_config;

use std::sync::Arc;

use tauri::Manager;

use tunnel::{TunnelStatus, TunnelSupervisor, LOCAL_PORT};
use tunnel_config::TunnelMode;

/// The desktop connection controller's exposure point for transport
/// diagnostics. Returns only a diagnostic enum - never ssh argv, a PID, or a
/// raw process error.
#[tauri::command]
fn tunnel_status(supervisor: tauri::State<'_, Arc<TunnelSupervisor>>) -> TunnelStatus {
    supervisor.status()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .invoke_handler(tauri::generate_handler![tunnel_status])
        .setup(|app| {
            let config_path = app.path().app_config_dir()?.join("tunnel.json");
            let config = tunnel_config::load_or_init(&config_path);
            let supervisor = match config.mode {
                TunnelMode::External => TunnelSupervisor::external(),
                TunnelMode::Managed => TunnelSupervisor::managed(config.ssh_target, LOCAL_PORT),
            };
            app.manage(supervisor);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while running TinyScry")
        .run(|app_handle, event| {
            // TinyScry owns exactly the SSH child its own supervisor spawned;
            // this terminates only that child, never an unrelated process.
            if let tauri::RunEvent::ExitRequested { .. } = event {
                if let Some(supervisor) = app_handle.try_state::<Arc<TunnelSupervisor>>() {
                    supervisor.shutdown();
                }
            }
        });
}
