"""Manage the dedicated Imp Caddy edge for Direct WSS.

Commands: hostname HOST | dns HOST | enable HOST UID | commit UID | rollback UID | disable UID.
No credential ever enters this helper. Mutations run as root (via sudo) and are
recorded as a pending transaction in a root-owned marker until `commit`.
"""

from __future__ import annotations

import contextlib
import http.client
import ipaddress
import json
import os
import re
import signal
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from types import FrameType
from typing import Any, NoReturn

from imp_relay.external_env import external_env

_IP = ipaddress.IPv4Address | ipaddress.IPv6Address
_TERMINATION = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
_ROOT_UID = 0
_ANCHOR = "/"
IMP_DIR = "/etc/imp"
UNIT_NAME = "imp-caddy.service"
UNIT_PATH = "/etc/systemd/system/imp-caddy.service"
CADDYFILE = "/etc/imp/Caddyfile"
MARKER = "/etc/imp/direct-edge.json"
STOCK_UNITS = ("/etc/systemd/system/caddy.service", "/etc/systemd/system/caddy-api.service")
CADDY_BIN = "/usr/bin/caddy"
OS_RELEASE = "/etc/os-release"
SYSTEMD_RUN_DIR = "/run/systemd/system"
# Any of these existing before the first Imp enable means the host is operator-managed.
PREEXISTING = (
    "/etc/caddy",
    "/usr/bin/caddy",
    "/usr/sbin/caddy",
    "/lib/systemd/system/caddy.service",
    "/usr/lib/systemd/system/caddy.service",
    "/etc/systemd/system/caddy.service",
    "/etc/systemd/system/caddy-api.service",
    "/etc/nginx",
    "/etc/apache2",
    "/etc/httpd",
)
APT_DIRS = ("/etc/apt/sources.list.d", "/etc/apt/trusted.gpg.d", "/etc/apt/keyrings")

_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "DEBIAN_FRONTEND": "noninteractive"}
_PROBE_PATHS = ("/unknown", "/ingest", "/action-consumer")
_EDGE_DEADLINE = 120.0
_EDGE_INTERVAL = 3.0
_EDGE_TIMEOUT = 10.0
_LOOPBACK = "127.0.0.1"
_LOOPBACK_PORT = 443
_RESERVED_TLDS = {
    "local",
    "localhost",
    "internal",
    "test",
    "invalid",
    "example",
    "lan",
    "home",
    "arpa",
    "onion",
}
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_PID = re.compile(r"pid=(\d+)")
_MARKER_KEYS = {"version", "owner_uid", "hostname", "pending"}
_PENDING_KEYS = {"op", "prior_enabled", "prior_active", "prep"}
_MANUAL = (
    "Imp will not adopt or change this. To continue, operate your own public HTTPS edge: valid TLS for the "
    "hostname, forwarding exactly /state, /action and /healthz to 127.0.0.1:8788 and answering 404 for every "
    "other path"
)

_UNIT = b"""[Unit]
Description=Imp Direct WSS Caddy edge
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
User=caddy
Group=caddy
ExecStart=/usr/bin/caddy run --config /etc/imp/Caddyfile --adapter caddyfile
Restart=on-failure
TimeoutStopSec=5s
LimitNOFILE=1048576
PrivateTmp=true
ProtectHome=true
ProtectSystem=full
NoNewPrivileges=true
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
"""


class EdgeError(Exception):
    """A concise command failure."""


class _Interrupted(BaseException):
    pass


def _refuse(message: str) -> EdgeError:
    return EdgeError(f"{message}. {_MANUAL}")


def _rerun(host: str) -> str:
    return f"./install.sh --connection direct --hostname {host}"


def _caddyfile(host: str, behind_nat: bool = False, *, version: int = 3) -> bytes:
    # Behind NAT port 80 is not forwarded; TLS-ALPN-01 on 443 stays the default challenge.
    tls = "\ttls {\n\t\tissuer acme {\n\t\t\tdisable_http_challenge\n\t\t}\n\t}\n" if behind_nat else ""
    matcher = (
        "\t@imp path /state /action /healthz\n"
        if version in (1, 2)
        else (
            "\t@imp expression `\n"
            '\t\t{http.request.orig_uri} == "/state" || {http.request.orig_uri}.startsWith("/state?") ||\n'
            '\t\t{http.request.orig_uri} == "/action" || {http.request.orig_uri}.startsWith("/action?") ||\n'
            '\t\t{http.request.orig_uri} == "/healthz" || {http.request.orig_uri}.startsWith("/healthz?")\n'
            "\t`\n"
        )
    )
    return (
        "{\n\tadmin off\n}\n\n"
        f"{host} {{\n"
        f"{tls}"
        f"{matcher}"
        "\thandle @imp {\n\t\treverse_proxy 127.0.0.1:8788\n\t}\n"
        "\thandle {\n\t\trespond 404\n\t}\n"
        "}\n"
    ).encode("ascii")


