mod mudlet;
mod node;
mod topmost;
mod tray;
mod tunnel;
mod tunnel_config;

use std::sync::Arc;

use tauri::Manager;

use node::{NodeStatus, NodeSupervisor};
use tunnel::{TunnelStatus, TunnelSupervisor, LOCAL_PORT};
use tunnel_config::{
    ConnectionConfigStore, ConnectionSettings, ConnectionSettingsUpdate, RuntimeConnectionConfig,
    TunnelMode,
};

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
fn node_status(supervisor: tauri::State<'_, Arc<NodeSupervisor>>) -> NodeStatus {
    supervisor.status()
}

#[tauri::command]
fn connection_config(config: tauri::State<'_, RuntimeConnectionConfig>) -> RuntimeConnectionConfig {
    config.inner().clone()
}

#[tauri::command]
fn connection_settings(
    store: tauri::State<'_, ConnectionConfigStore>,
) -> ConnectionSettings {
    store.settings()
}

#[tauri::command]
fn save_connection_settings(
    store: tauri::State<'_, ConnectionConfigStore>,
    update: ConnectionSettingsUpdate,
) -> Result<ConnectionSettings, String> {
    store.save(update)
}

#[tauri::command]
fn alerts_muted(state: tauri::State<'_, tray::AlertMuteState>) -> bool {
    state.is_muted()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(
            tauri_plugin_window_state::Builder::default()
                .with_state_flags(tauri_plugin_window_state::StateFlags::POSITION)
                .build(),
        )
        .invoke_handler(tauri::generate_handler![
            tunnel_status,
            node_status,
            connection_config,
            connection_settings,
            save_connection_settings,
            alerts_muted
        ])
        .setup(|app| {
            match mudlet::provision_helper(app.handle()) {
                Ok(path) => {
                    eprintln!("imp: Mudlet helper ready at {}", path.display());
                }
                Err(error) => {
                    eprintln!("imp: Mudlet helper provisioning skipped: {error}");
                }
            }

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
            let config_store = ConnectionConfigStore::new(config_path, config.clone());
            let tunnel_supervisor = match config.mode {
                TunnelMode::External => TunnelSupervisor::external(),
                TunnelMode::Local => TunnelSupervisor::inactive(),
                TunnelMode::Managed => TunnelSupervisor::managed(config.ssh_target, LOCAL_PORT),
                TunnelMode::Direct => TunnelSupervisor::direct(),
            };
            let node_supervisor = match config.mode {
                TunnelMode::Local => NodeSupervisor::local(app.handle().clone(), LOCAL_PORT),
                _ => NodeSupervisor::inactive(),
            };
            app.manage(runtime_connection);
            app.manage(config_store);
            app.manage(tunnel_supervisor);
            app.manage(node_supervisor);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while running Imp")
        .run(|app_handle, event| {
            // Imp owns exactly the SSH child its own supervisor spawned.
            // Clean it up when the event loop is actually exiting, rather than
            // when exit is merely requested.
            if let tauri::RunEvent::Exit = event {
                if let Some(supervisor) = app_handle.try_state::<Arc<NodeSupervisor>>() {
                    supervisor.shutdown();
                }
                if let Some(supervisor) = app_handle.try_state::<Arc<TunnelSupervisor>>() {
                    supervisor.shutdown();
                }
            }
        });
}
