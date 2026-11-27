use std::{
    cell::RefCell,
    io,
    mem::{align_of, offset_of, size_of},
    net::{SocketAddr, SocketAddrV4, TcpStream},
    ptr, slice,
};

use windows_sys::Win32::{
    Foundation::{ERROR_INSUFFICIENT_BUFFER, NO_ERROR},
    NetworkManagement::IpHelper::{
        GetExtendedTcpTable, MIB_TCPROW_OWNER_PID, MIB_TCPTABLE_OWNER_PID, MIB_TCP_STATE_ESTAB,
        TCP_TABLE_OWNER_PID_CONNECTIONS,
    },
    Networking::WinSock::AF_INET,
};

use crate::runtime::OwnedChild;

const ROWS_OFFSET: usize = offset_of!(MIB_TCPTABLE_OWNER_PID, table);
const MAX_TABLE_BYTES: u32 = 8 * 1024 * 1024;
const _: () = assert!(align_of::<MIB_TCPTABLE_OWNER_PID>() <= align_of::<u32>());

pub(super) struct Identity {
    pid: u32,
    table: RefCell<Vec<u32>>,
}

impl Identity {
    pub(super) fn new(child: &mut OwnedChild) -> io::Result<Self> {
        let identity = Self {
            pid: child.id(),
            table: RefCell::default(),
        };
        identity.require_live(child)?;
        Ok(identity)
    }

    pub(super) fn observes(&self, child: &mut OwnedChild, stream: &TcpStream) -> io::Result<bool> {
        let (SocketAddr::V4(client), SocketAddr::V4(server)) =
            (stream.local_addr()?, stream.peer_addr()?)
        else {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "expected IPv4 stream",
            ));
        };

        self.require_live(child)?;
        let attributed = self.observe_context_bind(server, client)?;
        self.require_live(child)?;
        // The table attributes this established endpoint, including while queued for accept.
        Ok(attributed)
    }

    fn require_live(&self, child: &mut OwnedChild) -> io::Result<()> {
        if self.pid == 0 || child.id() != self.pid || child.try_wait()?.is_some() {
            return Err(io::Error::new(
                io::ErrorKind::BrokenPipe,
                "SSH child is not live",
            ));
        }
        Ok(())
    }

    fn observe_context_bind(&self, server: SocketAddrV4, client: SocketAddrV4) -> io::Result<bool> {
        let mut table = self.table.borrow_mut();
        for _ in 0..3 {
            let capacity = (table.len() * size_of::<u32>()) as u32;
            let mut size = capacity;
            let buffer = if table.is_empty() {
                ptr::null_mut()
            } else {
                table.as_mut_ptr().cast()
            };
            // SAFETY: The buffer is DWORD-aligned and writable for size bytes, or null for sizing.
            match unsafe {
                GetExtendedTcpTable(
                    buffer,
                    &mut size,
                    0,
                    AF_INET.into(),
                    TCP_TABLE_OWNER_PID_CONNECTIONS,
                    0,
                )
            } {
                NO_ERROR => {
                    if size > capacity || (size as usize) < ROWS_OFFSET {
                        return Err(io::Error::new(
                            io::ErrorKind::InvalidData,
                            "invalid IPv4 TCP table size",
                        ));
                    }
                    let count = table[0] as usize;
                    if count > (size as usize - ROWS_OFFSET) / size_of::<MIB_TCPROW_OWNER_PID>() {
                        return Err(io::Error::new(
                            io::ErrorKind::InvalidData,
                            "truncated IPv4 TCP table",
                        ));
                    }
                    // SAFETY: The layout uses the SDK field offset and row stride; the reported
                    // row count fits the initialized, aligned buffer and rows contain only DWORDs.
                    let rows = unsafe {
                        let first = table.as_ptr().cast::<u8>().add(ROWS_OFFSET).cast();
                        slice::from_raw_parts(first, count)
                    };
                    return attributed(rows, server, client, self.pid);
                }
                ERROR_INSUFFICIENT_BUFFER if size > capacity && size <= MAX_TABLE_BYTES => {
                    table.resize((size as usize).div_ceil(size_of::<u32>()), 0);
                }
                error => return Err(io::Error::from_raw_os_error(error as i32)),
            }
        }
        Err(io::Error::from_raw_os_error(
            ERROR_INSUFFICIENT_BUFFER as i32,
        ))
    }
}

