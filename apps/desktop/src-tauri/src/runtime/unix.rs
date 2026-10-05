use std::env;
use std::ffi::{OsStr, OsString};
use std::fs::File;
use std::io::{self, Read, Write};
use std::os::fd::{AsRawFd, FromRawFd, IntoRawFd};
#[cfg(test)]
use std::os::unix::ffi::{OsStrExt, OsStringExt};
use std::os::unix::net::UnixStream;
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::process::{Child, ChildStdin, Command, ExitStatus, Stdio};
use std::time::Duration;

use super::RuntimeStdout;

#[cfg(not(test))]
const HELPER_MODE: &str = "--imp-runtime-owner";
#[cfg(test)]
const TEST_COMMAND_ENV: &str = "IMP_RUNTIME_OWNER_TEST_COMMAND";
#[cfg(test)]
const TEST_CONTROL_FD_ENV: &str = "IMP_RUNTIME_OWNER_TEST_CONTROL_FD";
const CONTROL_OK: u8 = 0;
const CONTROL_ERROR: u8 = 1;
const HELPER_FAILURE_EXIT: i32 = 125;

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
        let executable = env::current_exe()?;
        let (mut control_reader, control_writer) = UnixStream::pair()?;
        control_reader.set_read_timeout(Some(Duration::from_secs(10)))?;
        let control_writer = move_above_stdio(control_writer)?;
        let control_fd = control_writer.as_raw_fd();
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
        }

        #[cfg(not(test))]
        {
            guardian_command
                .arg(HELPER_MODE)
                .arg(control_fd.to_string())
                .arg(program)
                .args(args);
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
                set_cloexec(control_fd, false)
            });
        }

        let mut guardian = guardian_command.spawn()?;
        drop(control_writer);

        let lifetime = guardian.stdin.take().expect("piped runtime lifetime");
        let stdout = guardian
            .stdout
            .take()
            .map(|stdout| unsafe { File::from_raw_fd(stdout.into_raw_fd()) });
        let stderr = unsafe {
            File::from_raw_fd(guardian.stderr.take().expect("piped stderr").into_raw_fd())
        };

        let child_pid = match read_start(&mut control_reader) {
            Ok(pid) => pid,
            Err(error) => {
                drop(lifetime);
                let _ = guardian.wait();
                return Err(error);
            }
        };

        eprintln!(
            "imp: runtime owner guardian pid={} child pid={child_pid}",
            guardian.id()
        );

        Ok(OwnedChild {
            lifetime: Some(lifetime),
            guardian,
            control: control_reader,
            exit_status: None,
            id: child_pid,
            stdout,
            stderr: Some(stderr),
        })
    }
}

pub struct OwnedChild {
    // Dropping this sole writer is the teardown request, including Drop.
    lifetime: Option<ChildStdin>,
    guardian: Child,
    control: UnixStream,
    exit_status: Option<ExitStatus>,
    id: u32,
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
        if args.next().as_deref() != Some(OsStr::new(HELPER_MODE)) {
            return;
        }

        let Some(fd) = args
            .next()
            .and_then(|arg| arg.to_str().and_then(|value| value.parse::<i32>().ok()))
        else {
            std::process::exit(HELPER_FAILURE_EXIT);
        };
        let Some(program) = args.next() else {
            std::process::exit(HELPER_FAILURE_EXIT);
        };
        exit_guardian(run_guardian(fd, program, args));
    }
}

fn run_guardian(
    fd: i32,
    program: OsString,
    args: impl Iterator<Item = OsString>,
) -> GuardianOutcome {
    if fd <= libc::STDERR_FILENO || unsafe { libc::fcntl(fd, libc::F_GETFD) } == -1 {
        return GuardianOutcome::Failure;
    }
    let mut control = unsafe { File::from_raw_fd(fd) };
    if configure_guardian_signals().is_err() {
        return GuardianOutcome::Failure;
    }
    if let Err(error) = set_cloexec(fd, true) {
        send_start_error(&mut control, &error);
        return GuardianOutcome::Failure;
    }
    #[cfg(target_os = "linux")]
    if let Err(error) = enable_subreaper() {
        send_start_error(&mut control, &error);
        return GuardianOutcome::Failure;
    }

    #[cfg(target_os = "linux")]
    let guardian_pid = unsafe { libc::getpid() };
    let mut runtime = Command::new(program);
    runtime
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());

    #[cfg(test)]
    runtime
        .env_remove(TEST_COMMAND_ENV)
        .env_remove(TEST_CONTROL_FD_ENV);

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

    let child = match runtime.spawn() {
        Ok(child) => child,
        Err(error) => {
            send_start_error(&mut control, &error);
            return GuardianOutcome::Failure;
        }
    };
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
    Failure,
}

fn exit_guardian(outcome: GuardianOutcome) -> ! {
    match outcome {
        GuardianOutcome::Success => std::process::exit(0),
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

fn move_above_stdio(stream: UnixStream) -> io::Result<UnixStream> {
    if stream.as_raw_fd() > libc::STDERR_FILENO {
        return Ok(stream);
    }
    let fd = unsafe {
        libc::fcntl(
            stream.as_raw_fd(),
            libc::F_DUPFD_CLOEXEC,
            libc::STDERR_FILENO + 1,
        )
    };
    if fd == -1 {
        return Err(io::Error::last_os_error());
    }
    drop(stream);
    Ok(unsafe { UnixStream::from_raw_fd(fd) })
}

fn send_start_error(control: &mut File, error: &io::Error) {
    let mut record = [0; 5];
    record[0] = CONTROL_ERROR;
    record[1..].copy_from_slice(&error.raw_os_error().unwrap_or(libc::EIO).to_le_bytes());
    let _ = control.write_all(&record);
}

fn write_start_ok(control: &mut File, child_pid: u32) -> io::Result<()> {
    let mut record = [0; 5];
    record[0] = CONTROL_OK;
    record[1..].copy_from_slice(&child_pid.to_le_bytes());
    control.write_all(&record)
}

fn read_start(control: &mut impl Read) -> io::Result<u32> {
    let mut record = [0; 5];
    control.read_exact(&mut record)?;
    match record[0] {
        CONTROL_OK => Ok(u32::from_le_bytes(record[1..].try_into().unwrap())),
        CONTROL_ERROR => Err(io::Error::from_raw_os_error(i32::from_le_bytes(
            record[1..].try_into().unwrap(),
        ))),
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
        exit_guardian(run_guardian(control_fd, program, args.into_iter()));
    }
}
