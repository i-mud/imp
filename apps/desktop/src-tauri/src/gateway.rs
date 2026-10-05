//! Supervises Imp's bundled authenticated gateway for a desktop-local node.
//!
//! Unlike the local node supervisor, this supervisor never adopts an existing
//! gateway. Gateway `/healthz` intentionally contains no credential identity,
//! so an existing process cannot be proven to use this Imp instance's configured
//! pairing-token digest.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use parking_lot::Mutex;
use serde::Serialize;
use tauri::AppHandle;
use tauri_plugin_shell::ShellExt;

use crate::runtime::{self, OwnedChild, RuntimeOwner};
use crate::tunnel::{probe_gateway_healthz, probe_open};

const READY_TIMEOUT: Duration = Duration::from_secs(10);
const POLL_INTERVAL: Duration = Duration::from_millis(150);
const PORT_WATCH_INTERVAL: Duration = Duration::from_secs(1);
const INITIAL_BACKOFF: Duration = Duration::from_millis(500);
const MAX_BACKOFF: Duration = Duration::from_secs(30);

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum GatewayDiagnostic {
    /// Producer-side remote WSS is not active.
    Inactive,
    /// Starting the bundled gateway and waiting for `/healthz`.
    Starting,
    /// The gateway child owned by this Imp process is healthy.
    Owned,
    /// Something already owns the configured loopback gateway port.
    LocalPortUnavailable,
    /// The bundled gateway could not start or become healthy.
    SpawnUnavailable,
    /// The owned gateway exited or supervision has stopped.
    Down,
}

#[derive(Clone, Debug, Serialize)]
pub struct GatewayStatus {
    pub diagnostic: GatewayDiagnostic,
}

trait OwnedGateway: Send {
    fn terminated(&mut self) -> bool;
    fn kill(self: Box<Self>) -> Result<(), String>;
}

impl OwnedGateway for OwnedChild {
    fn terminated(&mut self) -> bool {
        runtime::terminated(self, "gateway")
    }

    fn kill(mut self: Box<Self>) -> Result<(), String> {
        OwnedChild::kill(&mut self)
            .and_then(|()| self.wait())
            .map(|status| runtime::log_exit(status, "gateway"))
            .map_err(|error| format!("failed to stop bundled Imp gateway: {error}"))
    }
}

type SpawnGateway = Arc<dyn Fn() -> Result<Box<dyn OwnedGateway>, String> + Send + Sync>;

pub struct GatewaySupervisor {
    status: Mutex<GatewayStatus>,
    child: Mutex<Option<Box<dyn OwnedGateway>>>,
    stop: Arc<AtomicBool>,
    worker: Mutex<Option<thread::JoinHandle<()>>>,
}

impl GatewaySupervisor {
    pub fn inactive() -> Arc<Self> {
        Arc::new(Self {
            status: Mutex::new(GatewayStatus {
                diagnostic: GatewayDiagnostic::Inactive,
            }),
            child: Mutex::new(None),
            stop: Arc::new(AtomicBool::new(false)),
            worker: Mutex::new(None),
        })
    }

    pub fn local(
        app: AppHandle,
        gateway_port: u16,
        relay_port: u16,
        pairing_token_sha256: String,
        owner: Arc<RuntimeOwner>,
    ) -> Arc<Self> {
        let spawn = sidecar_spawner(app, gateway_port, relay_port, pairing_token_sha256, owner);
        Self::with_spawner(gateway_port, spawn)
    }

    fn with_spawner(gateway_port: u16, spawn: SpawnGateway) -> Arc<Self> {
        let supervisor = Arc::new(Self {
            status: Mutex::new(GatewayStatus {
                diagnostic: GatewayDiagnostic::Starting,
            }),
            child: Mutex::new(None),
            stop: Arc::new(AtomicBool::new(false)),
            worker: Mutex::new(None),
        });

        let worker_ref = Arc::clone(&supervisor);
        let stop = Arc::clone(&supervisor.stop);
        let handle = thread::spawn(move || worker_ref.run(gateway_port, spawn, stop));
        *supervisor.worker.lock() = Some(handle);

        supervisor
    }

    pub fn status(&self) -> GatewayStatus {
        self.status.lock().clone()
    }

    /// Stops only a gateway child this supervisor owns.
    ///
    /// A listener already present on the gateway port is never owned and is
    /// therefore never signalled.
    pub fn shutdown(&self) {
        self.stop.store(true, Ordering::SeqCst);
        self.kill_owned();

        if let Some(handle) = self.worker.lock().take() {
            let _ = handle.join();
            self.set_diagnostic(GatewayDiagnostic::Down);
        }
    }

