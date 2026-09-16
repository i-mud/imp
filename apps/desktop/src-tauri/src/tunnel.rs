//! Supervises TinyScry's own SSH local-forward child.
//!
//! Uses the platform's system OpenSSH client as a direct-argv subprocess, so
//! the user's existing `~/.ssh/config`, `known_hosts` and agent keep working
//! unmodified: this module never parses SSH config, stores a password, or
//! embeds a key. Host verification is never weakened - `StrictHostKeyChecking`
//! and `UserKnownHostsFile` are never overridden.
//!
//! Diagnostics (`TunnelDiagnostic`) are internal: the desktop connection
//! controller may use them to enrich a reconnect message, but they are never
//! handed to a Svelte component as SSH argv, a PID, or a raw process error.

use std::collections::VecDeque;
use std::io::Read;
use std::net::{SocketAddr, TcpStream};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use parking_lot::Mutex;
use serde::Serialize;

pub const LOCAL_PORT: u16 = 8787;
const CONNECT_PROBE_TIMEOUT: Duration = Duration::from_millis(500);
const READY_TIMEOUT: Duration = Duration::from_secs(10);
const POLL_INTERVAL: Duration = Duration::from_millis(150);
const INITIAL_BACKOFF: Duration = Duration::from_millis(500);
const MAX_BACKOFF: Duration = Duration::from_secs(30);
const STDERR_TAIL_BYTES: usize = 4096;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TunnelDiagnostic {
    /// Managed mode is off; TinyScry does not own a tunnel.
    External,
    /// The configured local port already had a listener before TinyScry
    /// started, and it answers like a usable relay endpoint; TinyScry does
    /// not spawn its own child on top of it.
    ExternalPortInUse,
    /// The configured local port is occupied by something that does not
    /// look like a usable relay endpoint. TinyScry refuses to start rather
    /// than kill the owning process.
    LocalPortUnavailable,
    /// A managed SSH child could not establish or keep the forward.
    SshUnavailable,
    /// Establishing or re-establishing the managed tunnel.
    Reconnecting,
    /// The managed forward is up.
    Live,
    /// Not started, or stopped.
    Down,
}

#[derive(Clone, Debug, Serialize)]
pub struct TunnelStatus {
    pub diagnostic: TunnelDiagnostic,
}

/// Owns at most one SSH child. Never signals a process it did not spawn.
pub struct TunnelSupervisor {
    status: Mutex<TunnelStatus>,
    child: Mutex<Option<Child>>,
    stop: Arc<AtomicBool>,
    worker: Mutex<Option<thread::JoinHandle<()>>>,
}

impl TunnelSupervisor {
    /// External/manual mode: TinyScry owns no process. Kept for compatibility
    pub fn external() -> Arc<Self> {
        eprintln!("tinyscry: external tunnel mode; no SSH child");
        Arc::new(Self {
            status: Mutex::new(TunnelStatus {
                diagnostic: TunnelDiagnostic::External,
            }),
            child: Mutex::new(None),
            stop: Arc::new(AtomicBool::new(false)),
            worker: Mutex::new(None),
        })
    }

    /// Managed mode: spawn and supervise `ssh` in the background. If the
    /// local port is already occupied, this never kills the owning process -
    /// it classifies the port as an existing usable endpoint or a genuine
    /// conflict and never starts a child in either case.
    pub fn managed(ssh_target: String, local_port: u16) -> Arc<Self> {
        let supervisor = Arc::new(Self {
            status: Mutex::new(TunnelStatus {
                diagnostic: TunnelDiagnostic::Reconnecting,
            }),
            child: Mutex::new(None),
            stop: Arc::new(AtomicBool::new(false)),
            worker: Mutex::new(None),
        });
        let ssh_target = ssh_target.trim().to_owned();
        if ssh_target.is_empty() {
            supervisor.set_diagnostic(TunnelDiagnostic::SshUnavailable);
            eprintln!("tinyscry: managed tunnel requires a non-empty sshTarget");
            return supervisor;
        }

        if probe_relay_healthz(local_port) {
            supervisor.set_diagnostic(TunnelDiagnostic::ExternalPortInUse);
            eprintln!("tinyscry: using existing TinyScry relay on 127.0.0.1:{local_port}");
            return supervisor;
        }
        if probe_open(local_port) {
            supervisor.set_diagnostic(TunnelDiagnostic::LocalPortUnavailable);
            eprintln!("tinyscry: local port 127.0.0.1:{local_port} is unavailable");
            return supervisor;
        }

        let worker_ref = Arc::clone(&supervisor);
        let stop = Arc::clone(&supervisor.stop);
        let handle = thread::spawn(move || worker_ref.run(ssh_target, local_port, stop));
        *supervisor.worker.lock() = Some(handle);
        supervisor
    }

