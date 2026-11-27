use crate::runtime::RuntimeOwner;
use std::ffi::{OsStr, OsString};
use std::io::{self, Read, Write};
use std::net::{Ipv4Addr, Ipv6Addr, Shutdown, TcpListener, TcpStream};
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc};
use std::thread;
use std::time::Duration;

const MAX_STREAMS: usize = 8;
const POLL: Duration = Duration::from_millis(10);

pub fn run(
    host: &str,
    user: &str,
    port: Option<u16>,
    local_port: u16,
    config: &Path,
) -> io::Result<()> {
    let args = Arc::new(ssh_args(host, user, port, config)?);
    #[cfg(windows)]
    let (_winsock, listener) = windows::bind(local_port)?;
    #[cfg(not(windows))]
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, local_port))?;
    listener.set_nonblocking(true)?;

    let (stop, control) = mpsc::channel();
    thread::Builder::new().spawn(move || {
        let mut command = [0; 5];
        let result = match io::stdin().lock().read_exact(&mut command) {
            Ok(()) if command == *b"stop\n" => Ok(()),
            Ok(()) => Err(invalid("expected stop or EOF")),
            Err(err) if err.kind() == io::ErrorKind::UnexpectedEof => Ok(()),
            Err(err) => Err(err),
        };
        let _ = stop.send(result);
    })?;
    {
        let mut stdout = io::stdout().lock();
        writeln!(stdout, "READY {}", listener.local_addr()?.port())?;
        stdout.flush()?;
    }

    let stopping = Arc::new(AtomicBool::new(false));
    let mut streams: Vec<(Arc<TcpStream>, thread::JoinHandle<()>)> =
        Vec::with_capacity(MAX_STREAMS);
    let result = (|| loop {
        match control.recv_timeout(POLL) {
            Ok(result) => return result,
            Err(mpsc::RecvTimeoutError::Disconnected) => return Ok(()),
            Err(mpsc::RecvTimeoutError::Timeout) => {}
        }
        let mut i = 0;
        while i < streams.len() {
            if streams[i].1.is_finished() {
                let (_, worker) = streams.swap_remove(i);
                let _ = worker.join();
            } else {
                i += 1;
            }
        }
        while streams.len() < MAX_STREAMS {
            let socket = match listener.accept() {
                Ok((socket, _)) => socket,
                Err(err) if err.kind() == io::ErrorKind::Interrupted => continue,
                Err(err) if err.kind() == io::ErrorKind::WouldBlock => break,
                Err(err) => return Err(err),
            };
            socket.set_nonblocking(cfg!(windows))?;
            let socket = Arc::new(socket);
            let peer = Arc::clone(&socket);
            let (args, stopping) = (Arc::clone(&args), Arc::clone(&stopping));
            let worker = thread::Builder::new().spawn(move || {
                if forward(&peer, &args, &stopping).is_err() || stopping.load(Ordering::Acquire) {
                    abort(&peer);
                }
            })?;
            streams.push((socket, worker));
        }
    })();

    stopping.store(true, Ordering::Release);
    #[cfg(unix)]
    for (socket, _) in &streams {
        abort(socket);
    }
    for (_, worker) in streams {
        let _ = worker.join();
    }
    drop(listener);
    result
}

