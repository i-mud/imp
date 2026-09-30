//! Supervises Imp's bundled loopback node for Local connection mode.
//!
//! The node process is distinct from the UI. A MUD-client adapter talks only
//! to this host-local node; the desktop consumes the same state/action
//! endpoints it already uses for SSH-forwarded and other local endpoints.
//!
//! Ownership is strict:
//! - an existing healthy Imp node is adopted but never signalled;
//! - a foreign listener is never replaced or killed;
//! - only a sidecar process spawned by this supervisor is terminated;
//! - an owned node that exits or becomes unhealthy is restarted with bounded
//!   backoff.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use parking_lot::Mutex;
use serde::Serialize;
use tauri::AppHandle;
use tauri_plugin_shell::{
    process::{CommandChild, CommandEvent},
    ShellExt,
};

use crate::tunnel::{classify_local_port, LocalPortState};

const READY_TIMEOUT: Duration = Duration::from_secs(10);
const POLL_INTERVAL: Duration = Duration::from_millis(150);
const PORT_WATCH_INTERVAL: Duration = Duration::from_secs(1);
const INITIAL_BACKOFF: Duration = Duration::from_millis(500);
const MAX_BACKOFF: Duration = Duration::from_secs(30);

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum NodeDiagnostic {
    /// Local mode is not selected.
    Inactive,
    /// Starting an owned bundled node and waiting for `/healthz`.
    Starting,
    /// The bundled node started by this Imp process is healthy.
    Owned,
    /// A healthy node already existed on the loopback endpoint.
    Adopted,
    /// Something other than a healthy Imp node owns the loopback port.
    LocalPortUnavailable,
    /// The bundled sidecar could not start or become healthy.
    SpawnUnavailable,
    /// The owned node exited or supervision has stopped.
    Down,
}

#[derive(Clone, Debug, Serialize)]
pub struct NodeStatus {
    pub diagnostic: NodeDiagnostic,
}

trait OwnedNode: Send {
    fn terminated(&self) -> bool;
    fn kill(self: Box<Self>) -> Result<(), String>;
}

struct SidecarNode {
    child: CommandChild,
    terminated: Arc<AtomicBool>,
}

impl OwnedNode for SidecarNode {
    fn terminated(&self) -> bool {
        self.terminated.load(Ordering::SeqCst)
    }

    fn kill(self: Box<Self>) -> Result<(), String> {
        let SidecarNode { child, .. } = *self;
        child
            .kill()
            .map_err(|error| format!("failed to stop bundled Imp node: {error}"))
    }
}

type SpawnNode = Arc<dyn Fn() -> Result<Box<dyn OwnedNode>, String> + Send + Sync>;

pub struct NodeSupervisor {
    status: Mutex<NodeStatus>,
    child: Mutex<Option<Box<dyn OwnedNode>>>,
    stop: Arc<AtomicBool>,
    worker: Mutex<Option<thread::JoinHandle<()>>>,
}

impl NodeSupervisor {
    pub fn inactive() -> Arc<Self> {
        Arc::new(Self {
            status: Mutex::new(NodeStatus {
                diagnostic: NodeDiagnostic::Inactive,
            }),
            child: Mutex::new(None),
            stop: Arc::new(AtomicBool::new(false)),
            worker: Mutex::new(None),
        })
    }

    pub fn local(app: AppHandle, local_port: u16) -> Arc<Self> {
        let spawn = sidecar_spawner(app, local_port);
        Self::with_spawner(local_port, spawn)
    }

    fn with_spawner(local_port: u16, spawn: SpawnNode) -> Arc<Self> {
        let supervisor = Arc::new(Self {
            status: Mutex::new(NodeStatus {
                diagnostic: NodeDiagnostic::Starting,
            }),
            child: Mutex::new(None),
            stop: Arc::new(AtomicBool::new(false)),
            worker: Mutex::new(None),
        });

        let worker_ref = Arc::clone(&supervisor);
        let stop = Arc::clone(&supervisor.stop);
        let handle = thread::spawn(move || worker_ref.run(local_port, spawn, stop));
        *supervisor.worker.lock() = Some(handle);

        supervisor
    }

    pub fn status(&self) -> NodeStatus {
        self.status.lock().clone()
    }

    /// Stops only the sidecar this supervisor owns. An adopted node or foreign
    /// listener has no child handle here and therefore cannot be signalled.
    pub fn shutdown(&self) {
        self.stop.store(true, Ordering::SeqCst);
        self.kill_owned();

        if let Some(handle) = self.worker.lock().take() {
            let _ = handle.join();
            self.set_diagnostic(NodeDiagnostic::Down);
        }
    }

    fn set_diagnostic(&self, diagnostic: NodeDiagnostic) {
        self.status.lock().diagnostic = diagnostic;
    }

