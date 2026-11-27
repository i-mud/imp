use std::ffi::OsStr;
use std::fs;
use std::io::{self, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd, RawFd};
use std::os::unix::fs::{FileTypeExt, MetadataExt};
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::{self, Command};

use crate::runtime::{OwnedChild, RuntimeOwner};

const HELPER: &str = "--owned-forward-linux-exec";
const CHALLENGE: u8 = b'P';
const ACK: u8 = b'A';
const EXEC: u8 = b'X';
const TCP_ESTABLISHED: u8 = 1;

pub(super) fn spawn<'a>(
    owner: &RuntimeOwner,
    program: &'a OsStr,
    args: impl IntoIterator<Item = &'a OsStr>,
) -> io::Result<OwnedChild> {
    owner.spawn_duplex(
        OsStr::new("/proc/self/exe"),
        [OsStr::new(HELPER), program].into_iter().chain(args),
    )
}

pub(super) fn run_helper_if_requested() {
    let mut args = std::env::args_os().skip(1);
    if args.next().as_deref() != Some(OsStr::new(HELPER)) {
        return;
    }
    let Some(program) = args.next() else {
        process::exit(1);
    };
    let result = (|| -> io::Result<()> {
        let mut stdin = io::stdin().lock();
        let mut stdout = io::stdout().lock();
        read_byte(&mut stdin, CHALLENGE)?;
        stdout.write_all(&[ACK])?;
        stdout.flush()?;
        read_byte(&mut stdin, EXEC)
    })();
    if result.is_ok() {
        let _ = Command::new(program).args(args).exec();
    }
    process::exit(1);
}

pub(super) struct Identity {
    pidfd: OwnedFd,
    pid: u32,
    descriptors: PathBuf,
}

impl Identity {
    pub(super) fn new(child: &mut OwnedChild) -> io::Result<Self> {
        let pid = child.id();
        let mut stdin = child.stdin.take().ok_or_else(invalid_evidence)?;
        let stdout = child.stdout.as_mut().ok_or_else(invalid_evidence)?;
        if !pid_namespace_matches(&fs::read_to_string("/proc/self/status")?, process::id()) {
            return Err(invalid_evidence());
        }
        let pidfd = owned_fd(unsafe { libc::syscall(libc::SYS_pidfd_open, pid, 0) })?;
        // Only the guardian's gated child can acknowledge a challenge sent after pidfd_open.
        stdin.write_all(&[CHALLENGE])?;
        if poll(stdout.as_raw_fd(), 2_000)? & libc::POLLIN == 0 {
            return Err(invalid_evidence());
        }
        read_byte(stdout, ACK)?;
        let identity = Self {
            pidfd,
            pid,
            descriptors: PathBuf::from(format!("/proc/{pid}/fd")),
        };
        if child.try_wait()?.is_some() || !identity.alive()? {
            return Err(invalid_evidence());
        }
        stdin.write_all(&[EXEC])?;
        Ok(identity)
    }

    pub(super) fn alive(&self) -> io::Result<bool> {
        Ok(poll(self.pidfd.as_raw_fd(), 0)? == 0)
    }

