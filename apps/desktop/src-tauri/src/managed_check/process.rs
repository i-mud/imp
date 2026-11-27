use std::ffi::OsStr;
use std::fs::File;
use std::io::{self, Read, Write};
use std::process::{Command, ExitStatus};
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use super::{protocol, CheckResult};
use crate::runtime::{
    OwnedChild, RuntimeOwner, SshUnavailable, DIAGNOSTIC_CLEANUP, DIAGNOSTIC_POLL,
};
use crate::tunnel::{ssh_destination_args, strict_host_key_args};
use crate::tunnel_config::ManagedDestination;

const STDERR_LIMIT: usize = 4096;

pub(super) fn run(
    destination: &ManagedDestination,
    deadline: Instant,
    cancelled: &AtomicBool,
) -> CheckResult {
    run_command(
        &ssh_command(destination, None),
        deadline,
        cancelled,
        |_| {},
        |_| {},
    )
}

#[cfg(feature = "runtime-acceptance")]
#[allow(dead_code)] // Used by the acceptance harness's copy of this module.
pub(crate) fn run_fixture(
    destination: &ManagedDestination,
    deadline: Instant,
    cancelled: &AtomicBool,
    program: &OsStr,
    config: &OsStr,
    on_spawn: impl FnOnce(u32),
    on_cleanup: impl FnOnce(Duration),
) -> CheckResult {
    let command = ssh_command(destination, Some(config));
    let mut fixture = Command::new(program);
    fixture.args(command.get_args());
    run_command(&fixture, deadline, cancelled, on_spawn, on_cleanup)
}

fn ssh_command(destination: &ManagedDestination, config: Option<&OsStr>) -> Command {
    let mut command = Command::new("ssh");
    if let Some(config) = config {
        command.arg("-F").arg(config);
    }
    command.args(["-T", "-S", "none", "-W", "127.0.0.1:8787"]);
    for option in [
        "SessionType=none",
        "RemoteCommand=none",
        "PermitLocalCommand=no",
        "StdinNull=no",
        "ClearAllForwardings=yes",
        "ExitOnForwardFailure=yes",
        "Tunnel=no",
        "ForwardAgent=no",
        "ForwardX11=no",
        "ControlMaster=no",
        "ControlPersist=no",
        "ForkAfterAuthentication=no",
        "BatchMode=yes",
        "ConnectionAttempts=1",
        "AddKeysToAgent=no",
        // Suppress remote banners/reasons. Only these client source sites can emit evidence.
        "LogLevel=QUIET",
        concat!(
            "LogVerbose=*sshconnect2.c:verify_host_key_callback():*,",
            "*sshconnect2.c:userauth():*,*ssh.c:ssh_stdio_confirm():*"
        ),
    ] {
        command.arg("-o").arg(option);
    }
    strict_host_key_args(&mut command);
    ssh_destination_args(&mut command, destination);
    command
}

fn run_command(
    command: &Command,
    deadline: Instant,
    cancelled: &AtomicBool,
    on_spawn: impl FnOnce(u32),
    on_cleanup: impl FnOnce(Duration),
) -> CheckResult {
    let check = || stop_reason(cancelled.load(Ordering::Acquire), Instant::now(), deadline);
    if let Some(result) = check() {
        return result;
    }
    let Ok(owner) = RuntimeOwner::new() else {
        return CheckResult::ConnectionFailed;
    };
    let mut child =
        match owner.spawn_duplex_interruptible(command.get_program(), command.get_args(), &|| {
            match check() {
                Some(CheckResult::Cancelled) => Err(io::ErrorKind::ConnectionAborted.into()),
                Some(_) => Err(io::ErrorKind::TimedOut.into()),
                None => Ok(()),
            }
        }) {
            Ok(child) => child,
            Err(error) => return check().unwrap_or_else(|| spawn_failure(&error)),
        };
    let (result, cleanup_started) = match Duplex::new(&mut child, &owner, cancelled, deadline) {
        Ok(mut stream) => {
            on_spawn(stream.child.id());
            let result = protocol::exchange(&mut stream, deadline);
            let result = if result == CheckResult::ConnectionFailed && !stream.received {
                stream.ssh_failure().unwrap_or(result)
            } else {
                result
            };
            (
                check().unwrap_or(result),
                stream.retirement_started.unwrap_or_else(Instant::now),
            )
        }
        Err(_) => (CheckResult::ConnectionFailed, Instant::now()),
    };

    let retired = retire(&owner, &mut child, cleanup_started + DIAGNOSTIC_CLEANUP);
    on_cleanup(cleanup_started.elapsed());
    if retired {
        result
    } else {
        eprintln!(
            "imp: managed connection check could not confirm retirement within cleanup budget"
        );
        CheckResult::ConnectionFailed
    }
}