    fn run(self: Arc<Self>, local_port: u16, spawn: SpawnNode, stop: Arc<AtomicBool>) {
        let mut backoff = INITIAL_BACKOFF;

        while !stop.load(Ordering::SeqCst) {
            match classify_local_port(local_port) {
                LocalPortState::RelayEndpoint => {
                    self.set_diagnostic(NodeDiagnostic::Adopted);
                    eprintln!("imp: using existing local Imp node on 127.0.0.1:{local_port}");
                    backoff = INITIAL_BACKOFF;

                    self.wait_for_adopted_change(local_port, &stop);
                }
                LocalPortState::Foreign => {
                    self.set_diagnostic(NodeDiagnostic::LocalPortUnavailable);
                    sleep_unless_stopped(PORT_WATCH_INTERVAL, &stop);
                }
                LocalPortState::Free => {
                    self.set_diagnostic(NodeDiagnostic::Starting);
                    eprintln!("imp: starting local Imp node on 127.0.0.1:{local_port}");

                    match spawn() {
                        Ok(child) => {
                            *self.child.lock() = Some(child);

                            if self.wait_for_owned_ready(local_port, &stop) {
                                self.set_diagnostic(NodeDiagnostic::Owned);
                                eprintln!("imp: local Imp node is ready");
                                backoff = INITIAL_BACKOFF;

                                if self.monitor_owned(local_port, &stop) {
                                    return;
                                }

                                self.set_diagnostic(NodeDiagnostic::Down);
                            } else if stop.load(Ordering::SeqCst) {
                                return;
                            } else {
                                self.set_diagnostic(NodeDiagnostic::SpawnUnavailable);
                            }
                        }
                        Err(error) => {
                            self.set_diagnostic(NodeDiagnostic::SpawnUnavailable);
                            eprintln!("imp: local Imp node failed to start: {error}");
                        }
                    }

                    if stop.load(Ordering::SeqCst) {
                        return;
                    }

                    eprintln!("imp: retrying local Imp node in {backoff:?}");
                    sleep_unless_stopped(backoff, &stop);
                    backoff = (backoff * 2).min(MAX_BACKOFF);
                }
            }
        }
    }

    fn wait_for_adopted_change(&self, local_port: u16, stop: &AtomicBool) {
        while !stop.load(Ordering::SeqCst) {
            if classify_local_port(local_port) != LocalPortState::RelayEndpoint {
                return;
            }

            sleep_unless_stopped(PORT_WATCH_INTERVAL, stop);
        }
    }

    fn wait_for_owned_ready(&self, local_port: u16, stop: &AtomicBool) -> bool {
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

            match classify_local_port(local_port) {
                LocalPortState::RelayEndpoint => return true,
                LocalPortState::Foreign => {
                    // Another process won the bind race. Kill only our own
                    // child and let the next supervision cycle classify the
                    // actual listener.
                    self.kill_owned();
                    return false;
                }
                LocalPortState::Free => {}
            }

            sleep_unless_stopped(POLL_INTERVAL, stop);
        }

