use std::env;
use std::ffi::{OsStr, OsString};
use std::fs::File;
use std::io::{self, Read, Write};
use std::os::fd::{AsRawFd, FromRawFd, IntoRawFd, OwnedFd};
#[cfg(test)]
use std::os::unix::ffi::{OsStrExt, OsStringExt};
use std::os::unix::net::UnixStream;
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::process::{Child, ChildStdin, Command, ExitStatus, Stdio};
use std::time::{Duration, Instant};

use super::{
    confirm_retirement, RuntimeStdout, SshUnavailable, DIAGNOSTIC_CLEANUP, DIAGNOSTIC_POLL,
};

#[cfg(not(test))]
const HELPER_MODE: &str = "--imp-runtime-owner";
#[cfg(not(test))]
const DUPLEX_HELPER_MODE: &str = "--imp-runtime-owner-duplex";
#[cfg(test)]
const TEST_COMMAND_ENV: &str = "IMP_RUNTIME_OWNER_TEST_COMMAND";
#[cfg(test)]
const TEST_CONTROL_FD_ENV: &str = "IMP_RUNTIME_OWNER_TEST_CONTROL_FD";
#[cfg(test)]
const TEST_STDIN_FD_ENV: &str = "IMP_RUNTIME_OWNER_TEST_STDIN_FD";
const CONTROL_OK: u8 = 0;
const CONTROL_ERROR: u8 = 1;
const CONTROL_EXEC_ERROR: u8 = 2;
const HELPER_FAILURE_EXIT: i32 = 125;
const HELPER_NO_CHILD_EXIT: i32 = 126;

#[derive(Debug)]
pub(crate) struct StartupRetirementUnconfirmed {
    _guardian: Child,
}

pub struct RuntimeOwner;

impl RuntimeOwner {
    pub fn new() -> io::Result<Self> {
        Ok(Self)
    }

