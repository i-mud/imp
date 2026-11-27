use std::ffi::OsString;
use std::io::{self, BufRead, Read, Write};
use std::net::Ipv4Addr;
use std::path::Path;
use std::sync::Arc;
use std::time::{Duration, Instant};

use anyhow::{bail, ensure, Context, Result};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::oneshot;
use tokio::task::JoinSet;
use tokio::time::timeout;

use crate::session::Session;

const MAX_RAW_CONNECTIONS: usize = 8;

async fn connect_fixture(args: &[OsString]) -> Result<Session> {
    let [ssh_socket, user, identity, host_key, remote_port] = args else {
        bail!("expected SSH_SOCKET USER PRIVATE_KEY HOST_KEY REMOTE_PORT");
    };
    Session::connect(
        ssh_socket
            .to_str()
            .context("SSH_SOCKET encoding")?
            .parse()?,
        user.to_str().context("USER encoding")?,
        Path::new(identity),
        Path::new(host_key),
        remote_port
            .to_str()
            .context("REMOTE_PORT encoding")?
            .parse()?,
    )
    .await
}

pub async fn measure(args: Vec<OsString>) -> Result<()> {
    let started = Instant::now();
    let session = connect_fixture(&args).await?;
    println!(
        "ssh_auth_us={} ssh_ready_us={}",
        session.auth_duration().as_micros(),
        started.elapsed().as_micros(),
    );
    eprintln!(
        "russh 0.65.4 explicit CLOSE acknowledgment is not exposed; measuring EOF to peer CLOSE"
    );
    let measured = timeout(Duration::from_secs(15), async {
        for index in 1..=20 {
            let (open, close) = session
                .measure_channel()
                .await
                .with_context(|| format!("measure SSH channel {index}"))?;
            println!(
                "channel={index} open_confirmed_us={} eof_to_peer_close_us={}",
                open.as_micros(),
                close.as_micros(),
            );
        }
        Ok(())
    })
    .await
    .context("overall SSH channel measurement deadline elapsed")
    .and_then(|result| result);
    measured.and(session.disconnect().await)
}

pub async fn run(args: Vec<OsString>) -> Result<()> {
    ensure!(
        args.len() == 6,
        "usage: raw SSH_SOCKET USER PRIVATE_KEY HOST_KEY REMOTE_PORT LOCAL_PORT"
    );
    let (connection, port) = args.split_at(5);
    let local_port = port[0].to_str().context("LOCAL_PORT encoding")?.parse()?;
    let started = Instant::now();
    let session = Arc::new(connect_fixture(connection).await?);
    eprintln!(
        "raw ssh_auth_ready_ms={} ssh_auth_us={}",
        started.elapsed().as_millis(),
        session.auth_duration().as_micros(),
    );
    let listener = bind_exclusive(local_port).context("bind raw loopback listener exclusively")?;

    // Tokio's stdin uses a blocking read that can keep its runtime alive when
    // the SSH transport dies while the parent still has stdin open.
    let (send_stop, mut stop) = oneshot::channel();
    drop(std::thread::spawn(move || {
        let _ = send_stop.send(read_stop());
    }));
    {
        let mut stdout = io::stdout().lock();
        writeln!(stdout, "READY {}", listener.local_addr()?.port())?;
        stdout.flush()?;
    }

    let mut forwards = JoinSet::new();
    let result = loop {
        tokio::select! {
            command = &mut stop => break command.context("raw stdin thread exited").and_then(|result| result),
            _ = session.lost() => break Err(anyhow::anyhow!("raw SSH session lost")),
            accepted = listener.accept(), if forwards.len() < MAX_RAW_CONNECTIONS => match accepted {
                Ok((local, _)) => { forwards.spawn(forward(local, session.clone())); }
                Err(error) => break Err(error).context("accept raw loopback connection"),
            },
            joined = forwards.join_next(), if !forwards.is_empty() => {
                if let Some(Err(error)) = joined {
                    break Err(error).context("raw forwarding task failed");
                }
            }
        }
    };
    drop(listener);
    forwards.shutdown().await;
    let started = Instant::now();
    let disconnected = session.disconnect().await;
    eprintln!("raw ssh_shutdown_ms={}", started.elapsed().as_millis());
    result.and(disconnected)
}

fn read_stop() -> Result<()> {
    let mut line = String::new();
    let read = io::stdin()
        .lock()
        .take(6)
        .read_line(&mut line)
        .context("read raw stop command")?;
    ensure!(
        read == 0 || matches!(line.as_str(), "stop" | "stop\n" | "stop\r\n"),
        "raw mode expects stop or stdin EOF"
    );
    Ok(())
}

async fn forward(mut local: TcpStream, session: Arc<Session>) {
    if let Err(error) = local.set_nodelay(true) {
        eprintln!("raw forwarding failed to set TCP_NODELAY: {error}");
        return;
    }
    let opened = Instant::now();
    let mut remote = match session.open().await {
        Ok(stream) => stream,
        Err(error) => {
            eprintln!("raw forwarding failed: {error:#}");
            return;
        }
    };
    eprintln!("raw channel_open_ms={}", opened.elapsed().as_millis());
    let started = Instant::now();
    let result = tokio::io::copy_bidirectional(&mut local, &mut remote).await;
    let copy_ms = started.elapsed().as_millis();
    drop(remote);
    eprintln!("raw channel_copy_ms={copy_ms}");
    if let Err(error) = result {
        eprintln!("raw forwarding ended: {error}");
    }
}

// This listener is only a raw-stream test adapter; authority callers use SSH
// channels directly. An occupied port is an error, never an adopted listener.
#[cfg(not(windows))]
fn bind_exclusive(port: u16) -> io::Result<TcpListener> {
    let listener = std::net::TcpListener::bind((Ipv4Addr::LOCALHOST, port))?;
    listener.set_nonblocking(true)?;
    TcpListener::from_std(listener)
}

#[cfg(windows)]
fn bind_exclusive(port: u16) -> io::Result<TcpListener> {
    use std::net::SocketAddrV4;
    use std::os::windows::io::AsRawSocket;
    use windows_sys::Win32::Networking::WinSock as ws;

    let socket = socket2::Socket::new(socket2::Domain::IPV4, socket2::Type::STREAM, None)?;
    let exclusive = 1i32;
    // SAFETY: The socket is live and the option points to an initialized Winsock BOOL.
    if unsafe {
        ws::setsockopt(
            socket.as_raw_socket() as _,
            ws::SOL_SOCKET,
            ws::SO_EXCLUSIVEADDRUSE,
            (&exclusive as *const i32).cast(),
            std::mem::size_of_val(&exclusive) as i32,
        )
    } == ws::SOCKET_ERROR
    {
        return Err(io::Error::from_raw_os_error(unsafe {
            ws::WSAGetLastError()
        }));
    }
    socket.bind(&SocketAddrV4::new(Ipv4Addr::LOCALHOST, port).into())?;
    socket.listen(128)?;
    socket.set_nonblocking(true)?;
    TcpListener::from_std(socket.into())
}