fn forward(socket: &TcpStream, args: &[OsString], stopping: &AtomicBool) -> io::Result<()> {
    if stopping.load(Ordering::Acquire) {
        return Ok(());
    }
    let owner = RuntimeOwner::new()?;
    let mut child = owner.spawn_duplex(OsStr::new("ssh"), args.iter().map(OsString::as_os_str))?;
    #[cfg(windows)]
    let (end_input, end_output) = (AtomicBool::new(false), AtomicBool::new(false));
    thread::scope(|scope| {
        let result = (|| {
            let (Some(mut stdin), Some(mut stdout), Some(mut stderr)) =
                (child.stdin.take(), child.stdout.take(), child.stderr.take())
            else {
                return Err(io::Error::other("missing runtime stream pipes"));
            };
            let (send, copied) = mpsc::sync_channel(2);
            let input = send.clone();
            #[cfg(unix)]
            let mut source = socket;
            #[cfg(windows)]
            let mut source = windows::CopySocket::new(socket, stopping, &end_input);
            thread::Builder::new().spawn_scoped(scope, move || {
                let result = copy_stream(&mut source, &mut stdin);
                drop(stdin);
                // The child may close stdin while its final output is still buffered.
                let _ = input.send(match result {
                    Err(err) if err.kind() == io::ErrorKind::BrokenPipe => Ok(()),
                    result => result,
                });
            })?;
            #[cfg(unix)]
            let mut target = socket;
            #[cfg(windows)]
            let mut target = windows::CopySocket::new(socket, stopping, &end_output);
            thread::Builder::new().spawn_scoped(scope, move || {
                let result = copy_stream(&mut stdout, &mut target);
                if result.is_ok() {
                    let _ = socket.shutdown(Shutdown::Write);
                }
                let _ = send.send(result);
            })?;
            thread::Builder::new().spawn_scoped(scope, move || {
                let _ = io::copy(&mut stderr, &mut io::sink());
            })?;

            let mut remaining = 2;
            while remaining != 0 && !stopping.load(Ordering::Acquire) {
                if child.try_wait()?.is_some() {
                    // Retire descendants and stop waiting for local input without losing stdout.
                    #[cfg(unix)]
                    let _ = socket.shutdown(Shutdown::Read);
                    #[cfg(windows)]
                    end_input.store(true, Ordering::Release);
                    break;
                }
                match copied.recv_timeout(POLL) {
                    Ok(result) => {
                        result?;
                        remaining -= 1;
                    }
                    Err(mpsc::RecvTimeoutError::Timeout) => {}
                    Err(mpsc::RecvTimeoutError::Disconnected) => {
                        return Err(io::Error::other("runtime stream copy stopped"));
                    }
                }
            }
            Ok(())
        })();
        #[cfg(unix)]
        if result.is_err() || stopping.load(Ordering::Acquire) {
            abort(socket);
        }
        #[cfg(windows)]
        if result.is_err() {
            end_input.store(true, Ordering::Release);
            end_output.store(true, Ordering::Release);
        }
        // Pipe I/O can only be joined after killing descendants that hold pipe handles.
        #[cfg(windows)]
        let retired = {
            drop(owner);
            child.wait().map(|_| ())
        };
        #[cfg(unix)]
        let retired = child.kill();
        result.and(retired)
    })
}

fn copy_stream(source: &mut impl Read, target: &mut impl Write) -> io::Result<()> {
    // Linux socket-to-pipe splice can hold the pipe lock while waiting for more input.
    let mut buffer = [0; 8192];
    loop {
        match source.read(&mut buffer) {
            Ok(0) => return Ok(()),
            Ok(n) => target.write_all(&buffer[..n])?,
            Err(err) if err.kind() == io::ErrorKind::Interrupted => continue,
            Err(err) => return Err(err),
        }
    }
}

fn abort(socket: &TcpStream) {
    #[cfg(windows)]
    windows::abort_on_close(socket);
    #[cfg(unix)]
    {
        let _ = socket.shutdown(Shutdown::Both);
    }
}

fn ssh_args(host: &str, user: &str, port: Option<u16>, config: &Path) -> io::Result<Vec<OsString>> {
    let host = validate_host(host).ok_or_else(|| invalid("invalid SSH host"))?;
    if !matches!(user.bytes().next(), Some(b) if b.is_ascii_alphanumeric() || b == b'_')
        || user.len() > 255
        || !user
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
    {
        return Err(invalid("invalid SSH username"));
    }
    if port == Some(0) {
        return Err(invalid("invalid SSH port"));
    }
    if !config.is_absolute() || config.as_os_str().as_encoded_bytes().contains(&0) {
        return Err(invalid("expected an absolute SSH fixture config path"));
    }

    let mut args = vec![OsString::from("-F"), config.as_os_str().to_owned()];
    args.extend(["-T", "-S", "none", "-W", "127.0.0.1:8787"].map(OsString::from));
    // These command-line values take precedence over the trusted fixture config.
    for option in [
        "SessionType=none",
        "RemoteCommand=none",
        "PermitLocalCommand=no",
        "LocalCommand=none",
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
        "ConnectTimeout=10",
        "ServerAliveInterval=15",
        "ServerAliveCountMax=3",
        "PreferredAuthentications=publickey",
        "AddKeysToAgent=no",
        "StrictHostKeyChecking=yes",
        "UpdateHostKeys=no",
        "VerifyHostKeyDNS=no",
        "CheckHostIP=no",
        "KnownHostsCommand=none",
    ] {
        args.extend(["-o", option].map(OsString::from));
    }
    if let Some(port) = port {
        args.extend([OsString::from("-p"), port.to_string().into()]);
    }
    args.extend(["-l", user, "--", host].map(OsString::from));
    Ok(args)
}