fn attributed(
    rows: &[MIB_TCPROW_OWNER_PID],
    server: SocketAddrV4,
    client: SocketAddrV4,
    pid: u32,
) -> io::Result<bool> {
    let server_ip = u32::from_ne_bytes(server.ip().octets());
    let client_ip = u32::from_ne_bytes(client.ip().octets());
    let mut matching = rows.iter().filter(|row| {
        row.dwLocalAddr == server_ip
            && row.dwRemoteAddr == client_ip
            && u16::from_be(row.dwLocalPort as u16) == server.port()
            && u16::from_be(row.dwRemotePort as u16) == client.port()
    });
    let Some(row) = matching.next() else {
        return Ok(false);
    };
    if matching.next().is_some() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "ambiguous IPv4 TCP tuple",
        ));
    }
    if row.dwState != MIB_TCP_STATE_ESTAB as u32 {
        return Ok(false);
    }
    if row.dwOwningPid != pid {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "IPv4 TCP tuple attributed to a different PID",
        ));
    }
    // dwOwningPid is the context-bind issuer, not proof of userspace accept or exclusive use.
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        net::{Ipv4Addr, TcpListener},
        thread,
        time::{Duration, Instant},
    };

    #[test]
    fn requires_one_established_row_for_the_server_tuple_and_child() {
        let server = SocketAddrV4::new(Ipv4Addr::LOCALHOST, 0x1234);
        let client = SocketAddrV4::new(Ipv4Addr::new(127, 0, 0, 2), 0xabcd);
        let row = MIB_TCPROW_OWNER_PID {
            dwState: MIB_TCP_STATE_ESTAB as u32,
            dwLocalAddr: u32::from_ne_bytes(server.ip().octets()),
            dwLocalPort: server.port().to_be().into(),
            dwRemoteAddr: u32::from_ne_bytes(client.ip().octets()),
            dwRemotePort: client.port().to_be().into(),
            dwOwningPid: 42,
        };
        assert!(attributed(&[row], server, client, 42).unwrap());
        assert!(!attributed(&[row], client, server, 42).unwrap());
        assert!(attributed(&[row], server, client, 43).is_err());
        assert!(attributed(&[row, row], server, client, 42).is_err());
        let closed = MIB_TCPROW_OWNER_PID { dwState: 1, ..row };
        assert!(!attributed(&[closed], server, client, 42).unwrap());
    }

    #[test]
    fn context_bind_attribution_precedes_accept() {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        listener.set_nonblocking(true).unwrap();
        let stream = TcpStream::connect(listener.local_addr().unwrap()).unwrap();
        let (SocketAddr::V4(client), SocketAddr::V4(server)) =
            (stream.local_addr().unwrap(), stream.peer_addr().unwrap())
        else {
            panic!("expected IPv4 stream");
        };
        let identity = Identity {
            pid: std::process::id(),
            table: RefCell::default(),
        };
        let observe = || {
            let deadline = Instant::now() + Duration::from_millis(100);
            while Instant::now() < deadline {
                if identity.observe_context_bind(server, client).unwrap() {
                    assert!(Instant::now() < deadline, "late context bind observation");
                    return;
                }
                thread::sleep(Duration::from_millis(1));
            }
            panic!("no context bind observation for fixture connection");
        };

        // Only this thread can accept; keep the connection queued through the first snapshot.
        observe();
        let (_accepted, peer) = listener.accept().unwrap();
        assert_eq!(peer, SocketAddr::V4(client));
        observe();
    }
}
