use std::ffi::OsString;
use std::net::IpAddr;
use std::path::Path;

pub(super) struct Args {
    pub control: bool,
    pub local_port: u16,
    pub ssh: Vec<OsString>,
}

impl Args {
    pub fn parse(mut args: Vec<OsString>) -> Option<Self> {
        let control = args.first().is_some_and(|arg| arg == "--control");
        if control {
            args.remove(0);
        }
        let [config, host, user, ssh_port, local_port, remote_port] = args.as_slice() else {
            return None;
        };
        let config = Path::new(config);
        // The orchestrator owns and retains this private fixture file until ssh exits.
        if !config.is_absolute() || config.as_os_str().as_encoded_bytes().contains(&0) {
            return None;
        }
        let host = fixture_host(host.to_str()?)?;
        let user = user.to_str()?;
        if user.len() > 255
            || !matches!(user.bytes().next(), Some(b) if b.is_ascii_alphanumeric() || b == b'_')
            || !user
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
        {
            return None;
        }
        let local_port = port(local_port.to_str()?)?;
        let remote_port = port(remote_port.to_str()?)?;
        let mut ssh: Vec<OsString> = [
            "-N",
            "-T",
            "-S",
            "none",
            "-oForkAfterAuthentication=no",
            "-oExitOnForwardFailure=yes",
            "-oControlMaster=no",
            "-oBatchMode=yes",
            "-oIdentitiesOnly=yes",
            "-oIdentityAgent=none",
            "-oPreferredAuthentications=publickey",
            "-oStrictHostKeyChecking=yes",
            "-oVerifyHostKeyDNS=no",
            "-oUpdateHostKeys=no",
            "-oGlobalKnownHostsFile=none",
            "-oConnectTimeout=4",
            "-oConnectionAttempts=1",
            "-F",
        ]
        .into_iter()
        .map(OsString::from)
        .collect();
        ssh.push(config.as_os_str().to_owned());
        if ssh_port != "-" {
            let ssh_port = port(ssh_port.to_str()?)?;
            ssh.extend([OsString::from("-p"), ssh_port.to_string().into()]);
        }
        ssh.extend([
            "-L".into(),
            format!("127.0.0.1:{local_port}:127.0.0.1:{remote_port}").into(),
            "-l".into(),
            user.into(),
            "--".into(),
            host.into(),
        ]);
        Some(Self {
            control,
            local_port,
            ssh,
        })
    }
}

fn fixture_host(host: &str) -> Option<&str> {
    if host.parse::<IpAddr>().is_ok() {
        return Some(host);
    }
    let dns = host.strip_suffix('.').unwrap_or(host);
    // Do not let the resolver reinterpret a malformed IPv4 literal as an address.
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

fn port(value: &str) -> Option<u16> {
    if value.bytes().all(|byte| byte.is_ascii_digit()) {
        value.parse().ok().filter(|port| *port != 0)
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn option_like_inputs_are_rejected() {
        assert_eq!(fixture_host("127.0.0.1"), Some("127.0.0.1"));
        assert_eq!(fixture_host("fixture.example"), Some("fixture.example"));
        for input in [
            "-F",
            "x@y",
            "x y",
            "x;sh",
            "ssh://x",
            "bad..host",
            "127.1",
            "0x7f.1",
        ] {
            assert_eq!(fixture_host(input), None);
        }
        assert_eq!(port("2222"), Some(2222));
        for input in ["0", "65536", "+22", "-22", "22 xyz"] {
            assert_eq!(port(input), None);
        }
    }
}