    pub fn status(&self) -> TunnelStatus {
        self.status.lock().clone()
    }

    /// Terminates the child this supervisor owns, if any, and joins its
    /// worker thread. Idempotent; a no-op on an external-mode instance, and
    /// on a managed instance that never started a worker (e.g. a local-port
    /// conflict at construction) - neither case owns anything to report as
    /// newly `Down`.
    pub fn shutdown(&self) {
        self.stop.store(true, Ordering::SeqCst);
        if let Some(mut child) = self.child.lock().take() {
            let _ = child.kill();
            let _ = child.wait();
        }
        if let Some(handle) = self.worker.lock().take() {
            let _ = handle.join();
            self.set_diagnostic(TunnelDiagnostic::Down);
        }
    }

    fn set_diagnostic(&self, diagnostic: TunnelDiagnostic) {
        self.status.lock().diagnostic = diagnostic;
    }

    fn run(self: Arc<Self>, ssh_target: String, local_port: u16, stop: Arc<AtomicBool>) {
        let mut backoff = INITIAL_BACKOFF;
        while !stop.load(Ordering::SeqCst) {
            self.set_diagnostic(TunnelDiagnostic::Reconnecting);
            eprintln!("tinyscry: SSH tunnel starting ({ssh_target} -> 127.0.0.1:{local_port})");

            match spawn_ssh(&ssh_target, local_port) {
                Ok(mut child) => {
                    if wait_for_ready(&mut child, local_port, &stop) {
                        eprintln!("tinyscry: SSH tunnel established");
                        backoff = INITIAL_BACKOFF;
                        self.set_diagnostic(TunnelDiagnostic::Live);
                        *self.child.lock() = Some(child);
                        self.wait_for_exit_or_stop(&stop);
                        if stop.load(Ordering::SeqCst) {
                            return;
                        }
                        eprintln!("tinyscry: SSH tunnel exited");
                    } else {
                        let _ = child.kill();
                        let _ = child.wait();
                        self.set_diagnostic(TunnelDiagnostic::SshUnavailable);
                    }
                }
                Err(error) => {
                    eprintln!("tinyscry: SSH tunnel failed to start: {error}");
                    self.set_diagnostic(TunnelDiagnostic::SshUnavailable);
                }
            }

            if stop.load(Ordering::SeqCst) {
                return;
            }
            eprintln!("tinyscry: reconnecting SSH tunnel in {backoff:?}");
            sleep_unless_stopped(backoff, &stop);
            backoff = (backoff * 2).min(MAX_BACKOFF);
        }
    }

    /// Blocks until the owned child exits on its own, or `shutdown()` takes
    /// and kills it. Polling `try_wait()` is the boring, portable choice:
    /// `Child` exposes no readiness primitive to block on across platforms.
    fn wait_for_exit_or_stop(&self, stop: &AtomicBool) {
        loop {
            if stop.load(Ordering::SeqCst) {
                if let Some(mut child) = self.child.lock().take() {
                    let _ = child.kill();
                    let _ = child.wait();
                }
                return;
            }
            let exited = {
                let mut guard = self.child.lock();
                match guard.as_mut() {
                    Some(child) => !matches!(child.try_wait(), Ok(None)),
                    None => true,
                }
            };
            if exited {
                self.child.lock().take();
                return;
            }
            thread::sleep(POLL_INTERVAL);
        }
    }
}

fn local_addr(local_port: u16) -> SocketAddr {
    SocketAddr::from(([127, 0, 0, 1], local_port))
}

/// True if something already accepts connections on the local port.
fn probe_open(local_port: u16) -> bool {
    TcpStream::connect_timeout(&local_addr(local_port), CONNECT_PROBE_TIMEOUT).is_ok()
}

