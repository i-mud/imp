//! Supervises Imp's own SSH local-forward child.
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

#[cfg(windows)]
use std::os::windows::process::CommandExt;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};
#[cfg(windows)]
use windows_sys::Win32::System::Threading::CREATE_NO_WINDOW;

use parking_lot::Mutex;
use serde::Serialize;

pub const LOCAL_PORT: u16 = 8787;
const CONNECT_PROBE_TIMEOUT: Duration = Duration::from_millis(500);
/// An adopted endpoint is normally reached through an SSH forward, so its
/// `/healthz` round trip pays real network latency while the local connect
/// costs nothing - `ssh` accepts immediately whatever the far side is doing.
/// Reading the answer therefore must not share the connect probe's window: a
/// slow but valid relay read as "not a relay" makes a healthy adopted
/// endpoint flap between diagnostics.
const HEALTH_RESPONSE_TIMEOUT: Duration = Duration::from_secs(2);
/// Cap on what an unknown listener can make this process read.
const HEALTH_RESPONSE_BYTES: usize = 4096;
const READY_TIMEOUT: Duration = Duration::from_secs(10);
const POLL_INTERVAL: Duration = Duration::from_millis(150);
/// How often an occupied local port is re-probed while managed mode waits
/// for it: slow enough not to busy-loop, quick enough that a vanished relay
/// is taken over in about a second.
const PORT_WATCH_INTERVAL: Duration = Duration::from_secs(1);
const INITIAL_BACKOFF: Duration = Duration::from_millis(500);
const MAX_BACKOFF: Duration = Duration::from_secs(30);
const STDERR_TAIL_BYTES: usize = 4096;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TunnelDiagnostic {
    /// Managed mode is off; Imp does not own a tunnel.
    External,
    /// Direct WSS mode; no local SSH child or forwarded port is owned.
    Direct,
    /// The local port is held by something that answers like a usable relay
    /// endpoint. Managed mode adopts it: no child is spawned on top of it,
    /// and it is monitored so the forward can be taken over when it goes.
    ExternalPortInUse,
    /// The local port is occupied by something that does not look like a
    /// usable relay endpoint. Imp waits rather than kill the owner.
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
    /// No SSH transport is active. Used by Local mode, where node
    /// supervision is owned separately.
    pub fn inactive() -> Arc<Self> {
        Arc::new(Self {
            status: Mutex::new(TunnelStatus {
                diagnostic: TunnelDiagnostic::External,
            }),
            child: Mutex::new(None),
            stop: Arc::new(AtomicBool::new(false)),
            worker: Mutex::new(None),
        })
    }

    /// External/manual mode: Imp owns no process. Kept for compatibility.
    pub fn external() -> Arc<Self> {
        eprintln!("imp: external tunnel mode; no SSH child");
        Self::inactive()
    }

    /// Direct WSS mode: Imp owns no SSH process or local forward.
    pub fn direct() -> Arc<Self> {
        eprintln!("imp: direct WSS mode; no SSH child");
        Arc::new(Self {
            status: Mutex::new(TunnelStatus {
                diagnostic: TunnelDiagnostic::Direct,
            }),
            child: Mutex::new(None),
            stop: Arc::new(AtomicBool::new(false)),
            worker: Mutex::new(None),
        })
    }

    /// Managed mode: spawn and supervise `ssh` in the background. Imp
    /// never kills whatever already owns the local port. An existing usable
    /// relay endpoint is adopted - reported, monitored, and left alone - and
    /// the worker takes the forward over only once that endpoint is gone and
    /// the port is free again. An unrelated listener at startup is a refusal:
    /// no child, no supervision.
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
            eprintln!("imp: managed tunnel requires a non-empty sshTarget");
            return supervisor;
        }

        if probe_relay_healthz(local_port) {
            // Classified here so the diagnostic is already accurate when this
            // returns; the worker re-probes and owns it from then on.
            supervisor.set_diagnostic(TunnelDiagnostic::ExternalPortInUse);
        } else if probe_open(local_port) {
            supervisor.set_diagnostic(TunnelDiagnostic::LocalPortUnavailable);
            eprintln!("imp: local port 127.0.0.1:{local_port} is unavailable");
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
    /// on a managed instance that never started a worker (an unrelated
    /// listener already held the local port at construction) - neither case
    /// owns anything to report as newly `Down`.
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
            self.await_free_local_port(local_port, &stop);
            if stop.load(Ordering::SeqCst) {
                return;
            }
            self.set_diagnostic(TunnelDiagnostic::Reconnecting);
            eprintln!("imp: SSH tunnel starting ({ssh_target} -> 127.0.0.1:{local_port})");

            match spawn_ssh(&ssh_target, local_port) {
                Ok(mut child) => {
                    if wait_for_ready(&mut child, local_port, &stop) {
                        eprintln!("imp: SSH tunnel established");
                        backoff = INITIAL_BACKOFF;
                        self.set_diagnostic(TunnelDiagnostic::Live);
                        *self.child.lock() = Some(child);
                        self.wait_for_exit_or_stop(&stop);
                        if stop.load(Ordering::SeqCst) {
                            return;
                        }
                        eprintln!("imp: SSH tunnel exited");
                    } else {
                        let _ = child.kill();
                        let _ = child.wait();
                        self.set_diagnostic(TunnelDiagnostic::SshUnavailable);
                    }
                }
                Err(error) => {
                    eprintln!("imp: SSH tunnel failed to start: {error}");
                    self.set_diagnostic(TunnelDiagnostic::SshUnavailable);
                }
            }

            if stop.load(Ordering::SeqCst) {
                return;
            }
            eprintln!("imp: reconnecting SSH tunnel in {backoff:?}");
            sleep_unless_stopped(backoff, &stop);
            backoff = (backoff * 2).min(MAX_BACKOFF);
        }
    }

    /// Holds the forward back for as long as another process owns the local
    /// port, and returns only when it is free. A usable relay endpoint is
    /// adopted rather than replaced: it is reported as `ExternalPortInUse`
    /// and re-probed until it disappears, at which point managed mode takes
    /// the forward over without an application restart. Anything else keeps
    /// the port classified unavailable. Neither branch signals a process this
    /// supervisor did not spawn.
    ///
    /// Each cycle classifies the port once and then applies a single
    /// diagnostic transition, so a healthy adopted endpoint can never be
    /// published as a conflict in passing.
    fn await_free_local_port(&self, local_port: u16, stop: &AtomicBool) {
        let mut reported: Option<LocalPortState> = None;
        while !stop.load(Ordering::SeqCst) {
            let state = classify_local_port(local_port);
            let previous = reported.replace(state);
            let changed = previous != Some(state);
            match state {
                LocalPortState::RelayEndpoint => {
                    if changed {
                        eprintln!(
                            "imp: using existing Imp relay on 127.0.0.1:{local_port}"
                        );
                    }
                    self.set_diagnostic(TunnelDiagnostic::ExternalPortInUse);
                }
                LocalPortState::Foreign => {
                    if changed {
                        eprintln!("imp: local port 127.0.0.1:{local_port} is unavailable");
                    }
                    self.set_diagnostic(TunnelDiagnostic::LocalPortUnavailable);
                }
                LocalPortState::Free => {
                    if previous.is_some() {
                        eprintln!("imp: 127.0.0.1:{local_port} is free again");
                    }
                    return;
                }
            }
            sleep_unless_stopped(PORT_WATCH_INTERVAL, stop);
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

/// What the local port looked like in one watch cycle. The probes are ordered
/// here, once, so a caller never has to sequence them itself - and never
/// publishes an intermediate answer.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum LocalPortState {
    /// Held by something that answers like a usable Imp relay.
    RelayEndpoint,
    /// Held by something else; not ours to bind over or kill.
    Foreign,
    /// Nothing is listening; managed mode may take the forward.
    Free,
}

pub(crate) fn classify_local_port(local_port: u16) -> LocalPortState {
    if probe_relay_healthz(local_port) {
        LocalPortState::RelayEndpoint
    } else if probe_open(local_port) {
        LocalPortState::Foreign
    } else {
        LocalPortState::Free
    }
}

/// True if something already accepts connections on the local port.
fn probe_open(local_port: u16) -> bool {
    TcpStream::connect_timeout(&local_addr(local_port), CONNECT_PROBE_TIMEOUT).is_ok()
}

/// Best-effort check that an already-open local port looks like an Imp
/// relay rather than an unrelated service. A bare TCP connect only proves
/// *something* is listening; this reads relay's `/healthz` framing shape
/// without depending on the WebSocket/JSON protocol layer.
fn probe_relay_healthz(local_port: u16) -> bool {
    use std::io::{Read, Write};
    let Ok(mut stream) = TcpStream::connect_timeout(&local_addr(local_port), CONNECT_PROBE_TIMEOUT)
    else {
        return false;
    };
    let deadline = Instant::now() + HEALTH_RESPONSE_TIMEOUT;
    let request = format!(
        "GET /healthz HTTP/1.1\r\nHost: 127.0.0.1:{local_port}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    // Reads to EOF within one overall deadline rather than giving up on the
    // first short read: a tunnelled relay may answer in several segments.
    let mut response = Vec::new();
    let mut chunk = [0u8; 512];
    while response.len() < HEALTH_RESPONSE_BYTES {
        let Some(remaining) = deadline.checked_duration_since(Instant::now()) else {
            break;
        };
        if stream.set_read_timeout(Some(remaining)).is_err() {
            return false;
        }
        match stream.read(&mut chunk) {
            Ok(0) | Err(_) => break,
            Ok(read) => response.extend_from_slice(&chunk[..read]),
        }
    }
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

    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);

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
        eprintln!("imp: ssh: {line}");
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
    use std::sync::atomic::AtomicUsize;

    #[test]
    fn external_mode_owns_no_child() {
        let supervisor = TunnelSupervisor::external();

        assert_eq!(supervisor.status().diagnostic, TunnelDiagnostic::External);
        supervisor.shutdown(); // no-op; must not panic without a child
    }

    #[test]
    fn direct_mode_owns_no_ssh_child() {
        let supervisor = TunnelSupervisor::direct();

        assert_eq!(supervisor.status().diagnostic, TunnelDiagnostic::Direct);
        assert!(supervisor.child.lock().is_none());
        assert!(supervisor.worker.lock().is_none());

        supervisor.shutdown();

        assert_eq!(supervisor.status().diagnostic, TunnelDiagnostic::Direct);
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

    const RELAY_HEALTHZ: &str = "HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n{\"feed\":\"down\",\"producer_count\":0,\"has_snapshot\":false,\"seq\":null}";
    const RELAY_HEALTHZ_HEAD: &str = "HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n";
    const STALE_RELAY_BODY: &str =
        "{\"feed\":\"stale\",\"producer_count\":1,\"has_snapshot\":true,\"seq\":7}";
    const NOT_A_RELAY: &str = "HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\nnope";
    /// Long enough that headers and body cannot land in one read.
    const SEGMENT_GAP: Duration = Duration::from_millis(150);

    #[derive(Clone, Copy)]
    enum Reply {
        /// One write, as a loopback relay answers.
        Whole(&'static str),
        /// Headers then body, as a relay behind an SSH forward may arrive.
        Segmented(&'static str, &'static str),
        /// Accepts and then answers nothing, holding the connection open.
        Stall,
    }

    /// A loopback listener standing in for a process Imp does not own.
    /// It answers every request with the current reply, so a test can change
    /// how an adopted endpoint behaves without releasing the port: the port
    /// stays occupied throughout, leaving no window in which a takeover could
    /// race the test.
    struct ForeignListener {
        port: u16,
        reply: Arc<Mutex<Reply>>,
        requests: Arc<AtomicUsize>,
        stop: Arc<AtomicBool>,
        server: Option<thread::JoinHandle<()>>,
    }

    impl ForeignListener {
        fn start(body: &'static str) -> Self {
            Self::slow(body, Duration::ZERO)
        }

        /// Answers after `delay`, standing in for an endpoint reached through
        /// an SSH forward rather than on loopback.
        fn slow(body: &'static str, delay: Duration) -> Self {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            listener.set_nonblocking(true).unwrap();
            let port = listener.local_addr().unwrap().port();
            let reply = Arc::new(Mutex::new(Reply::Whole(body)));
            let requests = Arc::new(AtomicUsize::new(0));
            let stop = Arc::new(AtomicBool::new(false));
            let replies = Arc::clone(&reply);
            let served = Arc::clone(&requests);
            let serving = Arc::clone(&stop);
            let server = thread::spawn(move || {
                use std::io::{Read, Write};
                while !serving.load(Ordering::SeqCst) {
                    let Ok((mut stream, _)) = listener.accept() else {
                        thread::sleep(Duration::from_millis(20));
                        continue;
                    };
                    stream.set_nonblocking(false).unwrap();
                    let _ = stream.set_read_timeout(Some(Duration::from_millis(200)));
                    let _ = stream.read(&mut [0u8; 512]);
                    let reply = *replies.lock();
                    served.fetch_add(1, Ordering::SeqCst);
                    thread::sleep(delay);
                    match reply {
                        Reply::Whole(body) => {
                            let _ = stream.write_all(body.as_bytes());
                        }
                        Reply::Segmented(head, body) => {
                            let _ = stream.write_all(head.as_bytes());
                            let _ = stream.flush();
                            thread::sleep(SEGMENT_GAP);
                            let _ = stream.write_all(body.as_bytes());
                        }
                        Reply::Stall => {
                            while !serving.load(Ordering::SeqCst) {
                                thread::sleep(Duration::from_millis(20));
                            }
                        }
                    }
                }
            });
            Self {
                port,
                reply,
                requests,
                stop,
                server: Some(server),
            }
        }

        fn reply(&self, reply: Reply) {
            *self.reply.lock() = reply;
        }

        /// How many requests this listener has read so far; lets a test wait
        /// for a probe to be in flight instead of sleeping for one.
        fn requests(&self) -> usize {
            self.requests.load(Ordering::SeqCst)
        }

        /// Releases the port, as a vanishing relay endpoint would.
        fn stop(&mut self) {
            self.stop.store(true, Ordering::SeqCst);
            if let Some(server) = self.server.take() {
                server.join().unwrap();
            }
        }
    }

    impl Drop for ForeignListener {
        fn drop(&mut self) {
            self.stop();
        }
    }

    fn wait_for_diagnostic(
        supervisor: &TunnelSupervisor,
        wanted: TunnelDiagnostic,
        timeout: Duration,
    ) -> bool {
        let deadline = Instant::now() + timeout;
        while Instant::now() < deadline {
            if supervisor.status().diagnostic == wanted {
                return true;
            }
            thread::sleep(Duration::from_millis(25));
        }
        false
    }

    #[test]
    fn managed_mode_adopts_an_existing_relay_endpoint_without_owning_it() {
        let relay = ForeignListener::start(RELAY_HEALTHZ);

        let supervisor =
            TunnelSupervisor::managed("definitely-not-a-configured-host".into(), relay.port);

        assert_eq!(
            supervisor.status().diagnostic,
            TunnelDiagnostic::ExternalPortInUse
        );
        thread::sleep(PORT_WATCH_INTERVAL * 2 + POLL_INTERVAL);
        assert_eq!(
            supervisor.status().diagnostic,
            TunnelDiagnostic::ExternalPortInUse,
            "a healthy endpoint stays adopted across watch cycles"
        );
        assert!(
            supervisor.child.lock().is_none(),
            "an adopted endpoint is never owned as a child"
        );
        assert!(
            probe_relay_healthz(relay.port),
            "the external endpoint must be left running"
        );

        supervisor.shutdown();
    }

    #[test]
    fn classify_local_port_distinguishes_a_relay_a_foreign_owner_and_a_free_port() {
        let relay = ForeignListener::start(RELAY_HEALTHZ);
        for _ in 0..5 {
            assert_eq!(
                classify_local_port(relay.port),
                LocalPortState::RelayEndpoint,
                "a healthy endpoint classifies the same way every cycle"
            );
        }

        relay.reply(Reply::Whole(NOT_A_RELAY));
        assert_eq!(classify_local_port(relay.port), LocalPortState::Foreign);

        let free = TcpListener::bind("127.0.0.1:0").unwrap();
        let free_port = free.local_addr().unwrap().port();
        drop(free);
        assert_eq!(classify_local_port(free_port), LocalPortState::Free);
    }

    #[test]
    fn a_segmented_stale_health_response_is_still_a_relay_endpoint() {
        let relay = ForeignListener::start(RELAY_HEALTHZ);
        relay.reply(Reply::Segmented(RELAY_HEALTHZ_HEAD, STALE_RELAY_BODY));

        assert_eq!(
            classify_local_port(relay.port),
            LocalPortState::RelayEndpoint,
            "headers and body in separate reads, and a stale feed, are both still an adoptable relay"
        );
    }

    #[test]
    fn shutdown_during_a_stalled_health_read_stays_within_the_probe_bound() {
        let relay = ForeignListener::start(RELAY_HEALTHZ);
        let supervisor =
            TunnelSupervisor::managed("definitely-not-a-configured-host".into(), relay.port);
        assert_eq!(
            supervisor.status().diagnostic,
            TunnelDiagnostic::ExternalPortInUse
        );

        // Wait for a probe the listener has read but will never answer, so
        // shutdown lands squarely inside an outstanding health read.
        relay.reply(Reply::Stall);
        let before = relay.requests();
        while relay.requests() == before {
            thread::sleep(Duration::from_millis(10));
        }

        let started = Instant::now();
        supervisor.shutdown();
        let elapsed = started.elapsed();

        assert!(
            elapsed <= Duration::from_secs(3),
            "shutdown must stay within the health-response bound, took {elapsed:?}"
        );
        assert_eq!(supervisor.status().diagnostic, TunnelDiagnostic::Down);
        assert!(
            supervisor.child.lock().is_none(),
            "no owned SSH child may remain"
        );
        assert!(
            TcpStream::connect(("127.0.0.1", relay.port)).is_ok(),
            "the external listener must be left running"
        );
    }

    #[test]
    fn a_slow_adopted_endpoint_never_reports_a_transient_conflict() {
        // Answers well past the connect-probe window, like a relay reached
        // through an SSH forward: slow is not the same as foreign.
        let relay = ForeignListener::slow(RELAY_HEALTHZ, Duration::from_millis(800));
        assert_eq!(
            classify_local_port(relay.port),
            LocalPortState::RelayEndpoint
        );

        let supervisor =
            TunnelSupervisor::managed("definitely-not-a-configured-host".into(), relay.port);

        let deadline = Instant::now() + PORT_WATCH_INTERVAL * 2;
        while Instant::now() < deadline {
            assert_eq!(
                supervisor.status().diagnostic,
                TunnelDiagnostic::ExternalPortInUse,
                "a healthy adopted endpoint must stay adopted, never flap to a conflict"
            );
            thread::sleep(Duration::from_millis(10));
        }
        assert!(supervisor.child.lock().is_none());

        supervisor.shutdown();
    }

    #[test]
    fn managed_mode_takes_over_after_the_adopted_endpoint_disappears() {
        let mut relay = ForeignListener::start(RELAY_HEALTHZ);
        let supervisor =
            TunnelSupervisor::managed("definitely-not-a-configured-host".into(), relay.port);
        assert_eq!(
            supervisor.status().diagnostic,
            TunnelDiagnostic::ExternalPortInUse
        );

        relay.stop(); // the pre-existing forward goes away, e.g. a VPS reboot

        // The same supervisor, never reconstructed, must leave the adopted
        // state and start its own child. The target is deliberately
        // unresolvable, so the attempt fails fast instead of needing real
        // infrastructure - reaching `SshUnavailable` proves it spawned.
        let took_over = wait_for_diagnostic(
            &supervisor,
            TunnelDiagnostic::SshUnavailable,
            Duration::from_secs(10),
        );
        supervisor.shutdown();

        assert!(
            took_over,
            "managed mode must take the forward over without an application restart"
        );
    }

    #[test]
    fn managed_mode_never_takes_over_a_port_another_process_still_holds() {
        let relay = ForeignListener::start(RELAY_HEALTHZ);
        let supervisor =
            TunnelSupervisor::managed("definitely-not-a-configured-host".into(), relay.port);
        assert_eq!(
            supervisor.status().diagnostic,
            TunnelDiagnostic::ExternalPortInUse
        );

        relay.reply(Reply::Whole(NOT_A_RELAY)); // still bound; no longer a usable relay

        assert!(wait_for_diagnostic(
            &supervisor,
            TunnelDiagnostic::LocalPortUnavailable,
            Duration::from_secs(5)
        ));
        thread::sleep(PORT_WATCH_INTERVAL + POLL_INTERVAL);
        assert_eq!(
            supervisor.status().diagnostic,
            TunnelDiagnostic::LocalPortUnavailable,
            "an occupied port never becomes ours"
        );
        assert!(
            supervisor.child.lock().is_none(),
            "nothing may be spawned over a port another process owns"
        );
        assert!(
            TcpStream::connect(("127.0.0.1", relay.port)).is_ok(),
            "the owning listener must be left alone"
        );

        supervisor.shutdown();
    }

    #[test]
    fn shutdown_while_monitoring_an_adopted_endpoint_is_prompt_and_leaves_it_running() {
        let relay = ForeignListener::start(RELAY_HEALTHZ);
        let supervisor =
            TunnelSupervisor::managed("definitely-not-a-configured-host".into(), relay.port);
        assert_eq!(
            supervisor.status().diagnostic,
            TunnelDiagnostic::ExternalPortInUse
        );

        let started = Instant::now();
        supervisor.shutdown();
        supervisor.shutdown(); // must not panic or hang on a second call

        assert!(
            started.elapsed() < Duration::from_secs(3),
            "shutdown must not wait out a watch interval"
        );
        assert_eq!(supervisor.status().diagnostic, TunnelDiagnostic::Down);
        assert!(supervisor.child.lock().is_none());
        assert!(
            probe_relay_healthz(relay.port),
            "shutdown must not touch the external endpoint"
        );
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