fn retire(owner: &RuntimeOwner, child: &mut OwnedChild, deadline: Instant) -> bool {
    if matches!(owner.diagnostic_retired(child), Ok(true)) {
        return true;
    }
    if owner.begin_diagnostic_retirement(child).is_err() {
        return false;
    }
    loop {
        match owner.diagnostic_retired(child) {
            Ok(true) => return true,
            Ok(false) if Instant::now() < deadline => pause(deadline),
            _ => return false,
        }
    }
}

fn pause(deadline: Instant) {
    thread::park_timeout(DIAGNOSTIC_POLL.min(deadline.saturating_duration_since(Instant::now())));
}

fn stop_reason(cancelled: bool, now: Instant, deadline: Instant) -> Option<CheckResult> {
    if cancelled {
        Some(CheckResult::Cancelled)
    } else if now >= deadline {
        Some(CheckResult::Timeout)
    } else {
        None
    }
}

fn spawn_failure(error: &io::Error) -> CheckResult {
    if error
        .get_ref()
        .is_some_and(|cause| cause.is::<SshUnavailable>())
    {
        CheckResult::SshUnavailable
    } else {
        CheckResult::ConnectionFailed
    }
}

struct Duplex<'a> {
    stdin: File,
    stdout: File,
    stderr: File,
    // Overflow and read errors discard evidence but continue draining the pipe.
    diagnostic: Option<Vec<u8>>,
    stderr_eof: bool,
    received: bool,
    exit: Option<ExitStatus>,
    retirement_started: Option<Instant>,
    child: &'a mut OwnedChild,
    owner: &'a RuntimeOwner,
    cancelled: &'a AtomicBool,
    deadline: Instant,
}

impl<'a> Duplex<'a> {
    fn new(
        child: &'a mut OwnedChild,
        owner: &'a RuntimeOwner,
        cancelled: &'a AtomicBool,
        deadline: Instant,
    ) -> io::Result<Self> {
        let (Some(stdin), Some(stdout), Some(stderr)) =
            (child.stdin.take(), child.stdout.take(), child.stderr.take())
        else {
            return Err(io::Error::other("missing diagnostic pipes"));
        };
        for pipe in [&stdin, &stdout, &stderr] {
            nonblocking(pipe)?;
        }
        Ok(Self {
            stdin,
            stdout,
            stderr,
            diagnostic: Some(Vec::new()),
            stderr_eof: false,
            received: false,
            exit: None,
            retirement_started: None,
            child,
            owner,
            cancelled,
            deadline,
        })
    }

    fn pump(&mut self) -> io::Result<()> {
        if self.cancelled.load(Ordering::Acquire) {
            return Err(io::ErrorKind::ConnectionAborted.into());
        }
        let now = Instant::now();
        if now >= self.deadline {
            return Err(io::ErrorKind::TimedOut.into());
        }
        if self
            .retirement_started
            .is_some_and(|started| now >= started + DIAGNOSTIC_CLEANUP)
        {
            return Err(io::Error::other("runtime retirement deadline expired"));
        }
        if self.exit.is_none() {
            if let Some(status) = self.child.try_wait()? {
                // The Windows leader can exit before a descendant that holds its pipes.
                self.retirement_started = Some(now);
                self.owner.begin_diagnostic_retirement(self.child)?;
                self.exit = Some(status);
            }
        }
        self.drain_stderr();
        Ok(())
    }