    pub(super) fn observes(&self, child: &mut OwnedChild, stream: &TcpStream) -> io::Result<bool> {
        if child.id() != self.pid {
            return Err(invalid_evidence());
        }
        if !self.alive()? || child.try_wait()?.is_some() {
            return Ok(false);
        }
        let (local, peer) = (stream.local_addr()?, stream.peer_addr()?);
        if !matches!((local, peer), (SocketAddr::V4(local), SocketAddr::V4(peer))
            if local.ip().is_loopback() && peer.ip().is_loopback())
        {
            return Err(invalid_evidence());
        }
        let client_fd = stream.as_raw_fd();
        if !established(client_fd)? || !unbound_to_device(client_fd)? {
            return Ok(false);
        }
        let namespace = socket_value::<8>(client_fd, libc::SO_NETNS_COOKIE)?;
        let mut matched = None;
        // Proc selects socket FDs; the pidfd and metadata checks reject stale slots.
        for entry in fs::read_dir(&self.descriptors)? {
            let entry = entry?;
            let fd = entry
                .file_name()
                .to_str()
                .and_then(|name| name.parse::<RawFd>().ok())
                .filter(|fd| *fd >= 0)
                .ok_or_else(invalid_evidence)?;
            let metadata = match fs::metadata(entry.path()) {
                Ok(metadata) => metadata,
                Err(error) if error.raw_os_error() == Some(libc::ENOENT) => continue,
                Err(error) => return Err(error),
            };
            if !metadata.file_type().is_socket() {
                continue;
            }
            let Some(socket) = self.duplicate(fd, &metadata)? else {
                continue;
            };
            let Some(socket) = matching_socket(socket, peer, local, namespace)? else {
                continue;
            };
            if matched.replace((fd, socket, metadata)).is_some() {
                return Err(invalid_evidence());
            }
        }
        let Some((fd, socket, metadata)) = matched else {
            return Ok(false);
        };
        // The first handle keeps this socket identity alive across the FD-table walk.
        let Some(current) = self.duplicate(fd, &metadata)? else {
            return Ok(false);
        };
        let cookie = socket_value::<8>(socket.as_raw_fd(), libc::SO_COOKIE)?;
        let Some(current) = matching_socket(current, peer, local, namespace)? else {
            return Ok(false);
        };
        Ok(
            cookie == socket_value::<8>(current.as_raw_fd(), libc::SO_COOKIE)?
                && established(client_fd)?
                && child.try_wait()?.is_none()
                && self.alive()?,
        )
    }

    fn duplicate(&self, fd: RawFd, expected: &fs::Metadata) -> io::Result<Option<OwnedFd>> {
        let result = unsafe { libc::syscall(libc::SYS_pidfd_getfd, self.pidfd.as_raw_fd(), fd, 0) };
        let file = match owned_fd(result) {
            Ok(fd) => fs::File::from(fd),
            Err(error) if error.raw_os_error() == Some(libc::EBADF) => return Ok(None),
            Err(error) => return Err(error),
        };
        let actual = file.metadata()?;
        if !same_socket_inode(&actual, expected) {
            return Err(invalid_evidence());
        }
        Ok(Some(file.into()))
    }
}

fn same_socket_inode(actual: &fs::Metadata, expected: &fs::Metadata) -> bool {
    actual.file_type().is_socket()
        && actual.ino() == expected.ino()
        && actual.dev() == expected.dev()
}

fn read_byte(source: &mut impl Read, expected: u8) -> io::Result<()> {
    let mut byte = [0];
    source.read_exact(&mut byte)?;
    if byte[0] != expected {
        return Err(invalid_evidence());
    }
    Ok(())
}

fn pid_namespace_matches(status: &str, own: u32) -> bool {
    let mut ids = status
        .lines()
        .filter_map(|line| line.strip_prefix("NStgid:"));
    let Some(mut hierarchy) = ids.next().map(str::split_whitespace) else {
        return false;
    };
    hierarchy.next().and_then(|id| id.parse().ok()) == Some(own)
        && hierarchy.next().is_none()
        && ids.next().is_none()
}
fn owned_fd(result: libc::c_long) -> io::Result<OwnedFd> {
    if result == -1 {
        return Err(io::Error::last_os_error());
    }
    Ok(unsafe { OwnedFd::from_raw_fd(result as RawFd) })
}

fn poll(fd: RawFd, timeout_ms: libc::c_int) -> io::Result<libc::c_short> {
    let mut descriptor = libc::pollfd {
        fd,
        events: libc::POLLIN,
        revents: 0,
    };
    if unsafe { libc::poll(&mut descriptor, 1, timeout_ms) } == -1 {
        return Err(io::Error::last_os_error());
    }
    Ok(descriptor.revents)
}