    pub fn spawn<'a>(
        &self,
        program: &'a OsStr,
        args: impl IntoIterator<Item = &'a OsStr>,
        stdout: RuntimeStdout,
    ) -> io::Result<OwnedChild> {
        self.spawn_with_stdin(program, args, stdout, false, None)
    }

    #[cfg(feature = "runtime-acceptance")]
    #[allow(dead_code)] // Used by the acceptance harness's copy of this module.
    pub fn spawn_duplex<'a>(
        &self,
        program: &'a OsStr,
        args: impl IntoIterator<Item = &'a OsStr>,
    ) -> io::Result<OwnedChild> {
        self.spawn_with_stdin(program, args, RuntimeStdout::Piped, true, None)
    }

    pub(crate) fn spawn_duplex_interruptible<'a>(
        &self,
        program: &'a OsStr,
        args: impl IntoIterator<Item = &'a OsStr>,
        check: &dyn Fn() -> io::Result<()>,
    ) -> io::Result<OwnedChild> {
        check()?;
        self.spawn_with_stdin(program, args, RuntimeStdout::Piped, true, Some(check))
    }

    pub(crate) fn begin_diagnostic_retirement(&self, child: &mut OwnedChild) -> io::Result<()> {
        child.lifetime.take();
        Ok(())
    }

    pub(crate) fn diagnostic_retired(&self, child: &mut OwnedChild) -> io::Result<bool> {
        child.try_wait().map(|status| status.is_some())
    }

    fn spawn_with_stdin<'a>(
        &self,
        program: &'a OsStr,
        args: impl IntoIterator<Item = &'a OsStr>,
        stdout: RuntimeStdout,
        pipe_stdin: bool,
        check: Option<&dyn Fn() -> io::Result<()>>,
    ) -> io::Result<OwnedChild> {
        let executable = env::current_exe()?;
        let (mut control_reader, control_writer) = UnixStream::pair()?;
        control_reader.set_read_timeout(Some(if check.is_some() {
            DIAGNOSTIC_POLL
        } else {
            Duration::from_secs(10)
        }))?;
        let control_writer = move_above_stdio(control_writer.into())?;
        let control_fd = control_writer.as_raw_fd();
        // Guardian stdin is reserved for lifetime; send child stdin over a separate pipe.
        let (stdin_read, stdin_write) = if pipe_stdin {
            let (read, write) = io::pipe()?;
            (
                Some(move_above_stdio(read.into())?),
                Some(File::from(OwnedFd::from(write))),
            )
        } else {
            (None, None)
        };
        let stdin_fd = stdin_read.as_ref().map(AsRawFd::as_raw_fd);
        let mut guardian_command = Command::new(executable);
        let args = args.into_iter();

        #[cfg(test)]
        {
            guardian_command
                .args([
                    "--exact",
                    "runtime::platform::tests::lifetime_helper",
                    "--ignored",
                    "--nocapture",
                    "--quiet",
                ])
                .env(TEST_COMMAND_ENV, encode_command(program, args))
                .env(TEST_CONTROL_FD_ENV, control_fd.to_string());
            if let Some(fd) = stdin_fd {
                guardian_command.env(TEST_STDIN_FD_ENV, fd.to_string());
            } else {
                guardian_command.env_remove(TEST_STDIN_FD_ENV);
            }
        }

        #[cfg(not(test))]
        {
            if let Some(fd) = stdin_fd {
                guardian_command
                    .arg(DUPLEX_HELPER_MODE)
                    .arg(control_fd.to_string())
                    .arg(fd.to_string());
            } else {
                guardian_command
                    .arg(HELPER_MODE)
                    .arg(control_fd.to_string());
            }
            guardian_command.arg(program).args(args);
        }

        guardian_command
            .stdin(Stdio::piped())
            .stdout(match stdout {
                RuntimeStdout::Piped => Stdio::piped(),
                RuntimeStdout::Null => Stdio::null(),
            })
            .stderr(Stdio::piped());

        // A separate session keeps desktop terminal signals and unrelated
        // same-session processes out of the owned child's private group.
        unsafe {
            guardian_command.pre_exec(move || {
                if libc::setsid() == -1 {
                    return Err(io::Error::last_os_error());
                }
                set_cloexec(control_fd, false)?;
                if let Some(fd) = stdin_fd {
                    set_cloexec(fd, false)?;
                }
                Ok(())
            });
        }

        if let Some(check) = check {
            check()?;
        }
        let mut guardian = guardian_command.spawn()?;
        drop(control_writer);
        drop(stdin_read);

        let mut startup = || {
            let lifetime = guardian.stdin.take().expect("piped runtime lifetime");
            let stdout = guardian
                .stdout
                .take()
                .map(|stdout| unsafe { File::from_raw_fd(stdout.into_raw_fd()) });
            let stderr = unsafe {
                File::from_raw_fd(guardian.stderr.take().expect("piped stderr").into_raw_fd())
            };
            (
                read_start(&mut control_reader, check),
                lifetime,
                stdout,
                stderr,
            )
        };
        let (start, lifetime, stdout, stderr) = if check.is_some() {
            match catch_unwind(AssertUnwindSafe(startup)) {
                Ok(start) => start,
                Err(_) => {
                    drop(guardian.stdin.take());
                    return Err(StartupRetirementUnconfirmed::from_guardian(guardian));
                }
            }
        } else {
            startup()
        };
        let child_pid = match start {
            Ok(pid) => pid,
            Err(error) => {
                drop(lifetime);
                if check.is_some() {
                    return Err(StartupRetirementUnconfirmed::after_failed_start(
                        guardian,
                        error,
                        Instant::now() + DIAGNOSTIC_CLEANUP,
                    ));
                }
                let _ = guardian.wait();
                return Err(error);
            }
        };

        if check.is_none() {
            eprintln!(
                "imp: runtime owner guardian pid={} child pid={child_pid}",
                guardian.id()
            );
        }

        Ok(OwnedChild {
            lifetime: Some(lifetime),
            guardian,
            control: control_reader,
            exit_status: None,
            id: child_pid,
            stdin: stdin_write,
            stdout,
            stderr: Some(stderr),
        })
    }
}

impl StartupRetirementUnconfirmed {
    pub(crate) fn after_failed_start(
        mut guardian: Child,
        error: io::Error,
        deadline: Instant,
    ) -> io::Error {
        if confirm_retirement(deadline, || startup_retired(guardian.try_wait()?)) {
            error
        } else {
            Self::from_guardian(guardian)
        }
    }