    fn drain_stderr(&mut self) {
        if self.stderr_eof {
            return;
        }
        let mut buffer = [0; STDERR_LIMIT];
        // A continually writing child cannot starve stdout or cancellation.
        for _ in 0..4 {
            match pipe_read(&mut self.stderr, &mut buffer) {
                Ok(0) => {
                    self.stderr_eof = true;
                    break;
                }
                Ok(count) => {
                    if let Some(transcript) = &mut self.diagnostic {
                        if transcript.len() + count <= STDERR_LIMIT {
                            transcript.extend_from_slice(&buffer[..count]);
                        } else {
                            self.diagnostic = None;
                        }
                    }
                }
                Err(error) if retry(&error) => break,
                Err(_) => {
                    self.diagnostic = None;
                    self.stderr_eof = true;
                    break;
                }
            }
        }
    }

    fn ssh_failure(&mut self) -> Option<CheckResult> {
        // Pre-channel failure requires actual stdout EOF, not merely a write failure.
        if !matches!(self.read(&mut [0; 1]), Ok(0)) {
            return None;
        }
        loop {
            self.pump().ok()?;
            self.diagnostic.as_ref()?;
            if let Some(status) = self.exit {
                if status.code() != Some(255) {
                    return None;
                }
                if self.stderr_eof {
                    return ssh_fatal(self.diagnostic.as_deref()?, self.child.id());
                }
            }
            pause(self.deadline);
        }
    }
}

impl Read for Duplex<'_> {
    fn read(&mut self, bytes: &mut [u8]) -> io::Result<usize> {
        loop {
            self.pump()?;
            match pipe_read(&mut self.stdout, bytes) {
                Err(error) if retry(&error) => pause(self.deadline),
                Ok(count) => {
                    self.received |= count != 0;
                    return Ok(count);
                }
                Err(error) => return Err(error),
            }
        }
    }
}

impl Write for Duplex<'_> {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        if bytes.is_empty() {
            return Ok(0);
        }
        loop {
            self.pump()?;
            match self.stdin.write(bytes) {
                #[cfg(windows)]
                Ok(0) => pause(self.deadline), // A full byte-mode pipe writes no bytes.
                Err(error) if retry(&error) => pause(self.deadline),
                result => return result,
            }
        }
    }

    fn flush(&mut self) -> io::Result<()> {
        self.pump() // File has no user-space write buffering.
    }
}

fn retry(error: &io::Error) -> bool {
    matches!(
        error.kind(),
        io::ErrorKind::Interrupted | io::ErrorKind::WouldBlock
    )
}

#[cfg(unix)]
fn pipe_read(pipe: &mut File, bytes: &mut [u8]) -> io::Result<usize> {
    pipe.read(bytes)
}