    fn set_diagnostic(&self, diagnostic: GatewayDiagnostic) {
        self.status.lock().diagnostic = diagnostic;
    }

    fn run(self: Arc<Self>, gateway_port: u16, spawn: SpawnGateway, stop: Arc<AtomicBool>) {
        let mut backoff = INITIAL_BACKOFF;

        while !stop.load(Ordering::SeqCst) {
            // Deliberately do not identify or adopt an existing gateway.
            // Its health response cannot prove which pairing credential it owns.
            if probe_open(gateway_port) {
                self.set_diagnostic(GatewayDiagnostic::LocalPortUnavailable);
                self.wait_for_port_free(gateway_port, &stop);
                backoff = INITIAL_BACKOFF;
                continue;
            }

            self.set_diagnostic(GatewayDiagnostic::Starting);
            eprintln!("imp: starting local authenticated gateway on 127.0.0.1:{gateway_port}");

            match spawn() {
                Ok(child) => {
                    *self.child.lock() = Some(child);

                    if self.wait_for_owned_ready(gateway_port, &stop) {
                        self.set_diagnostic(GatewayDiagnostic::Owned);
                        eprintln!("imp: local authenticated gateway is ready");
                        backoff = INITIAL_BACKOFF;

                        if self.monitor_owned(gateway_port, &stop) {
                            return;
                        }

                        self.set_diagnostic(GatewayDiagnostic::Down);
                    } else if stop.load(Ordering::SeqCst) {
                        return;
                    } else {
                        self.set_diagnostic(GatewayDiagnostic::SpawnUnavailable);
                    }
                }
                Err(error) => {
                    self.set_diagnostic(GatewayDiagnostic::SpawnUnavailable);
                    eprintln!("imp: local authenticated gateway failed to start: {error}");
                }
            }

            if stop.load(Ordering::SeqCst) {
                return;
            }

            eprintln!("imp: retrying local authenticated gateway in {backoff:?}");
            sleep_unless_stopped(backoff, &stop);
            backoff = (backoff * 2).min(MAX_BACKOFF);
        }
    }

    fn wait_for_port_free(&self, gateway_port: u16, stop: &AtomicBool) {
        while !stop.load(Ordering::SeqCst) && probe_open(gateway_port) {
            sleep_unless_stopped(PORT_WATCH_INTERVAL, stop);
        }
    }

    fn wait_for_owned_ready(&self, gateway_port: u16, stop: &AtomicBool) -> bool {
        let deadline = Instant::now() + READY_TIMEOUT;

        while Instant::now() < deadline {
            if stop.load(Ordering::SeqCst) {
                self.kill_owned();
                return false;
            }

            if self.owned_terminated() {
                self.clear_owned();
                return false;
            }

            // Like the node, the frozen gateway may accept TCP briefly before
            // its HTTP health endpoint is ready. Keep waiting inside the
            // bounded readiness window instead of killing it immediately.
            if probe_gateway_healthz(gateway_port) {
                return true;
            }

            sleep_unless_stopped(POLL_INTERVAL, stop);
        }

        self.kill_owned();
        false
    }

    /// Returns true only when application shutdown requested termination.
    fn monitor_owned(&self, gateway_port: u16, stop: &AtomicBool) -> bool {
        loop {
            if stop.load(Ordering::SeqCst) {
                return true;
            }

            if self.owned_terminated() {
                self.clear_owned();
                eprintln!("imp: local authenticated gateway exited");
                return false;
            }

            if !probe_gateway_healthz(gateway_port) {
                eprintln!("imp: local authenticated gateway became unhealthy");
                self.kill_owned();
                return false;
            }

            sleep_unless_stopped(PORT_WATCH_INTERVAL, stop);
        }
    }

    fn owned_terminated(&self) -> bool {
        self.child
            .lock()
            .as_mut()
            .is_some_and(|child| child.terminated())
    }

    fn clear_owned(&self) {
        self.child.lock().take();
    }

    fn kill_owned(&self) {
        if let Some(child) = self.child.lock().take() {
            if let Err(error) = child.kill() {
                eprintln!("imp: {error}");
            }
        }
    }
}