    fn from_guardian(guardian: Child) -> io::Error {
        // Lifetime is already closed; only the original guardian may
        // signal its private group.
        io::Error::other(Self {
            _guardian: guardian,
        })
    }
}

impl std::fmt::Display for StartupRetirementUnconfirmed {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("runtime guardian startup retirement unconfirmed")
    }
}

impl std::error::Error for StartupRetirementUnconfirmed {}

fn startup_retired(status: Option<ExitStatus>) -> io::Result<bool> {
    match status {
        None => Ok(false),
        Some(status) if status.success() || status.code() == Some(HELPER_NO_CHILD_EXIT) => Ok(true),
        Some(_) => Err(io::Error::other(
            "runtime guardian did not confirm retirement",
        )),
    }
}

pub struct OwnedChild {
    // Dropping this sole writer is the teardown request, including Drop.
    lifetime: Option<ChildStdin>,
    guardian: Child,
    control: UnixStream,
    exit_status: Option<ExitStatus>,
    id: u32,
    pub stdin: Option<File>,
    pub stdout: Option<File>,
    pub stderr: Option<File>,
}

impl OwnedChild {
    pub fn id(&self) -> u32 {
        self.id
    }

    pub fn try_wait(&mut self) -> io::Result<Option<ExitStatus>> {
        if let Some(status) = self.exit_status {
            return Ok(Some(status));
        }
        if self.guardian.try_wait()?.is_none() {
            return Ok(None);
        }
        self.read_exit_status().map(Some)
    }

    pub fn kill(&mut self) -> io::Result<()> {
        self.lifetime.take();
        self.wait().map(|_| ())
    }

    pub fn wait(&mut self) -> io::Result<ExitStatus> {
        if let Some(status) = self.exit_status {
            return Ok(status);
        }
        self.guardian.wait()?;
        self.read_exit_status()
    }

    fn read_exit_status(&mut self) -> io::Result<ExitStatus> {
        let mut record = [0; 5];
        self.control.read_exact(&mut record)?;
        let value = i32::from_le_bytes(record[1..].try_into().unwrap());
        match record[0] {
            CONTROL_OK => {
                let status = ExitStatus::from_raw(value);
                self.exit_status = Some(status);
                Ok(status)
            }
            CONTROL_ERROR => Err(io::Error::from_raw_os_error(value)),
            _ => Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "runtime guardian sent an invalid exit record",
            )),
        }
    }
}

pub fn run_helper_if_requested() {
    #[cfg(not(test))]
    {
        let mut args = env::args_os();
        let _ = args.next();
        let mode = args.next();
        let duplex = mode.as_deref() == Some(OsStr::new(DUPLEX_HELPER_MODE));
        if !duplex && mode.as_deref() != Some(OsStr::new(HELPER_MODE)) {
            return;
        }

        let mut next_fd = || {
            args.next()
                .and_then(|arg| arg.to_str()?.parse::<i32>().ok())
        };
        let Some(fd) = next_fd() else {
            std::process::exit(HELPER_FAILURE_EXIT);
        };
        let stdin_fd = if duplex {
            let Some(fd) = next_fd() else {
                std::process::exit(HELPER_FAILURE_EXIT);
            };
            Some(fd)
        } else {
            None
        };
        let Some(program) = args.next() else {
            std::process::exit(HELPER_FAILURE_EXIT);
        };
        exit_guardian(run_guardian(fd, stdin_fd, program, args));
    }
}

