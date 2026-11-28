mod gateway;
mod managed_check;
mod mudlet;
mod node;
mod remote_access;
mod runtime;
mod topmost;
mod tray;
mod tunnel;
mod tunnel_config;

use std::sync::Arc;

use tauri::Manager;

use gateway::{GatewayStatus, GatewaySupervisor};
use managed_check::{ExitReadiness, ManagedCheck};
use node::{NodeStatus, NodeSupervisor};
use tunnel::{TunnelStatus, TunnelSupervisor, NODE_PORT, SSH_FORWARD_PORT};
use tunnel_config::{
    ConnectionConfigStore, ConnectionSettingsStatus, ConnectionSettingsUpdate, LoadedConfig,
    RuntimeConnectionConfig, TunnelMode,
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
fn gateway_status(supervisor: tauri::State<'_, Arc<GatewaySupervisor>>) -> GatewayStatus {
    supervisor.status()
}

#[tauri::command]
fn connection_config(config: tauri::State<'_, RuntimeConnectionConfig>) -> RuntimeConnectionConfig {
    config.inner().clone()
}

#[tauri::command]
async fn connection_settings(
    store: tauri::State<'_, Arc<ConnectionConfigStore>>,
) -> Result<ConnectionSettingsStatus, String> {
    let store = Arc::clone(store.inner());
    tauri::async_runtime::spawn_blocking(move || store.settings())
        .await
        .map_err(|_| "Imp could not read connection settings.".to_owned())?
}

#[tauri::command]
async fn save_connection_settings(
    store: tauri::State<'_, Arc<ConnectionConfigStore>>,
    check: tauri::State<'_, Arc<ManagedCheck>>,
    update: ConnectionSettingsUpdate,
) -> Result<ConnectionSettingsStatus, String> {
    let reservation = check.inner().reserve_save()?;
    let store = Arc::clone(store.inner());
    tauri::async_runtime::spawn_blocking(move || {
        let _reservation = reservation;
        store.save(update)
    })
    .await
    .map_err(|_| "Imp could not save connection settings.".to_owned())?
}

#[tauri::command]
fn alerts_muted(state: tauri::State<'_, tray::AlertMuteState>) -> bool {
    state.is_muted()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    runtime::run_helper_if_requested();
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
            gateway_status,
            connection_config,
            connection_settings,
            save_connection_settings,
            managed_check::check_managed_connection,
            managed_check::cancel_managed_connection,
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

            let config_dir = app.path().app_config_dir()?;
            let runtime_owner =
                Arc::new(runtime::RuntimeOwner::new().map_err(|error| {
                    format!("failed to initialize Imp runtime ownership: {error}")
                })?);
            let config_path = config_dir.join("tunnel.json");
            let remote_access_path = config_dir.join("remote-access.json");

            let config = tunnel_config::load(&config_path);
            let remote_access = remote_access::load_or_init(&remote_access_path);
            let runtime_connection = config.runtime_connection_config();
            let tunnel_supervisor = match &config {
                LoadedConfig::Configured(config) => match config.mode {
                    TunnelMode::External => TunnelSupervisor::external(),
                    TunnelMode::Local => TunnelSupervisor::inactive(),
                    TunnelMode::Managed => match config.managed_destination() {
                        Ok(destination) => TunnelSupervisor::managed(
                            destination,
                            SSH_FORWARD_PORT,
                            Arc::clone(&runtime_owner),
                        ),
                        Err(error) => {
                            eprintln!("imp: invalid managed tunnel destination: {error}");
                            TunnelSupervisor::inactive()
                        }
                    },
                    TunnelMode::Direct => TunnelSupervisor::direct(),
                },
                LoadedConfig::Unconfigured | LoadedConfig::Unavailable => {
                    TunnelSupervisor::inactive()
                }
            };
            let config_store = ConnectionConfigStore::new(config_path, config);
            let node_supervisor =
                NodeSupervisor::local(app.handle().clone(), NODE_PORT, Arc::clone(&runtime_owner));
            let gateway_supervisor = if remote_access.wss_enabled {
                GatewaySupervisor::local(
                    app.handle().clone(),
                    remote_access.gateway_port,
                    NODE_PORT,
                    remote_access.pairing_token_sha256.clone(),
                    Arc::clone(&runtime_owner),
                )
            } else {
                GatewaySupervisor::inactive()
            };

            app.manage(runtime_connection);
            app.manage(Arc::new(config_store));
            app.manage(Arc::new(ManagedCheck::default()));
            app.manage(tunnel_supervisor);
            app.manage(runtime_owner);
            app.manage(node_supervisor);
            app.manage(gateway_supervisor);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while running Imp")
        .run(|app_handle, event| {
            if let tauri::RunEvent::ExitRequested { api, code, .. } = &event {
                if let Some(check) = app_handle.try_state::<Arc<ManagedCheck>>() {
                    match check.begin_shutdown() {
                        ExitReadiness::Ready(_) => {}
                        ExitReadiness::Waiting => api.prevent_exit(),
                        ExitReadiness::NeedsWait => {
                            api.prevent_exit();
                            let check = Arc::clone(check.inner());
                            let app = app_handle.clone();
                            let code = code.unwrap_or(0);
                            tauri::async_runtime::spawn_blocking(move || {
                                check.shutdown();
                                app.exit(code);
                            });
                        }
                    }
                }
            }

            // Clean up only processes owned by this Imp instance when the
            // event loop is actually exiting, rather than when exit is merely
            // requested. Stop the gateway before its local node.
            if let tauri::RunEvent::Exit = event {
                if let Some(check) = app_handle.try_state::<Arc<ManagedCheck>>() {
                    match check.begin_shutdown() {
                        ExitReadiness::Ready(outcome) => outcome.report(),
                        ExitReadiness::NeedsWait | ExitReadiness::Waiting => {
                            eprintln!(
                                "imp: exit started before connection settings shutdown completed"
                            );
                        }
                    }
                }
                if let Some(supervisor) = app_handle.try_state::<Arc<GatewaySupervisor>>() {
                    supervisor.shutdown();
                }
                if let Some(supervisor) = app_handle.try_state::<Arc<NodeSupervisor>>() {
                    supervisor.shutdown();
                }
                if let Some(supervisor) = app_handle.try_state::<Arc<TunnelSupervisor>>() {
                    supervisor.shutdown();
                }
            }
        });
}