fn validate_host(host: &str) -> Option<&str> {
    if let Some(ip) = host
        .strip_prefix('[')
        .and_then(|host| host.strip_suffix(']'))
    {
        return ip.parse::<Ipv6Addr>().ok().map(|_| ip);
    }
    if host.parse::<Ipv4Addr>().is_ok() || host.parse::<Ipv6Addr>().is_ok() {
        return Some(host);
    }
    let dns = host.strip_suffix('.').unwrap_or(host);
    // Resolvers also recognize abbreviated, octal and hexadecimal IPv4 spellings.
    let numeric = dns.split('.').all(|label| {
        label.bytes().all(|b| b.is_ascii_digit())
            || label
                .strip_prefix("0x")
                .or_else(|| label.strip_prefix("0X"))
                .is_some_and(|tail| !tail.is_empty() && tail.bytes().all(|b| b.is_ascii_hexdigit()))
    });
    (dns.len() <= 253
        && !numeric
        && dns.split('.').all(|label| {
            let label = label.as_bytes();
            label.len() <= 63
                && label.first().is_some_and(u8::is_ascii_alphanumeric)
                && label.last().is_some_and(u8::is_ascii_alphanumeric)
                && label
                    .iter()
                    .all(|b| b.is_ascii_alphanumeric() || *b == b'-')
        }))
    .then_some(host)
}

fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message)
}

#[cfg(windows)]
mod windows {
    use super::*;
    use std::mem::{size_of, size_of_val, MaybeUninit};
    use std::os::windows::io::{AsRawSocket, FromRawSocket, OwnedSocket};
    use std::ptr::null;
    use windows_sys::Win32::Networking::WinSock as ws;

    pub(super) struct Winsock;

    impl Drop for Winsock {
        fn drop(&mut self) {
            // SAFETY: Balances the successful WSAStartup that created this guard.
            unsafe { ws::WSACleanup() };
        }
    }

    pub(super) fn bind(port: u16) -> io::Result<(Winsock, TcpListener)> {
        let mut data = MaybeUninit::<ws::WSADATA>::uninit();
        // SAFETY: WinSock writes WSADATA to this properly sized and aligned storage.
        let code = unsafe { ws::WSAStartup(0x0202, data.as_mut_ptr()) };
        if code != 0 {
            return Err(io::Error::from_raw_os_error(code));
        }
        let winsock = Winsock;
        // SAFETY: Requests a new non-inheritable IPv4 TCP socket, without protocol overrides.
        let raw = unsafe {
            ws::WSASocketW(
                ws::AF_INET.into(),
                ws::SOCK_STREAM,
                ws::IPPROTO_TCP,
                null(),
                0,
                ws::WSA_FLAG_OVERLAPPED | ws::WSA_FLAG_NO_HANDLE_INHERIT,
            )
        };
        if raw == ws::INVALID_SOCKET {
            return Err(last_error());
        }
        // SAFETY: WSASocketW returned a fresh owned socket.
        let socket = unsafe { OwnedSocket::from_raw_socket(raw as _) };
        let exclusive = 1i32;
        let address = ws::SOCKADDR_IN {
            sin_family: ws::AF_INET,
            sin_port: port.to_be(),
            sin_addr: ws::IN_ADDR {
                S_un: ws::IN_ADDR_0 {
                    S_addr: u32::from_ne_bytes(Ipv4Addr::LOCALHOST.octets()),
                },
            },
            sin_zero: [0; 8],
        };
        // SAFETY: The socket is live, and both inputs are initialized for the given sizes.
        if unsafe {
            ws::setsockopt(
                raw,
                ws::SOL_SOCKET,
                ws::SO_EXCLUSIVEADDRUSE,
                (&exclusive as *const i32).cast(),
                size_of_val(&exclusive) as i32,
            ) == ws::SOCKET_ERROR
                || ws::bind(
                    raw,
                    (&address as *const ws::SOCKADDR_IN).cast(),
                    size_of::<ws::SOCKADDR_IN>() as i32,
                ) == ws::SOCKET_ERROR
                || ws::listen(raw, MAX_STREAMS as i32) == ws::SOCKET_ERROR
        } {
            return Err(last_error());
        }
        Ok((winsock, TcpListener::from(socket)))
    }