fn run_guardian(
    fd: i32,
    stdin_fd: Option<i32>,
    program: OsString,
    args: impl Iterator<Item = OsString>,
) -> GuardianOutcome {
    if fd <= libc::STDERR_FILENO || unsafe { libc::fcntl(fd, libc::F_GETFD) } == -1 {
        return GuardianOutcome::NoChild;
    }
    let mut control = unsafe { File::from_raw_fd(fd) };
    if configure_guardian_signals().is_err() {
        return GuardianOutcome::NoChild;
    }
    if let Err(error) = set_cloexec(fd, true) {
        send_start_error(&mut control, &error);
        return GuardianOutcome::NoChild;
    }
    let stdin = match stdin_fd {
        Some(stdin_fd) => {
            let result = if stdin_fd <= libc::STDERR_FILENO || stdin_fd == fd {
                Err(io::Error::from_raw_os_error(libc::EBADF))
            } else {
                set_cloexec(stdin_fd, true)
            };
            if let Err(error) = result {
                send_start_error(&mut control, &error);
                return GuardianOutcome::NoChild;
            }
            Stdio::from(unsafe { File::from_raw_fd(stdin_fd) })
        }
        None => Stdio::null(),
    };
    #[cfg(target_os = "linux")]
    if let Err(error) = enable_subreaper() {
        send_start_error(&mut control, &error);
        return GuardianOutcome::NoChild;
    }

    #[cfg(target_os = "linux")]
    let guardian_pid = unsafe { libc::getpid() };
    let mut runtime = Command::new(program);
    runtime
        .args(args)
        .stdin(stdin)
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());

    #[cfg(test)]
    runtime
        .env_remove(TEST_COMMAND_ENV)
        .env_remove(TEST_CONTROL_FD_ENV)
        .env_remove(TEST_STDIN_FD_ENV);

    unsafe {
        runtime.pre_exec(move || {
            if libc::setpgid(0, 0) == -1 {
                return Err(io::Error::last_os_error());
            }
            set_signal(libc::SIGPIPE, libc::SIG_DFL)?;
            #[cfg(target_os = "linux")]
            {
                if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL, 0, 0, 0) == -1 {
                    return Err(io::Error::last_os_error());
                }
                if libc::getppid() != guardian_pid {
                    return Err(io::Error::from_raw_os_error(libc::ECANCELED));
                }
            }
            Ok(())
        });
    }

    if stdin_fd.is_some() && lifetime_closed_before_spawn(libc::STDIN_FILENO) {
        return GuardianOutcome::NoChild;
    }
    let child = match runtime.spawn() {
        Ok(child) => child,
        Err(error) => {
            write_start_error(&mut control, CONTROL_EXEC_ERROR, &error);
            return GuardianOutcome::NoChild;
        }
    };
    drop(runtime);
    let child_pid = child.id();
    let _ = write_start_ok(&mut control, child_pid);

    let monitor_error = wait_for_exit_or_lifetime_eof(child_pid).err();
    // WNOWAIT in child_exited keeps the leader PID and process group pinned through this signal.
    let group_error = signal_owned_group(child_pid).err();
    let child_status = wait_for_child(child_pid);
    #[cfg(target_os = "linux")]
    let descendants_error = if group_error.is_none() {
        reap_group_children(child_pid).err()
    } else {
        None
    };
    #[cfg(not(target_os = "linux"))]
    let descendants_error = if group_error.is_none() {
        wait_for_group_exit(child_pid).err()
    } else {
        None
    };

    drop(child);

    if let Some(error) = monitor_error.or(group_error).or(descendants_error) {
        let _ = write_finish_error(&mut control, &error);
        return GuardianOutcome::Failure;
    }
    match child_status {
        Ok(status) => {
            if write_finish_ok(&mut control, status).is_err() {
                return GuardianOutcome::Failure;
            }
            GuardianOutcome::Success
        }
        Err(error) => {
            let _ = write_finish_error(&mut control, &error);
            GuardianOutcome::Failure
        }
    }
}

enum GuardianOutcome {
    Success,
    NoChild,
    Failure,
}

fn exit_guardian(outcome: GuardianOutcome) -> ! {
    match outcome {
        GuardianOutcome::Success => std::process::exit(0),
        GuardianOutcome::NoChild => std::process::exit(HELPER_NO_CHILD_EXIT),
        GuardianOutcome::Failure => std::process::exit(HELPER_FAILURE_EXIT),
    }
}

fn wait_for_exit_or_lifetime_eof(child_pid: u32) -> io::Result<()> {
    loop {
        if child_exited(child_pid)? {
            return Ok(());
        }

        let mut descriptor = libc::pollfd {
            fd: libc::STDIN_FILENO,
            events: libc::POLLIN | libc::POLLHUP,
            revents: 0,
        };
        let result = unsafe { libc::poll(&mut descriptor, 1, 50) };
        if result == -1 {
            let error = io::Error::last_os_error();
            if error.kind() == io::ErrorKind::Interrupted {
                continue;
            }
            return Err(error);
        }
        if result == 0 {
            continue;
        }
        if descriptor.revents & libc::POLLNVAL != 0 {
            return Ok(());
        }
        if descriptor.revents & (libc::POLLIN | libc::POLLHUP | libc::POLLERR) != 0
            && lifetime_eof()?
        {
            return Ok(());
        }
    }
}

