//! Isolated native parent/child fixtures; never terminates the test runner.

#[allow(dead_code)]
#[path = "../../src/runtime.rs"]
mod runtime;
#[allow(dead_code)]
#[path = "../../src/tunnel.rs"]
mod tunnel;

use std::io::{self, BufRead, Read, Write};
use std::net::TcpListener;
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{SystemTime, UNIX_EPOCH};

fn worker(record: &Path, grandchild: Option<&Path>, escape: bool, stdin_control: bool) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let token = format!(
        "{:x}-{:x}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    if let Some(record) = grandchild {
        // Deliberately no child cleanup: the ownership container must stop descendants.
        let mut command = Command::new(std::env::current_exe().unwrap());
        command
            .arg("worker")
            .arg(record)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        if escape {
            command.arg("escape-control");
        }
        #[allow(clippy::zombie_processes)]
        command.spawn().unwrap();
    }
    std::fs::write(
        record,
        format!(
            "{} {} {}\n",
            std::process::id(),
            listener.local_addr().unwrap().port(),
            token
        ),
    )
    .unwrap();
    let exiting = Arc::new(AtomicBool::new(false));
    if stdin_control {
        listener.set_nonblocking(true).unwrap();
        let exiting = Arc::clone(&exiting);
        thread::spawn(move || {
            let mut byte = [0];
            if io::stdin().read(&mut byte).unwrap_or(0) == 1 && byte[0] == b'x' {
                std::process::exit(91);
            }
            exiting.store(true, Ordering::Release);
        });
    }
    #[cfg(target_os = "linux")]
    let can_escape = escape && grandchild.is_none();
    loop {
        if stdin_control && exiting.load(Ordering::Acquire) {
            return;
        }
        let (mut connection, _) = match listener.accept() {
            Ok(connection) => connection,
            Err(error) if stdin_control && error.kind() == io::ErrorKind::WouldBlock => {
                thread::sleep(std::time::Duration::from_millis(10));
                continue;
            }
            Err(error) => panic!("fixture listener failed: {error}"),
        };
        if stdin_control {
            connection
                .set_read_timeout(Some(std::time::Duration::from_millis(50)))
                .unwrap();
        }
        let mut reader = io::BufReader::new(connection.try_clone().unwrap());
        loop {
            let mut request = String::new();
            match reader.read_line(&mut request) {
                Ok(0) => break,
                Ok(_) => {}
                Err(error)
                    if stdin_control
                        && matches!(
                            error.kind(),
                            io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut
                        ) =>
                {
                    if exiting.load(Ordering::Acquire) {
                        return;
                    }
                }
                Err(_) => break,
            }
            match request.trim_end() {
                request if request == format!("probe {token}") => {
                    connection
                        .write_all(format!("alive {token}\n").as_bytes())
                        .unwrap();
                }
                request if request == format!("exit {token}") => return,
                #[cfg(target_os = "linux")]
                request if can_escape && request == format!("setsid {token}") => {
                    let response = if unsafe { libc::setsid() } == -1 {
                        format!("setsid-failed {token}\n")
                    } else {
                        format!("setsid-ok {token}\n")
                    };
                    connection.write_all(response.as_bytes()).unwrap();
                }
                _ => {}
            }
        }
    }
}

fn io_writer(large: bool) {
    let mut input = [0];
    assert_eq!(io::stdin().read(&mut input).unwrap(), 0);
    if large {
        let stdout = io::stdout();
        let mut stdout = stdout.lock();
        let block = [b'x'; 8192];
        for _ in 0..128 {
            stdout.write_all(&block).unwrap();
        }
        drop(stdout);
        io::stderr().write_all(b"captured stderr\n").unwrap();
    } else {
        io::stdout().write_all(b"captured stdout\0\xff\n").unwrap();
        io::stderr().write_all(b"captured stderr\0\xfe\n").unwrap();
    }
}

fn abort_on_stdin() {
    thread::spawn(|| {
        let mut byte = [0];
        if io::stdin().read(&mut byte).unwrap_or(0) == 1 && byte[0] == b'x' {
            std::process::exit(91);
        }
    });
}

fn capture_io(output: &Path, error: &Path, large: bool) {
    abort_on_stdin();
    let mut command = Command::new(std::env::current_exe().unwrap());
    command.arg(if large {
        "io-large-writer"
    } else {
        "io-writer"
    });
    let owner = runtime::RuntimeOwner::new().unwrap();
    let mut child = owner
        .spawn(
            command.get_program(),
            command.get_args(),
            if large {
                runtime::RuntimeStdout::Null
            } else {
                runtime::RuntimeStdout::Piped
            },
        )
        .unwrap();
    let mut stdout = Vec::new();
    if large {
        assert!(child.stdout.is_none());
    } else {
        child
            .stdout
            .take()
            .unwrap()
            .read_to_end(&mut stdout)
            .unwrap();
    }
    let mut stderr = Vec::new();
    child
        .stderr
        .take()
        .unwrap()
        .read_to_end(&mut stderr)
        .unwrap();
    assert!(child.wait().unwrap().success());
    std::fs::write(output, stdout).unwrap();
    std::fs::write(error, stderr).unwrap();
}

