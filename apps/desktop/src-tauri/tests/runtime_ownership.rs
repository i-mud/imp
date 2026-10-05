#![cfg(feature = "runtime-acceptance")]

#[allow(dead_code)]
#[path = "../src/runtime.rs"]
mod runtime;

use std::io::{BufRead, Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
const HARNESS: &str = env!("CARGO_BIN_EXE_runtime-ownership-harness");

fn wait_until(mut predicate: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(10);
    while !predicate() {
        assert!(
            Instant::now() < deadline,
            "native process deadline exceeded"
        );
        thread::sleep(Duration::from_millis(20));
    }
}

struct Parent {
    child: Child,
    #[cfg(target_os = "linux")]
    pidfd: Option<std::os::fd::OwnedFd>,
    #[cfg(target_os = "linux")]
    children: Vec<(u32, std::os::fd::OwnedFd)>,
}

impl Parent {
    fn new(child: Child) -> Self {
        #[cfg(target_os = "linux")]
        // The observer reaps only its exact adopted fixture identities, including negative controls.
        assert_eq!(
            unsafe { libc::prctl(libc::PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) },
            0
        );
        #[cfg(target_os = "linux")]
        let pidfd = open_pidfd(child.id());
        Self {
            child,
            #[cfg(target_os = "linux")]
            pidfd,
            #[cfg(target_os = "linux")]
            children: Vec::new(),
        }
    }

    fn start(directory: &Path, count: usize, mode: &str) -> Self {
        let mut command = Command::new(HARNESS);
        command.args([
            if mode == "uncontained" {
                "uncontained-parent"
            } else {
                "parent"
            },
            directory.to_str().unwrap(),
            &count.to_string(),
            mode,
        ]);
        let mut parent = Self::new(command.stdin(Stdio::piped()).spawn().unwrap());
        wait_until(|| {
            #[cfg(target_os = "linux")]
            parent.retain_children();
            assert!(
                parent.child.try_wait().unwrap().is_none(),
                "fixture parent exited during startup"
            );
            directory.join("parent-ready").exists()
        });
        parent
    }

    fn stop(&mut self, abrupt: bool) {
        #[cfg(target_os = "linux")]
        self.retain_children();
        if abrupt {
            #[cfg(target_os = "linux")]
            if let Some(pidfd) = &self.pidfd {
                let _ = signal_pidfd(pidfd, libc::SIGKILL);
            } else if let Some(mut stdin) = self.child.stdin.take() {
                let _ = stdin.write_all(b"x");
            }
            #[cfg(windows)]
            self.child.kill().unwrap();
            #[cfg(all(unix, not(target_os = "linux")))]
            if let Some(mut stdin) = self.child.stdin.take() {
                stdin.write_all(b"x").unwrap();
            }
        } else {
            self.child.stdin.take().unwrap().write_all(b"q").unwrap();
        }
        wait_until(|| self.child.try_wait().unwrap().is_some());
    }

    #[cfg(target_os = "linux")]
    fn retain_children(&mut self) {
        let Some(parent) = &self.pidfd else { return };
        if !pidfd_alive(parent) {
            return;
        }
        let Ok(children) =
            std::fs::read_to_string(format!("/proc/{0}/task/{0}/children", self.child.id()))
        else {
            return;
        };
        for pid in children
            .split_whitespace()
            .filter_map(|pid| pid.parse().ok())
        {
            if self.children.iter().any(|(known, _)| *known == pid) {
                continue;
            }
            if let Some(handle) = open_pidfd(pid) {
                if process_parent_pid(pid) == Some(self.child.id()) && pidfd_alive(parent) {
                    self.children.push((pid, handle));
                }
            }
        }
    }
}

impl Drop for Parent {
    fn drop(&mut self) {
        #[cfg(target_os = "linux")]
        self.retain_children();
        #[cfg(target_os = "linux")]
        if let Some(pidfd) = &self.pidfd {
            let _ = signal_pidfd(pidfd, libc::SIGKILL);
        } else if self.child.try_wait().ok().flatten().is_none() {
            if let Some(mut stdin) = self.child.stdin.take() {
                let _ = stdin.write_all(b"x");
            }
        }
        #[cfg(windows)]
        if self.child.try_wait().ok().flatten().is_none() {
            let _ = self.child.kill();
        }
        #[cfg(all(unix, not(target_os = "linux")))]
        if let Some(mut stdin) = self.child.stdin.take() {
            let _ = stdin.write_all(b"x");
        }
        let _ = self.child.wait();
        #[cfg(target_os = "linux")]
        for (_, handle) in &self.children {
            let deadline = Instant::now() + Duration::from_secs(3);
            while pidfd_alive(handle) && Instant::now() < deadline {
                thread::sleep(Duration::from_millis(20));
            }
            let _ = signal_pidfd(handle, libc::SIGKILL);
            let deadline = Instant::now() + Duration::from_secs(3);
            while pidfd_alive(handle) && Instant::now() < deadline {
                thread::sleep(Duration::from_millis(20));
            }
            reap_pidfd(handle);
        }
    }
}

#[derive(Clone)]
struct Probe {
    token: String,
    channel: Arc<Mutex<TcpStream>>,
}

impl Probe {
    fn connect(port: u16, token: String) -> Option<Self> {
        let stream = TcpStream::connect_timeout(
            &SocketAddr::from(([127, 0, 0, 1], port)),
            Duration::from_millis(250),
        )
        .ok()?;
        stream
            .set_read_timeout(Some(Duration::from_millis(250)))
            .ok()?;
        stream
            .set_write_timeout(Some(Duration::from_millis(250)))
            .ok()?;
        Some(Self {
            token,
            channel: Arc::new(Mutex::new(stream)),
        })
    }

    fn response(&self, operation: &str) -> Option<String> {
        let mut stream = self.channel.lock().ok()?;
        stream
            .write_all(format!("{operation} {}\n", self.token).as_bytes())
            .ok()?;
        let mut response = String::new();
        std::io::BufReader::new(&mut *stream)
            .read_line(&mut response)
            .ok()?;
        Some(response)
    }

    fn alive(&self) -> bool {
        self.response("probe")
            .is_some_and(|response| response == format!("alive {}\n", self.token))
    }

    fn request_exit(&self) {
        if let Ok(mut stream) = self.channel.lock() {
            let _ = stream.write_all(format!("exit {}\n", self.token).as_bytes());
        }
    }
}

struct Process {
    pid: u32,
    port: u16,
    control: Option<Probe>,
    #[cfg(windows)]
    handle: std::os::windows::io::OwnedHandle,
    #[cfg(target_os = "linux")]
    pidfd: std::os::fd::OwnedFd,
    #[cfg(target_os = "linux")]
    start_time: Option<u64>,
}

impl Process {
    #[cfg(any(windows, target_os = "linux"))]
    fn retain(pid: u32) -> Self {
        #[cfg(windows)]
        {
            use std::os::windows::io::FromRawHandle;
            use windows_sys::Win32::System::Threading::{
                OpenProcess, PROCESS_SYNCHRONIZE, PROCESS_TERMINATE,
            };
            let handle = unsafe { OpenProcess(PROCESS_SYNCHRONIZE | PROCESS_TERMINATE, 0, pid) };
            assert!(!handle.is_null());
            Self {
                pid,
                port: 0,
                control: None,
                handle: unsafe { std::os::windows::io::OwnedHandle::from_raw_handle(handle) },
            }
        }
        #[cfg(target_os = "linux")]
        {
            Self {
                pid,
                port: 0,
                control: None,
                pidfd: open_pidfd(pid).expect("could not retain fixture pidfd"),
                start_time: None,
            }
        }
    }

    fn read(path: &Path) -> Self {
        wait_until(|| std::fs::read_to_string(path).is_ok_and(|record| record.ends_with('\n')));
        let record = std::fs::read_to_string(path).unwrap();
        let mut record = record.split_whitespace();
        let pid = record.next().unwrap().parse().unwrap();
        #[cfg(any(windows, target_os = "linux"))]
        let mut process = Self::retain(pid);
        #[cfg(all(unix, not(target_os = "linux")))]
        let mut process = Self {
            pid,
            port: 0,
            control: None,
        };
        let port = record.next().unwrap().parse().unwrap();
        let token = record.next().unwrap().to_owned();
        process.port = port;
        #[cfg(target_os = "linux")]
        {
            process.start_time =
                Some(process_start_time(pid).expect("fixture process missing from /proc"));
        }
        wait_until(|| {
            let Some(probe) = Probe::connect(port, token.clone()) else {
                return false;
            };
            process.control = Some(probe);
            process.control.as_ref().unwrap().alive()
        });
        process
    }

    fn probe(&self) -> Probe {
        self.control
            .as_ref()
            .expect("process observer has no authenticated probe")
            .clone()
    }

    fn alive(&self) -> bool {
        #[cfg(windows)]
        {
            use std::os::windows::io::AsRawHandle;
            use windows_sys::Win32::Foundation::WAIT_TIMEOUT;
            use windows_sys::Win32::System::Threading::WaitForSingleObject;
            unsafe { WaitForSingleObject(self.handle.as_raw_handle(), 0) == WAIT_TIMEOUT }
        }
        #[cfg(target_os = "linux")]
        {
            pidfd_alive(&self.pidfd)
        }
        #[cfg(all(unix, not(target_os = "linux")))]
        {
            self.control.as_ref().is_some_and(Probe::alive)
        }
    }

    fn assert_serving(&self) {
        assert!(self.alive(), "pid={} is not alive", self.pid);
        assert!(
            self.probe().alive(),
            "pid={} failed authenticated probe",
            self.pid
        );
    }

    fn request_exit(&self) {
        self.probe().request_exit();
    }
    #[cfg(target_os = "linux")]
    fn escape(&self) {
        let control = self.control.as_ref().unwrap();
        assert_eq!(
            control.response("setsid"),
            Some(format!("setsid-ok {}\n", control.token))
        );
    }

    #[cfg(target_os = "linux")]
    fn reaped(&self) -> bool {
        process_start_time(self.pid) != self.start_time
    }

    fn cleanup(&self) {
        #[cfg(windows)]
        {
            use std::os::windows::io::AsRawHandle;
            use windows_sys::Win32::System::Threading::TerminateProcess;
            if self.alive()
                && unsafe { TerminateProcess(self.handle.as_raw_handle(), 23) } == 0
                && self.alive()
            {
                eprintln!(
                    "failed to terminate observed fixture pid={}: {}",
                    self.pid,
                    std::io::Error::last_os_error()
                );
            }
        }
        #[cfg(target_os = "linux")]
        if self.alive() {
            if let Err(error) = signal_pidfd(&self.pidfd, libc::SIGKILL) {
                eprintln!("failed to kill observed fixture pid={}: {error}", self.pid);
            }
        }
        #[cfg(all(unix, not(target_os = "linux")))]
        if let Some(control) = &self.control {
            control.request_exit();
        }
        let deadline = Instant::now() + Duration::from_secs(3);
        while self.alive() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(20));
        }
        if self.alive() {
            if thread::panicking() {
                eprintln!(
                    "observed fixture pid={} remained alive after cleanup",
                    self.pid
                );
            } else {
                panic!(
                    "observed fixture pid={} remained alive after cleanup",
                    self.pid
                );
            }
        }
        #[cfg(target_os = "linux")]
        {
            reap_pidfd(&self.pidfd);
        }
    }
}