#[cfg(windows)]
fn pipe_read(pipe: &mut File, bytes: &mut [u8]) -> io::Result<usize> {
    use std::os::windows::io::AsRawHandle;
    use std::ptr::null_mut;
    use windows_sys::Win32::Foundation::{
        ERROR_BROKEN_PIPE, ERROR_NO_DATA, ERROR_PIPE_NOT_CONNECTED,
    };
    use windows_sys::Win32::System::Pipes::PeekNamedPipe;

    match pipe.read(bytes) {
        Ok(0) if !bytes.is_empty() => {}
        Err(error) if error.raw_os_error() == Some(ERROR_NO_DATA as i32) => {}
        Err(error) if error.raw_os_error() == Some(ERROR_PIPE_NOT_CONNECTED as i32) => {
            return Ok(0)
        }
        result => return result,
    }

    // Windows can return zero for a connected empty pipe in PIPE_NOWAIT mode.
    // Only probe that case, after reading any bytes buffered before peer close.
    if unsafe {
        PeekNamedPipe(
            pipe.as_raw_handle(),
            null_mut(),
            0,
            null_mut(),
            null_mut(),
            null_mut(),
        )
    } != 0
    {
        return Err(io::ErrorKind::WouldBlock.into());
    }
    let error = io::Error::last_os_error();
    if matches!(
        error.raw_os_error().map(|code| code as u32),
        Some(ERROR_BROKEN_PIPE | ERROR_PIPE_NOT_CONNECTED)
    ) {
        Ok(0)
    } else {
        Err(error)
    }
}

#[cfg(unix)]
fn nonblocking(pipe: &File) -> io::Result<()> {
    use std::os::fd::AsRawFd;

    let fd = pipe.as_raw_fd();
    // Only the parent end is nonblocking; OpenSSH sees the ordinary child end.
    let flags = unsafe { libc::fcntl(fd, libc::F_GETFL) };
    if flags < 0 || unsafe { libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK) } < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

#[cfg(windows)]
fn nonblocking(pipe: &File) -> io::Result<()> {
    use std::os::windows::io::AsRawHandle;
    use std::ptr::null;
    use windows_sys::Win32::System::Pipes::{SetNamedPipeHandleState, PIPE_NOWAIT};

    // The Windows API supports changing the wait mode of anonymous CreatePipe handles.
    if unsafe { SetNamedPipeHandleState(pipe.as_raw_handle(), &PIPE_NOWAIT, null(), null()) } == 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

fn ssh_fatal(bytes: &[u8], pid: u32) -> Option<CheckResult> {
    let mut records = std::str::from_utf8(bytes)
        .ok()?
        .strip_suffix('\n')?
        .rsplit('\n');
    let (site, message) = ssh_record(records.next()?, pid)?;
    // LogVerbose forces debug records from these functions too. For the vetted
    // clients, only userauth() logs before fatal/success, using these two formats.
    if !records.all(|line| {
        match line
            .strip_prefix("debug2: ")
            .and_then(|line| ssh_record(line, pid))
        {
            Some(("sshconnect2.c:userauth", "we did not send a packet, disable method")) => true,
            Some(("sshconnect2.c:userauth", message)) => message
                .strip_prefix("we sent a ")
                .and_then(|message| message.strip_suffix(" packet, wait for reply"))
                .is_some_and(|method| {
                    matches!(
                        method,
                        "publickey"
                            | "password"
                            | "keyboard-interactive"
                            | "hostbased"
                            | "gssapi-with-mic"
                            | "none"
                    )
                }),
            _ => false,
        }
    }) {
        return None;
    }
    match (site, message) {
        ("sshconnect2.c:verify_host_key_callback", "Host key verification failed.") => {
            Some(CheckResult::HostVerification)
        }
        ("ssh.c:ssh_stdio_confirm", "stdio forwarding failed") => {
            Some(CheckResult::ForwardingUnavailable)
        }
        ("sshconnect2.c:userauth", _) => {
            let (destination, methods) = message.split_once(": Permission denied (")?;
            let (user, host) = destination.rsplit_once('@')?;
            let methods = methods.strip_suffix(").")?;
            (!user.is_empty()
                && !host.is_empty()
                && methods
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b",-_@.".contains(&b)))
            .then_some(CheckResult::Authentication)
        }
        _ => None,
    }
}