/// Best-effort check that an already-open local port looks like a TinyScry
/// relay rather than an unrelated service. A bare TCP connect only proves
/// *something* is listening; this reads relay's `/healthz` framing shape
/// without depending on the WebSocket/JSON protocol layer.
fn probe_relay_healthz(local_port: u16) -> bool {
    use std::io::{Read, Write};
    let Ok(mut stream) = TcpStream::connect_timeout(&local_addr(local_port), CONNECT_PROBE_TIMEOUT)
    else {
        return false;
    };
    if stream
        .set_read_timeout(Some(CONNECT_PROBE_TIMEOUT))
        .is_err()
    {
        return false;
    }
    let request = format!(
        "GET /healthz HTTP/1.1\r\nHost: 127.0.0.1:{local_port}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut response = Vec::new();
    let _ = stream.read_to_end(&mut response);
    let Some(body_start) = response.windows(4).position(|window| window == b"\r\n\r\n") else {
        return false;
    };
    let headers = &response[..body_start];
    if !(headers.starts_with(b"HTTP/1.1 200") || headers.starts_with(b"HTTP/1.0 200")) {
        return false;
    }
    let Ok(health) = serde_json::from_slice::<serde_json::Value>(&response[body_start + 4..])
    else {
        return false;
    };
    matches!(
        health.get("feed").and_then(serde_json::Value::as_str),
        Some("down" | "stale" | "live")
    ) && health
        .get("producer_count")
        .and_then(serde_json::Value::as_u64)
        .is_some()
        && health
            .get("has_snapshot")
            .and_then(serde_json::Value::as_bool)
            .is_some()
        && health
            .get("seq")
            .is_some_and(|seq| seq.is_null() || seq.as_u64().is_some())
}

fn ssh_command(ssh_target: &str, local_port: u16) -> Command {
    let mut command = Command::new("ssh");
    command
        .arg("-N")
        .arg("-T")
        .arg("-o")
        .arg("BatchMode=yes")
        .arg("-o")
        .arg("ExitOnForwardFailure=yes")
        .arg("-o")
        .arg("ServerAliveInterval=15")
        .arg("-o")
        .arg("ServerAliveCountMax=3")
        .arg("-L")
        .arg(format!("127.0.0.1:{local_port}:127.0.0.1:8787"))
        .arg("--")
        .arg(ssh_target)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped());
    command
}

fn spawn_ssh(ssh_target: &str, local_port: u16) -> std::io::Result<Child> {
    let mut child = ssh_command(ssh_target, local_port).spawn()?;

    if let Some(stderr) = child.stderr.take() {
        thread::spawn(move || drain_stderr(stderr));
    }
    Ok(child)
}

/// Drains the entire pipe so OpenSSH can never block on stderr, retaining
/// only a bounded tail for failure diagnostics.
fn drain_stderr(mut stderr: std::process::ChildStderr) {
    let mut tail = VecDeque::with_capacity(STDERR_TAIL_BYTES);
    let mut chunk = [0u8; 512];
    loop {
        match stderr.read(&mut chunk) {
            Ok(0) | Err(_) => break,
            Ok(read) => {
                for byte in &chunk[..read] {
                    if tail.len() == STDERR_TAIL_BYTES {
                        tail.pop_front();
                    }
                    tail.push_back(*byte);
                }
            }
        }
    }

    let bytes = tail.into_iter().collect::<Vec<_>>();
    for line in String::from_utf8_lossy(&bytes).lines() {
        eprintln!("tinyscry: ssh: {line}");
    }
}