impl Drop for Process {
    fn drop(&mut self) {
        self.cleanup();
    }
}
#[cfg(target_os = "linux")]
fn reap_pidfd(pidfd: &std::os::fd::OwnedFd) {
    use std::os::fd::AsRawFd;
    let mut info = unsafe { std::mem::zeroed::<libc::siginfo_t>() };
    // A live guardian may already have reaped this child; ECHILD is harmless.
    unsafe {
        libc::waitid(
            libc::P_PIDFD,
            pidfd.as_raw_fd() as libc::id_t,
            &mut info,
            libc::WEXITED | libc::WNOHANG,
        );
    }
}

#[cfg(target_os = "linux")]
fn pidfd_alive(pidfd: &std::os::fd::OwnedFd) -> bool {
    use std::os::fd::AsRawFd;
    let mut descriptor = libc::pollfd {
        fd: pidfd.as_raw_fd(),
        events: libc::POLLIN,
        revents: 0,
    };
    unsafe { libc::poll(&mut descriptor, 1, 0) <= 0 }
}

#[cfg(target_os = "linux")]
fn signal_pidfd(pidfd: &std::os::fd::OwnedFd, signal: i32) -> std::io::Result<()> {
    use std::os::fd::AsRawFd;
    let result = unsafe {
        libc::syscall(
            libc::SYS_pidfd_send_signal,
            pidfd.as_raw_fd(),
            signal,
            std::ptr::null::<libc::siginfo_t>(),
            0,
        )
    };
    if result == -1 {
        Err(std::io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[cfg(target_os = "linux")]
fn process_parent_pid(pid: u32) -> Option<u32> {
    let stat = std::fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
    stat.rsplit_once(") ")?
        .1
        .split_whitespace()
        .nth(1)?
        .parse()
        .ok()
}

#[cfg(target_os = "linux")]
fn open_pidfd(pid: u32) -> Option<std::os::fd::OwnedFd> {
    use std::os::fd::FromRawFd;
    let fd = unsafe { libc::syscall(libc::SYS_pidfd_open, pid as libc::pid_t, 0) as i32 };
    (fd >= 0).then(|| unsafe { std::os::fd::OwnedFd::from_raw_fd(fd) })
}

#[cfg(target_os = "linux")]
fn process_start_time(pid: u32) -> Option<u64> {
    let stat = std::fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
    stat.rsplit_once(") ")?
        .1
        .split_whitespace()
        .nth(19)?
        .parse()
        .ok()
}
#[cfg(target_os = "linux")]
#[test]
fn guardian_death_kills_its_direct_child_via_pdeathsig() {
    use std::io::{BufRead, BufReader};

    let directory = tempfile::tempdir().unwrap();
    let mut command = Command::new(HARNESS);
    command
        .args(["parent", directory.path().to_str().unwrap(), "1", "plain"])
        .stdin(Stdio::piped())
        .stderr(Stdio::piped());
    let mut parent = Parent::new(command.spawn().unwrap());
    let mut stderr = BufReader::new(parent.child.stderr.take().unwrap());
    let mut line = String::new();
    loop {
        line.clear();
        assert!(stderr.read_line(&mut line).unwrap() > 0);
        if line.contains("runtime owner guardian pid=") {
            break;
        }
    }
    let guardian_pid: u32 = line
        .split("guardian pid=")
        .nth(1)
        .unwrap()
        .split_whitespace()
        .next()
        .unwrap()
        .parse()
        .unwrap();
    let child_pid: u32 = line
        .split("child pid=")
        .nth(1)
        .unwrap()
        .trim()
        .parse()
        .unwrap();
    let guardian = open_pidfd(guardian_pid).expect("could not retain guardian pidfd");
    assert_eq!(process_parent_pid(guardian_pid), Some(parent.child.id()));
    let child = Process::read(&directory.path().join("child-0"));
    assert_eq!(child.pid, child_pid);
    child.assert_serving();

    let _ = signal_pidfd(&guardian, libc::SIGKILL);
    wait_until(|| !pidfd_alive(&guardian));
    wait_until(|| !child.alive());
    parent.stop(true);
}

#[cfg(target_os = "linux")]
#[test]
fn setsid_escape_failure_unwinds_and_cleans_exact_fixtures() {
    let directory = tempfile::tempdir().unwrap();
    let mut parent = Parent::start(directory.path(), 1, "escape");
    let processes = vec![
        Process::read(&directory.path().join("child-0")),
        Process::read(&directory.path().join("grandchild-0")),
    ];
    for process in &processes {
        process.assert_serving();
    }
    processes[1].escape();
    let probes: Vec<_> = processes.iter().map(Process::probe).collect();
    parent.stop(true);
    wait_until(|| !processes[0].alive());
    assert!(
        processes[1].alive(),
        "setsid grandchild did not escape the owned process group"
    );

    let failure = catch_unwind(AssertUnwindSafe(move || {
        assert!(
            !processes[1].alive(),
            "uncontained setsid grandchild survived parent death"
        );
    }));
    assert!(
        failure.is_err(),
        "uncontained fixture did not fail observation"
    );
    wait_until(|| probes.iter().all(|probe| !probe.alive()));
}

#[cfg(any(windows, target_os = "linux"))]
#[test]
fn withheld_probe_failure_cleans_exact_fixture() {
    let directory = tempfile::tempdir().unwrap();
    let mut parent = Parent::start(directory.path(), 1, "uncontained");
    let root = Process::read(&directory.path().join("child-0"));
    let record_path = directory.path().join("grandchild-0");
    wait_until(|| record_path.exists());
    let original = std::fs::read_to_string(&record_path).unwrap();
    let mut fields = original.split_whitespace();
    let pid: u32 = fields.next().unwrap().parse().unwrap();
    let port: u16 = fields.next().unwrap().parse().unwrap();
    let token = fields.next().unwrap();
    let identity = Process::retain(pid);
    assert!(
        identity.alive(),
        "uncontained fixture exited before observer setup"
    );
    std::fs::write(&record_path, format!("{pid} {port} withheld-{token}\n")).unwrap();

    let failure = catch_unwind(AssertUnwindSafe(|| Process::read(&record_path)));
    let survived_setup = identity.alive();
    identity.cleanup();
    assert!(TcpListener::bind(("127.0.0.1", port)).is_ok());
    parent.stop(true);
    root.cleanup();
    assert!(TcpListener::bind(("127.0.0.1", root.port)).is_ok());
    assert!(failure.is_err(), "withheld probe unexpectedly passed setup");
    eprintln!(
        "partial handshake pid={pid} setup_failed=true survived_before_safety_cleanup={survived_setup} listener_released=true"
    );
    assert!(
        !survived_setup,
        "fixture survived failed observer setup before external exact-identity cleanup"
    );
}

#[test]
fn uncontained_launcher_failure_unwinds_and_cleans_exact_fixtures() {
    let directory = tempfile::tempdir().unwrap();
    let mut parent = Parent::start(directory.path(), 1, "uncontained");
    let processes = vec![
        Process::read(&directory.path().join("child-0")),
        Process::read(&directory.path().join("grandchild-0")),
    ];
    let probes: Vec<_> = processes.iter().map(Process::probe).collect();
    parent.stop(true);
    for process in &processes {
        process.assert_serving();
    }
    let failure = catch_unwind(AssertUnwindSafe(move || {
        assert!(
            processes.iter().all(|process| !process.alive()),
            "uncontained fixture survived launcher death"
        );
    }));
    assert!(
        failure.is_err(),
        "uncontained fixture did not fail observation"
    );
    wait_until(|| probes.iter().all(|probe| !probe.alive()));
}

#[test]
fn owned_stdio_pipes_are_exact_and_null_stdout_does_not_block_large_writes() {
    let directory = tempfile::tempdir().unwrap();
    let stdout = directory.path().join("stdout");
    let stderr = directory.path().join("stderr");
    let mut helper = Parent::new(
        Command::new(HARNESS)
            .args([
                "io-capture",
                stdout.to_str().unwrap(),
                stderr.to_str().unwrap(),
            ])
            .stdin(Stdio::piped())
            .spawn()
            .unwrap(),
    );
    wait_until(|| helper.child.try_wait().unwrap().is_some());
    assert!(helper.child.wait().unwrap().success());
    assert_eq!(std::fs::read(&stdout).unwrap(), b"captured stdout\0\xff\n");
    assert_eq!(std::fs::read(&stderr).unwrap(), b"captured stderr\0\xfe\n");

    let stdout = directory.path().join("null-stdout");
    let stderr = directory.path().join("null-stderr");
    let mut helper = Parent::new(
        Command::new(HARNESS)
            .args([
                "io-null",
                stdout.to_str().unwrap(),
                stderr.to_str().unwrap(),
            ])
            .stdin(Stdio::piped())
            .spawn()
            .unwrap(),
    );
    wait_until(|| helper.child.try_wait().unwrap().is_some());
    assert!(helper.child.wait().unwrap().success());
    assert!(std::fs::read(&stdout).unwrap().is_empty());
    assert_eq!(std::fs::read(&stderr).unwrap(), b"captured stderr\n");
}

#[test]
fn abrupt_parent_death_kills_multiple_owned_trees_and_preserves_external_process() {
    let directory = tempfile::tempdir().unwrap();
    let external_record = directory.path().join("external");
    let _external = Parent::new(
        Command::new(HARNESS)
            .arg("external-worker")
            .arg(&external_record)
            .stdin(Stdio::piped())
            .spawn()
            .unwrap(),
    );
    let external = Process::read(&external_record);
    let mut parent = Parent::start(directory.path(), 2, "tree");
    let mut owned = Vec::new();
    for index in 0..2 {
        owned.push(Process::read(
            &directory.path().join(format!("child-{index}")),
        ));
        owned.push(Process::read(
            &directory.path().join(format!("grandchild-{index}")),
        ));
    }
    for process in &owned {
        process.assert_serving();
    }
    external.assert_serving();
    eprintln!(
        "hard parent={} owned={:?} external={}",
        parent.child.id(),
        owned.iter().map(|p| p.pid).collect::<Vec<_>>(),
        external.pid
    );
    parent.stop(true);
    wait_until(|| owned.iter().all(|process| !process.alive()));
    for process in &owned {
        assert!(
            TcpListener::bind(("127.0.0.1", process.port)).is_ok(),
            "owned port {} not reusable",
            process.port
        );
    }
    external.assert_serving();
}

#[test]
fn normal_shutdown_and_restart_release_owned_listeners() {
    for _ in 0..2 {
        let directory = tempfile::tempdir().unwrap();
        let mut parent = Parent::start(directory.path(), 2, "tree");
        let owned: Vec<_> = (0..2)
            .flat_map(|index| {
                [
                    Process::read(&directory.path().join(format!("child-{index}"))),
                    Process::read(&directory.path().join(format!("grandchild-{index}"))),
                ]
            })
            .collect();
        for process in &owned {
            process.assert_serving();
        }
        parent.stop(false);
        wait_until(|| owned.iter().all(|process| !process.alive()));
        for process in &owned {
            assert!(TcpListener::bind(("127.0.0.1", process.port)).is_ok());
            #[cfg(target_os = "linux")]
            assert!(
                process.reaped(),
                "normal shutdown left owned pid={} unreaped",
                process.pid
            );
        }
    }
}

#[test]
fn owned_child_exit_crash_and_restart_are_reaped() {
    let owner = runtime::RuntimeOwner::new().unwrap();
    for abrupt in [false, true] {
        let directory = tempfile::tempdir().unwrap();
        let record = directory.path().join("child");
        let mut command = Command::new(HARNESS);
        command.arg("worker").arg(&record);
        let mut child = owner
            .spawn(
                command.get_program(),
                command.get_args(),
                runtime::RuntimeStdout::Piped,
            )
            .unwrap();
        let observed = Process::read(&record);
        assert_eq!(child.id(), observed.pid);
        observed.assert_serving();
        if abrupt {
            child.kill().unwrap();
        } else {
            observed.request_exit();
        }
        wait_until(|| child.try_wait().unwrap().is_some());
        let status = child.wait().unwrap();
        if abrupt {
            assert!(!status.success());
            #[cfg(unix)]
            {
                use std::os::unix::process::ExitStatusExt;
                assert_eq!(status.signal(), Some(libc::SIGKILL));
            }
        } else {
            assert_eq!(status.code(), Some(0));
        }
        child.kill().unwrap(); // already exited: no spurious shutdown failure
        assert!(!observed.alive());
        assert!(TcpListener::bind(("127.0.0.1", observed.port)).is_ok());
    }
}

#[test]
fn native_arguments_survive_quoting_and_failed_spawn_executes_nothing() {
    let owner = runtime::RuntimeOwner::new().unwrap();
    let arguments = [
        "",
        "a b",
        "quote\"here",
        "back\\slash\\",
        "ends in slash \\",
        "雪",
    ];
    let mut command = Command::new(HARNESS);
    command.arg("echo").args(arguments);
    let mut child = owner
        .spawn(
            command.get_program(),
            command.get_args(),
            runtime::RuntimeStdout::Piped,
        )
        .unwrap();
    let mut output = String::new();
    child
        .stdout
        .take()
        .unwrap()
        .read_to_string(&mut output)
        .unwrap();
    assert!(child.wait().unwrap().success());
    assert_eq!(
        serde_json::from_str::<Vec<String>>(output.lines().last().unwrap()).unwrap(),
        arguments
    );
    let directory = tempfile::tempdir().unwrap();
    let missing = Command::new(directory.path().join("missing-owned-executable"));
    assert!(owner
        .spawn(
            missing.get_program(),
            missing.get_args(),
            runtime::RuntimeStdout::Piped,
        )
        .is_err());
    let mut command = Command::new(HARNESS);
    command.args(["exit", "0"]);
    assert!(
        owner
            .spawn(
                command.get_program(),
                command.get_args(),
                runtime::RuntimeStdout::Piped,
            )
            .unwrap()
            .wait()
            .unwrap()
            .success(),
        "failed startup must not poison subsequent ownership"
    );
}

#[cfg(windows)]
#[test]
fn nested_job_keeps_inner_parent_death_ownership() {
    let directory = tempfile::tempdir().unwrap();
    let outer = runtime::RuntimeOwner::new().unwrap();
    let mut command = Command::new(HARNESS);
    command
        .arg("parent")
        .arg(directory.path())
        .args(["1", "tree", "linger"]);
    let mut parent = outer
        .spawn(
            command.get_program(),
            command.get_args(),
            runtime::RuntimeStdout::Piped,
        )
        .unwrap();
    runtime::log_output(&mut parent, "nested-parent");
    wait_until(|| directory.path().join("parent-ready").exists());
    let child = Process::read(&directory.path().join("child-0"));
    let grandchild = Process::read(&directory.path().join("grandchild-0"));
    child.assert_serving();
    grandchild.assert_serving();
    parent.kill().unwrap();
    parent.wait().unwrap();
    // The outer job is still open: the inner desktop job alone must kill these.
    wait_until(|| !child.alive() && !grandchild.alive());
}