fn ssh_record(line: &str, pid: u32) -> Option<(&str, &str)> {
    let line = line.strip_suffix('\r').unwrap_or(line);
    if line.is_empty() || line.chars().any(char::is_control) {
        return None;
    }
    let (site, line) = line.split_once("():")?;
    let (number, tail) = line.split_once(" (")?;
    if number.is_empty() || !number.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let (tag, message) = tail.split_once("): ")?;
    // OpenSSH 9.x tags only pid; 10.x prefixes bin. Match the exact spawned pid.
    let owner = tag.strip_prefix("pid=").or_else(|| {
        let (bin, owner) = tag.strip_prefix("bin=")?.rsplit_once(", pid=")?;
        (!bin.is_empty()).then_some(owner)
    })?;
    if owner.parse::<u32>().ok()? != pid {
        return None;
    }
    let (path, site) = site.rsplit_once(['/', '\\']).unwrap_or(("", site));
    if !path
        .bytes()
        .all(|b| b.is_ascii_alphanumeric() || b":/\\._-".contains(&b))
    {
        return None;
    }
    Some((site, message))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tunnel_config::SshDestination;

    #[cfg(windows)]
    #[test]
    fn windows_anonymous_pipes_do_not_block_when_empty_or_full() {
        use std::os::windows::io::FromRawHandle;
        use std::ptr::{null, null_mut};
        use windows_sys::Win32::System::Pipes::CreatePipe;

        let (mut read, mut write) = (null_mut(), null_mut());
        assert_ne!(
            unsafe { CreatePipe(&mut read, &mut write, null(), 4096) },
            0
        );
        let mut reader = unsafe { File::from_raw_handle(read) };
        let mut writer = unsafe { File::from_raw_handle(write) };
        nonblocking(&reader).unwrap();
        nonblocking(&writer).unwrap();
        assert_eq!(
            pipe_read(&mut reader, &mut [0; 1]).unwrap_err().kind(),
            io::ErrorKind::WouldBlock
        );
        assert_eq!(writer.write(b"x").unwrap(), 1);
        let mut first = [0; 1];
        assert_eq!(pipe_read(&mut reader, &mut first).unwrap(), 1);
        assert_eq!(&first, b"x");

        let mut written = 0;
        let full = (0..32).any(|_| match writer.write(&[1; 4096]) {
            Ok(0) => true,
            Ok(count) => {
                written += count;
                false
            }
            Err(error) if retry(&error) => true,
            Err(error) => panic!("unexpected pipe write error: {error}"),
        });
        assert!(full && written != 0);
        drop(writer);
        let mut drained = 0;
        let mut bytes = [0; 4096];
        loop {
            let count = pipe_read(&mut reader, &mut bytes).unwrap();
            if count == 0 {
                break;
            }
            drained += count;
        }
        assert_eq!(drained, written);
    }

    #[test]
    fn ssh_arguments_keep_owned_forward_and_target_policy() {
        let destination = ManagedDestination::Server(SshDestination {
            host: "2001:db8::1".into(),
            username: "fixture".into(),
            port: Some(2222),
        });
        let command = ssh_command(&destination, None);
        let args: Vec<_> = command
            .get_args()
            .map(|arg| arg.to_str().unwrap())
            .collect();
        assert_eq!(command.get_program(), "ssh");
        assert_eq!(
            &args[..6],
            ["-T", "-S", "none", "-W", "127.0.0.1:8787", "-o"]
        );
        assert_eq!(
            &args[args.len() - 6..],
            ["-l", "fixture", "-p", "2222", "--", "2001:db8::1"]
        );
        for option in [
            "StrictHostKeyChecking=yes",
            "NoHostAuthenticationForLocalhost=no",
            "VerifyHostKeyDNS=no",
            "UpdateHostKeys=no",
            "ClearAllForwardings=yes",
            "Tunnel=no",
            "SessionType=none",
            "RemoteCommand=none",
            "ForkAfterAuthentication=no",
            "StdinNull=no",
            "LogLevel=QUIET",
        ] {
            assert!(args.windows(2).any(|pair| pair == ["-o", option]));
        }
        assert!(!args
            .iter()
            .any(|arg| matches!(*arg, "-L" | "-R" | "-D" | "-O" | "-F")));
        let alias = ManagedDestination::Alias("fixture-alias".into());
        let command = ssh_command(&alias, None);
        let args: Vec<_> = command
            .get_args()
            .map(|arg| arg.to_str().unwrap())
            .collect();
        assert!(args.contains(&"StrictHostKeyChecking=yes"));
        assert_eq!(&args[args.len() - 2..], ["--", "fixture-alias"]);
    }

    #[test]
    fn only_vetted_client_fatal_records_are_evidence() {
        let trust = b"sshconnect2.c:verify_host_key_callback():105 (pid=42): Host key verification failed.\r\n";
        let auth = b"sshconnect2.c:userauth():542 (bin=ssh, pid=42): fixture@example: Permission denied (publickey).\n";
        let forward = b"src\\ssh.c:ssh_stdio_confirm():1887 (pid=42): stdio forwarding failed\r\n";
        let attempted = b"debug2: sshconnect2.c:userauth():561 (bin=ssh, pid=42): we sent a publickey packet, wait for reply\r\n";
        let skipped = b"debug2: src\\sshconnect2.c:userauth():564 (pid=42): we did not send a packet, disable method\n";
        let unknown = b"debug2: sshconnect2.c:userauth():564 (pid=42): a remote-shaped message\n";
        let wrong_site = b"debug2: packet.c:sshpkt_fatal():9 (pid=42): we did not send a packet, disable method\n";
        assert_eq!(ssh_fatal(trust, 42), Some(CheckResult::HostVerification));
        assert_eq!(ssh_fatal(auth, 42), Some(CheckResult::Authentication));
        assert_eq!(
            ssh_fatal(forward, 42),
            Some(CheckResult::ForwardingUnavailable)
        );
        assert_eq!(
            ssh_fatal(&[attempted.as_slice(), skipped, auth].concat(), 42),
            Some(CheckResult::Authentication)
        );
        assert_eq!(
            ssh_fatal(&[attempted.as_slice(), forward].concat(), 42),
            Some(CheckResult::ForwardingUnavailable)
        );
        assert_eq!(
            ssh_fatal(&[attempted.as_slice(), unknown, auth].concat(), 42),
            None
        );
        assert_eq!(ssh_fatal(&[wrong_site.as_slice(), auth].concat(), 42), None);
        assert_eq!(ssh_fatal(&[auth.as_slice(), trust].concat(), 42), None);
        assert_eq!(ssh_fatal(&[auth.as_slice(), unknown].concat(), 42), None);
        assert_eq!(ssh_fatal(trust, 41), None);
        let spoof = [b"Received disconnect:\n".as_slice(), trust].concat();
        assert_eq!(ssh_fatal(&spoof, 42), None);
        assert_eq!(ssh_fatal(&trust[..trust.len() - 1], 42), None);
        assert_eq!(ssh_fatal(b"Host key verification failed.\n", 42), None);
        assert_eq!(
            ssh_fatal(
                b"packet.c:sshpkt_fatal():1 (pid=42): stdio forwarding failed\n",
                42
            ),
            None
        );
    }

    #[test]
    fn deadline_and_cancellation_are_selected_without_waiting() {
        let now = Instant::now();
        let deadline = now + DIAGNOSTIC_POLL;
        assert_eq!(stop_reason(false, now, deadline), None);
        assert_eq!(
            stop_reason(true, now, deadline),
            Some(CheckResult::Cancelled)
        );
        assert_eq!(
            stop_reason(false, deadline, deadline),
            Some(CheckResult::Timeout)
        );
        assert_eq!(
            spawn_failure(&io::Error::new(io::ErrorKind::NotFound, SshUnavailable)),
            CheckResult::SshUnavailable
        );
        assert_eq!(
            spawn_failure(&io::ErrorKind::NotFound.into()),
            CheckResult::ConnectionFailed
        );
    }
}