fn lifetime_closed_before_spawn(fd: libc::c_int) -> bool {
    let mut descriptor = libc::pollfd {
        fd,
        events: 0,
        revents: 0,
    };
    // poll reports hangup even without requesting readability. Treat poll failure as closed.
    (unsafe { libc::poll(&mut descriptor, 1, 0) }) != 0
}

#[cfg(test)]
#[test]
fn preclosed_guardian_lifetime_prevents_launch() {
    let (reader, writer) = io::pipe().unwrap();
    assert!(!lifetime_closed_before_spawn(reader.as_raw_fd()));
    drop(writer);
    assert!(lifetime_closed_before_spawn(reader.as_raw_fd()));
}

fn lifetime_eof() -> io::Result<bool> {
    let mut byte = 0u8;
    loop {
        let result = unsafe { libc::read(libc::STDIN_FILENO, (&mut byte as *mut u8).cast(), 1) };
        if result == 0 {
            return Ok(true);
        }
        if result > 0 {
            return Ok(false);
        }
        let error = io::Error::last_os_error();
        if error.kind() != io::ErrorKind::Interrupted {
            return Err(error);
        }
    }
}

fn child_exited(child_pid: u32) -> io::Result<bool> {
    loop {
        let mut info: libc::siginfo_t = unsafe { std::mem::zeroed() };
        let result = unsafe {
            libc::waitid(
                libc::P_PID,
                child_pid as libc::id_t,
                &mut info,
                libc::WEXITED | libc::WNOHANG | libc::WNOWAIT,
            )
        };
        if result == 0 {
            return Ok(unsafe { info.si_pid() } != 0);
        }
        let error = io::Error::last_os_error();
        if error.kind() != io::ErrorKind::Interrupted {
            return Err(error);
        }
    }
}

fn signal_owned_group(child_pid: u32) -> io::Result<()> {
    let group_result = signal_process(-(child_pid as libc::pid_t));
    let child_result = signal_process(child_pid as libc::pid_t);
    group_result.and(child_result)
}

fn signal_process(pid: libc::pid_t) -> io::Result<()> {
    loop {
        if unsafe { libc::kill(pid, libc::SIGKILL) } == 0 {
            return Ok(());
        }
        let error = io::Error::last_os_error();
        if error.kind() == io::ErrorKind::Interrupted {
            continue;
        }
        if error.raw_os_error() == Some(libc::ESRCH) {
            return Ok(());
        }
        return Err(error);
    }
}

fn wait_for_child(child_pid: u32) -> io::Result<i32> {
    let mut status = 0;
    loop {
        let result = unsafe { libc::waitpid(child_pid as libc::pid_t, &mut status, 0) };
        if result == child_pid as libc::pid_t {
            return Ok(status);
        }
        let error = io::Error::last_os_error();
        if error.kind() != io::ErrorKind::Interrupted {
            return Err(error);
        }
    }
}