fn sidecar_spawner(
    app: AppHandle,
    gateway_port: u16,
    relay_port: u16,
    pairing_token_sha256: String,
    owner: Arc<RuntimeOwner>,
) -> SpawnGateway {
    Arc::new(move || {
        let command = app
            .shell()
            .sidecar("imp-node")
            .map_err(|error| format!("failed to resolve bundled Imp gateway: {error}"))?
            .arg("gateway")
            .arg("--host")
            .arg("127.0.0.1")
            .arg("--port")
            .arg(gateway_port.to_string())
            .arg("--relay-url")
            .arg(format!("ws://127.0.0.1:{relay_port}"))
            .arg("--token-sha256")
            .arg(pairing_token_sha256.clone());

        let command: std::process::Command = command.into();
        let mut child = owner
            .spawn(
                command.get_program(),
                command.get_args(),
                runtime::RuntimeStdout::Piped,
            )
            .map_err(|error| format!("failed to spawn owned bundled Imp gateway: {error}"))?;
        eprintln!("imp: owned gateway pid={}", child.id());
        runtime::log_output(&mut child, "gateway");

        Ok(Box::new(child) as Box<dyn OwnedGateway>)
    })
}

fn sleep_unless_stopped(duration: Duration, stop: &AtomicBool) {
    let deadline = Instant::now() + duration;

    while Instant::now() < deadline {
        if stop.load(Ordering::SeqCst) {
            return;
        }

        thread::sleep(POLL_INTERVAL.min(duration));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};
    use std::sync::atomic::AtomicUsize;

    const GATEWAY_HEALTH_RESPONSE: &str =
        "HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n{\"status\":\"ok\"}";
    const FOREIGN_RESPONSE: &str = "HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\nnope";

    struct TestListener {
        port: u16,
        stop: Arc<AtomicBool>,
        worker: Option<thread::JoinHandle<()>>,
    }

    impl TestListener {
        fn start(response: &'static str) -> Self {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            listener.set_nonblocking(true).unwrap();
            let port = listener.local_addr().unwrap().port();

            let stop = Arc::new(AtomicBool::new(false));
            let worker_stop = Arc::clone(&stop);

            let worker = thread::spawn(move || {
                while !worker_stop.load(Ordering::SeqCst) {
                    match listener.accept() {
                        Ok((mut stream, _)) => {
                            let _ = stream.set_read_timeout(Some(Duration::from_millis(250)));
                            let _ = stream.read(&mut [0u8; 1024]);
                            let _ = stream.write_all(response.as_bytes());
                        }
                        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                            thread::sleep(Duration::from_millis(10));
                        }
                        Err(_) => return,
                    }
                }
            });

            Self {
                port,
                stop,
                worker: Some(worker),
            }
        }

        fn stop(&mut self) {
            self.stop.store(true, Ordering::SeqCst);
            if let Some(worker) = self.worker.take() {
                worker.join().unwrap();
            }
        }
    }

    impl Drop for TestListener {
        fn drop(&mut self) {
            self.stop();
        }
    }

    struct FakeGateway {
        kills: Arc<AtomicUsize>,
        terminated: Arc<AtomicBool>,
    }

    impl OwnedGateway for FakeGateway {
        fn terminated(&mut self) -> bool {
            self.terminated.load(Ordering::SeqCst)
        }

        fn kill(self: Box<Self>) -> Result<(), String> {
            self.kills.fetch_add(1, Ordering::SeqCst);
            Ok(())
        }
    }

    fn fake_spawner(spawns: Arc<AtomicUsize>, kills: Arc<AtomicUsize>) -> SpawnGateway {
        Arc::new(move || {
            spawns.fetch_add(1, Ordering::SeqCst);
            Ok(Box::new(FakeGateway {
                kills: Arc::clone(&kills),
                terminated: Arc::new(AtomicBool::new(false)),
            }) as Box<dyn OwnedGateway>)
        })
    }

    fn wait_until(mut predicate: impl FnMut() -> bool) -> bool {
        let deadline = Instant::now() + Duration::from_secs(3);

        while Instant::now() < deadline {
            if predicate() {
                return true;
            }
            thread::sleep(Duration::from_millis(20));
        }

        false
    }

    #[test]
    fn inactive_supervisor_owns_nothing() {
        let supervisor = GatewaySupervisor::inactive();

        assert_eq!(supervisor.status().diagnostic, GatewayDiagnostic::Inactive);
        assert!(supervisor.child.lock().is_none());
        assert!(supervisor.worker.lock().is_none());

        supervisor.shutdown();

        assert_eq!(supervisor.status().diagnostic, GatewayDiagnostic::Inactive);
    }

    #[test]
    fn existing_foreign_listener_is_never_spawned_over_or_killed() {
        let listener = TestListener::start(FOREIGN_RESPONSE);
        let spawns = Arc::new(AtomicUsize::new(0));
        let kills = Arc::new(AtomicUsize::new(0));

        let supervisor = GatewaySupervisor::with_spawner(
            listener.port,
            fake_spawner(Arc::clone(&spawns), Arc::clone(&kills)),
        );

        assert!(wait_until(|| {
            supervisor.status().diagnostic == GatewayDiagnostic::LocalPortUnavailable
        }));

        supervisor.shutdown();

        assert_eq!(spawns.load(Ordering::SeqCst), 0);
        assert_eq!(kills.load(Ordering::SeqCst), 0);
        assert!(TcpStream::connect(("127.0.0.1", listener.port)).is_ok());
    }

    #[test]
    fn existing_valid_gateway_is_still_never_adopted() {
        let listener = TestListener::start(GATEWAY_HEALTH_RESPONSE);
        let spawns = Arc::new(AtomicUsize::new(0));
        let kills = Arc::new(AtomicUsize::new(0));

        let supervisor = GatewaySupervisor::with_spawner(
            listener.port,
            fake_spawner(Arc::clone(&spawns), Arc::clone(&kills)),
        );

        assert!(wait_until(|| {
            supervisor.status().diagnostic == GatewayDiagnostic::LocalPortUnavailable
        }));

        supervisor.shutdown();

        assert_eq!(spawns.load(Ordering::SeqCst), 0);
        assert_eq!(kills.load(Ordering::SeqCst), 0);
        assert!(probe_gateway_healthz(listener.port));
    }

    #[test]
    fn free_port_spawns_and_shutdown_kills_only_owned_child() {
        let reservation = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = reservation.local_addr().unwrap().port();
        drop(reservation);

        let spawns = Arc::new(AtomicUsize::new(0));
        let kills = Arc::new(AtomicUsize::new(0));

        let supervisor = GatewaySupervisor::with_spawner(
            port,
            fake_spawner(Arc::clone(&spawns), Arc::clone(&kills)),
        );

        assert!(wait_until(|| spawns.load(Ordering::SeqCst) == 1));
        assert!(wait_until(|| supervisor.child.lock().is_some()));

        supervisor.shutdown();

        assert_eq!(kills.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn owned_startup_tolerates_tcp_before_gateway_health() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let port = listener.local_addr().unwrap().port();

        let stop = Arc::new(AtomicBool::new(false));
        let worker_stop = Arc::clone(&stop);
        let requests = Arc::new(AtomicUsize::new(0));
        let worker_requests = Arc::clone(&requests);

        let worker = thread::spawn(move || {
            while !worker_stop.load(Ordering::SeqCst) {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        let _ = stream.set_read_timeout(Some(Duration::from_millis(250)));
                        let _ = stream.read(&mut [0u8; 1024]);

                        let request = worker_requests.fetch_add(1, Ordering::SeqCst);
                        let response = if request == 0 {
                            FOREIGN_RESPONSE
                        } else {
                            GATEWAY_HEALTH_RESPONSE
                        };

                        let _ = stream.write_all(response.as_bytes());
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(10));
                    }
                    Err(_) => return,
                }
            }
        });

        let kills = Arc::new(AtomicUsize::new(0));
        let supervisor = GatewaySupervisor::inactive();

        *supervisor.child.lock() = Some(Box::new(FakeGateway {
            kills: Arc::clone(&kills),
            terminated: Arc::new(AtomicBool::new(false)),
        }) as Box<dyn OwnedGateway>);

        let ready = supervisor.wait_for_owned_ready(port, &AtomicBool::new(false));

        stop.store(true, Ordering::SeqCst);
        worker.join().unwrap();

        assert!(
            ready,
            "TCP acceptance before /healthz readiness must not cause restart"
        );
        assert_eq!(kills.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn unhealthy_owned_gateway_is_killed_for_restart() {
        let listener = TestListener::start(FOREIGN_RESPONSE);
        let kills = Arc::new(AtomicUsize::new(0));
        let supervisor = GatewaySupervisor::inactive();

        *supervisor.child.lock() = Some(Box::new(FakeGateway {
            kills: Arc::clone(&kills),
            terminated: Arc::new(AtomicBool::new(false)),
        }) as Box<dyn OwnedGateway>);

        assert!(!supervisor.monitor_owned(listener.port, &AtomicBool::new(false)));
        assert_eq!(kills.load(Ordering::SeqCst), 1);
    }
}