fn main() {
    runtime::run_helper_if_requested();
    let args: Vec<_> = std::env::args_os().collect();
    match args[1].to_str().unwrap() {
        "worker" => {
            let escape = args.get(3).is_some_and(|arg| arg == "escape-control");
            let grandchild = if escape {
                None
            } else {
                args.get(3).map(Path::new)
            };
            worker(Path::new(&args[2]), grandchild, escape, false);
        }
        "escape-worker" => worker(Path::new(&args[2]), args.get(3).map(Path::new), true, false),
        "external-worker" => worker(Path::new(&args[2]), None, false, true),
        "uncontained-parent" => {
            let directory = Path::new(&args[2]);
            let count: usize = args[3].to_str().unwrap().parse().unwrap();
            for index in 0..count {
                let mut command = Command::new(std::env::current_exe().unwrap());
                command
                    .arg("worker")
                    .arg(directory.join(format!("child-{index}")))
                    .arg(directory.join(format!("grandchild-{index}")))
                    .stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::null());
                // Negative control: bypass RuntimeOwner and leave both generations running.
                #[allow(clippy::zombie_processes)]
                command.spawn().unwrap();
            }
            std::fs::write(directory.join("parent-ready"), b"ready").unwrap();
            let _ = io::stdin().read(&mut [0]);
            std::process::exit(91);
        }
        "parent" => {
            let directory = Path::new(&args[2]);
            let count: usize = args[3].to_str().unwrap().parse().unwrap();
            let mode = args[4].to_str().unwrap();
            let owner = runtime::RuntimeOwner::new().unwrap();
            let mut children = Vec::new();
            for index in 0..count {
                let mut command = Command::new(std::env::current_exe().unwrap());
                command
                    .arg(if mode == "escape" {
                        "escape-worker"
                    } else {
                        "worker"
                    })
                    .arg(directory.join(format!("child-{index}")));
                if mode == "tree" || mode == "escape" {
                    command.arg(directory.join(format!("grandchild-{index}")));
                }
                let mut child = owner
                    .spawn(
                        command.get_program(),
                        command.get_args(),
                        runtime::RuntimeStdout::Piped,
                    )
                    .unwrap();
                runtime::log_output(&mut child, "fixture");
                children.push(child);
            }
            std::fs::write(
                directory.join("parent-ready"),
                std::process::id().to_string(),
            )
            .unwrap();
            if args.get(5).is_some_and(|arg| arg == "linger") {
                loop {
                    std::thread::sleep(std::time::Duration::from_secs(1));
                }
            }
            let mut byte = [0];
            if std::io::stdin().read(&mut byte).unwrap_or(0) == 1 && byte[0] == b'x' {
                std::process::exit(91);
            }
            for child in &mut children {
                child.kill().unwrap();
                child.wait().unwrap();
            }
        }
        "runtime" => {
            let owner = runtime::RuntimeOwner::new().unwrap();
            let mut child = owner
                .spawn(
                    &args[3],
                    args[4..].iter().map(|arg| arg.as_os_str()),
                    runtime::RuntimeStdout::Piped,
                )
                .unwrap();
            std::fs::write(&args[2], child.id().to_string()).unwrap();
            runtime::log_output(&mut child, "runtime");
            let _ = std::io::stdin().read(&mut [0]);
            child.kill().unwrap();
            child.wait().unwrap();
        }
        "ssh" => {
            if let Some(home) = args.get(6) {
                std::env::set_var("HOME", home);
                #[cfg(windows)]
                std::env::set_var("USERPROFILE", home);
            }
            let ssh = tunnel::ssh_command(
                args[3].to_str().unwrap(),
                args[4].to_str().unwrap().parse().unwrap(),
            );
            let mut command = Command::new(ssh.get_program());
            command.arg("-F").arg(&args[2]).args(ssh.get_args());
            let owner = runtime::RuntimeOwner::new().unwrap();
            let mut child = owner
                .spawn(
                    command.get_program(),
                    command.get_args(),
                    runtime::RuntimeStdout::Null,
                )
                .unwrap();
            std::fs::write(&args[5], child.id().to_string()).unwrap();
            runtime::log_output(&mut child, "fixture-ssh");
            let _ = std::io::stdin().read(&mut [0]);
            child.kill().unwrap();
            child.wait().unwrap();
        }
        "io-capture" => capture_io(Path::new(&args[2]), Path::new(&args[3]), false),
        "io-null" => capture_io(Path::new(&args[2]), Path::new(&args[3]), true),
        "io-writer" => io_writer(false),
        "io-large-writer" => io_writer(true),
        "exit" => std::process::exit(args[2].to_str().unwrap().parse().unwrap()),
        "echo" => println!(
            "{}",
            serde_json::to_string(
                &args[2..]
                    .iter()
                    .map(|s| s.to_str().unwrap())
                    .collect::<Vec<_>>()
            )
            .unwrap()
        ),
        _ => panic!("unknown fixture mode"),
    }
}