def _nat(marker: dict[str, Any]) -> bool:
    return bool(marker.get("behind_nat", False))


def _pending(op: str, enabled: bool, active: bool, prep: list[str] | None = None) -> dict[str, Any]:
    return {"op": op, "prior_enabled": enabled, "prior_active": active, "prep": prep}


def _marker_bytes(
    uid: int, host: str, pending: dict[str, Any] | None, behind_nat: bool = False, *, version: int = 3
) -> bytes:
    body: dict[str, Any] = {"version": version, "owner_uid": uid, "hostname": host, "pending": pending}
    if version in (2, 3):
        body["behind_nat"] = behind_nat
    return (json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


def _valid_host(host: str) -> bool:
    if not host.isascii() or not 4 <= len(host) <= 253:
        return False
    labels = host.split(".")
    tld = labels[-1]
    return (
        len(labels) >= 2
        and all(_LABEL.fullmatch(label) for label in labels)
        and len(tld) >= 2
        and not tld.isdigit()
        and tld not in _RESERVED_TLDS
    )


def _euid() -> int:
    return os.geteuid()


def _run(args: list[str], timeout: float = 120) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, env=_ENV, stdin=subprocess.DEVNULL
        )
    except (OSError, subprocess.TimeoutExpired):
        raise EdgeError(f"could not run {args[0]}") from None


def _cmd(*args: str, timeout: float = 120) -> str:
    result = _run(list(args), timeout)
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        suffix = f": {detail[0][:160]}" if detail else ""
        raise EdgeError(f"{' '.join(args[:2])} failed{suffix}")
    return result.stdout


@contextlib.contextmanager
def _signal_handlers(handler: Any) -> Iterator[None]:
    saved = {sig: signal.signal(sig, handler) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        yield
    finally:
        for sig, old in saved.items():
            signal.signal(sig, old)


def _raise_interrupted(signum: int, frame: FrameType | None) -> NoReturn:
    raise _Interrupted


def _sudo_command() -> list[str]:
    if getattr(sys, "frozen", False):
        # The frozen runtime has no host Python: re-execute the sibling multicall binary.
        return [os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "imp-server"), "direct-edge"]
    return [sys.executable, os.path.abspath(__file__)]


def _sudo(args: list[str], *, interactive: bool) -> int:
    interactive = interactive and sys.stdin.isatty() and sys.stdout.isatty()
    command = [
        "sudo",
        *([] if interactive else ["-n"]),
        "--",
        *_sudo_command(),
        *args,
    ]
    with _signal_handlers(signal.SIG_IGN):
        try:
            return subprocess.run(command, env=external_env()).returncode
        except OSError:
            raise EdgeError("sudo is required to change the public edge") from None


def _check_platform() -> None:
    if not sys.platform.startswith("linux"):
        raise _refuse("automatic Direct WSS edge supports Debian/Ubuntu Linux with systemd only")
    try:
        with open(OS_RELEASE, encoding="utf-8") as handle:
            ids = dict(
                line.rstrip("\n").split("=", 1) for line in handle if "=" in line and not line.startswith("#")
            )
    except OSError:
        raise _refuse(
            "could not identify this operating system; automatic Direct WSS edge needs Debian/Ubuntu"
        ) from None
    if ids.get("ID", "").strip("\"'") not in ("debian", "ubuntu"):
        raise _refuse("automatic Direct WSS edge supports Debian and Ubuntu only")
    if not os.path.isdir(SYSTEMD_RUN_DIR):
        raise _refuse("automatic Direct WSS edge requires a running systemd")


def _resolve(host: str) -> list[str]:
    try:
        info = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise EdgeError(f"{host} does not resolve in DNS; create an A/AAAA record for this server") from None
    return sorted({str(entry[4][0]).split("%")[0] for entry in info})


def _local_addresses() -> set[_IP]:
    try:
        data = json.loads(_cmd("ip", "-j", "address", "show", "scope", "global"))
        # iproute2 can emit an empty addr_info entry (observed on a Linode); it carries no address.
        found = {ipaddress.ip_address(a["local"]) for i in data for a in i.get("addr_info", []) if a}
    except (ValueError, TypeError, KeyError, AttributeError):
        raise EdgeError("could not read this server's addresses") from None
    return {ip for ip in found if ip.is_global}


