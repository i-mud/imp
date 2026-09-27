mod topmost;
mod tray;
mod tunnel;
mod tunnel_config;

use std::sync::Arc;

use tauri::Manager;

use tunnel::{TunnelStatus, TunnelSupervisor, LOCAL_PORT};
use tunnel_config::{RuntimeConnectionConfig, TunnelMode};

/// The desktop connection controller's exposure point for transport
/// diagnostics. Returns only a diagnostic enum - never ssh argv, a PID, or a
/// raw process error.
#[tauri::command]
fn tunnel_status(supervisor: tauri::State<'_, Arc<TunnelSupervisor>>) -> TunnelStatus {
    supervisor.status()
}

/// Returns only the renderer-facing connection tuple. SSH target/process
/// details never cross this boundary.
#[tauri::command]
fn connection_config(config: tauri::State<'_, RuntimeConnectionConfig>) -> RuntimeConnectionConfig {
    config.inner().clone()
}

#[tauri::command]
fn alerts_muted(state: tauri::State<'_, tray::AlertMuteState>) -> bool {
    state.is_muted()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(
            tauri_plugin_window_state::Builder::default()
                .with_state_flags(tauri_plugin_window_state::StateFlags::POSITION)
                .build(),
        )
        .invoke_handler(tauri::generate_handler![
            tunnel_status,
            connection_config,
            alerts_muted
        ])
        .setup(|app| {
            app.manage(tray::AlertMuteState::default());
            tray::install(app)?;

            if let Some(hud) = app.get_webview_window("hud") {
                topmost::install(&hud)?;

                let app_handle = app.handle().clone();
                hud.on_window_event(move |event| {
                    if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        app_handle.exit(0);
                    }
                });
            }

            let config_path = app.path().app_config_dir()?.join("tunnel.json");
            let config = tunnel_config::load_or_init(&config_path);
            let runtime_connection = config.runtime_connection_config();
            let supervisor = match config.mode {
                TunnelMode::External => TunnelSupervisor::external(),
                TunnelMode::Managed => TunnelSupervisor::managed(config.ssh_target, LOCAL_PORT),
                TunnelMode::Direct => TunnelSupervisor::direct(),
            };
            app.manage(runtime_connection);
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
