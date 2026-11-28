//! Lifetime ownership for the node, gateway, and managed SSH trees only.
//! Runtime children inherit cwd/environment and are hidden on Windows. The
//! diagnostic uses duplex pipes; gameplay uses null stdin and piped stderr.

use std::io;
use std::time::{Duration, Instant};

pub(crate) const DIAGNOSTIC_POLL: Duration = Duration::from_millis(10);
pub(crate) const DIAGNOSTIC_CLEANUP: Duration = Duration::from_secs(1);

#[derive(Debug)]
pub(crate) struct SshUnavailable;

impl std::fmt::Display for SshUnavailable {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("OpenSSH executable unavailable")
    }
}

impl std::error::Error for SshUnavailable {}

#[cfg(unix)]
#[path = "runtime/unix.rs"]
mod platform;
#[cfg(windows)]
#[path = "runtime/windows.rs"]
mod platform;

pub use platform::{OwnedChild, RuntimeOwner};

#[cfg(unix)]
pub(crate) type StartupRetirementUnconfirmed = platform::StartupRetirementUnconfirmed;

pub(crate) fn confirm_retirement(
    deadline: Instant,
    mut retired: impl FnMut() -> io::Result<bool>,
) -> bool {
    loop {
        match retired() {
            Ok(true) => return true,
            Ok(false) => {
                let left = deadline.saturating_duration_since(Instant::now());
                if left.is_zero() {
                    return false;
                }
                std::thread::park_timeout(DIAGNOSTIC_POLL.min(left));
            }
            Err(_) => return false,
        }
    }
}

#[derive(Clone, Copy)]
pub enum RuntimeStdout {
    Piped,
    Null,
}

pub fn run_helper_if_requested() {
    #[cfg(unix)]
    platform::run_helper_if_requested();
}

pub fn terminated(child: &mut OwnedChild, role: &str) -> bool {
    match child.try_wait() {
        Ok(None) => false,
        Ok(Some(status)) => {
            log_exit(status, role);
            true
        }
        Err(error) => {
            eprintln!("imp: {role} process wait error: {error}");
            true
        }
    }
}

pub fn log_exit(status: std::process::ExitStatus, role: &str) {
    #[cfg(unix)]
    let signal = {
        use std::os::unix::process::ExitStatusExt;
        status.signal()
    };
    #[cfg(windows)]
    let signal: Option<i32> = None;
    eprintln!(
        "imp: {role} process terminated (code={:?}, signal={signal:?})",
        status.code()
    );
}

pub fn log_output(child: &mut OwnedChild, role: &'static str) {
    use std::io::{BufRead, BufReader};

    for pipe in [child.stdout.take(), child.stderr.take()]
        .into_iter()
        .flatten()
    {
        std::thread::spawn(move || {
            let mut reader = BufReader::new(pipe);
            let mut bytes = Vec::new();
            loop {
                bytes.clear();
                match reader.read_until(b'\n', &mut bytes) {
                    Ok(0) => break,
                    Ok(_) => eprintln!(
                        "imp: {role}: {}",
                        String::from_utf8_lossy(&bytes).trim_end_matches(['\r', '\n'])
                    ),
                    Err(error) => {
                        eprintln!("imp: {role} output error: {error}");
                        break;
                    }
                }
            }
        });
    }
}