def _nat_candidate(host: str) -> str:
    """The one global IPv4 A of a NAT hostname; any AAAA, extra, or non-global answer is refused."""
    try:
        resolved = [ipaddress.ip_address(a) for a in _resolve(host)]
    except ValueError:
        raise EdgeError(f"{host} resolved to an unparseable address") from None
    if any(ip.version != 4 for ip in resolved):
        raise EdgeError(f"{host} has an AAAA record; behind NAT it must have one global IPv4 A record only")
    if len(resolved) != 1:
        raise EdgeError(f"{host} needs exactly one global IPv4 A record behind NAT, found {len(resolved)}")
    ip = resolved[0]
    if not ip.is_global or ip.is_multicast or ip.is_reserved:
        raise EdgeError(f"{host} resolves to non-global address {ip}; behind NAT it must be your public IPv4")
    return str(ip)


def _check_dns(host: str, behind_nat: bool = False) -> None:
    if behind_nat:
        _nat_candidate(host)
        return
    try:
        resolved = {ipaddress.ip_address(a) for a in _resolve(host)}
    except ValueError:
        raise EdgeError(f"{host} resolved to an unparseable address") from None
    if not resolved:
        raise EdgeError(f"{host} does not resolve in DNS; create an A/AAAA record for this server")
    shown = ", ".join(sorted(map(str, resolved)))
    if not all(ip.is_global for ip in resolved):
        raise _refuse(
            f"{host} resolves to non-public address(es): {shown}; "
            "DNS must point at this server's public address"
        )
    local = _local_addresses()
    if not local:
        raise _refuse("this server has no globally routable address assigned (NAT, proxy or private hosting)")
    if not resolved <= local:
        raise EdgeError(
            f"{host} resolves to: {shown}\n"
            f"this server addresses: {', '.join(sorted(map(str, local)))}\n"
            f"update DNS so {host} resolves only to this server, then rerun: {_rerun(host)}"
        )


def _walk(directory: str) -> bool:
    """Whether directory exists with safe root-owned components; unsafe raises."""
    rel = os.path.relpath(directory, _ANCHOR)
    path = _ANCHOR
    for part in [None, *([] if rel == "." else rel.split(os.sep))]:
        path = path if part is None else os.path.join(path, part)
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return False
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != _ROOT_UID or st.st_mode & 0o022:
            raise EdgeError(f"{path} is not a safe root-owned directory")
    return True