    // Winsock shutdown does not reliably wake a pending blocking recv or send.
    pub(super) struct CopySocket<'a> {
        socket: &'a TcpStream,
        stopping: &'a AtomicBool,
        ended: &'a AtomicBool,
    }

    impl<'a> CopySocket<'a> {
        pub(super) fn new(
            socket: &'a TcpStream,
            stopping: &'a AtomicBool,
            ended: &'a AtomicBool,
        ) -> Self {
            Self {
                socket,
                stopping,
                ended,
            }
        }

        fn transfer(
            &self,
            events: ws::WSAPOLL_EVENT_FLAGS,
            mut operation: impl FnMut() -> io::Result<usize>,
        ) -> io::Result<usize> {
            let mut fd = ws::WSAPOLLFD {
                fd: self.socket.as_raw_socket() as _,
                events,
                revents: 0,
            };
            while !self.stopping.load(Ordering::Acquire) && !self.ended.load(Ordering::Acquire) {
                match operation() {
                    Err(err) if err.kind() == io::ErrorKind::WouldBlock => {}
                    result => return result,
                }
                // SAFETY: The descriptor holds one live socket and is writable by WinSock.
                if unsafe { ws::WSAPoll(&mut fd, 1, POLL.as_millis() as i32) } == ws::SOCKET_ERROR {
                    return Err(last_error());
                }
            }
            Err(io::ErrorKind::ConnectionAborted.into())
        }
    }

    impl Read for CopySocket<'_> {
        fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
            let mut socket = self.socket;
            self.transfer(ws::POLLRDNORM, || socket.read(buffer))
        }
    }

    impl Write for CopySocket<'_> {
        fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
            let mut socket = self.socket;
            self.transfer(ws::POLLWRNORM, || socket.write(buffer))
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    pub(super) fn abort_on_close(socket: &TcpStream) {
        // A cancelled connection must not pin the exclusive listener's port awaiting peer FIN.
        let linger = ws::LINGER {
            l_onoff: 1,
            l_linger: 0,
        };
        // SAFETY: Provides the initialized linger option for a live socket.
        unsafe {
            ws::setsockopt(
                socket.as_raw_socket() as _,
                ws::SOL_SOCKET,
                ws::SO_LINGER,
                (&linger as *const ws::LINGER).cast(),
                size_of_val(&linger) as i32,
            );
        }
    }

    fn last_error() -> io::Error {
        // SAFETY: Reads the caller's thread-local WinSock error immediately after failure.
        io::Error::from_raw_os_error(unsafe { ws::WSAGetLastError() })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strict_forward_argv() {
        let config = std::env::current_dir().unwrap().join("ssh config");
        let args = ssh_args("[::1]", "test_user", Some(2222), &config).unwrap();
        assert_eq!(args[0], "-F");
        assert_eq!(args[1], config.as_os_str());
        assert_eq!(args[2..7], ["-T", "-S", "none", "-W", "127.0.0.1:8787"]);
        assert_eq!(
            args[args.len() - 6..],
            ["-p", "2222", "-l", "test_user", "--", "::1"]
        );
        for option in [
            "BatchMode=yes",
            "StrictHostKeyChecking=yes",
            "SessionType=none",
            "ClearAllForwardings=yes",
            "ForkAfterAuthentication=no",
            "StdinNull=no",
        ] {
            assert!(args.windows(2).any(|pair| pair == ["-o", option]));
        }
        let default_port = ssh_args("127.0.0.1", "user", None, &config).unwrap();
        assert!(!default_port.iter().any(|arg| arg == "-p"));
    }

    #[test]
    fn rejects_ambiguous_or_injected_arguments() {
        for host in [
            "ssh-host.example.",
            "single",
            "127.0.0.1",
            "2001:db8::1",
            "[::1]",
        ] {
            assert!(validate_host(host).is_some());
        }
        for host in [
            "",
            "-F",
            "user@host",
            "a b",
            "127.1",
            "010.0.0.1",
            "0x7f000001",
            "0x7f.0.0.1",
            "host:22",
            "host..test",
            "a;id",
            "fe80::1%scope",
            "[host]",
        ] {
            assert!(validate_host(host).is_none());
        }
        let config = std::env::current_dir().unwrap().join("ssh-config");
        for user in ["", "-user", "user@host", "a b", "user%h", "a;b"] {
            assert!(ssh_args("host", user, None, &config).is_err());
        }
        assert!(ssh_args("host", "user", Some(0), &config).is_err());
        assert!(ssh_args("host", "user", None, Path::new("relative")).is_err());
    }
}
