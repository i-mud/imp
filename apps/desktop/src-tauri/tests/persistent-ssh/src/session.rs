use std::net::{Shutdown, SocketAddr};
use std::path::Path;
use std::sync::Arc;
use std::time::{Duration, Instant};

use anyhow::{bail, ensure, Context, Result};
use futures_util::FutureExt;
use russh::keys::{self, Algorithm, PrivateKeyWithHashAlg, PublicKey, PublicKeyOrCertificate};
use russh::{client, Channel, ChannelMsg, Disconnect, Preferred};
use tokio::net::TcpStream;
use tokio::sync::{watch, Semaphore};
use tokio::time::timeout;

const LOOPBACK: &str = "127.0.0.1";
const SSH_DEADLINE: Duration = Duration::from_secs(10);
const MAX_PENDING_OPENS: usize = 64;
static HOST_KEY_ALGORITHMS: [Algorithm; 1] = [Algorithm::Ed25519];

// russh sends EOF (not CLOSE) on AsyncWrite shutdown, and peer EOF ends only
// the read half. Dropping the stream schedules full channel CLOSE.
pub type Stream = russh::ChannelStream<client::Msg>;

struct PinnedKey {
    key: PublicKey,
    _lifetime: watch::Sender<()>,
}

impl client::Handler for PinnedKey {
    type Error = russh::Error;

    async fn check_server_key(
        &mut self,
        server_key: &PublicKeyOrCertificate,
    ) -> Result<bool, Self::Error> {
        // Match the key bytes, not an OpenSSH comment, fingerprint, or a certificate.
        Ok(
            matches!(server_key, PublicKeyOrCertificate::PublicKey { key, .. }
            if key.key_data() == self.key.key_data()),
        )
    }
}

// russh's Handle detaches its transport task when dropped. Own a duplicate
// socket so cancellation or owner drop can shut down the actual connection.
struct Transport(Arc<std::net::TcpStream>);

impl Drop for Transport {
    fn drop(&mut self) {
        let _ = self.0.shutdown(Shutdown::Both);
    }
}

pub struct Session {
    handle: Arc<client::Handle<PinnedKey>>,
    transport: Transport,
    lifetime: watch::Receiver<()>,
    pending_opens: Arc<Semaphore>,
    remote_port: u16,
    auth_duration: Duration,
}

impl Session {
    pub async fn connect(
        ssh_addr: SocketAddr,
        username: &str,
        identity: &Path,
        host_key: &Path,
        remote_port: u16,
    ) -> Result<Self> {
        ensure!(
            ssh_addr.ip().is_loopback(),
            "fixture SSH socket must be loopback"
        );
        ensure!(remote_port != 0, "SSH relay port must be nonzero");
        let key = keys::load_public_key(host_key).context("read fixture SSH host key")?;
        ensure!(
            key.algorithm() == Algorithm::Ed25519,
            "fixture SSH host key must be Ed25519"
        );
        let identity =
            keys::load_secret_key(identity, None).context("read fixture SSH auth key")?;
        ensure!(
            identity.algorithm() == Algorithm::Ed25519,
            "fixture SSH auth key must be Ed25519"
        );

        timeout(SSH_DEADLINE, async {
            let tcp = TcpStream::connect(ssh_addr)
                .await
                .context("connect to fixture SSH socket")?;
            tcp.set_nodelay(true)
                .context("set TCP_NODELAY on fixture SSH socket")?;
            let tcp = tcp.into_std()?;
            let transport = Transport(Arc::new(tcp.try_clone()?));
            let (lifetime, ended) = watch::channel(());
            let config = client::Config {
                preferred: Preferred {
                    key: std::borrow::Cow::Borrowed(&HOST_KEY_ALGORITHMS),
                    ..Preferred::default()
                },
                keepalive_interval: Some(Duration::from_secs(2)),
                keepalive_max: 3,
                ..client::Config::default()
            };
            let mut handle = client::connect_stream(
                Arc::new(config),
                TcpStream::from_std(tcp)?,
                PinnedKey {
                    key,
                    _lifetime: lifetime,
                },
            )
            .await
            .context("fixture SSH handshake and host key verification")?;
            // Public key only: no agent, SSH config, password, or command channel.
            let identity = PrivateKeyWithHashAlg::new(Arc::new(identity), None);
            let auth_started = Instant::now();
            let auth = handle
                .authenticate_publickey(username, identity)
                .await
                .context("fixture SSH auth exchange")?;
            let auth_duration = auth_started.elapsed();
            ensure!(auth.success(), "fixture SSH auth rejected");
            Ok(Self {
                handle: Arc::new(handle),
                transport,
                lifetime: ended,
                remote_port,
                auth_duration,
                pending_opens: Arc::new(Semaphore::new(MAX_PENDING_OPENS)),
            })
        })
        .await
        .context("fixture SSH handshake/auth deadline elapsed")?
    }