fn matching_socket(
    socket: OwnedFd,
    local: SocketAddr,
    peer: SocketAddr,
    namespace: [u8; 8],
) -> io::Result<Option<TcpStream>> {
    let fd = socket.as_raw_fd();
    let protocol = match socket_value::<4>(fd, libc::SO_PROTOCOL) {
        Ok(value) => i32::from_ne_bytes(value),
        Err(error) if error.raw_os_error() == Some(libc::ENOTSOCK) => return Ok(None),
        Err(error) => return Err(error),
    };
    if protocol != libc::IPPROTO_TCP {
        return Ok(None);
    }
    let stream = TcpStream::from(socket);
    if stream.local_addr()? != local {
        return Ok(None);
    }
    match stream.peer_addr() {
        Ok(addr) if addr == peer => {}
        Ok(_) => return Ok(None),
        Err(error) if error.raw_os_error() == Some(libc::ENOTCONN) => return Ok(None),
        Err(error) => return Err(error),
    }
    let matched = established(fd)?
        && unbound_to_device(fd)?
        && socket_value::<8>(fd, libc::SO_NETNS_COOKIE)? == namespace;
    Ok(matched.then_some(stream))
}

fn established(fd: RawFd) -> io::Result<bool> {
    // TCP_INFO's first UAPI byte is tcpi_state, independent of the kernel's struct size.
    let mut state = [0];
    if socket_option(fd, libc::IPPROTO_TCP, libc::TCP_INFO, &mut state)? != state.len() {
        return Err(invalid_evidence());
    }
    Ok(state[0] == TCP_ESTABLISHED)
}

fn unbound_to_device(fd: RawFd) -> io::Result<bool> {
    Ok(socket_option(
        fd,
        libc::SOL_SOCKET,
        libc::SO_BINDTODEVICE,
        &mut [0; libc::IFNAMSIZ],
    )? == 0)
}

fn socket_value<const N: usize>(fd: RawFd, name: libc::c_int) -> io::Result<[u8; N]> {
    let mut value = [0; N];
    if socket_option(fd, libc::SOL_SOCKET, name, &mut value)? != N {
        return Err(invalid_evidence());
    }
    Ok(value)
}

fn socket_option(
    fd: RawFd,
    level: libc::c_int,
    name: libc::c_int,
    value: &mut [u8],
) -> io::Result<usize> {
    let mut size = value.len() as libc::socklen_t;
    if unsafe { libc::getsockopt(fd, level, name, value.as_mut_ptr().cast(), &mut size) } == -1 {
        return Err(io::Error::last_os_error());
    }
    Ok(size as usize)
}

fn invalid_evidence() -> io::Error {
    io::Error::new(
        io::ErrorKind::InvalidData,
        "invalid or ambiguous Linux forwarding evidence",
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::{Ipv4Addr, TcpListener};

    #[test]
    fn namespace_numbers_need_a_single_unambiguous_level() {
        assert!(pid_namespace_matches("NStgid:\t42", 42));
        assert!(!pid_namespace_matches("NStgid:\t71\t42", 42));
        assert!(!pid_namespace_matches("NStgid:\t42\t42", 42));
        assert!(!pid_namespace_matches("NStgid:\t42\nNStgid:\t42", 42));
        assert!(!pid_namespace_matches("Pid:\t42", 42));
    }

    #[test]
    fn only_the_established_reverse_tuple_matches() {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let client = TcpStream::connect(listener.local_addr().unwrap()).unwrap();
        let (server, _) = listener.accept().unwrap();
        let local = client.peer_addr().unwrap();
        let peer = client.local_addr().unwrap();
        let namespace = socket_value(client.as_raw_fd(), libc::SO_NETNS_COOKIE).unwrap();
        assert!(matching_socket(server.into(), local, peer, namespace)
            .unwrap()
            .is_some());
        assert!(matching_socket(listener.into(), local, peer, namespace)
            .unwrap()
            .is_none());
        assert!(matching_socket(client.into(), local, peer, namespace)
            .unwrap()
            .is_none());
    }

    #[test]
    fn socket_metadata_rejects_replaced_slots() {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let client = TcpStream::connect(listener.local_addr().unwrap()).unwrap();
        let client = fs::File::from(OwnedFd::from(client));
        let listener = fs::File::from(OwnedFd::from(listener));
        let expected = fs::metadata(format!("/proc/self/fd/{}", client.as_raw_fd())).unwrap();
        assert!(same_socket_inode(&client.metadata().unwrap(), &expected));
        assert!(!same_socket_inode(&listener.metadata().unwrap(), &expected));
        assert!(!same_socket_inode(
            &fs::metadata("/dev/null").unwrap(),
            &expected
        ));
    }
}