def _read_owned(path: str) -> bytes | None:
    if not _walk(os.path.dirname(path)):
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    except OSError:
        raise EdgeError(f"{path} is not a regular file") from None
    with os.fdopen(fd, "rb") as handle:
        st = os.fstat(handle.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid != _ROOT_UID or stat.S_IMODE(st.st_mode) != 0o644:
            raise EdgeError(f"{path} is not a root-owned 0644 file")
        if st.st_nlink != 1 or st.st_size > 65536:
            raise EdgeError(f"{path} is not a single-link small file")
        return handle.read()


def _parse_marker(data: bytes, uid: int) -> dict[str, Any] | None:
    try:
        marker = json.loads(data)
        pending = marker["pending"]
        host = marker["hostname"]
        version = marker["version"]
        nat = marker.get("behind_nat", False)
        migration = pending is not None and pending.get("op") == "migrate"
        valid = (
            set(marker) == _MARKER_KEYS | ({"behind_nat"} if version in (2, 3) else set())
            and type(version) is int
            and version in (1, 2, 3)
            and isinstance(nat, bool)
            and (version != 2 or nat is True)
            and type(marker["owner_uid"]) is int
            and marker["owner_uid"] == uid
            and isinstance(host, str)
            and _valid_host(host)
            and (
                pending is None
                or (
                    set(pending)
                    == _PENDING_KEYS | ({"prior_marker", "prior_caddyfile"} if migration else set())
                    and pending["op"] in ("enable", "disable", "migrate")
                    and isinstance(pending["prior_enabled"], bool)
                    and isinstance(pending["prior_active"], bool)
                    and (
                        pending["prep"] is None
                        or (
                            isinstance(pending["prep"], list)
                            and set(pending["prep"]) <= {os.path.basename(p) for p in STOCK_UNITS}
                        )
                    )
                )
            )
            and data == _marker_bytes(uid, host, pending, nat, version=version)
            and (
                not migration
                or (
                    version == 3
                    and pending["prep"] is None
                    and pending["prior_marker"]
                    == _marker_bytes(uid, host, None, nat, version=2 if nat else 1).decode("ascii")
                    and pending["prior_caddyfile"]
                    == _caddyfile(host, nat, version=2 if nat else 1).decode("ascii")
                )
            )
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    return marker if valid else None


def _inspect(uid: int) -> tuple[str, dict[str, Any] | None]:
    """('absent'|'owned'|'foreign', marker).

    Owned means exact bytes, owner and hostname; pre-package recovery also permits
    files already removed by rollback. A foreign result carries a valid marker
    when one exists, so a pending transaction is never ignored.
    """
    contents: list[bytes | None] = []
    clean = True
    for path in (MARKER, CADDYFILE, UNIT_PATH):
        try:
            contents.append(_read_owned(path))
        except EdgeError:
            contents.append(None)
            clean = False
    marker_data, caddy_data, unit_data = contents
    if clean and all(c is None for c in contents):
        return "absent", None
    marker = _parse_marker(marker_data, uid) if marker_data is not None else None
    preparing = marker is not None and marker["pending"] is not None and marker["pending"]["prep"] is not None
    migrating = marker is not None and marker["pending"] is not None and marker["pending"]["op"] == "migrate"
    owned = (
        clean
        and marker is not None
        and (
            caddy_data == _caddyfile(marker["hostname"], _nat(marker), version=marker["version"])
            or preparing
            and caddy_data is None
            or migrating
            and caddy_data == marker["pending"]["prior_caddyfile"].encode("ascii")
        )
        and (unit_data == _UNIT or preparing and unit_data is None)
    )
    return ("owned" if owned else "foreign"), marker


def _atomic(path: str, data: bytes, *, exclusive: bool = False) -> None:
    directory = os.path.dirname(path)
    if not _walk(directory):
        raise EdgeError(f"{directory} is missing")
    # Termination signals stay blocked from staging creation until the temp is gone and the directory is
    # synced, so none can strand a temp or an fd; a pending one is delivered when the mask is restored.
    old = signal.pthread_sigmask(signal.SIG_BLOCK, _TERMINATION)
    try:
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=f".{os.path.basename(path)}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fchmod(handle.fileno(), 0o644)
                os.fsync(handle.fileno())
            if exclusive:
                os.link(tmp, path)
            else:
                os.replace(tmp, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
        dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError as exc:
        raise EdgeError(f"could not write {path}: {exc.strerror or type(exc).__name__}") from None
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old)


def _write_marker(
    uid: int, host: str, pending: dict[str, Any] | None, behind_nat: bool = False, *, version: int = 3
) -> None:
    _atomic(MARKER, _marker_bytes(uid, host, pending, behind_nat, version=version))


@contextlib.contextmanager
def _lock(*, create: bool) -> Iterator[None]:
    import fcntl

    if not _walk(IMP_DIR):
        if not create:
            yield
            return
        os.mkdir(IMP_DIR, 0o755)
        os.chmod(IMP_DIR, 0o755)
    fd = os.open(IMP_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise EdgeError("another Direct edge operation is running") from None
        yield
    finally:
        os.close(fd)


def _show() -> dict[str, str]:
    out = _cmd(
        "systemctl",
        "show",
        UNIT_NAME,
        "-p",
        "Id,LoadState,FragmentPath,DropInPaths,ActiveState,UnitFileState,NeedDaemonReload,MainPID",
    )
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def _unit_state(*, allow_unloaded: bool = False, strict: bool = True) -> tuple[bool, bool, int]:
    """(enabled, active, MainPID) of the exact Imp unit.

    strict refuses stale systemd state and any state beyond enabled/disabled and
    active/inactive, so operator drift is never adopted. Recovery passes strict=False.
    """
    props = _show()
    if allow_unloaded and props.get("Id") == UNIT_NAME and props.get("LoadState") == "not-found":
        return False, False, 0
    if (
        props.get("Id") != UNIT_NAME
        or props.get("LoadState") != "loaded"
        or props.get("FragmentPath") != UNIT_PATH
        or props.get("DropInPaths")
    ):
        raise _refuse(f"{UNIT_NAME} is not the exact Imp-owned unit")
    if strict and (
        props.get("NeedDaemonReload") == "yes"
        or props.get("UnitFileState") not in ("enabled", "disabled")
        or props.get("ActiveState") not in ("active", "inactive")
    ):
        raise _refuse(f"{UNIT_NAME} is in a state Imp did not set (stale, static, failed or transitioning)")
    return (
        props.get("UnitFileState") == "enabled",
        props.get("ActiveState") == "active",
        int(props.get("MainPID") or 0),
    )


def _check_listeners(allowed_pid: int) -> None:
    for line in _cmd("ss", "-H", "-tulnp").splitlines():
        fields = line.split()
        if len(fields) < 5:
            continue
        netid, port = fields[0], fields[4].rsplit(":", 1)[-1]
        if not ((netid == "tcp" and port in ("80", "443")) or (netid == "udp" and port == "443")):
            continue
        pids = {int(p) for p in _PID.findall(line)}
        if not pids or pids - {allowed_pid}:
            raise _refuse(f"{netid} port {port} is in use by another service")


def _preflight_fresh() -> None:
    for path in PREEXISTING:
        if os.path.lexists(path) and not (path in STOCK_UNITS and _is_mask(path)):
            raise _refuse(f"{path} exists, so this host already has operator-managed web server state")
    for directory in APT_DIRS:
        if os.path.isdir(directory) and any("caddy" in name.lower() for name in os.listdir(directory)):
            raise _refuse("Caddy-specific apt configuration already exists")
    if _run(["dpkg-query", "-W", "caddy"]).returncode == 0:
        raise _refuse("a caddy package is already known to dpkg")
    props = _show()
    if props.get("LoadState") != "not-found" or props.get("ActiveState") not in (None, "inactive"):
        raise _refuse(f"{UNIT_NAME} is already known to systemd")
    _check_listeners(0)


def _check_masks() -> None:
    for path in STOCK_UNITS:
        if not _is_mask(path):
            raise _refuse(f"{path} is not the /dev/null mask Imp created (it was changed or removed)")


def _is_mask(path: str) -> bool:
    return os.path.islink(path) and os.readlink(path) == "/dev/null"


def _mask_stock() -> None:
    """Fresh edge only: mask stock Caddy so the package default listener can never start.

    An operator's existing /dev/null mask is left as found.
    """
    for path in STOCK_UNITS:
        if not _is_mask(path):
            _cmd("systemctl", "mask", os.path.basename(path))
    _check_masks()


def _ensure_package() -> None:
    if not os.path.exists(CADDY_BIN):
        _cmd("apt-get", "update", timeout=600)
        _cmd("apt-get", "install", "-y", "--no-install-recommends", "caddy", timeout=900)
    if not os.path.exists(CADDY_BIN):
        raise EdgeError("caddy package installation did not provide /usr/bin/caddy")


def _alarm(signum: int, frame: FrameType | None) -> NoReturn:
    raise TimeoutError("probe exceeded its time limit")


def _https_status(host: str, path: str, behind_nat: bool = False) -> int:
    """Status of one HTTPS GET; the whole connect/TLS/header exchange is bounded by a timer."""
    if any(signal.getitimer(signal.ITIMER_REAL)):
        raise EdgeError("an interval timer is already armed; refusing to override it")
    connection = http.client.HTTPSConnection(
        host, 443, timeout=_EDGE_TIMEOUT, context=ssl.create_default_context()
    )
    if behind_nat:
        # Hairpin NAT may not work from inside, so only the TCP destination moves to this server; the TLS
        # handshake still validates the public hostname against the system CA store, and Host stays public.
        def loopback(address: Any, timeout: Any, source: Any = None) -> socket.socket:
            return socket.create_connection((_LOOPBACK, _LOOPBACK_PORT), timeout, source)

        connection._create_connection = loopback  # type: ignore[attr-defined]
    previous = signal.signal(signal.SIGALRM, _alarm)
    try:
        signal.setitimer(signal.ITIMER_REAL, _EDGE_TIMEOUT)
        connection.request("GET", path, headers={"User-Agent": "imp-direct-edge"})
        return connection.getresponse().status
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        connection.close()


def _verify_edge(host: str, behind_nat: bool = False) -> None:
    deadline = time.monotonic() + _EDGE_DEADLINE
    for path in _PROBE_PATHS:
        while True:
            try:
                status = _https_status(host, path, behind_nat)
            except (OSError, http.client.HTTPException) as exc:
                reason = (
                    "certificate validation failed"
                    if isinstance(exc, ssl.SSLCertVerificationError)
                    else type(exc).__name__
                )
                if time.monotonic() >= deadline:
                    raise EdgeError(f"https://{host}{path} not ready: {reason}") from None
                time.sleep(_EDGE_INTERVAL)
                continue
            if status != 404:
                raise EdgeError(
                    f"https://{host}{path} returned {status}, expected 404 (redirects are not followed)"
                )
            break


def _restore(pending: dict[str, Any]) -> None:
    """Attempt both prior-state operations, even if one fails; incomplete restoration keeps the journal."""
    prior = (pending["prior_enabled"], pending["prior_active"])
    actions = (
        ("systemctl", "daemon-reload"),
        (
            "systemctl",
            ("restart" if pending["op"] == "migrate" else "start") if prior[1] else "stop",
            UNIT_NAME,
        ),
        ("systemctl", "enable" if prior[0] else "disable", UNIT_NAME),
    )
    problems: list[str] = []
    for action in actions:
        try:
            _cmd(*action)
        except EdgeError as exc:
            problems.append(str(exc))
    try:
        if (not problems or pending["op"] != "migrate") and _unit_state(strict=False)[:2] == prior:
            return
        problems.append("state differs from the prior state")
    except EdgeError as exc:
        problems.append(str(exc))
    raise EdgeError(f"could not restore the previous edge state ({'; '.join(problems[:2])})")


def _recovery_check(status: str, marker: dict[str, Any] | None) -> None:
    if status == "foreign" and marker is not None and marker["pending"]:
        raise EdgeError(
            "recovery required: Imp edge files changed while a pending transaction exists. Nothing was "
            f"committed or restored; inspect {UNIT_NAME}, {CADDYFILE} and {MARKER} and recover manually"
        )


def _abort_prep(prep: list[str]) -> None:
    """Return to the absent baseline; only masks Imp planned to create are removed, never operator masks."""
    try:
        for path in STOCK_UNITS:
            if os.path.basename(path) in prep and _is_mask(path):
                os.unlink(path)
        for path in (UNIT_PATH, CADDYFILE):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(path)
        _cmd("systemctl", "daemon-reload")
        os.unlink(MARKER)
    except OSError as exc:
        raise EdgeError(f"preparation rollback incomplete: {exc.strerror or type(exc).__name__}") from None


def _restore_pending(uid: int, marker: dict[str, Any]) -> None:
    pending = marker["pending"]
    if pending["prep"] is not None:
        _abort_prep(pending["prep"])
        return
    if pending["op"] == "migrate":
        _check_masks()
        _unit_state(strict=False)
        _atomic(CADDYFILE, pending["prior_caddyfile"].encode("ascii"))
        _restore(pending)
        _, active, pid = _unit_state()
        _check_listeners(pid if active else 0)
        _atomic(MARKER, pending["prior_marker"].encode("ascii"))
        return
    _restore(pending)
    _write_marker(uid, marker["hostname"], None, _nat(marker), version=marker["version"])


def _undo(uid: int) -> None:
    """Roll back from persisted state, so it also covers interruptions between steps."""
    status, marker = _inspect(uid)
    _recovery_check(status, marker)
    if status == "owned" and marker is not None and marker["pending"]:
        _restore_pending(uid, marker)


def _failed(exc: BaseException, uid: int) -> None:
    with _signal_handlers(signal.SIG_IGN):
        try:
            _undo(uid)
        except EdgeError as undo:
            raise EdgeError(
                f"{exc or 'interrupted'}; rollback incomplete, pending record kept ({undo})"
            ) from exc


def _admit_change(marker: dict[str, Any]) -> None:
    if marker["pending"]:
        raise EdgeError("a pending Direct edge transaction exists; commit or roll it back first")


def _admit_enable(status: str, marker: dict[str, Any] | None, host: str, behind_nat: bool = False) -> None:
    _recovery_check(status, marker)
    if status == "foreign" or (status == "owned" and marker is None):
        raise _refuse("existing Imp edge files are not exact Imp-owned state")
    if status == "owned" and marker is not None:
        if marker["hostname"] != host:
            raise EdgeError(
                f"Imp edge is already configured for {marker['hostname']}; refusing to change hostname"
            )
        if _nat(marker) != behind_nat:
            raise EdgeError("Imp edge is already configured with a different NAT topology; refusing change")
        _admit_change(marker)
        if marker["version"] != 3:
            raise EdgeError(
                "legacy Direct edge must be migrated before enable; "
                "run ./install.sh without --connection, then retry Direct"
            )


def _enable(host: str, uid: int, behind_nat: bool = False) -> None:
    _check_platform()
    _check_dns(host, behind_nat)
    with _lock(create=True), _signal_handlers(_raise_interrupted):
        status, marker = _inspect(uid)
        _admit_enable(status, marker, host, behind_nat)
        fresh = status == "absent"
        prior = (False, False)
        if fresh:
            _preflight_fresh()
        else:
            # Validate the existing owned edge before any write or daemon-reload.
            _check_masks()
            prior = _unit_state(allow_unloaded=True)[:2]
        try:
            # Pre-package journal, durable before any generated file or mask: records which stock
            # masks Imp may create, so rollback can undo exactly those and the files published next.
            prep = [os.path.basename(p) for p in STOCK_UNITS if not _is_mask(p)] if fresh else None
            _write_marker(uid, host, _pending("enable", *prior, prep), behind_nat)
            if fresh:
                for path, data in ((CADDYFILE, _caddyfile(host, behind_nat)), (UNIT_PATH, _UNIT)):
                    _atomic(path, data, exclusive=True)
                _mask_stock()
                _write_marker(uid, host, _pending("enable", *prior), behind_nat)  # package boundary
            _ensure_package()
            _cmd("systemctl", "daemon-reload")
            _, active, pid = _unit_state()
            _check_listeners(pid if active else 0)
            _cmd(
                "runuser",
                "-u",
                "caddy",
                "--",
                CADDY_BIN,
                "validate",
                "--config",
                CADDYFILE,
                "--adapter",
                "caddyfile",
            )
            _cmd("systemctl", "enable", "--now", UNIT_NAME)
            enabled, active, pid = _unit_state()
            if not (enabled and active):
                raise EdgeError(f"{UNIT_NAME} did not become enabled and active")
            _check_listeners(pid)
            _verify_edge(host, behind_nat)
        except BaseException as exc:
            _failed(exc, uid)
            raise
    print(f"Direct edge for {host} is serving 404 on unrelated paths (pending commit)")


def _disable(uid: int) -> None:
    with _lock(create=False), _signal_handlers(_raise_interrupted):
        status, marker = _inspect(uid)
        _recovery_check(status, marker)
        if status != "owned" or marker is None:
            print("no Imp-owned Direct edge; nothing changed")
            return
        _admit_change(marker)
        enabled, active, _ = _unit_state()
        try:
            _write_marker(
                uid,
                marker["hostname"],
                _pending("disable", enabled, active),
                _nat(marker),
                version=marker["version"],
            )
            _cmd("systemctl", "disable", "--now", UNIT_NAME)
            if _unit_state(strict=False)[:2] != (False, False):
                raise EdgeError(f"{UNIT_NAME} is still enabled or active")
        except BaseException as exc:
            _failed(exc, uid)
            raise
    print("Direct edge disabled (pending commit)")


def _commit(uid: int) -> None:
    with _lock(create=False):
        status, marker = _inspect(uid)
        _recovery_check(status, marker)
        if status == "owned" and marker is not None and marker["pending"]:
            if marker["pending"]["prep"] is not None:
                raise EdgeError("Direct edge preparation did not finish; roll it back, then enable again")
            if marker["pending"]["op"] == "migrate":
                if _read_owned(CADDYFILE) != _caddyfile(marker["hostname"], _nat(marker)):
                    raise EdgeError("Direct edge migration did not finish; roll it back, then migrate again")
                prior = marker["pending"]
                _, active, pid = _unit_state()
                if _unit_state()[:2] != (prior["prior_enabled"], prior["prior_active"]):
                    raise EdgeError("Direct edge migration state changed; roll it back")
                _check_masks()
                _check_listeners(pid if active else 0)
            _write_marker(uid, marker["hostname"], None, _nat(marker), version=marker["version"])


def _rollback(uid: int) -> None:
    with _lock(create=False), _signal_handlers(signal.SIG_IGN):
        status, marker = _inspect(uid)
        _recovery_check(status, marker)
        if status == "owned" and marker is not None and marker["pending"]:
            _restore_pending(uid, marker)


def _migration_needed(uid: int) -> bool:
    status, marker = _inspect(uid)
    _recovery_check(status, marker)
    if status == "absent":
        return False
    if status != "owned" or marker is None:
        raise _refuse("existing Imp edge files are not exact Imp-owned state")
    _admit_change(marker)
    _check_masks()
    _unit_state()
    return marker["version"] in (1, 2)


def _migrate(uid: int) -> None:
    with _lock(create=False), _signal_handlers(_raise_interrupted):
        if not _migration_needed(uid):
            return
        _, marker = _inspect(uid)
        assert marker is not None
        host, nat = marker["hostname"], _nat(marker)
        enabled, active, pid = _unit_state()
        _check_listeners(pid if active else 0)
        pending = _pending("migrate", enabled, active)
        prior_marker = _read_owned(MARKER)
        prior_caddyfile = _read_owned(CADDYFILE)
        if prior_marker != _marker_bytes(
            uid, host, None, nat, version=marker["version"]
        ) or prior_caddyfile != _caddyfile(host, nat, version=marker["version"]):
            raise _refuse("legacy edge changed before migration")
        pending.update(
            prior_marker=prior_marker.decode("ascii"),
            prior_caddyfile=prior_caddyfile.decode("ascii"),
        )
        marker.update(version=3, behind_nat=nat, pending=pending)
        try:
            _write_marker(uid, host, pending, nat)
            # A unique validation file never replaces the live config until Caddy accepts it.
            old = signal.pthread_sigmask(signal.SIG_BLOCK, _TERMINATION)
            try:
                fd, candidate = tempfile.mkstemp(dir=IMP_DIR, prefix=".Caddyfile.validate.", suffix=".tmp")
                try:
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(_caddyfile(host, nat))
                        handle.flush()
                        os.fchmod(handle.fileno(), 0o644)
                    signal.pthread_sigmask(signal.SIG_SETMASK, old)
                    _cmd(
                        "runuser",
                        "-u",
                        "caddy",
                        "--",
                        CADDY_BIN,
                        "validate",
                        "--config",
                        candidate,
                        "--adapter",
                        "caddyfile",
                    )
                    status, current = _inspect(uid)
                    if status != "owned" or current != marker:
                        raise EdgeError("Direct edge changed during migration validation")
                    _atomic(CADDYFILE, _caddyfile(host, nat))
                finally:
                    signal.pthread_sigmask(signal.SIG_BLOCK, _TERMINATION)
                    os.unlink(candidate)
            except OSError as exc:
                raise EdgeError(f"could not stage edge migration: {exc.strerror}") from None
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, old)
            if active:
                _cmd("systemctl", "restart", UNIT_NAME)
            now_enabled, now_active, pid = _unit_state()
            if (now_enabled, now_active) != (enabled, active):
                raise EdgeError("Direct edge migration changed the prior service state")
            _check_listeners(pid if active else 0)
            if active:
                _verify_edge(host, nat)
        except BaseException as exc:
            _failed(exc, uid)
            raise
    print("Direct edge raw-path policy migrated (pending commit)")


_USAGE = (
    "usage: imp-direct-edge hostname HOST | dns HOST [--behind-nat] | nat-candidate HOST | "
    "enable HOST UID [--behind-nat] | commit UID | rollback UID | disable UID | owned-hostname UID | "
    "ssh-committed HOST UID | migration-needed UID | migrate UID"
)
_ARITY = {
    "hostname": 1,
    "dns": 1,
    "nat-candidate": 1,
    "enable": 2,
    "commit": 1,
    "rollback": 1,
    "disable": 1,
    "owned-hostname": 1,
    "ssh-committed": 2,
    "migration-needed": 1,
    "migrate": 1,
}


def _dispatch(args: list[str]) -> int:
    full = args
    behind_nat = bool(args) and args[0] in ("dns", "enable") and args[-1] == "--behind-nat"
    if behind_nat:
        args = args[:-1]
    if not args or args[0] not in _ARITY or len(args) - 1 != _ARITY[args[0]]:
        raise EdgeError(_USAGE)
    cmd = args[0]
    host = args[1] if cmd in ("hostname", "dns", "nat-candidate", "enable", "ssh-committed") else ""
    if cmd in ("hostname", "dns", "nat-candidate", "enable", "ssh-committed") and not _valid_host(host):
        raise EdgeError("hostname must be a lowercase ASCII public domain name (no URL, port, IP or spaces)")
    if cmd == "hostname":
        return 0
    if cmd == "nat-candidate":
        _check_platform()
        print(_nat_candidate(host))
        return 0
    if cmd == "dns":
        _check_platform()
        _check_dns(host, behind_nat)
        print(f"{host} resolves to one public IPv4" if behind_nat else f"{host} resolves only to this server")
        return 0
    if not re.fullmatch(r"[1-9][0-9]*", args[-1]):
        raise EdgeError("UID must be a positive decimal user id")
    uid = int(args[-1])
    if cmd == "migration-needed":
        if uid != os.getuid():
            raise EdgeError("UID must be the invoking user")
        with _lock(create=False):
            print("yes" if _migration_needed(uid) else "no")
        return 0
    if cmd in ("owned-hostname", "ssh-committed"):
        if uid != os.getuid():
            raise EdgeError("UID must be the invoking user")
        with _lock(create=False):
            status, marker = _inspect(uid)
            _recovery_check(status, marker)
            if cmd == "owned-hostname":
                if status == "owned" and marker is not None:
                    print(marker["hostname"])
                return 0
            if (
                status != "owned"
                or marker is None
                or marker["hostname"] != host
                or marker["pending"] is not None
                or _unit_state() != (False, False, 0)
            ):
                raise EdgeError("durable owned edge does not prove the requested SSH commit")
            return 0
    if _euid() == 0:
        if os.environ.get("SUDO_UID") != str(uid):
            raise EdgeError("run as the target user; sudo is invoked internally")
        if cmd == "enable":
            _enable(host, uid, behind_nat)
        elif cmd == "migrate":
            _migrate(uid)
        elif cmd == "disable":
            _disable(uid)
        elif cmd == "commit":
            _commit(uid)
        else:
            _rollback(uid)
        return 0
    if uid != os.getuid():
        raise EdgeError("UID must be the invoking user")
    if cmd == "migrate":
        with _lock(create=False):
            if not _migration_needed(uid):
                return 0
        return _sudo(full, interactive=True)
    status, marker = _inspect(uid)
    if cmd == "enable":
        _check_platform()
        _check_dns(host, behind_nat)
        _admit_enable(status, marker, host, behind_nat)
        if status == "absent":
            _preflight_fresh()
    else:
        _recovery_check(status, marker)
        if status != "owned" or marker is None:
            if cmd == "disable":
                print("no Imp-owned Direct edge; nothing changed")
            return 0
        if cmd == "disable":
            _admit_change(marker)
        elif not marker["pending"]:
            return 0
    return _sudo(full, interactive=cmd in ("enable", "disable"))


def main(argv: list[str] | None = None) -> int:
    try:
        return _dispatch(sys.argv[1:] if argv is None else list(argv))
    except EdgeError as exc:
        print(f"imp direct edge: {exc}", file=sys.stderr)
        return 1
    except (_Interrupted, KeyboardInterrupt):
        print("imp direct edge: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