    pub fn auth_duration(&self) -> Duration {
        self.auth_duration
    }

    pub async fn measure_channel(&self) -> Result<(Duration, Duration)> {
        ensure!(!self.is_closed(), "SSH session closed before measuring");
        let open_started = Instant::now();
        let mut channel = timeout(SSH_DEADLINE, direct_channel(&self.handle, self.remote_port))
            .await
            .context("measurement direct-tcpip opening deadline elapsed")?
            .context("open fixture SSH direct-tcpip measurement channel")?;
        let open_duration = open_started.elapsed();
        ensure!(
            channel.wait().now_or_never().is_none(),
            "measurement peer sent a message before the fixture sent EOF"
        );

        // russh 0.65.4 removes channel state when sending CLOSE, so it does
        // not expose that reply. Keep state with EOF and observe peer CLOSE.
        let eof_started = Instant::now();
        let eof_duration = timeout(SSH_DEADLINE, async {
            channel.eof().await.context("send measurement EOF")?;
            loop {
                match channel.wait().await {
                    Some(ChannelMsg::Eof) => {}
                    Some(ChannelMsg::Close) => break Ok(eof_started.elapsed()),
                    None => bail!("measurement channel ended without an explicit peer CLOSE"),
                    Some(_) => bail!("unexpected message from no-payload measurement channel"),
                }
            }
        })
        .await
        .context("measurement EOF-to-peer-CLOSE deadline elapsed")??;
        ensure!(
            !self.is_closed(),
            "SSH session lost during channel measurement"
        );
        Ok((open_duration, eof_duration))
    }

    pub async fn open(&self) -> Result<Stream> {
        ensure!(!self.is_closed(), "SSH session closed");
        let permit = self
            .pending_opens
            .clone()
            .try_acquire_owned()
            .context("too many pending SSH channel opens")?;
        let (handle, tcp, port) = (
            self.handle.clone(),
            self.transport.0.clone(),
            self.remote_port,
        );
        // Dropping a russh open mid-flight can orphan a channel. If our waiter
        // leaves, the task still consumes confirmation and drops the stream.
        tokio::spawn(async move {
            let _permit = permit;
            match timeout(SSH_DEADLINE, direct_channel(&handle, port)).await {
                Ok(channel) => Ok(channel
                    .context("open fixture SSH direct-tcpip forwarding channel")?
                    .into_stream()),
                Err(elapsed) => {
                    // The peer may still confirm an abandoned open, so retire this transport.
                    let _ = tcp.shutdown(Shutdown::Both);
                    Err(elapsed).context("fixture SSH direct-tcpip opening deadline elapsed")
                }
            }
        })
        .await
        .context("fixture SSH channel open task")?
    }

    pub fn is_closed(&self) -> bool {
        self.handle.is_closed() || self.lifetime.has_changed().is_err()
    }

    pub async fn lost(&self) {
        let _ = self.lifetime.clone().changed().await;
    }

    pub async fn disconnect(&self) -> Result<()> {
        let result = timeout(Duration::from_secs(2), async {
            if !self.is_closed() {
                self.handle
                    .disconnect(Disconnect::ByApplication, "fixture stopped", "")
                    .await?;
            }
            self.lost().await;
            Ok(())
        })
        .await
        .context("fixture SSH disconnect deadline elapsed")
        .and_then(|result| result);
        if result.is_err() {
            let _ = self.transport.0.shutdown(Shutdown::Both);
        }
        result
    }
}

async fn direct_channel(
    handle: &client::Handle<PinnedKey>,
    port: u16,
) -> Result<Channel<client::Msg>, russh::Error> {
    handle
        .channel_open_direct_tcpip(LOOPBACK, u32::from(port), LOOPBACK, 0)
        .await
}