        self.kill_owned();
        false
    }

    /// Returns true only when shutdown requested termination. Every other
    /// return means the owned node needs another supervision cycle.
    fn monitor_owned(&self, local_port: u16, stop: &AtomicBool) -> bool {
        loop {
            if stop.load(Ordering::SeqCst) {
                return true;
            }

            if self.owned_terminated() {
                self.clear_owned();
                eprintln!("imp: local Imp node exited");
                return false;
            }

            if classify_local_port(local_port) != LocalPortState::RelayEndpoint {
                eprintln!("imp: local Imp node became unhealthy");
                self.kill_owned();
                return false;
            }

            sleep_unless_stopped(PORT_WATCH_INTERVAL, stop);
        }
    }

    fn owned_terminated(&self) -> bool {
        self.child
            .lock()
            .as_ref()
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

fn sidecar_spawner(app: AppHandle, local_port: u16) -> SpawnNode {
    Arc::new(move || {
        let command = app
            .shell()
            .sidecar("imp-node")
            .map_err(|error| format!("failed to resolve bundled Imp node: {error}"))?
            .arg("--host")
            .arg("127.0.0.1")
            .arg("--port")
            .arg(local_port.to_string());

        let (mut events, child) = command
            .spawn()
            .map_err(|error| format!("failed to spawn bundled Imp node: {error}"))?;

        let terminated = Arc::new(AtomicBool::new(false));
        let event_terminated = Arc::clone(&terminated);

        // Drain the sidecar pipes even though supervision uses `/healthz`.
        // This prevents a noisy child from ever blocking on a full pipe.
        tauri::async_runtime::spawn(async move {
            while let Some(event) = events.recv().await {
                match event {
                    CommandEvent::Stdout(bytes) => {
                        for line in String::from_utf8_lossy(&bytes).lines() {
                            eprintln!("imp: node: {line}");
                        }
                    }
                    CommandEvent::Stderr(bytes) => {
                        for line in String::from_utf8_lossy(&bytes).lines() {
                            eprintln!("imp: node: {line}");
                        }
                    }
                    CommandEvent::Error(error) => {
                        eprintln!("imp: node process event error: {error}");
                    }
                    CommandEvent::Terminated(payload) => {
                        eprintln!(
                            "imp: node process terminated (code={:?}, signal={:?})",
                            payload.code, payload.signal
                        );
                        break;
                    }
                    _ => {}
                }
            }

            event_terminated.store(true, Ordering::SeqCst);
        });

        Ok(Box::new(SidecarNode { child, terminated }) as Box<dyn OwnedNode>)
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

    const HEALTH_RESPONSE: &str = concat!(
        "HTTP/1.1 200 OK\r\n",
        "Connection: close\r\n",
        "\r\n",
        "{\"feed\":\"down\",\"producer_count\":0,\"has_snapshot\":false,\"seq\":null}"
    );

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

    struct FakeNode {
        kills: Arc<AtomicUsize>,
    }

    impl OwnedNode for FakeNode {
        fn terminated(&self) -> bool {
            false
        }

        fn kill(self: Box<Self>) -> Result<(), String> {
            self.kills.fetch_add(1, Ordering::SeqCst);
            Ok(())
        }
    }

    fn fake_spawner(spawns: Arc<AtomicUsize>, kills: Arc<AtomicUsize>) -> SpawnNode {
        Arc::new(move || {
            spawns.fetch_add(1, Ordering::SeqCst);
            Ok(Box::new(FakeNode {
                kills: Arc::clone(&kills),
            }) as Box<dyn OwnedNode>)
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
        let supervisor = NodeSupervisor::inactive();

        assert_eq!(supervisor.status().diagnostic, NodeDiagnostic::Inactive);
        assert!(supervisor.child.lock().is_none());
        assert!(supervisor.worker.lock().is_none());

        supervisor.shutdown();

        assert_eq!(supervisor.status().diagnostic, NodeDiagnostic::Inactive);
    }

    #[test]
    fn local_mode_adopts_an_existing_node_without_spawning_or_killing_it() {
        let node = TestListener::start(HEALTH_RESPONSE);
        let spawns = Arc::new(AtomicUsize::new(0));
        let kills = Arc::new(AtomicUsize::new(0));

        let supervisor = NodeSupervisor::with_spawner(
            node.port,
            fake_spawner(Arc::clone(&spawns), Arc::clone(&kills)),
        );

        assert!(wait_until(|| {
            supervisor.status().diagnostic == NodeDiagnostic::Adopted
        }));
        assert_eq!(spawns.load(Ordering::SeqCst), 0);

        supervisor.shutdown();

        assert_eq!(kills.load(Ordering::SeqCst), 0);
        assert!(
            TcpStream::connect(("127.0.0.1", node.port)).is_ok(),
            "shutdown must leave an adopted node running"
        );
    }

    #[test]
    fn local_mode_refuses_a_foreign_listener_without_spawning() {
        let foreign = TestListener::start(FOREIGN_RESPONSE);
        let spawns = Arc::new(AtomicUsize::new(0));
        let kills = Arc::new(AtomicUsize::new(0));

        let supervisor = NodeSupervisor::with_spawner(
            foreign.port,
            fake_spawner(Arc::clone(&spawns), Arc::clone(&kills)),
        );

        assert!(wait_until(|| {
            supervisor.status().diagnostic == NodeDiagnostic::LocalPortUnavailable
        }));

        supervisor.shutdown();

        assert_eq!(spawns.load(Ordering::SeqCst), 0);
        assert_eq!(kills.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn local_mode_spawns_on_a_free_port_and_shutdown_kills_only_its_child() {
        let reservation = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = reservation.local_addr().unwrap().port();
        drop(reservation);

        let spawns = Arc::new(AtomicUsize::new(0));
        let kills = Arc::new(AtomicUsize::new(0));

        let supervisor = NodeSupervisor::with_spawner(
            port,
            fake_spawner(Arc::clone(&spawns), Arc::clone(&kills)),
        );

        assert!(wait_until(|| spawns.load(Ordering::SeqCst) == 1));
        assert!(wait_until(|| supervisor.child.lock().is_some()));

        supervisor.shutdown();

        assert_eq!(kills.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn local_mode_takes_over_after_an_adopted_node_disappears() {
        let mut node = TestListener::start(HEALTH_RESPONSE);
        let spawns = Arc::new(AtomicUsize::new(0));
        let kills = Arc::new(AtomicUsize::new(0));

        let supervisor = NodeSupervisor::with_spawner(
            node.port,
            fake_spawner(Arc::clone(&spawns), Arc::clone(&kills)),
        );

        assert!(wait_until(|| {
            supervisor.status().diagnostic == NodeDiagnostic::Adopted
        }));

        node.stop();

        assert!(
            wait_until(|| spawns.load(Ordering::SeqCst) >= 1),
            "the bundled node should be started after the adopted endpoint disappears"
        );

        supervisor.shutdown();

        assert_eq!(kills.load(Ordering::SeqCst), 1);
    }
}