#[cfg(target_os = "linux")]
fn enable_subreaper() -> io::Result<()> {
    if unsafe { libc::prctl(libc::PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) } == -1 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[cfg(target_os = "linux")]
fn reap_group_children(group_pid: u32) -> io::Result<()> {
    loop {
        let mut status = 0;
        let result = unsafe { libc::waitpid(-(group_pid as libc::pid_t), &mut status, 0) };
        if result > 0 {
            continue;
        }
        let error = io::Error::last_os_error();
        if error.kind() == io::ErrorKind::Interrupted {
            continue;
        }
        if error.raw_os_error() == Some(libc::ECHILD) {
            return Ok(());
        }
        return Err(error);
    }
}

#[cfg(not(target_os = "linux"))]
fn wait_for_group_exit(group_pid: u32) -> io::Result<()> {
    loop {
        if unsafe { libc::kill(-(group_pid as libc::pid_t), 0) } == -1 {
            let error = io::Error::last_os_error();
            if error.raw_os_error() == Some(libc::ESRCH) {
                return Ok(());
            }
            if error.kind() != io::ErrorKind::Interrupted {
                return Err(error);
            }
        }
        std::thread::sleep(Duration::from_millis(10));
    }
}

fn configure_guardian_signals() -> io::Result<()> {
    set_signal(libc::SIGPIPE, libc::SIG_IGN)?;
    set_signal(libc::SIGCHLD, libc::SIG_DFL)
}

fn set_signal(signal: libc::c_int, handler: libc::sighandler_t) -> io::Result<()> {
    if unsafe { libc::signal(signal, handler) } == libc::SIG_ERR {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

fn set_cloexec(fd: libc::c_int, enabled: bool) -> io::Result<()> {
    let flags = unsafe { libc::fcntl(fd, libc::F_GETFD) };
    if flags == -1 {
        return Err(io::Error::last_os_error());
    }
    let flags = if enabled {
        flags | libc::FD_CLOEXEC
    } else {
        flags & !libc::FD_CLOEXEC
    };
    if unsafe { libc::fcntl(fd, libc::F_SETFD, flags) } == -1 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

fn move_above_stdio(fd: OwnedFd) -> io::Result<OwnedFd> {
    if fd.as_raw_fd() > libc::STDERR_FILENO {
        return Ok(fd);
    }
    let duplicate = unsafe {
        libc::fcntl(
            fd.as_raw_fd(),
            libc::F_DUPFD_CLOEXEC,
            libc::STDERR_FILENO + 1,
        )
    };
    if duplicate == -1 {
        return Err(io::Error::last_os_error());
    }
    Ok(unsafe { OwnedFd::from_raw_fd(duplicate) })
}

fn send_start_error(control: &mut File, error: &io::Error) {
    write_start_error(control, CONTROL_ERROR, error);
}

fn write_start_error(control: &mut File, kind: u8, error: &io::Error) {
    let mut record = [0; 5];
    record[0] = kind;
    record[1..].copy_from_slice(&error.raw_os_error().unwrap_or(libc::EIO).to_le_bytes());
    let _ = control.write_all(&record);
}

fn write_start_ok(control: &mut File, child_pid: u32) -> io::Result<()> {
    let mut record = [0; 5];
    record[0] = CONTROL_OK;
    record[1..].copy_from_slice(&child_pid.to_le_bytes());
    control.write_all(&record)
}

fn read_start(
    control: &mut impl Read,
    check: Option<&dyn Fn() -> io::Result<()>>,
) -> io::Result<u32> {
    let mut record = [0; 5];
    if let Some(check) = check {
        let mut pending = &mut record[..];
        while !pending.is_empty() {
            check()?;
            match control.read(pending) {
                Ok(0) => return Err(io::ErrorKind::UnexpectedEof.into()),
                Ok(count) => pending = &mut pending[count..],
                Err(error)
                    if matches!(
                        error.kind(),
                        io::ErrorKind::Interrupted
                            | io::ErrorKind::WouldBlock
                            | io::ErrorKind::TimedOut
                    ) => {}
                Err(error) => return Err(error),
            }
        }
    } else {
        control.read_exact(&mut record)?;
    }
    match record[0] {
        CONTROL_OK => Ok(u32::from_le_bytes(record[1..].try_into().unwrap())),
        CONTROL_ERROR | CONTROL_EXEC_ERROR => {
            let code = i32::from_le_bytes(record[1..].try_into().unwrap());
            if check.is_some()
                && record[0] == CONTROL_EXEC_ERROR
                && matches!(
                    code,
                    libc::ENOENT | libc::ENOTDIR | libc::EACCES | libc::ENOEXEC
                )
            {
                return Err(io::Error::new(io::ErrorKind::NotFound, SshUnavailable));
            }
            Err(io::Error::from_raw_os_error(code))
        }
        _ => Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "runtime guardian sent an invalid start record",
        )),
    }
}

fn write_finish_ok(control: &mut File, status: i32) -> io::Result<()> {
    let mut record = [0; 5];
    record[0] = CONTROL_OK;
    record[1..].copy_from_slice(&status.to_le_bytes());
    control.write_all(&record)
}

fn write_finish_error(control: &mut File, error: &io::Error) -> io::Result<()> {
    let mut record = [0; 5];
    record[0] = CONTROL_ERROR;
    record[1..].copy_from_slice(&error.raw_os_error().unwrap_or(libc::EIO).to_le_bytes());
    control.write_all(&record)
}

#[cfg(test)]
fn encode_command<'a>(program: &'a OsStr, args: impl Iterator<Item = &'a OsStr>) -> String {
    serde_json::to_string(
        &std::iter::once(program)
            .chain(args)
            .map(OsStrExt::as_bytes)
            .collect::<Vec<_>>(),
    )
    .unwrap()
}

#[cfg(test)]
fn decode_command(encoded: &str) -> Option<(OsString, Vec<OsString>)> {
    let mut args = serde_json::from_str::<Vec<Vec<u8>>>(encoded)
        .ok()?
        .into_iter()
        .map(OsString::from_vec);
    let program = args.next()?;
    Some((program, args.collect()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn guardian_exit_alone_is_not_startup_retirement_evidence() {
        let code = |code| Some(ExitStatus::from_raw(code << 8));
        let expired = Instant::now();
        for status in [code(0), code(HELPER_NO_CHILD_EXIT)] {
            assert!(confirm_retirement(expired, || startup_retired(status)));
        }
        for status in [
            None,
            code(HELPER_FAILURE_EXIT),
            Some(ExitStatus::from_raw(libc::SIGKILL)),
        ] {
            assert!(!confirm_retirement(expired, || startup_retired(status)));
        }
    }

    #[test]
    fn panic_during_guardian_startup_keeps_the_original_guardian() {
        let executable = env::current_exe().unwrap();
        let calls = std::cell::Cell::new(0);
        let check = || {
            calls.set(calls.get() + 1);
            // After the two pre-spawn checks, the guardian is already owned.
            assert!(calls.get() < 3, "injected failure during startup read");
            Ok(())
        };
        let Err(error) = RuntimeOwner::new().unwrap().spawn_duplex_interruptible(
            executable.as_os_str(),
            [OsStr::new("--list")],
            &check,
        ) else {
            panic!("startup panic was treated as normal");
        };
        let mut evidence = error.downcast::<StartupRetirementUnconfirmed>().unwrap();
        evidence._guardian.wait().unwrap();
    }

    #[test]
    #[ignore]
    fn lifetime_helper() {
        let control_fd = env::var(TEST_CONTROL_FD_ENV)
            .ok()
            .and_then(|fd| fd.parse::<i32>().ok());
        let encoded = env::var(TEST_COMMAND_ENV).ok();
        let Some(control_fd) = control_fd else {
            std::process::exit(HELPER_FAILURE_EXIT);
        };
        let Some((program, args)) = encoded.as_deref().and_then(decode_command) else {
            std::process::exit(HELPER_FAILURE_EXIT);
        };
        let stdin_fd = env::var(TEST_STDIN_FD_ENV)
            .ok()
            .and_then(|fd| fd.parse::<i32>().ok());
        exit_guardian(run_guardian(
            control_fd,
            stdin_fd,
            program,
            args.into_iter(),
        ));
    }

    #[cfg(feature = "runtime-acceptance")]
    #[test]
    fn duplex_stdin_eof_does_not_cross_siblings() {
        let owner = RuntimeOwner::new().unwrap();
        let spawn =
            |script| owner.spawn_duplex(OsStr::new("/bin/sh"), ["-c", script].map(OsStr::new));
        let mut first =
            spawn(r#"read -r value && [ "$value" = payload ] && ! read -r extra"#).unwrap();
        let mut sibling = spawn("read -r extra").unwrap();

        let mut stdin = first.stdin.take().unwrap();
        stdin.write_all(b"payload\n").unwrap();
        drop(stdin);

        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        let status = loop {
            if let Some(status) = first.try_wait().unwrap() {
                break Some(status);
            }
            if std::time::Instant::now() >= deadline {
                first.kill().unwrap();
                break None;
            }
            std::thread::sleep(Duration::from_millis(10));
        };
        let sibling_alive = sibling.try_wait().unwrap().is_none();
        sibling.kill().unwrap();
        assert!(status.expect("child did not receive stdin EOF").success());
        assert!(sibling_alive);
    }
}