/// Polls for the forwarded port becoming reachable, bailing out immediately
/// if the child exits first (e.g. `ExitOnForwardFailure=yes` firing).
fn wait_for_ready(child: &mut Child, local_port: u16, stop: &AtomicBool) -> bool {
    let deadline = Instant::now() + READY_TIMEOUT;
    while Instant::now() < deadline {
        if stop.load(Ordering::SeqCst) {
            return false;
        }
        if !matches!(child.try_wait(), Ok(None)) {
            return false;
        }
        if probe_open(local_port) {
            return true;
        }
        thread::sleep(POLL_INTERVAL);
    }
    false
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
    use std::net::TcpListener;

    #[test]
    fn external_mode_owns_no_child() {
        let supervisor = TunnelSupervisor::external();

        assert_eq!(supervisor.status().diagnostic, TunnelDiagnostic::External);
        supervisor.shutdown(); // no-op; must not panic without a child
    }

    #[test]
    fn ssh_argv_preserves_host_verification_and_forwards_loopback_only() {
        let command = ssh_command("-option-shaped-alias", 9000);
        let args = command
            .get_args()
            .map(|arg| arg.to_string_lossy().into_owned())
            .collect::<Vec<_>>();

        assert_eq!(
            args,
            [
                "-N",
                "-T",
                "-o",
                "BatchMode=yes",
                "-o",
                "ExitOnForwardFailure=yes",
                "-o",
                "ServerAliveInterval=15",
                "-o",
                "ServerAliveCountMax=3",
                "-L",
                "127.0.0.1:9000:127.0.0.1:8787",
                "--",
                "-option-shaped-alias",
            ]
        );
        assert!(!args.iter().any(|arg| arg == "StrictHostKeyChecking=no"));
        assert!(!args.iter().any(|arg| arg == "UserKnownHostsFile=/dev/null"));
    }

    #[test]
    fn managed_mode_never_kills_a_pre_existing_listener_on_the_local_port() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let accept_thread = thread::spawn(move || {
            for _ in 0..3 {
                let _ = listener.accept();
            }
        });

        let supervisor = TunnelSupervisor::managed("unreachable-alias-for-test".into(), port);
        let status = supervisor.status();

        assert_eq!(status.diagnostic, TunnelDiagnostic::LocalPortUnavailable);
        assert!(
            supervisor.worker.lock().is_none(),
            "no child should have been spawned"
        );
        supervisor.shutdown();
        drop(TcpStream::connect(("127.0.0.1", port)));
        let _ = accept_thread.join();
    }

    #[test]
    fn managed_mode_recognizes_an_existing_relay_endpoint_on_the_local_port() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = thread::spawn(move || {
            use std::io::{Read, Write};
            if let Ok((mut stream, _)) = listener.accept() {
                let mut buf = [0u8; 512];
                let _ = stream.read(&mut buf);
                let _ = stream.write_all(
                    b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n{\"feed\":\"down\",\"producer_count\":0,\"has_snapshot\":false,\"seq\":null}",
                );
            }
        });

        let supervisor = TunnelSupervisor::managed("unreachable-alias-for-test".into(), port);
        let status = supervisor.status();

        assert_eq!(status.diagnostic, TunnelDiagnostic::ExternalPortInUse);
        assert!(
            supervisor.worker.lock().is_none(),
            "no child should have been spawned"
        );
        let _ = server.join();
    }

    #[test]
    fn managed_mode_reports_ssh_unavailable_for_an_unreachable_target() {
        // A local port that is genuinely free, and a target ssh(1) itself
        // will refuse (bad alias). Exercises the failure/backoff path
        // without depending on real network access.
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        drop(listener); // release the port; ssh should fail to reach the alias, not the bind

        let supervisor = TunnelSupervisor::managed("definitely-not-a-configured-host".into(), port);

        let deadline = Instant::now() + Duration::from_secs(5);
        let mut saw_unavailable = false;
        while Instant::now() < deadline {
            if supervisor.status().diagnostic == TunnelDiagnostic::SshUnavailable {
                saw_unavailable = true;
                break;
            }
            thread::sleep(Duration::from_millis(50));
        }
        supervisor.shutdown();

        assert!(
            saw_unavailable,
            "expected SshUnavailable after ssh could not reach the alias"
        );
    }

    #[test]
    fn shutdown_terminates_the_owned_child_reports_down_and_is_idempotent() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        drop(listener);

        let supervisor = TunnelSupervisor::managed("definitely-not-a-configured-host".into(), port);
        thread::sleep(Duration::from_millis(200));

        supervisor.shutdown();
        supervisor.shutdown(); // must not panic or hang on a second call

        assert!(supervisor.child.lock().is_none());
        assert_eq!(supervisor.status().diagnostic, TunnelDiagnostic::Down);
    }

    #[test]
    fn managed_mode_rejects_an_empty_target_without_spawning() {
        let supervisor = TunnelSupervisor::managed("  ".into(), LOCAL_PORT);

        assert_eq!(
            supervisor.status().diagnostic,
            TunnelDiagnostic::SshUnavailable
        );
        assert!(supervisor.worker.lock().is_none());
    }

    #[test]
    fn shutdown_on_external_mode_leaves_the_external_diagnostic_untouched() {
        let supervisor = TunnelSupervisor::external();

        supervisor.shutdown();

        assert_eq!(supervisor.status().diagnostic, TunnelDiagnostic::External);
    }
}
