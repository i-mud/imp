from __future__ import annotations

import contextlib
import http.client
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn, cast

import pytest

from imp_relay import direct_edge as edge

HOST = "imp.example.org"
UID = os.getuid()
GLOBAL_V4 = "93.184.216.34"
GLOBAL_V6 = "2606:2800:220:1::1"
REAL_SUDO = edge._sudo
REAL_HTTPS_STATUS = edge._https_status
REAL_RUN = edge._run
REAL_RESOLVE = edge._resolve
TRANSACTION = "12345678-1234-4234-8234-123456789abc"
OTHER_TRANSACTION = "abcdefab-abcd-4abc-8abc-abcdefabcdef"


class Fake:
    """Fake host boundary: files are real (under tmp_path); commands and probes are simulated."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.enabled = False
        self.active = False
        self.pkg_known = False
        self.fail: dict[str, bool] = {}  # command prefix -> True
        self.calls: list[tuple[str, ...]] = []
        self.listeners: list[tuple[str, int, int]] = []
        self.local = [GLOBAL_V4, GLOBAL_V6]
        self.empty_addr_info = False
        self.dns = [GLOBAL_V4]
        self.https: Callable[[str, str], int] = lambda host, path: 404
        self.probe_nat: list[bool] = []
        self.sudo_calls: list[tuple[list[str], bool]] = []
        self.need_reload = "no"
        self.loaded_without_file = False

    def run(self, args: list[str], timeout: float = 120) -> SimpleNamespace:
        self.calls.append(tuple(args))
        key = " ".join(args[:2])
        if self.fail.get(key) or self.fail.get(" ".join(args[:3])):
            if args[:2] == ["systemctl", "restart"]:
                self.active = False
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")
        out = ""
        rc = 0
        if args[0] == "systemctl":
            if args[1] == "show":
                loaded = os.path.exists(edge.UNIT_PATH) or self.loaded_without_file
                out = (
                    f"Id={edge.UNIT_NAME}\nLoadState={'loaded' if loaded else 'not-found'}\n"
                    f"FragmentPath={edge.UNIT_PATH}\nDropInPaths=\n"
                    f"ActiveState={'active' if self.active else 'inactive'}\n"
                    f"UnitFileState={'enabled' if self.enabled else 'disabled'}\n"
                    f"MainPID={4242 if self.active else 0}\n"
                    f"NeedDaemonReload={self.need_reload}\n"
                )
            elif args[1] == "enable":
                self.enabled = True
                self.active = self.active or "--now" in args
            elif args[1] == "disable":
                self.enabled = False
                self.active = self.active and "--now" not in args
            elif args[1] in ("start", "restart"):
                self.active = True
            elif args[1] == "stop":
                self.active = False
            elif args[1] == "mask":
                for unit in args[2:]:
                    os.symlink("/dev/null", Path(edge.STOCK_UNITS[0]).parent / unit)
                self.loaded_without_file = os.path.exists(edge.UNIT_PATH)
            elif args[1] == "daemon-reload":
                self.loaded_without_file = os.path.exists(edge.UNIT_PATH)
        elif args[0] == "apt-get" and args[1] == "install":
            Path(edge.CADDY_BIN).write_text("caddy")
        elif args[0] == "dpkg-query":
            rc = 0 if self.pkg_known else 1
        elif args[0] == "ip":
            entries: list[dict[str, str]] = [{"local": a} for a in self.local]
            if self.empty_addr_info:
                entries.append({})
            out = json.dumps([{"addr_info": entries}])
        elif args[0] == "ss":
            out = "".join(
                f'{n} {"LISTEN" if n == "tcp" else "UNCONN"} 0 4096 *:{p} *:* users:(("x",pid={pid},fd=3))\n'
                for n, p, pid in self.listeners
            )
        return SimpleNamespace(returncode=rc, stdout=out, stderr="")

    def ran(self, *prefix: str) -> list[tuple[str, ...]]:
        return [c for c in self.calls if c[: len(prefix)] == prefix]

    def marker(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(Path(edge.MARKER).read_text()))

    def state(self) -> tuple[bool, bool]:
        return self.enabled, self.active


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fake:
    root = tmp_path / "root"
    for sub in ("etc/systemd/system", "usr/bin", "etc/apt/sources.list.d"):
        (root / sub).mkdir(parents=True)
    for p in (root, *root.rglob("*")):
        p.chmod(0o755)
    f = Fake(root)
    paths = {
        "_ANCHOR": str(root),
        "_ROOT_UID": UID,
        "IMP_DIR": str(root / "etc/imp"),
        "UNIT_PATH": str(root / "etc/systemd/system/imp-caddy.service"),
        "CADDYFILE": str(root / "etc/imp/Caddyfile"),
        "MARKER": str(root / "etc/imp/direct-edge.json"),
        "STOCK_UNITS": (
            str(root / "etc/systemd/system/caddy.service"),
            str(root / "etc/systemd/system/caddy-api.service"),
        ),
        "CADDY_BIN": str(root / "usr/bin/caddy"),
        "PREEXISTING": (
            *(str(root / p) for p in ("etc/caddy", "usr/bin/caddy", "etc/nginx", "etc/apache2")),
            str(root / "etc/systemd/system/caddy.service"),
            str(root / "etc/systemd/system/caddy-api.service"),
        ),
        "APT_DIRS": (str(root / "etc/apt/sources.list.d"),),
        "_EDGE_DEADLINE": 0.0,
        "_EDGE_INTERVAL": 0.0,
    }
    for name, value in paths.items():
        monkeypatch.setattr(edge, name, value)
    monkeypatch.setattr(edge, "_check_platform", lambda: None)
    monkeypatch.setattr(edge, "_euid", lambda: 0)
    monkeypatch.setenv("SUDO_UID", str(UID))
    monkeypatch.setattr(edge, "_run", f.run)
    monkeypatch.setattr(edge, "_resolve", lambda host: list(f.dns))

    def probe(host: str, path: str, behind_nat: bool = False) -> int:
        f.probe_nat.append(behind_nat)
        return f.https(host, path)

    monkeypatch.setattr(edge, "_https_status", probe)

    def sudo(args: list[str], *, interactive: bool) -> int:
        f.sudo_calls.append((args, interactive))
        return 0

    monkeypatch.setattr(edge, "_sudo", sudo)
    return f


def enable(host: str = HOST, nat: bool = False) -> int:
    return edge.main(["enable", host, str(UID), *(["--behind-nat"] if nat else [])])


def owned_enabled(fake: Fake) -> None:
    assert enable() == 0
    assert edge.main(["commit", str(UID)]) == 0


@pytest.mark.parametrize(
    "host",
    [
        "",
        "UPPER.example.org",
        "1.2.3.4",
        "https://imp.example.org",
        "a..org",
        "exa mple.org",
        "localhost",
        "box.local",
        "ex_ample.org",
        "imp.example.org:443",
        "-a.example.org",
        "imp.example.org.",
        "imp.example.org\n",
        "bücher.example.org",
        "a" * 64 + ".org",
        "nodots",
    ],
)
def test_malformed_hostnames_rejected(fake: Fake, host: str, capsys: pytest.CaptureFixture[str]) -> None:
    for cmd in (["hostname", host], ["dns", host], ["preflight", host], ["enable", host, str(UID)]):
        assert edge.main(cmd) == 1
    assert not fake.calls


def test_hostname_command_validates_only(fake: Fake) -> None:
    assert edge.main(["hostname", HOST]) == 0
    assert not fake.calls


def test_dns_ok_accepts_dual_stack(fake: Fake) -> None:
    fake.dns = [GLOBAL_V4, GLOBAL_V6]
    assert edge.main(["dns", HOST]) == 0


def test_dns_mismatch_reports_both_sides_and_rerun(fake: Fake, capsys: pytest.CaptureFixture[str]) -> None:
    fake.dns = ["8.8.8.8"]
    assert edge.main(["enable", HOST, str(UID)]) == 1
    err = capsys.readouterr().err
    assert HOST in err and "8.8.8.8" in err and GLOBAL_V4 in err
    assert f"./install.sh --connection direct --hostname {HOST}" in err
    assert not fake.ran("systemctl") and not os.path.exists(edge.IMP_DIR)


def test_dns_one_unassigned_answer_rejected(fake: Fake) -> None:
    fake.dns = [GLOBAL_V4, "8.8.8.8"]  # e.g. proxy/CDN front in the answer set
    assert edge.main(["dns", HOST]) == 1


def test_dns_ignores_empty_addr_info_entry(fake: Fake) -> None:
    fake.empty_addr_info = True
    assert edge.main(["dns", HOST]) == 0


@pytest.mark.parametrize("answer", ["10.0.0.5", "192.168.1.9", "127.0.0.1", "fd00::1", "100.64.0.1"])
def test_dns_private_answers_rejected(fake: Fake, answer: str, capsys: pytest.CaptureFixture[str]) -> None:
    fake.dns = [answer]
    fake.local = [answer]
    assert edge.main(["dns", HOST]) == 1
    assert "non-public" in capsys.readouterr().err


def test_dns_nat_host_without_global_address_rejected(fake: Fake, capsys: pytest.CaptureFixture[str]) -> None:
    fake.local = ["10.1.2.3"]
    assert edge.main(["dns", HOST]) == 1
    assert "NAT" in capsys.readouterr().err


def test_dns_failure_rejected(fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(host: str) -> NoReturn:
        raise edge.EdgeError("no dns")

    monkeypatch.setattr(edge, "_resolve", boom)
    assert edge.main(["dns", HOST]) == 1


@pytest.fixture
def readonly(fake: Fake, monkeypatch: pytest.MonkeyPatch) -> Callable[[list[str]], int]:
    def forbidden(*args: Any, **kwargs: Any) -> NoReturn:
        raise AssertionError("read-only preflight reached a privilege or mutation boundary")

    for name in ("_euid", "_sudo", "_lock"):
        monkeypatch.setattr(edge, name, forbidden)

    def invoke(args: list[str]) -> int:
        before = {
            p.relative_to(fake.root): p.read_bytes() if p.is_file() else None for p in fake.root.rglob("*")
        }
        result = edge.main(args)
        after = {
            p.relative_to(fake.root): p.read_bytes() if p.is_file() else None for p in fake.root.rglob("*")
        }
        assert after == before
        assert fake.state() == (False, False) and not fake.sudo_calls
        assert all(call == ("ip", "-j", "address", "show", "scope", "global") for call in fake.calls)
        return result

    return invoke


@pytest.mark.parametrize(
    ("answers", "local", "expected"),
    [
        ([GLOBAL_V4], [GLOBAL_V4], "direct"),
        ([GLOBAL_V4, GLOBAL_V6], [GLOBAL_V4, GLOBAL_V6], "direct"),
        (["8.8.8.8"], [GLOBAL_V4], "nat-mismatch 8.8.8.8"),
        (["8.8.8.8"], ["10.1.2.3"], "nat-no-public 8.8.8.8"),
        (["8.8.8.8"], [], "nat-no-public 8.8.8.8"),
    ],
)
def test_preflight_classifies_once_readonly(
    fake: Fake,
    readonly: Callable[[list[str]], int],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    answers: list[str],
    local: list[str],
    expected: str,
) -> None:
    fake.local = local
    fake.empty_addr_info = True
    lookups: list[str] = []

    def resolve(host: str) -> list[str]:
        lookups.append(host)
        return list(answers)

    monkeypatch.setattr(edge, "_resolve", resolve)
    for _ in range(2):
        assert readonly(["preflight", HOST]) == 0
        captured = capsys.readouterr()
        assert captured.out == expected + "\n" and captured.err == ""
    assert lookups == [HOST, HOST]
    assert len(fake.calls) == 2


@pytest.mark.parametrize("data", [[], [{"addr_info": []}], [{"addr_info": [{}]}]])
def test_preflight_successfully_empty_interfaces_allow_nat(
    fake: Fake,
    readonly: Callable[[list[str]], int],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    data: Any,
) -> None:
    monkeypatch.setattr(
        edge, "_run", lambda args, timeout=120: SimpleNamespace(returncode=0, stdout=json.dumps(data))
    )
    assert readonly(["preflight", HOST]) == 0
    assert capsys.readouterr().out == f"nat-no-public {GLOBAL_V4}\n"


@pytest.mark.parametrize(
    "payload",
    [
        "{",
        "{}",
        "null",
        '""',
        "0",
        "false",
        "[null]",
        "[false]",
        "[0]",
        "[[]]",
        "[{}]",
        '[{"addr_info": null}]',
        '[{"addr_info": {}}]',
        '[{"addr_info": ""}]',
        '[{"addr_info": [null]}]',
        '[{"addr_info": [false]}]',
        '[{"addr_info": [0]}]',
        '[{"addr_info": [""]}]',
        '[{"addr_info": [[]]}]',
        '[{"addr_info": [{"family": "inet"}]}]',
        '[{"addr_info": [{"local": null}]}]',
        '[{"addr_info": [{"local": false}]}]',
        '[{"addr_info": [{"local": 134744072}]}]',
        '[{"addr_info": [{"local": []}]}]',
        '[{"addr_info": [{"local": ""}]}]',
        '[{"addr_info": [{"local": "134744072"}]}]',
        '[{"addr_info": [{"local": "broken"}]}]',
        '[{"addr_info": [{"local": "999.1.2.3"}]}]',
        '[{"addr_info": [{"local": "8.8.8.8/32"}]}]',
        '[{"addr_info": [{"local": "8.8.8.8"}, false]}]',
        '[{"addr_info": [{"local": "8.8.8.8"}]}, {}]',
    ],
)
def test_malformed_interfaces_never_allow_nat_or_enable(
    fake: Fake,
    readonly: Callable[[list[str]], int],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    payload: str,
) -> None:
    def run(args: list[str], timeout: float = 120) -> SimpleNamespace:
        fake.calls.append(tuple(args))
        return SimpleNamespace(returncode=0, stdout=payload)

    monkeypatch.setattr(edge, "_run", run)
    for command in ("preflight", "dns"):
        assert readonly([command, HOST]) == 1
        captured = capsys.readouterr()
        assert captured.out == "" and "could not read this server's addresses" in captured.err
    # The privileged revalidation uses the same strict discovery before creating its lock or files.
    monkeypatch.setattr(edge, "_euid", lambda: 0)
    assert enable() == 1
    assert not os.path.exists(edge.IMP_DIR) and fake.state() == (False, False)
    assert all(call[0] == "ip" for call in fake.calls)


@pytest.mark.parametrize("failure", ["missing", "permission", "nonzero", "timeout"])
def test_preflight_ip_failure_never_allows_nat(
    fake: Fake,
    readonly: Callable[[list[str]], int],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    def run(args: list[str], **kwargs: Any) -> SimpleNamespace:
        fake.calls.append(tuple(args))
        if failure == "missing":
            raise FileNotFoundError("ip missing")
        if failure == "permission":
            raise PermissionError("ip not executable")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        return SimpleNamespace(returncode=1, stdout="", stderr="inspection denied")

    monkeypatch.setattr(edge, "_run", REAL_RUN)
    monkeypatch.setattr(subprocess, "run", run)
    assert readonly(["preflight", HOST]) == 1
    captured = capsys.readouterr()
    expected = "ip -j failed: inspection denied" if failure == "nonzero" else "could not run ip"
    assert captured.out == "" and expected in captured.err
    assert len(fake.calls) == 1
    assert readonly(["dns", HOST, "--behind-nat"]) == 0
    captured = capsys.readouterr()
    assert captured.out == f"{HOST} resolves to one public IPv4\n" and captured.err == ""
    assert len(fake.calls) == 1


@pytest.mark.parametrize("answers", [[], ["10.1.2.3"], ["127.0.0.1"], ["fd00::1"], ["broken"]])
def test_preflight_invalid_dns_never_allows_nat(
    fake: Fake,
    readonly: Callable[[list[str]], int],
    capsys: pytest.CaptureFixture[str],
    answers: list[str],
) -> None:
    fake.dns = answers
    assert readonly(["preflight", HOST]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err
    assert not fake.calls


def test_preflight_dns_failure_preserves_diagnostic(
    readonly: Callable[[list[str]], int],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(*args: Any, **kwargs: Any) -> NoReturn:
        raise socket.gaierror("resolver unavailable")

    monkeypatch.setattr(edge, "_resolve", REAL_RESOLVE)
    monkeypatch.setattr(socket, "getaddrinfo", fail)
    assert readonly(["preflight", HOST]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"{HOST} does not resolve in DNS; create an A/AAAA record for this server" in captured.err


@pytest.mark.parametrize(
    "answers",
    [[GLOBAL_V4, "8.8.8.8"], ["8.8.8.8", GLOBAL_V6], [GLOBAL_V6]],
)
@pytest.mark.parametrize("local", [[GLOBAL_V4], []])
def test_preflight_nat_rejections_preserve_direct_diagnostic(
    fake: Fake,
    readonly: Callable[[list[str]], int],
    capsys: pytest.CaptureFixture[str],
    answers: list[str],
    local: list[str],
) -> None:
    fake.dns = answers
    fake.local = local
    assert readonly(["dns", HOST]) == 1
    direct = capsys.readouterr()
    assert readonly(["preflight", HOST]) == 1
    preflight = capsys.readouterr()
    assert direct.out == preflight.out == "" and direct.err == preflight.err
    if local:
        assert (
            "this server addresses:" in preflight.err and "./install.sh --connection direct" in preflight.err
        )
    else:
        assert "no globally routable address assigned" in preflight.err


def test_removed_nat_candidate_command_rejected(fake: Fake, capsys: pytest.CaptureFixture[str]) -> None:
    assert edge.main(["nat-candidate", HOST]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "usage:" in captured.err and not fake.calls


def test_platform_rejects_other_distro(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    release = tmp_path / "os-release"
    release.write_text("ID=fedora\n")
    monkeypatch.setattr(edge, "OS_RELEASE", str(release))
    with pytest.raises(edge.EdgeError):
        edge._check_platform()


def test_fresh_enable_orders_mask_before_apt_and_records_pending(
    fake: Fake, capsys: pytest.CaptureFixture[str]
) -> None:
    assert enable() == 0
    names = [c[:2] for c in fake.calls]
    assert names.index(("systemctl", "mask")) < names.index(("apt-get", "install"))
    assert fake.marker() == {
        "hostname": HOST,
        "owner_uid": UID,
        "version": 3,
        "behind_nat": False,
        "pending": {"op": "enable", "prior_enabled": False, "prior_active": False, "prep": None},
    }
    assert fake.state() == (True, True)
    names = [c[:2] for c in fake.calls]
    assert names.index(("runuser", "-u")) < names.index(("systemctl", "enable"))
    for unit in edge.STOCK_UNITS:
        assert os.readlink(unit) == "/dev/null"
    assert edge.main(["commit", str(UID)]) == 0
    assert fake.marker()["pending"] is None


def test_caddyfile_forwards_only_exact_routes() -> None:
    lines = [line.strip() for line in edge._caddyfile(HOST).decode().splitlines()]
    assert [x for x in lines if x.startswith("reverse_proxy")] == ["reverse_proxy 127.0.0.1:8788"]
    assert "respond 404" in lines and "8787" not in edge._caddyfile(HOST).decode()


def test_caddyfile_nat_adds_only_http_challenge_disable() -> None:
    config = edge._caddyfile(HOST, True).decode()
    assert "tls {" in config and "issuer acme {" in config
    assert "disable_http_challenge" in config
    assert "disable_tlsalpn_challenge" not in config
    assert edge._caddyfile(HOST) == edge._caddyfile(HOST, False)
    assert "tls" not in edge._caddyfile(HOST).decode()
    assert "8787" not in edge._caddyfile(HOST, True).decode()


def test_nat_dns_accepts_unmatched_single_global_a(fake: Fake, capsys: pytest.CaptureFixture[str]) -> None:
    fake.local = ["10.1.2.3"]  # no local public address is required behind NAT
    fake.dns = ["8.8.8.8"]
    assert edge.main(["dns", HOST, "--behind-nat"]) == 0
    assert capsys.readouterr().out == f"{HOST} resolves to one public IPv4\n"
    assert not fake.calls  # read-only: no local address or systemd probing


@pytest.mark.parametrize(
    "answers",
    [
        ["10.0.0.5"],
        ["192.168.1.9"],
        ["127.0.0.1"],
        ["169.254.1.1"],
        ["100.64.0.1"],
        ["192.0.2.1"],
        ["224.0.0.1"],
        ["240.0.0.1"],
        ["0.0.0.0"],
        [GLOBAL_V4, "8.8.8.8"],
        [GLOBAL_V6],
        [GLOBAL_V4, GLOBAL_V6],
        [],
    ],
)
def test_nat_dns_rejects_everything_but_one_global_a(
    fake: Fake, answers: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    fake.dns = answers
    assert edge.main(["dns", HOST, "--behind-nat"]) == 1
    assert enable(nat=True) == 1
    assert capsys.readouterr().out == "" and not os.path.exists(edge.IMP_DIR)


def test_nat_dns_flag_only_on_dns_and_enable(fake: Fake) -> None:
    for cmd in (["commit", str(UID), "--behind-nat"], ["preflight", HOST, "--behind-nat"]):
        assert edge.main(cmd) == 1
    assert not fake.calls


def test_nat_enable_roundtrip_and_ownership(fake: Fake) -> None:
    fake.local = []  # not required behind NAT
    fake.dns = ["8.8.8.8"]
    assert enable(nat=True) == 0
    assert fake.probe_nat == [True] * 3
    assert Path(edge.CADDYFILE).read_bytes() == edge._caddyfile(HOST, True)
    assert fake.marker() == {
        "hostname": HOST,
        "owner_uid": UID,
        "version": 3,
        "behind_nat": True,
        "pending": {"op": "enable", "prior_enabled": False, "prior_active": False, "prep": None},
    }
    assert edge.main(["commit", str(UID)]) == 0
    assert fake.marker()["pending"] is None and fake.marker()["version"] == 3
    assert edge._inspect(UID)[0] == "owned"
    assert enable(nat=True) == 0  # same topology repeats
    assert edge.main(["rollback", str(UID)]) == 0
    assert edge.main(["disable", str(UID)]) == 0
    assert fake.marker()["behind_nat"] is True and fake.marker()["pending"]["op"] == "disable"
    assert edge.main(["rollback", str(UID)]) == 0
    assert fake.state() == (True, True) and fake.marker()["behind_nat"] is True
    assert edge.main(["disable", str(UID)]) == 0
    assert edge.main(["commit", str(UID)]) == 0
    assert fake.marker()["version"] == 3 and fake.state() == (False, False)
    assert edge._inspect(UID)[0] == "owned"


def test_nat_probe_failure_rolls_back_retaining_topology(fake: Fake) -> None:
    assert enable(nat=True) == 0 and edge.main(["commit", str(UID)]) == 0
    fake.https = lambda host, path: 502
    assert enable(nat=True) == 1
    assert fake.state() == (True, True)
    assert fake.marker()["pending"] is None and fake.marker()["behind_nat"] is True


def test_nat_fresh_failure_returns_to_absent_baseline(fake: Fake) -> None:
    fake.https = lambda host, path: 502
    assert enable(nat=True) == 1
    assert fake.state() == (False, False) and fake.marker()["pending"] is None


@pytest.mark.parametrize("owned_nat", [False, True])
def test_topology_change_is_refused_without_commands(fake: Fake, owned_nat: bool) -> None:
    assert enable(nat=owned_nat) == 0 and edge.main(["commit", str(UID)]) == 0
    before = Path(edge.MARKER).read_bytes()
    fake.calls.clear()
    assert enable(nat=not owned_nat) == 1
    assert not fake.ran("systemctl") and Path(edge.MARKER).read_bytes() == before


def test_v3_direct_marker_stays_byte_identical_and_valid(fake: Fake) -> None:
    assert enable() == 0
    pending = fake.marker()["pending"]
    assert Path(edge.MARKER).read_bytes() == edge._marker_bytes(UID, HOST, pending)
    raw = Path(edge.MARKER).read_bytes()
    assert b'"version":3' in raw and b'"behind_nat":false' in raw
    assert edge.main(["commit", str(UID)]) == 0
    assert Path(edge.MARKER).read_bytes() == edge._marker_bytes(UID, HOST, None)
    assert Path(edge.CADDYFILE).read_bytes() == edge._caddyfile(HOST)


@pytest.mark.parametrize("tamper", ["strip", "add", "false_flag", "unknown_version"])
def test_marker_topology_drift_revokes_ownership(fake: Fake, tamper: str) -> None:
    nat = tamper == "strip"
    assert enable(nat=nat) == 0 and edge.main(["commit", str(UID)]) == 0
    body = fake.marker()
    if tamper == "strip":
        body.pop("behind_nat")
        body["version"] = 1
    elif tamper == "add":
        body.update(behind_nat=True, version=2)
    elif tamper == "false_flag":
        body.update(behind_nat=False, version=2)
    else:
        body["version"] = 4
    Path(edge.MARKER).write_text(json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n")
    assert edge._inspect(UID)[0] == "foreign"


def test_unprivileged_nat_enable_forwards_flag_to_sudo(fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(edge, "_euid", lambda: UID or 1000)
    fake.local = []
    assert enable(nat=True) == 0
    assert fake.sudo_calls == [(["enable", HOST, str(UID), "--behind-nat"], True)]


def test_read_owned_rejects_fifo_without_blocking(fake: Fake) -> None:
    import threading

    os.makedirs(Path(edge.MARKER).parent, exist_ok=True)
    os.chmod(Path(edge.MARKER).parent, 0o755)
    os.mkfifo(edge.MARKER)
    outcome: list[BaseException | None] = []

    def read() -> None:
        try:
            edge._read_owned(edge.MARKER)
            outcome.append(None)
        except BaseException as exc:
            outcome.append(exc)

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    thread.join(5)
    if thread.is_alive():  # regression: a blocking open; release it before failing
        with contextlib.suppress(OSError):
            os.close(os.open(edge.MARKER, os.O_WRONLY | os.O_NONBLOCK))
        thread.join(5)
        pytest.fail("_read_owned blocked on a FIFO")
    assert isinstance(outcome[0], edge.EdgeError)


def test_apt_failure_leaves_stock_masked_and_nothing_public(fake: Fake) -> None:
    fake.fail["apt-get install"] = True
    assert enable() == 1
    assert not fake.ran("systemctl", "enable") and not fake.ran("systemctl", "start")
    assert fake.state() == (False, False)
    for unit in edge.STOCK_UNITS:
        assert os.readlink(unit) == "/dev/null"
    assert fake.marker()["pending"] is None  # owned, disabled, safe to retry
    fake.fail.clear()
    assert enable() == 0  # retry reuses the owned config without adopting anything


def absent_baseline(fake: Fake) -> bool:
    paths = (edge.MARKER, edge.CADDYFILE, edge.UNIT_PATH, *edge.STOCK_UNITS)
    return not any(os.path.lexists(p) for p in paths) and fake.state() == (False, False)


@pytest.mark.parametrize("failing", ["caddy.service", "caddy-api.service"])
def test_mask_failure_before_package_returns_to_absent_and_retries(fake: Fake, failing: str) -> None:
    fake.fail[f"systemctl mask {failing}"] = True
    assert enable() == 1
    assert absent_baseline(fake)
    assert not fake.ran("apt-get")
    assert fake.ran("systemctl", "mask", "caddy.service")
    fake.fail.clear()
    fake.calls.clear()
    assert enable() == 0  # direct retry as a fresh install
    assert len(fake.ran("systemctl", "mask")) == 2


def test_preparation_cache_reload_failure_keeps_recoverable_journal(fake: Fake) -> None:
    os.symlink("/dev/null", edge.STOCK_UNITS[0])
    fake.fail["systemctl mask caddy-api.service"] = True
    fake.fail["systemctl daemon-reload"] = True
    assert enable() == 1
    assert fake.marker()["pending"]["prep"] == ["caddy-api.service"]
    assert not os.path.exists(edge.UNIT_PATH) and not os.path.exists(edge.CADDYFILE)
    fake.fail.clear()
    assert edge.main(["rollback", str(UID)]) == 0
    assert not os.path.exists(edge.MARKER)
    assert os.readlink(edge.STOCK_UNITS[0]) == "/dev/null"
    assert enable() == 0


def test_preparation_unlink_failure_preserves_recovery_journal(
    fake: Fake, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    os.symlink("/dev/null", edge.STOCK_UNITS[0])
    fake.fail["systemctl mask caddy-api.service"] = True
    fake.fail["systemctl daemon-reload"] = True
    assert enable() == 1
    fake.fail.clear()
    capsys.readouterr()
    journal = Path(edge.MARKER).read_bytes()
    real_unlink = os.unlink

    def unlink(path: str, *args: Any, **kwargs: Any) -> None:
        if path == edge.MARKER:
            raise PermissionError(1, "Operation not permitted")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", unlink)
    with pytest.raises(edge.EdgeError, match="preparation rollback incomplete"):
        edge._abort_prep(fake.marker()["pending"]["prep"])
    assert edge.main(["rollback", str(UID)]) == 1
    output = capsys.readouterr()
    assert "preparation rollback incomplete" in output.err and "Traceback" not in output.err
    assert Path(edge.MARKER).read_bytes() == journal
    assert fake.state() == (False, False) and os.readlink(edge.STOCK_UNITS[0]) == "/dev/null"
    monkeypatch.setattr(os, "unlink", real_unlink)
    assert edge.main(["rollback", str(UID)]) == 0
    assert not Path(edge.MARKER).exists()


@pytest.mark.parametrize(
    "drift", ["none", "pending", "hostname", "config", "enabled", "active", "stale", "uid"]
)
def test_ssh_commit_reconciliation_requires_exact_owned_disabled_state(
    fake: Fake, capsys: pytest.CaptureFixture[str], drift: str
) -> None:
    owned_enabled(fake)
    capsys.readouterr()
    assert edge.main(["owned-hostname", str(UID)]) == 0
    assert capsys.readouterr().out == HOST + "\n"
    assert edge.main(["disable", str(UID)]) == 0
    if drift != "pending":
        assert edge.main(["commit", str(UID)]) == 0
    host, uid = HOST, UID
    if drift == "hostname":
        host = "other.example.org"
    elif drift == "config":
        Path(edge.CADDYFILE).write_text("operator edit\n")
    elif drift == "enabled":
        fake.enabled = True
    elif drift == "active":
        fake.active = True
    elif drift == "stale":
        fake.need_reload = "yes"
    elif drift == "uid":
        uid += 1
    fake.calls.clear()
    assert edge.main(["ssh-committed", host, str(uid)]) == (0 if drift == "none" else 1)
    assert all(c[:2] == ("systemctl", "show") for c in fake.calls)


def test_only_missing_mask_is_created_and_undone(fake: Fake) -> None:
    os.symlink("/dev/null", edge.STOCK_UNITS[0])
    fake.fail[f"systemctl mask {os.path.basename(edge.STOCK_UNITS[1])}"] = True
    assert enable() == 1
    assert os.readlink(edge.STOCK_UNITS[0]) == "/dev/null"
    assert not os.path.lexists(edge.STOCK_UNITS[1]) and not os.path.exists(edge.MARKER)


def test_non_null_stock_unit_still_refused(fake: Fake) -> None:
    Path(edge.STOCK_UNITS[0]).write_text("[Service]\n")
    assert enable() == 1
    assert not fake.ran("systemctl", "mask") and not os.path.exists(edge.MARKER)


@pytest.mark.parametrize("preexisting", [False, True])
def test_abrupt_death_before_package_is_rolled_back_from_journal(
    fake: Fake, monkeypatch: pytest.MonkeyPatch, preexisting: bool
) -> None:
    if preexisting:
        os.symlink("/dev/null", edge.STOCK_UNITS[0])
    fake.fail[f"systemctl mask {os.path.basename(edge.STOCK_UNITS[1])}"] = True
    with monkeypatch.context() as m:
        m.setattr(edge, "_failed", lambda *a, **k: None)  # process died: no in-process cleanup ran
        assert enable() == 1
    pending = fake.marker()["pending"]
    assert pending["prep"] == [os.path.basename(p) for p in edge.STOCK_UNITS[preexisting:]]
    assert edge.main(["commit", str(UID)]) == 1  # a half-prepared edge is never blessed
    assert fake.marker()["pending"] == pending
    fake.fail.clear()
    assert edge.main(["rollback", str(UID)]) == 0
    assert os.path.lexists(edge.STOCK_UNITS[0]) is preexisting  # operator mask untouched, Imp's gone
    assert not any(os.path.lexists(p) for p in (edge.MARKER, edge.CADDYFILE, edge.UNIT_PATH))
    assert enable() == 0  # retry


def test_package_failure_after_both_masks_keeps_masks_and_journal(fake: Fake) -> None:
    fake.fail["apt-get install"] = True
    assert enable() == 1
    for unit in edge.STOCK_UNITS:
        assert os.readlink(unit) == "/dev/null"
    assert os.path.exists(edge.CADDYFILE) and fake.marker()["pending"] is None
    assert edge.main(["rollback", str(UID)]) == 0  # recovery never unmasks
    for unit in edge.STOCK_UNITS:
        assert os.readlink(unit) == "/dev/null"


def test_preexisting_masks_are_never_adopted_or_unmasked(fake: Fake) -> None:
    for unit in edge.STOCK_UNITS:
        os.symlink("/dev/null", unit)
    fake.fail["apt-get install"] = True
    assert enable() == 1
    assert not fake.ran("systemctl", "mask")
    assert edge.main(["rollback", str(UID)]) == 0
    for unit in edge.STOCK_UNITS:
        assert os.readlink(unit) == "/dev/null"
    fake.fail.clear()
    assert enable() == 0


def test_failure_before_marker_removes_created_files(fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(
        uid: int, host: str, pending: dict[str, Any] | None, behind_nat: bool = False, *, version: int = 3
    ) -> NoReturn:
        raise edge.EdgeError("disk")

    monkeypatch.setattr(edge, "_write_marker", boom)
    assert enable() == 1
    assert not os.path.exists(edge.CADDYFILE) and not os.path.exists(edge.UNIT_PATH)
    assert not fake.ran("systemctl", "mask")


def test_caddy_validation_failure_never_activates(fake: Fake) -> None:
    fake.fail["runuser -u"] = True
    assert enable() == 1
    assert not fake.ran("systemctl", "enable") and fake.state() == (False, False)


@pytest.mark.parametrize(
    "responder,needle",
    [
        (lambda h, p: 301, "returned 301"),
        (lambda h, p: 200, "returned 200"),
        (lambda h, p: 502, "returned 502"),
        (lambda h, p: (_ for _ in ()).throw(ssl.SSLCertVerificationError("bad")), "certificate"),
        (lambda h, p: (_ for _ in ()).throw(ConnectionRefusedError()), "not ready"),
        (lambda h, p: 404 if p == "/unknown" else 200, "/ingest"),
        (lambda h, p: 200 if p == "/unknown" else 404, "/unknown"),
        (lambda h, p: 200 if p == "/action-consumer" else 404, "/action-consumer"),
    ],
)
def test_https_probe_failures_roll_back_fresh_enable(
    fake: Fake, capsys: pytest.CaptureFixture[str], responder: Callable[[str, str], int], needle: str
) -> None:
    fake.https = responder
    assert enable() == 1
    assert needle in capsys.readouterr().err
    assert fake.state() == (False, False)
    assert fake.marker()["pending"] is None


def test_operator_edited_caddyfile_refuses_without_commands(fake: Fake) -> None:
    owned_enabled(fake)
    fake.calls.clear()
    Path(edge.CADDYFILE).write_text("# operator edit\n")
    assert enable() == 1
    assert edge.main(["disable", str(UID)]) == 0  # not owned: no-op
    assert edge.main(["rollback", str(UID)]) == 0
    assert not fake.ran("systemctl") and Path(edge.CADDYFILE).read_text() == "# operator edit\n"
    assert fake.state() == (True, True)


@pytest.mark.parametrize("mutate", ["mode", "symlink", "other_uid", "bad_unit"])
def test_ownership_drift_revokes(fake: Fake, mutate: str) -> None:
    owned_enabled(fake)
    fake.calls.clear()
    if mutate == "mode":
        os.chmod(edge.MARKER, 0o666)
    elif mutate == "symlink":
        os.replace(edge.MARKER, edge.MARKER + ".real")
        os.symlink(edge.MARKER + ".real", edge.MARKER)
    elif mutate == "other_uid":
        assert edge._inspect(UID + 1)[0] == "foreign"
    else:
        Path(edge.UNIT_PATH).write_bytes(edge._UNIT + b"# x\n")
    assert edge._inspect(UID)[0] == "foreign" or mutate == "other_uid"
    assert not fake.ran("systemctl", "enable")


def test_hostname_change_rejected(fake: Fake, capsys: pytest.CaptureFixture[str]) -> None:
    owned_enabled(fake)
    fake.calls.clear()
    assert enable("other.example.org") == 1
    assert "refusing to change hostname" in capsys.readouterr().err
    assert not fake.ran("systemctl", "enable")


@pytest.mark.parametrize("pre", ["etc/caddy", "usr/bin/caddy", "etc/nginx", "etc/apache2"])
def test_preexisting_web_state_refused(fake: Fake, pre: str) -> None:
    (fake.root / pre).parent.mkdir(parents=True, exist_ok=True)
    (fake.root / pre).mkdir() if not pre.startswith("usr") else (fake.root / pre).write_text("x")
    assert enable() == 1
    assert not os.path.exists(edge.UNIT_PATH) and not fake.ran("systemctl")


def test_known_caddy_package_and_third_party_repo_refused(fake: Fake) -> None:
    fake.pkg_known = True
    assert enable() == 1
    fake.pkg_known = False
    (fake.root / "etc/apt/sources.list.d/caddy-stable.list").write_text("deb x")
    assert enable() == 1
    assert not os.path.exists(edge.UNIT_PATH)


@pytest.mark.parametrize("listener", [("tcp", 443, 99), ("tcp", 80, 99), ("udp", 443, 99)])
def test_foreign_listener_refused(fake: Fake, listener: tuple[str, int, int]) -> None:
    fake.listeners = [listener]
    assert enable() == 1
    assert not os.path.exists(edge.UNIT_PATH)


def test_unrelated_port_ignored(fake: Fake) -> None:
    fake.listeners = [("tcp", 8443, 99), ("udp", 53, 99)]
    assert enable() == 0


def test_repeat_unchanged_owned_edge_keeps_running_and_attributes_listeners(fake: Fake) -> None:
    owned_enabled(fake)
    fake.listeners = [("tcp", 80, 4242), ("tcp", 443, 4242), ("udp", 443, 4242)]
    fake.calls.clear()
    assert enable() == 0
    assert fake.marker()["pending"]["prior_enabled"] and fake.marker()["pending"]["prior_active"]
    assert not fake.ran("systemctl", "stop") and not fake.ran("systemctl", "start")
    assert not fake.ran("apt-get")


def test_repeat_failure_restores_enabled_active(fake: Fake) -> None:
    owned_enabled(fake)
    fake.https = lambda h, p: 302
    assert enable() == 1
    assert fake.state() == (True, True)
    assert fake.marker()["pending"] is None


def test_repeat_with_stopped_owned_edge_restores_stopped(fake: Fake) -> None:
    owned_enabled(fake)
    fake.enabled = fake.active = False
    fake.https = lambda h, p: 200
    assert enable() == 1
    assert fake.state() == (False, False)


def test_pending_conflict_blocks_enable_and_disable(fake: Fake) -> None:
    assert enable() == 0  # pending left for installer
    fake.calls.clear()
    assert enable() == 1
    assert edge.main(["disable", str(UID)]) == 1
    assert not fake.ran("systemctl", "disable")
    assert edge.main(["rollback", str(UID)]) == 0
    assert fake.state() == (False, False) and fake.marker()["pending"] is None


def test_rollback_after_repeat_enable_restores_prior_state(fake: Fake) -> None:
    owned_enabled(fake)
    assert enable() == 0
    assert edge.main(["rollback", str(UID)]) == 0
    assert fake.state() == (True, True) and fake.marker()["pending"] is None


def test_disable_is_pending_until_commit_and_rollback_restores(fake: Fake) -> None:
    owned_enabled(fake)
    assert edge.main(["disable", str(UID)]) == 0
    assert fake.state() == (False, False)
    assert fake.marker()["pending"]["op"] == "disable"
    assert edge.main(["rollback", str(UID)]) == 0
    assert fake.state() == (True, True)
    assert edge.main(["disable", str(UID)]) == 0
    assert edge.main(["commit", str(UID)]) == 0
    assert fake.state() == (False, False) and fake.marker()["pending"] is None
    assert fake.enabled is False and os.path.exists(edge.CADDYFILE)  # owned config retained


def test_disable_failure_restores_running_edge(fake: Fake) -> None:
    owned_enabled(fake)
    fake.fail["systemctl disable"] = True
    assert edge.main(["disable", str(UID)]) == 1
    # the failed disable leaves the edge as it was, so rollback to prior state succeeded
    assert fake.state() == (True, True) and fake.marker()["pending"] is None
    fake.fail.clear()
    assert edge.main(["rollback", str(UID)]) == 0
    assert fake.state() == (True, True)


def test_commit_and_rollback_noop_without_pending(fake: Fake) -> None:
    assert edge.main(["commit", str(UID)]) == 0
    assert edge.main(["rollback", str(UID)]) == 0
    assert edge.main(["disable", str(UID)]) == 0
    assert not fake.calls and not os.path.exists(edge.IMP_DIR)


def test_interrupt_rolls_back_then_exits_130(fake: Fake) -> None:
    def interrupted(host: str, path: str) -> NoReturn:
        raise edge._Interrupted

    fake.https = interrupted
    assert enable() == 130
    assert fake.state() == (False, False) and fake.marker()["pending"] is None


@pytest.mark.parametrize("nat", [False, True])
@pytest.mark.parametrize("boundary", [1, 2, 3])
def test_native_signal_after_each_publication_rolls_back_and_retries(
    fake: Fake, monkeypatch: pytest.MonkeyPatch, boundary: int, nat: bool
) -> None:
    # Atomic publications: 1 journal, 2 Caddyfile, 3 unit file; the signal lands right after publication.
    import signal

    real, count = edge._atomic, 0

    def atomic(path: str, data: bytes, **kw: Any) -> None:
        nonlocal count
        real(path, data, **kw)
        count += 1
        if count == boundary:
            os.kill(os.getpid(), signal.SIGTERM)

    monkeypatch.setattr(edge, "_atomic", atomic)
    assert enable(nat=nat) == 130
    assert not any(
        os.path.lexists(p) for p in (edge.CADDYFILE, edge.UNIT_PATH, edge.MARKER, *edge.STOCK_UNITS)
    )
    monkeypatch.setattr(edge, "_atomic", real)
    assert enable(nat=nat) == 0
    assert fake.marker().get("behind_nat", False) is nat


@pytest.mark.parametrize("publication", [1, 2])
def test_native_signal_during_staging_leaves_no_temp_and_preserves_strangers(
    fake: Fake, monkeypatch: pytest.MonkeyPatch, publication: int
) -> None:
    import signal

    dirs = {os.path.dirname(p) for p in (edge.CADDYFILE, edge.UNIT_PATH, edge.MARKER)}
    for directory in dirs:
        Path(directory).mkdir(parents=True, exist_ok=True)
    strangers = {
        os.path.join(d, name): b"operator"
        for d in dirs
        for name in (f"{os.path.basename(p)}.tmp" for p in (edge.CADDYFILE, edge.UNIT_PATH, edge.MARKER))
    }
    for stranger, content in strangers.items():
        Path(stranger).write_bytes(content)
    before = {d: set(os.listdir(d)) for d in dirs}
    fds = len(os.listdir("/proc/self/fd"))

    real, count = tempfile.mkstemp, 0

    def mkstemp(*args: Any, **kw: Any) -> tuple[int, str]:
        nonlocal count
        made: tuple[int, str] = real(*args, **kw)
        count += 1
        if count == publication:  # the signal lands right after the staging file exists
            os.kill(os.getpid(), signal.SIGTERM)
        return made

    monkeypatch.setattr(tempfile, "mkstemp", mkstemp)
    assert enable() == 130
    assert fake.state() == (False, False) and not Path(edge.MARKER).exists()
    assert {d: set(os.listdir(d)) for d in dirs} == before
    assert len(os.listdir("/proc/self/fd")) == fds
    assert enable() == 0
    for stranger, content in strangers.items():
        assert Path(stranger).read_bytes() == content
    assert not [n for d in dirs for n in os.listdir(d) if n.startswith(".") and n.endswith(".tmp")]


def test_staging_oserror_is_concise_edge_error(
    fake: Fake, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*args: Any, **kw: Any) -> NoReturn:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(tempfile, "mkstemp", boom)
    assert enable() == 1
    assert "could not write" in capsys.readouterr().err


def test_rollback_failure_keeps_pending_record(fake: Fake) -> None:
    def fail_probe(host: str, path: str) -> int:
        fake.fail["systemctl stop"] = True
        return 200

    fake.https = fail_probe
    assert enable() == 1
    assert fake.marker()["pending"] is not None
    fake.fail.clear()
    assert edge.main(["rollback", str(UID)]) == 0
    assert fake.state() == (False, False) and fake.marker()["pending"] is None


def test_root_mode_requires_matching_sudo_uid(fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUDO_UID", str(UID + 5))
    assert enable() == 1
    assert not fake.calls


def test_unprivileged_enable_uses_interactive_sudo_after_prechecks(
    fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(edge, "_euid", lambda: UID or 1000)
    assert enable() == 0
    assert fake.sudo_calls == [(["enable", HOST, str(UID)], True)]
    assert not os.path.exists(edge.UNIT_PATH)


def test_unprivileged_refusals_never_reach_sudo(fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(edge, "_euid", lambda: UID or 1000)
    fake.dns = ["8.8.8.8"]
    assert enable() == 1
    fake.dns = [GLOBAL_V4]
    fake.listeners = [("tcp", 443, 7)]
    assert enable() == 1
    assert edge.main(["disable", str(UID)]) == 0
    assert edge.main(["commit", str(UID)]) == 0
    assert edge.main(["rollback", str(UID)]) == 0
    assert fake.sudo_calls == []


def test_unprivileged_commit_rollback_use_noninteractive_sudo_only_when_pending(
    fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert enable() == 0
    monkeypatch.setattr(edge, "_euid", lambda: UID or 1000)
    assert edge.main(["commit", str(UID)]) == 0
    assert edge.main(["rollback", str(UID)]) == 0
    assert fake.sudo_calls == [(["commit", str(UID)], False), (["rollback", str(UID)], False)]


def test_unprivileged_rejects_other_uid(fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(edge, "_euid", lambda: UID or 1000)
    assert edge.main(["commit", str(UID + 1)]) == 1


def test_usage_errors_are_concise(fake: Fake, capsys: pytest.CaptureFixture[str]) -> None:
    assert edge.main([]) == 1
    assert edge.main(["enable", HOST]) == 1
    assert edge.main(["commit", "abc"]) == 1
    assert edge.main(["commit", "0"]) == 1
    assert "usage:" in capsys.readouterr().err


@pytest.mark.parametrize("returncode", [0, 1])
def test_noninteractive_sudo_never_prompts(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
) -> None:
    commands: list[list[str]] = []

    def run(command: list[str], **_: object) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(subprocess, "run", run)
    assert REAL_SUDO(["disable", str(UID)], interactive=True) == returncode
    assert commands[0][:3] == ["sudo", "-n", "--"]


@pytest.mark.parametrize("cmd", ["enable", "dns"])
def test_manual_requirement_on_unsupported_nat_and_operator_state(
    fake: Fake, capsys: pytest.CaptureFixture[str], cmd: str
) -> None:
    fake.local = ["10.1.2.3"]
    assert edge.main([cmd, HOST] + ([str(UID)] if cmd == "enable" else [])) == 1
    assert "forwarding exactly /state, /action and /healthz" in capsys.readouterr().err


def test_operator_unmask_is_drift_and_not_remasked(fake: Fake) -> None:
    owned_enabled(fake)
    os.unlink(edge.STOCK_UNITS[0])
    fake.calls.clear()
    assert enable() == 1
    assert not fake.ran("systemctl", "mask") and not fake.ran("systemctl", "enable")
    assert fake.marker()["pending"] is None and fake.state() == (True, True)


@pytest.mark.parametrize("attr,value", [("need_reload", "yes")])
def test_stale_or_unsupported_manager_state_refused_before_mutation(
    fake: Fake, attr: str, value: str
) -> None:
    owned_enabled(fake)
    setattr(fake, attr, value)
    fake.calls.clear()
    assert enable() == 1
    assert not fake.ran("systemctl", "daemon-reload") and not fake.ran("systemctl", "enable")
    assert fake.marker()["pending"] is None


def test_fresh_rejects_unit_already_loaded_in_manager(fake: Fake) -> None:
    fake.loaded_without_file = True
    assert enable() == 1
    assert not os.path.exists(edge.UNIT_PATH)


@pytest.mark.parametrize("cmd", ["commit", "rollback", "disable"])
def test_foreign_files_with_pending_marker_require_recovery(
    fake: Fake, capsys: pytest.CaptureFixture[str], cmd: str
) -> None:
    assert enable() == 0  # pending left
    Path(edge.CADDYFILE).write_text("# operator edit\n")
    fake.calls.clear()
    assert edge.main([cmd, str(UID)]) == 1
    assert "recovery required" in capsys.readouterr().err
    assert not fake.ran("systemctl") and fake.marker()["pending"] is not None
    assert Path(edge.CADDYFILE).read_text() == "# operator edit\n"


def test_restore_attempts_enable_even_when_stop_fails(fake: Fake) -> None:
    owned_enabled(fake)
    fake.enabled = fake.active = True
    marker = {"op": "disable", "prior_enabled": False, "prior_active": False}
    fake.fail["systemctl stop"] = True
    with pytest.raises(edge.EdgeError, match="could not restore"):
        edge._restore(marker)
    assert fake.enabled is False  # disable was still attempted


def test_probe_timer_bounds_slow_headers_and_restores_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    import signal
    import time

    class Slow:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def request(self, *args: Any, **kwargs: Any) -> None:
            time.sleep(5)

        def close(self) -> None:
            pass

    monkeypatch.setattr(http.client, "HTTPSConnection", Slow)
    monkeypatch.setattr(edge, "_EDGE_TIMEOUT", 0.1)
    before = signal.getsignal(signal.SIGALRM)
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        REAL_HTTPS_STATUS(HOST, "/x")
    assert time.monotonic() - start < 2
    assert signal.getsignal(signal.SIGALRM) == before
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0


def test_probe_refuses_to_override_armed_timer() -> None:
    import signal

    signal.setitimer(signal.ITIMER_REAL, 30)
    try:
        with pytest.raises(edge.EdgeError, match="already armed"):
            REAL_HTTPS_STATUS(HOST, "/x")
        assert signal.getitimer(signal.ITIMER_REAL)[0] > 0
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def test_subprocess_timeout_kills_even_a_signal_ignoring_query() -> None:
    code = (
        "import signal,time;"
        "[signal.signal(s,signal.SIG_IGN) for s in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP)];"
        "time.sleep(30)"
    )
    started = time.monotonic()
    with pytest.raises(edge.EdgeError, match="could not run"):
        REAL_RUN([sys.executable, "-c", code], timeout=0.1)
    assert time.monotonic() - started < 2


@pytest.fixture
def tls_edge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    import socket
    import subprocess
    import threading

    def serve(name: str) -> tuple[Path, SimpleNamespace]:
        cert, key = tmp_path / f"{name}.pem", tmp_path / f"{name}.key"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key)]
            + ["-out", str(cert)]
            + ["-days", "1", "-subj", f"/CN={name}", "-addext", f"subjectAltName=DNS:{name}"],
            check=True,
            capture_output=True,
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        seen = SimpleNamespace(sni=[], host=[])
        context.sni_callback = lambda sock, server_name, ctx: seen.sni.append(server_name)
        listener = socket.socket()
        listener.bind((edge._LOOPBACK, 0))
        listener.listen()

        def loop() -> None:
            while True:
                try:
                    conn, _ = listener.accept()
                except OSError:
                    return
                try:
                    with context.wrap_socket(conn, server_side=True) as tls:
                        request = tls.recv(4096).decode()
                        seen.host += [x for x in request.split("\r\n") if x.startswith("Host:")]
                        tls.sendall(b"HTTP/1.1 404 X\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                except (OSError, ssl.SSLError):
                    pass

        threading.Thread(target=loop, daemon=True).start()
        seen.port = listener.getsockname()[1]
        seen.listener = listener
        return cert, seen

    opened: list[socket.socket] = []

    def start(name: str) -> SimpleNamespace:
        cert, seen = serve(name)
        opened.append(seen.listener)
        # Trust only this disposable certificate through the normal store; TLS validation stays on.
        monkeypatch.setenv("SSL_CERT_FILE", str(cert))
        monkeypatch.setattr(edge, "_LOOPBACK_PORT", seen.port)
        return seen

    yield start
    for listener in opened:
        listener.close()


def test_nat_probe_overrides_only_tcp_destination(tls_edge: Any) -> None:
    seen = tls_edge(HOST)
    assert REAL_HTTPS_STATUS(HOST, "/unknown", True) == 404
    assert seen.sni == [HOST] and seen.host == [f"Host: {HOST}"]


def test_nat_probe_still_validates_the_public_hostname(tls_edge: Any) -> None:
    seen = tls_edge("localhost")  # trusted cert, but not for the public hostname
    with pytest.raises(ssl.SSLCertVerificationError):
        REAL_HTTPS_STATUS(HOST, "/unknown", True)
    assert seen.host == []


def test_nat_probe_rejects_untrusted_certificate(tls_edge: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    tls_edge(HOST)
    monkeypatch.setenv("SSL_CERT_FILE", "/nonexistent")
    with pytest.raises(ssl.SSLCertVerificationError):
        REAL_HTTPS_STATUS(HOST, "/unknown", True)


@pytest.mark.parametrize("behind_nat", [False, True])
def test_probe_selects_public_or_loopback_destination(
    monkeypatch: pytest.MonkeyPatch, behind_nat: bool
) -> None:
    addresses: list[tuple[str, int]] = []

    def refuse(address: tuple[str, int], *args: Any, **kwargs: Any) -> NoReturn:
        addresses.append(address)
        raise OSError("dial refused")

    monkeypatch.setattr(socket, "create_connection", refuse)
    with pytest.raises(OSError, match="dial refused"):
        REAL_HTTPS_STATUS(HOST, "/unknown", behind_nat)
    assert addresses == [("127.0.0.1", 443) if behind_nat else (HOST, 443)]


def legacy_edge(fake: Fake, nat: bool = False) -> tuple[bytes, bytes]:
    assert enable(nat=nat) == 0
    assert edge.main(["commit", str(UID)]) == 0
    version = 2 if nat else 1
    marker = edge._marker_bytes(UID, HOST, None, nat, version=version)
    config = edge._caddyfile(HOST, nat, version=version)
    Path(edge.MARKER).write_bytes(marker)
    Path(edge.CADDYFILE).write_bytes(config)
    fake.calls.clear()
    fake.probe_nat.clear()
    return marker, config


@pytest.mark.parametrize("nat", [False, True])
def test_migration_query_is_strict_credential_free_and_current_is_noop(
    fake: Fake, nat: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    assert edge.main(["migration-needed", str(UID)]) == 0
    assert capsys.readouterr().out == "no\n"
    old = legacy_edge(fake, nat)
    capsys.readouterr()
    assert edge.main(["migration-needed", str(UID)]) == 0
    assert capsys.readouterr().out == "yes\n"
    assert all(c[:2] == ("systemctl", "show") for c in fake.calls)
    assert not fake.sudo_calls
    assert (Path(edge.MARKER).read_bytes(), Path(edge.CADDYFILE).read_bytes()) == old
    assert edge.main(["migrate", str(UID)]) == 0
    assert edge.main(["commit", str(UID)]) == 0
    capsys.readouterr()
    before = (Path(edge.MARKER).read_bytes(), Path(edge.CADDYFILE).read_bytes())
    fake.calls.clear()
    assert edge.main(["migration-needed", str(UID)]) == 0
    assert capsys.readouterr().out == "no\n"
    assert edge.main(["migrate", str(UID)]) == 0
    assert all(c[:2] == ("systemctl", "show") for c in fake.calls)
    assert (Path(edge.MARKER).read_bytes(), Path(edge.CADDYFILE).read_bytes()) == before


@pytest.mark.parametrize("nat", [False, True])
@pytest.mark.parametrize("enabled,active", [(True, True), (False, True), (True, False), (False, False)])
def test_migration_preserves_baseline_and_commits_exact_bytes(
    fake: Fake, nat: bool, enabled: bool, active: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker, config = legacy_edge(fake, nat)
    fake.enabled, fake.active = enabled, active
    untouched = {}
    for relative in (
        "home/user/.config/imp/gateway.env",
        "home/user/.config/imp/identity",
        "var/lib/caddy/cert",
    ):
        path = fake.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"secret baseline\n")
        untouched[path] = (path.read_bytes(), path.stat().st_mtime_ns)
    unit = Path(edge.UNIT_PATH).read_bytes()
    run = fake.run
    candidates = []

    def validating(args: list[str], timeout: float = 120) -> SimpleNamespace:
        if args[0] == "runuser":
            candidate = Path(args[args.index("--config") + 1])
            candidates.append(candidate)
            assert candidate != Path(edge.CADDYFILE)
            assert candidate.read_bytes() == edge._caddyfile(HOST, nat)
            assert candidate.stat().st_mode & 0o777 == 0o644
            assert Path(edge.CADDYFILE).read_bytes() == config
            pending = fake.marker()["pending"]
            assert pending["prior_marker"].encode("ascii") == marker
            assert pending["prior_caddyfile"].encode("ascii") == config
            assert edge._inspect(UID)[0] == "owned"
        return run(args, timeout)

    monkeypatch.setattr(edge, "_run", validating)
    assert edge.main(["migrate", str(UID)]) == 0
    assert candidates and all(not p.exists() for p in candidates)
    assert fake.state() == (enabled, active)
    assert bool(fake.ran("systemctl", "restart")) is active
    assert not fake.ran("systemctl", "enable") and not fake.ran("systemctl", "disable")
    assert bool(fake.probe_nat) is active
    assert Path(edge.CADDYFILE).read_bytes() == edge._caddyfile(HOST, nat)
    assert edge._inspect(UID)[0] == "owned"
    assert fake.marker()["pending"]["op"] == "migrate"
    assert edge.main(["commit", str(UID)]) == 0
    assert Path(edge.MARKER).read_bytes() == edge._marker_bytes(UID, HOST, None, nat)
    assert Path(edge.UNIT_PATH).read_bytes() == unit
    assert all((p.read_bytes(), p.stat().st_mtime_ns) == before for p, before in untouched.items())
    assert all(os.readlink(p) == "/dev/null" for p in edge.STOCK_UNITS)
    assert not fake.ran("apt-get")


@pytest.mark.parametrize("nat", [False, True])
def test_legacy_disable_and_recovery_keep_version_but_enable_requires_migration(
    fake: Fake, nat: bool
) -> None:
    marker, config = legacy_edge(fake, nat)
    assert enable(nat=nat) == 1
    assert not fake.ran("systemctl", "enable")
    assert edge.main(["disable", str(UID)]) == 0
    assert fake.marker()["version"] == (2 if nat else 1)
    assert edge.main(["rollback", str(UID)]) == 0
    assert Path(edge.MARKER).read_bytes() == marker and Path(edge.CADDYFILE).read_bytes() == config
    assert edge.main(["disable", str(UID)]) == 0
    assert edge.main(["commit", str(UID)]) == 0
    assert Path(edge.MARKER).read_bytes() == marker


@pytest.mark.parametrize("command", ["migration-needed", "migrate"])
@pytest.mark.parametrize(
    "drift",
    [
        "uid",
        "hostname",
        "topology",
        "mode",
        "symlink",
        "owner",
        "mask",
        "unit",
        "config",
        "pending",
        "stale",
        "service",
    ],
)
def test_migration_refuses_drift_and_pending_before_mutation(
    fake: Fake, command: str, drift: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy_edge(fake)
    if drift in ("uid", "hostname", "topology"):
        body = fake.marker()
        if drift == "uid":
            body["owner_uid"] = UID + 1
        elif drift == "hostname":
            body["hostname"] = "other.example.org"
        else:
            body.update(version=2, behind_nat=True)
        Path(edge.MARKER).write_text(json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n")
    elif drift == "mode":
        Path(edge.CADDYFILE).chmod(0o666)
    elif drift == "symlink":
        Path(edge.CADDYFILE).unlink()
        Path(edge.CADDYFILE).symlink_to("/dev/null")
    elif drift == "owner":
        monkeypatch.setattr(edge, "_ROOT_UID", UID + 1)
    elif drift == "mask":
        os.unlink(edge.STOCK_UNITS[0])
    elif drift == "unit":
        Path(edge.UNIT_PATH).write_bytes(b"# operator unit\n")
    elif drift == "config":
        Path(edge.CADDYFILE).write_bytes(b"# operator config\n")
    elif drift == "pending":
        assert edge.main(["disable", str(UID)]) == 0
    elif drift == "stale":
        fake.need_reload = "yes"
    else:
        run = fake.run

        def foreign_service(args: list[str], timeout: float = 120) -> SimpleNamespace:
            result = run(args, timeout)
            if args[:2] == ["systemctl", "show"]:
                result.stdout = result.stdout.replace("DropInPaths=\n", "DropInPaths=/operator.conf\n")
            return result

        monkeypatch.setattr(edge, "_run", foreign_service)
    before = Path(edge.MARKER).read_bytes()
    fake.calls.clear()
    assert edge.main([command, str(UID)]) == 1
    assert Path(edge.MARKER).read_bytes() == before
    assert all(c[:2] == ("systemctl", "show") for c in fake.calls)
    assert not fake.sudo_calls


def test_migration_query_uid_and_sudo_contract(fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    legacy_edge(fake)
    monkeypatch.setattr(edge, "_euid", lambda: UID or 1000)
    assert edge.main(["migration-needed", str(UID + 1)]) == 1
    assert edge.main(["migrate", str(UID + 1)]) == 1
    assert not fake.sudo_calls
    assert edge.main(["migration-needed", str(UID)]) == 0
    assert not fake.sudo_calls
    assert edge.main(["migrate", str(UID)]) == 0
    assert fake.sudo_calls == [(["migrate", str(UID)], True)]


@pytest.mark.parametrize("nat", [False, True])
@pytest.mark.parametrize("failure", ["validation", "publication", "published", "restart", "verify"])
def test_migration_failure_restores_old_bytes_and_loaded_config(
    fake: Fake, failure: str, nat: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker, config = legacy_edge(fake, nat)
    credential = fake.root / "gateway.env"
    credential.write_bytes(b"credential baseline\n")
    before = (credential.read_bytes(), credential.stat().st_mtime_ns)
    run = fake.run
    restart_configs = []
    restarts = 0

    def commands(args: list[str], timeout: float = 120) -> SimpleNamespace:
        nonlocal restarts
        if args[:2] == ["systemctl", "restart"]:
            restarts += 1
            restart_configs.append(Path(edge.CADDYFILE).read_bytes())
            if failure == "restart" and restarts == 1:
                fake.active = False  # systemd restart can stop successfully and fail to start.
                return SimpleNamespace(returncode=1, stdout="", stderr="start failed")
        return run(args, timeout)

    monkeypatch.setattr(edge, "_run", commands)
    if failure == "validation":
        fake.fail["runuser -u"] = True
    elif failure in ("publication", "published"):
        atomic = edge._atomic
        failed = False

        def publish(path: str, data: bytes, **kw: Any) -> None:
            nonlocal failed
            if path == edge.CADDYFILE and not failed:
                failed = True
                if failure == "published":
                    atomic(path, data, **kw)
                raise edge.EdgeError("publication failed")
            atomic(path, data, **kw)

        monkeypatch.setattr(edge, "_atomic", publish)
    elif failure == "verify":
        fake.https = lambda host, path: 502
    assert edge.main(["migrate", str(UID)]) == 1
    assert Path(edge.MARKER).read_bytes() == marker
    assert Path(edge.CADDYFILE).read_bytes() == config
    assert fake.state() == (True, True)
    assert restart_configs[-1] == config
    assert not list(Path(edge.IMP_DIR).glob(".Caddyfile*.tmp"))
    assert (credential.read_bytes(), credential.stat().st_mtime_ns) == before


def test_migration_incomplete_restore_keeps_valid_journal_and_retries(fake: Fake) -> None:
    marker, config = legacy_edge(fake, True)
    fake.fail["systemctl restart"] = True
    assert edge.main(["migrate", str(UID)]) == 1
    assert fake.state() == (True, False)
    assert fake.marker()["pending"]["op"] == "migrate"
    assert Path(edge.CADDYFILE).read_bytes() == config and edge._inspect(UID)[0] == "owned"
    assert edge.main(["commit", str(UID)]) == 1
    fake.fail.clear()
    assert edge.main(["rollback", str(UID)]) == 0
    assert fake.state() == (True, True) and Path(edge.MARKER).read_bytes() == marker
    assert edge.main(["migrate", str(UID)]) == 0
    assert edge.main(["rollback", str(UID)]) == 0
    assert Path(edge.MARKER).read_bytes() == marker and Path(edge.CADDYFILE).read_bytes() == config


@pytest.mark.parametrize("snapshot", ["prior_marker", "prior_caddyfile"])
def test_migration_recovery_refuses_modified_snapshot(fake: Fake, snapshot: str) -> None:
    legacy_edge(fake)
    assert edge.main(["migrate", str(UID)]) == 0
    body = fake.marker()
    body["pending"][snapshot] += "# operator\n"
    Path(edge.MARKER).write_text(json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n")
    fake.calls.clear()
    assert edge._inspect(UID)[0] == "foreign"
    assert edge.main(["rollback", str(UID)]) == 0  # Invalid journal is never adopted or executed.
    assert not fake.calls


@pytest.mark.parametrize("nat", [False, True])
@pytest.mark.parametrize("signame", ["SIGINT", "SIGTERM", "SIGHUP"])
@pytest.mark.parametrize("boundary", [1, 2])
def test_native_migration_signal_after_journal_or_publication_restores_and_retries(
    fake: Fake, monkeypatch: pytest.MonkeyPatch, boundary: int, signame: str, nat: bool
) -> None:
    import signal

    marker, config = legacy_edge(fake, nat)
    atomic = edge._atomic
    count = 0

    def publish(path: str, data: bytes, **kw: Any) -> None:
        nonlocal count
        atomic(path, data, **kw)
        count += 1
        if count == boundary:
            os.kill(os.getpid(), getattr(signal, signame))

    monkeypatch.setattr(edge, "_atomic", publish)
    assert edge.main(["migrate", str(UID)]) == 130
    assert fake.state() == (True, True)
    assert Path(edge.MARKER).read_bytes() == marker and Path(edge.CADDYFILE).read_bytes() == config
    assert not list(Path(edge.IMP_DIR).glob(".Caddyfile*.tmp"))
    monkeypatch.setattr(edge, "_atomic", atomic)
    assert edge.main(["migrate", str(UID)]) == 0
    assert edge.main(["rollback", str(UID)]) == 0


@pytest.mark.parametrize("nat", [False, True])
@pytest.mark.parametrize("managed", ["CADDYFILE", "UNIT_PATH"])
def test_migration_refuses_operator_edit_during_validation(
    fake: Fake, nat: bool, managed: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, config = legacy_edge(fake, nat)
    run = fake.run
    path = Path(getattr(edge, managed))
    operator_bytes = b"# operator edit during validation\n"

    def commands(args: list[str], timeout: float = 120) -> SimpleNamespace:
        result = run(args, timeout)
        if args[0] == "runuser":
            path.write_bytes(operator_bytes)
        return result

    monkeypatch.setattr(edge, "_run", commands)
    assert edge.main(["migrate", str(UID)]) == 1
    assert path.read_bytes() == operator_bytes
    assert fake.marker()["pending"]["op"] == "migrate"
    assert not fake.ran("systemctl", "restart") and not fake.ran("systemctl", "stop")
    assert fake.state() == (True, True)
    assert not list(Path(edge.IMP_DIR).glob(".Caddyfile*.tmp"))
    if managed == "UNIT_PATH":
        assert Path(edge.CADDYFILE).read_bytes() == config


def inactive_legacy_edge(fake: Fake, nat: bool) -> tuple[bytes, bytes]:
    baseline = legacy_edge(fake, nat)
    assert edge.main(["disable", str(UID)]) == 0
    assert edge.main(["commit", str(UID)]) == 0
    fake.calls.clear()
    return baseline


@pytest.mark.parametrize("nat", [False, True])
@pytest.mark.parametrize("finish", ["commit", "rollback"])
def test_inactive_legacy_enable_stages_current_policy_with_legacy_journal(
    fake: Fake, nat: bool, finish: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker, config = inactive_legacy_edge(fake, nat)
    credential = fake.root / "gateway.env"
    credential.write_bytes(b"retained identity\n")
    untouched = credential.read_bytes(), credential.stat().st_mtime_ns
    run = fake.run
    candidates = []

    def commands(args: list[str], timeout: float = 120) -> SimpleNamespace:
        if args[0] == "runuser":
            candidate = Path(args[args.index("--config") + 1])
            candidates.append(candidate)
            assert candidate != Path(edge.CADDYFILE)
            assert candidate.read_bytes() == edge._caddyfile(HOST, nat)
            assert Path(edge.CADDYFILE).read_bytes() == config
            assert fake.marker() == json.loads(
                edge._marker_bytes(
                    UID, HOST, edge._pending("enable", False, False), nat, version=2 if nat else 1
                )
            )
        return run(args, timeout)

    monkeypatch.setattr(edge, "_run", commands)
    assert enable(nat=nat) == 0
    assert fake.state() == (True, True)
    assert len(candidates) == 1 and not candidates[0].exists()
    assert Path(edge.CADDYFILE).read_bytes() == edge._caddyfile(HOST, nat)
    assert edge._inspect(UID)[0] == "owned"
    assert fake.marker()["pending"] == edge._pending("enable", False, False)
    assert fake.marker()["version"] == (2 if nat else 1)
    assert edge.main([finish, str(UID)]) == 0
    if finish == "commit":
        assert Path(edge.MARKER).read_bytes() == edge._marker_bytes(UID, HOST, None, nat)
        assert fake.state() == (True, True)
    else:
        assert Path(edge.MARKER).read_bytes() == marker
        assert Path(edge.CADDYFILE).read_bytes() == config
        assert fake.state() == (False, False)
    assert (credential.read_bytes(), credential.stat().st_mtime_ns) == untouched
    assert Path(edge.UNIT_PATH).read_bytes() == edge._UNIT
    assert all(os.readlink(p) == "/dev/null" for p in edge.STOCK_UNITS)
    assert not fake.ran("apt-get") and not fake.ran("systemctl", "restart")


@pytest.mark.parametrize("nat", [False, True])
@pytest.mark.parametrize("unprivileged", [False, True])
@pytest.mark.parametrize(
    "drift",
    [
        "uid",
        "hostname",
        "topology",
        "config",
        "unit",
        "mode",
        "owner",
        "symlink",
        "mask",
        "pending",
        "enabled",
        "active",
        "pid",
        "unloaded",
        "stale",
        "dropins",
    ],
)
def test_inactive_legacy_enable_refuses_drift_before_mutation_or_sudo(
    fake: Fake, nat: bool, unprivileged: bool, drift: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    inactive_legacy_edge(fake, nat)
    if drift == "uid":
        Path(edge.MARKER).write_bytes(edge._marker_bytes(UID + 1, HOST, None, nat, version=2 if nat else 1))
    elif drift == "hostname":
        other = "other.example.org"
        Path(edge.MARKER).write_bytes(edge._marker_bytes(UID, other, None, nat, version=2 if nat else 1))
        Path(edge.CADDYFILE).write_bytes(edge._caddyfile(other, nat, version=2 if nat else 1))
    elif drift == "topology":
        Path(edge.MARKER).write_bytes(edge._marker_bytes(UID, HOST, None, not nat, version=1 if nat else 2))
        Path(edge.CADDYFILE).write_bytes(edge._caddyfile(HOST, not nat, version=1 if nat else 2))
    elif drift == "config":
        Path(edge.CADDYFILE).write_bytes(b"# operator config\n")
    elif drift == "unit":
        Path(edge.UNIT_PATH).write_bytes(b"# operator unit\n")
    elif drift == "mode":
        Path(edge.CADDYFILE).chmod(0o666)
    elif drift == "owner":
        monkeypatch.setattr(edge, "_ROOT_UID", UID + 1)
    elif drift == "symlink":
        Path(edge.CADDYFILE).unlink()
        Path(edge.CADDYFILE).symlink_to("/dev/null")
    elif drift == "mask":
        os.unlink(edge.STOCK_UNITS[0])
    elif drift == "pending":
        edge._write_marker(UID, HOST, edge._pending("enable", False, False), nat, version=2 if nat else 1)
    elif drift == "enabled":
        fake.enabled = True
    elif drift == "active":
        fake.active = True
    elif drift == "stale":
        fake.need_reload = "yes"
    else:
        run = fake.run

        def commands(args: list[str], timeout: float = 120) -> SimpleNamespace:
            result = run(args, timeout)
            if args[:2] == ["systemctl", "show"]:
                before, after = {
                    "pid": ("MainPID=0", "MainPID=4242"),
                    "unloaded": ("LoadState=loaded", "LoadState=not-found"),
                    "dropins": ("DropInPaths=\n", "DropInPaths=/operator.conf\n"),
                }[drift]
                result.stdout = result.stdout.replace(before, after)
            return result

        monkeypatch.setattr(edge, "_run", commands)
    if unprivileged:
        monkeypatch.setattr(edge, "_euid", lambda: UID or 1000)
    before = Path(edge.MARKER).read_bytes()
    fake.calls.clear()
    assert enable(nat=nat) == 1
    assert Path(edge.MARKER).read_bytes() == before
    assert all(c[0] in ("ip", "systemctl") and (c[0] != "systemctl" or c[1] == "show") for c in fake.calls)
    assert not fake.sudo_calls


@pytest.mark.parametrize("nat", [False, True])
@pytest.mark.parametrize("drift", ["enabled", "active", "pid"])
def test_legacy_enable_rechecks_second_unit_read_before_journal(
    fake: Fake, nat: bool, drift: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker, config = inactive_legacy_edge(fake, nat)
    paths = [Path(p) for p in (edge.MARKER, edge.CADDYFILE, edge.UNIT_PATH)]
    before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
    run = fake.run
    reads = 0

    def commands(args: list[str], timeout: float = 120) -> SimpleNamespace:
        nonlocal reads
        result = run(args, timeout)
        if args[:2] == ["systemctl", "show"]:
            reads += 1
            if reads == 1:
                fake.enabled = drift == "enabled"
                fake.active = drift == "active"
            elif drift == "pid":
                result.stdout = result.stdout.replace("MainPID=0", "MainPID=4242")
        return result

    monkeypatch.setattr(edge, "_run", commands)
    assert enable(nat=nat) == 1
    assert reads == 2
    assert [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths] == before
    assert Path(edge.MARKER).read_bytes() == marker
    assert Path(edge.CADDYFILE).read_bytes() == config
    assert edge._inspect(UID)[0] == "owned"
    assert fake.state() == (drift == "enabled", drift == "active")
    assert all(
        c[0] in ("ip", "ss", "systemctl") and (c[0] != "systemctl" or c[1] == "show") for c in fake.calls
    )
    assert not fake.sudo_calls


@pytest.mark.parametrize("nat", [False, True])
@pytest.mark.parametrize("unprivileged", [False, True])
@pytest.mark.parametrize("netid,port", [("tcp", 80), ("tcp", 443), ("udp", 443)])
def test_legacy_enable_refuses_foreign_listener_before_mutation_or_sudo(
    fake: Fake, nat: bool, unprivileged: bool, netid: str, port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    inactive_legacy_edge(fake, nat)
    fake.listeners = [(netid, port, 9000)]
    paths = [Path(p) for p in (edge.MARKER, edge.CADDYFILE, edge.UNIT_PATH)]
    before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
    if unprivileged:
        monkeypatch.setattr(edge, "_euid", lambda: UID or 1000)
    assert enable(nat=nat) == 1
    assert [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths] == before
    assert fake.state() == (False, False)
    assert edge._inspect(UID)[0] == "owned"
    assert fake.ran("ss")
    assert all(
        c[0] in ("ip", "ss", "systemctl") and (c[0] != "systemctl" or c[1] == "show") for c in fake.calls
    )
    assert not fake.sudo_calls


@pytest.mark.parametrize("nat", [False, True])
@pytest.mark.parametrize(
    "failure", ["validation", "publication", "published", "activation", "state", "pid", "verify"]
)
def test_inactive_legacy_enable_failure_restores_exact_baseline(
    fake: Fake, nat: bool, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker, config = inactive_legacy_edge(fake, nat)
    if failure == "validation":
        fake.fail["runuser -u"] = True
    elif failure in ("publication", "published"):
        atomic = edge._atomic
        failed = False

        def publish(path: str, data: bytes, **kw: Any) -> None:
            nonlocal failed
            if path == edge.CADDYFILE and not failed:
                failed = True
                if failure == "published":
                    atomic(path, data, **kw)
                raise edge.EdgeError("publication failed")
            atomic(path, data, **kw)

        monkeypatch.setattr(edge, "_atomic", publish)
    elif failure in ("activation", "state", "pid"):
        run = fake.run

        def commands(args: list[str], timeout: float = 120) -> SimpleNamespace:
            result = run(args, timeout)
            if args[:2] == ["systemctl", "enable"]:
                if failure == "activation":
                    result.returncode, result.stderr = 1, "activation failed after start"
                elif failure == "state":
                    fake.enabled = False
            if failure == "pid" and fake.active and args[:2] == ["systemctl", "show"]:
                result.stdout = result.stdout.replace("MainPID=4242", "MainPID=0")
            return result

        monkeypatch.setattr(edge, "_run", commands)
    else:
        fake.https = lambda host, path: 502
    assert enable(nat=nat) == 1
    assert Path(edge.MARKER).read_bytes() == marker
    assert Path(edge.CADDYFILE).read_bytes() == config
    assert fake.state() == (False, False)
    assert not list(Path(edge.IMP_DIR).glob(".Caddyfile*.tmp"))
    assert not fake.ran("systemctl", "start") and not fake.ran("systemctl", "restart")


@pytest.mark.parametrize("nat", [False, True])
@pytest.mark.parametrize("failure", ["stop", "disable", "state", "listener", "restore"])
def test_inactive_legacy_rollback_failure_keeps_current_policy_and_journal(
    fake: Fake, nat: bool, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker, config = inactive_legacy_edge(fake, nat)
    assert enable(nat=nat) == 0
    journal = Path(edge.MARKER).read_bytes()
    if failure in ("stop", "disable"):
        fake.fail[f"systemctl {failure}"] = True
    elif failure == "state":
        fake.need_reload = "yes"
    elif failure == "listener":
        fake.listeners = [("tcp", 443, 4242)]
    else:
        atomic = edge._atomic

        def publish(path: str, data: bytes, **kw: Any) -> None:
            if path == edge.CADDYFILE:
                assert fake.state() == (False, False)
                raise edge.EdgeError("restore failed")
            atomic(path, data, **kw)

        monkeypatch.setattr(edge, "_atomic", publish)
    assert edge.main(["rollback", str(UID)]) == 1
    assert Path(edge.MARKER).read_bytes() == journal
    assert Path(edge.CADDYFILE).read_bytes() == edge._caddyfile(HOST, nat)
    assert edge._inspect(UID)[0] == "owned"
    fake.fail.clear()
    fake.need_reload = "no"
    fake.listeners.clear()
    if failure == "restore":
        monkeypatch.setattr(edge, "_atomic", atomic)
    assert edge.main(["rollback", str(UID)]) == 0
    assert Path(edge.MARKER).read_bytes() == marker
    assert Path(edge.CADDYFILE).read_bytes() == config
    assert fake.state() == (False, False)


@pytest.mark.parametrize("nat", [False, True])
def test_old_pending_legacy_enable_cannot_commit_or_restart(fake: Fake, nat: bool) -> None:
    marker, config = inactive_legacy_edge(fake, nat)
    edge._write_marker(UID, HOST, edge._pending("enable", False, False), nat, version=2 if nat else 1)
    journal = Path(edge.MARKER).read_bytes()
    assert edge.main(["commit", str(UID)]) == 1
    assert enable(nat=nat) == 1
    assert Path(edge.MARKER).read_bytes() == journal
    assert Path(edge.CADDYFILE).read_bytes() == config
    assert not fake.ran("systemctl", "enable")
    assert edge.main(["rollback", str(UID)]) == 0
    assert Path(edge.MARKER).read_bytes() == marker


@pytest.mark.parametrize("nat", [False, True])
@pytest.mark.parametrize("drift", ["enabled", "active", "pid", "mask", "stale"])
def test_legacy_enable_commit_rechecks_owned_running_service(
    fake: Fake, nat: bool, drift: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    inactive_legacy_edge(fake, nat)
    assert enable(nat=nat) == 0
    journal = Path(edge.MARKER).read_bytes()
    if drift == "enabled":
        fake.enabled = False
    elif drift == "active":
        fake.active = False
    elif drift == "mask":
        os.unlink(edge.STOCK_UNITS[0])
    elif drift == "stale":
        fake.need_reload = "yes"
    else:
        run = fake.run

        def commands(args: list[str], timeout: float = 120) -> SimpleNamespace:
            result = run(args, timeout)
            if args[:2] == ["systemctl", "show"]:
                result.stdout = result.stdout.replace("MainPID=4242", "MainPID=0")
            return result

        monkeypatch.setattr(edge, "_run", commands)
    assert edge.main(["commit", str(UID)]) == 1
    assert Path(edge.MARKER).read_bytes() == journal


@pytest.mark.parametrize("nat", [False, True])
@pytest.mark.parametrize("managed", ["CADDYFILE", "UNIT_PATH", "MARKER"])
def test_legacy_enable_rechecks_ownership_after_validation(
    fake: Fake, nat: bool, managed: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, config = inactive_legacy_edge(fake, nat)
    run = fake.run
    path = Path(getattr(edge, managed))
    operator_bytes = b"# operator edit during validation\n"

    def commands(args: list[str], timeout: float = 120) -> SimpleNamespace:
        result = run(args, timeout)
        if args[0] == "runuser":
            path.write_bytes(operator_bytes)
        return result

    monkeypatch.setattr(edge, "_run", commands)
    assert enable(nat=nat) == 1
    assert path.read_bytes() == operator_bytes
    assert not fake.ran("systemctl", "enable") and not fake.ran("systemctl", "stop")
    assert fake.state() == (False, False)
    assert not list(Path(edge.IMP_DIR).glob(".Caddyfile*.tmp"))
    if managed != "CADDYFILE":
        assert Path(edge.CADDYFILE).read_bytes() == config


@pytest.mark.parametrize("nat", [False, True])
@pytest.mark.parametrize("signame", ["SIGINT", "SIGTERM", "SIGHUP"])
@pytest.mark.parametrize("boundary", [1, 2])
def test_legacy_enable_signal_after_journal_or_publication_restores_baseline(
    fake: Fake, nat: bool, signame: str, boundary: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    import signal

    marker, config = inactive_legacy_edge(fake, nat)
    atomic = edge._atomic
    count = 0

    def publish(path: str, data: bytes, **kw: Any) -> None:
        nonlocal count
        atomic(path, data, **kw)
        count += 1
        if count == boundary:
            os.kill(os.getpid(), getattr(signal, signame))

    monkeypatch.setattr(edge, "_atomic", publish)
    assert enable(nat=nat) == 130
    assert fake.state() == (False, False)
    assert Path(edge.MARKER).read_bytes() == marker and Path(edge.CADDYFILE).read_bytes() == config
    assert not list(Path(edge.IMP_DIR).glob(".Caddyfile*.tmp"))


@pytest.mark.parametrize("nat", [False, True])
@pytest.mark.parametrize("target", ["config", "marker"])
@pytest.mark.parametrize("published", [False, True])
def test_legacy_rollback_publication_error_leaves_only_exact_templates_while_stopped(
    fake: Fake, nat: bool, target: str, published: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker, config = inactive_legacy_edge(fake, nat)
    assert enable(nat=nat) == 0
    journal = Path(edge.MARKER).read_bytes()
    atomic = edge._atomic
    failed = False
    target_path = edge.CADDYFILE if target == "config" else edge.MARKER

    def publish(path: str, data: bytes, **kw: Any) -> None:
        nonlocal failed
        if path == target_path and not failed:
            failed = True
            assert fake.state() == (False, False)
            if published:
                atomic(path, data, **kw)
            raise edge.EdgeError("rollback publication failed")
        atomic(path, data, **kw)

    monkeypatch.setattr(edge, "_atomic", publish)
    assert edge.main(["rollback", str(UID)]) == 1
    expected_marker = marker if target == "marker" and published else journal
    expected_config = edge._caddyfile(HOST, nat) if target == "config" and not published else config
    assert Path(edge.MARKER).read_bytes() == expected_marker
    assert Path(edge.CADDYFILE).read_bytes() == expected_config
    assert fake.state() == (False, False)
    assert edge._inspect(UID)[0] == "owned"
    assert edge.main(["rollback", str(UID)]) == 0
    assert Path(edge.MARKER).read_bytes() == marker
    assert Path(edge.CADDYFILE).read_bytes() == config


@pytest.mark.parametrize("nat", [False, True])
def test_legacy_enable_failed_verification_and_stop_retains_current_policy(
    fake: Fake, nat: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    marker, config = inactive_legacy_edge(fake, nat)
    fake.https = lambda host, path: 502
    fake.fail["systemctl stop"] = True
    assert enable(nat=nat) == 1
    assert "rollback incomplete" in capsys.readouterr().err
    assert fake.marker()["pending"] == edge._pending("enable", False, False)
    assert fake.marker()["version"] == (2 if nat else 1)
    assert Path(edge.CADDYFILE).read_bytes() == edge._caddyfile(HOST, nat)
    assert fake.state() == (False, True)
    fake.fail.clear()
    assert edge.main(["rollback", str(UID)]) == 0
    assert Path(edge.MARKER).read_bytes() == marker
    assert Path(edge.CADDYFILE).read_bytes() == config


@pytest.mark.parametrize("nat", [False, True])
def test_current_disabled_edge_enable_keeps_current_journal_and_live_validation(
    fake: Fake, nat: bool
) -> None:
    assert enable(nat=nat) == 0
    assert edge.main(["commit", str(UID)]) == 0
    assert edge.main(["disable", str(UID)]) == 0
    assert edge.main(["commit", str(UID)]) == 0
    fake.calls.clear()
    assert enable(nat=nat) == 0
    assert fake.marker()["version"] == 3
    assert fake.marker()["pending"] == edge._pending("enable", False, False)
    validators = fake.ran("runuser")
    assert len(validators) == 1
    assert validators[0][validators[0].index("--config") + 1] == edge.CADDYFILE
    assert edge.main(["rollback", str(UID)]) == 0
    assert fake.state() == (False, False) and fake.marker()["version"] == 3


@pytest.mark.parametrize("nat", [False, True])
def test_legacy_enable_repeats_admission_at_privileged_boundary(
    fake: Fake, nat: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker, config = inactive_legacy_edge(fake, nat)
    monkeypatch.setattr(edge, "_euid", lambda: UID or 1000)

    def sudo(args: list[str], *, interactive: bool) -> int:
        fake.enabled = True
        monkeypatch.setattr(edge, "_euid", lambda: 0)
        return edge.main(args)

    monkeypatch.setattr(edge, "_sudo", sudo)
    assert enable(nat=nat) == 1
    assert fake.state() == (True, False)
    assert Path(edge.MARKER).read_bytes() == marker
    assert Path(edge.CADDYFILE).read_bytes() == config
    assert not fake.ran("systemctl", "enable")


@pytest.mark.parametrize("op", ["enable", "disable", "migrate"])
@pytest.mark.parametrize("transaction", [TRANSACTION, "", None, 1, TRANSACTION.upper(), "not-a-uuid"])
def test_transaction_marker_field_is_only_valid_on_enable(op: str, transaction: Any) -> None:
    pending = edge._pending(op, False, False)
    if op == "migrate":
        pending.update(
            prior_marker=edge._marker_bytes(UID, HOST, None, version=1).decode("ascii"),
            prior_caddyfile=edge._caddyfile(HOST, version=1).decode("ascii"),
        )
    pending["transaction"] = transaction
    parsed = edge._parse_marker(edge._marker_bytes(UID, HOST, pending), UID)
    assert (parsed is not None) == (op == "enable" and transaction == TRANSACTION)


@pytest.mark.parametrize(
    "args",
    [
        ["enable", HOST, str(UID), "--transaction"],
        ["commit", str(UID), "--transaction", ""],
        ["rollback", str(UID), "--transaction", TRANSACTION.upper()],
        ["enable", HOST, str(UID), "--transaction", "not-a-uuid"],
        ["enable", HOST, str(UID), "--transaction", TRANSACTION, "--transaction", TRANSACTION],
        ["commit", str(UID), "--transaction", TRANSACTION, "extra"],
        ["enable", HOST, str(UID), "--behind-nat", "--behind-nat"],
        ["disable", str(UID), "--transaction", TRANSACTION],
        ["migrate", str(UID), "--transaction", TRANSACTION],
        ["dns", HOST, "--transaction", TRANSACTION],
        ["owned-hostname", str(UID), "--transaction", TRANSACTION],
        ["ssh-committed", HOST, str(UID), "--transaction", TRANSACTION],
        ["migration-needed", str(UID), "--transaction", TRANSACTION],
        ["hostname", HOST, "--transaction", TRANSACTION],
        ["preflight", HOST, "--transaction", TRANSACTION],
    ],
)
def test_transaction_cli_rejects_invalid_options_before_effects(fake: Fake, args: list[str]) -> None:
    assert edge.main(args) == 1
    assert not fake.calls and not fake.sudo_calls
    assert not Path(edge.IMP_DIR).exists()


@pytest.mark.parametrize("nat_first", [False, True])
def test_transaction_and_nat_options_preserved_through_sudo(
    fake: Fake, nat_first: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(edge, "_euid", lambda: UID or 1000)
    options = ["--transaction", TRANSACTION]
    options = ["--behind-nat", *options] if nat_first else [*options, "--behind-nat"]
    args = ["enable", HOST, str(UID), *options]
    assert edge.main(args) == 0
    assert fake.sudo_calls == [(args, True)]


@pytest.mark.parametrize("op", ["commit", "rollback"])
@pytest.mark.parametrize("owner", [None, OTHER_TRANSACTION])
@pytest.mark.parametrize("unprivileged", [False, True])
def test_guarded_recovery_refuses_unowned_journal_without_mutation_or_sudo(
    fake: Fake, op: str, owner: str | None, unprivileged: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = ["enable", HOST, str(UID)]
    if owner is not None:
        args.extend(["--transaction", owner])
    assert edge.main(args) == 0
    paths = [edge.MARKER, edge.CADDYFILE, edge.UNIT_PATH, *edge.STOCK_UNITS]
    before = [(Path(p).lstat().st_mtime_ns, Path(p).lstat().st_mode, Path(p).read_bytes()) for p in paths]
    state = fake.state()
    fake.calls.clear()
    if unprivileged:
        monkeypatch.setattr(edge, "_euid", lambda: UID or 1000)
    assert edge.main([op, str(UID), "--transaction", TRANSACTION]) == 1
    assert [
        (Path(p).lstat().st_mtime_ns, Path(p).lstat().st_mode, Path(p).read_bytes()) for p in paths
    ] == before
    assert fake.state() == state
    assert not fake.calls and not fake.sudo_calls


@pytest.mark.parametrize("op", ["commit", "rollback"])
@pytest.mark.parametrize("owner", [None, OTHER_TRANSACTION])
def test_guarded_recovery_rechecks_owner_under_root_lock(
    fake: Fake, op: str, owner: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert edge.main(["enable", HOST, str(UID), "--transaction", TRANSACTION]) == 0
    monkeypatch.setattr(edge, "_euid", lambda: UID or 1000)
    expected = b""
    lock = edge._lock
    locked = False

    @contextlib.contextmanager
    def tracked_lock(*, create: bool) -> Any:
        nonlocal locked
        with lock(create=create):
            locked = True
            try:
                yield
            finally:
                locked = False

    inspect = edge._inspect

    def inspect_locked(uid: int) -> tuple[str, dict[str, Any] | None]:
        assert locked
        return inspect(uid)

    def sudo(args: list[str], *, interactive: bool) -> int:
        nonlocal expected
        assert not locked and not interactive
        edge._write_marker(UID, HOST, edge._pending("enable", False, False, transaction=owner))
        expected = Path(edge.MARKER).read_bytes()
        monkeypatch.setattr(edge, "_euid", lambda: 0)
        return edge.main(args)

    monkeypatch.setattr(edge, "_lock", tracked_lock)
    monkeypatch.setattr(edge, "_inspect", inspect_locked)
    monkeypatch.setattr(edge, "_sudo", sudo)
    fake.calls.clear()
    assert edge.main([op, str(UID), "--transaction", TRANSACTION]) == 1
    assert Path(edge.MARKER).read_bytes() == expected
    assert fake.state() == (True, True) and not fake.calls


@pytest.mark.parametrize("nat", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("op", ["commit", "rollback"])
def test_transaction_retained_through_enable_publications_and_owned_recovery(
    fake: Fake, nat: bool, legacy: bool, op: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = inactive_legacy_edge(fake, nat) if legacy else None
    atomic = edge._atomic
    journals = []
    run = fake.run

    def publish(path: str, data: bytes, **kw: Any) -> None:
        atomic(path, data, **kw)
        if path == edge.MARKER:
            marker = edge._parse_marker(data, UID)
            assert marker is not None and marker["pending"]["transaction"] == TRANSACTION
            journals.append(marker)

    def commands(args: list[str], timeout: float = 120) -> SimpleNamespace:
        if args[0] in ("apt-get", "runuser"):
            assert fake.marker()["pending"]["transaction"] == TRANSACTION
        return run(args, timeout)

    monkeypatch.setattr(edge, "_atomic", publish)
    monkeypatch.setattr(edge, "_run", commands)
    args = ["enable", HOST, str(UID), "--transaction", TRANSACTION]
    assert edge.main([*args, *(["--behind-nat"] if nat else [])]) == 0
    assert len(journals) == (1 if legacy else 2)
    if not legacy:
        assert journals[0]["pending"]["prep"] == [os.path.basename(p) for p in edge.STOCK_UNITS]
        assert journals[1]["pending"]["prep"] is None
    assert fake.marker()["pending"]["transaction"] == TRANSACTION
    assert edge._inspect(UID)[0] == "owned"
    monkeypatch.setattr(edge, "_atomic", atomic)
    assert edge.main([op, str(UID), "--transaction", TRANSACTION]) == 0
    assert fake.marker()["pending"] is None
    assert fake.state() == ((True, True) if op == "commit" else (False, False))
    if baseline is not None and op == "rollback":
        assert (Path(edge.MARKER).read_bytes(), Path(edge.CADDYFILE).read_bytes()) == baseline
    fake.calls.clear()
    assert edge.main([op, str(UID), "--transaction", TRANSACTION]) == (1 if op == "commit" else 0)
    assert not fake.calls


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("owner", [None, OTHER_TRANSACTION])
def test_enable_failure_cleanup_does_not_undo_replaced_transaction(
    fake: Fake, legacy: bool, owner: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    if legacy:
        inactive_legacy_edge(fake, False)
    expected = b""
    calls = []

    def fail_probe(host: str, path: str) -> int:
        nonlocal expected
        marker = fake.marker()
        marker["pending"] = edge._pending("enable", False, False, transaction=owner)
        edge._write_marker(UID, HOST, marker["pending"], version=marker["version"])
        expected = Path(edge.MARKER).read_bytes()
        calls.extend(fake.calls)
        return 200

    fake.https = fail_probe
    assert edge.main(["enable", HOST, str(UID), "--transaction", TRANSACTION]) == 1
    assert Path(edge.MARKER).read_bytes() == expected
    assert fake.state() == (True, True)
    assert fake.calls == calls
    assert Path(edge.CADDYFILE).read_bytes() == edge._caddyfile(HOST)


@pytest.mark.parametrize("op", ["commit", "rollback"])
@pytest.mark.parametrize("transaction", [None, TRANSACTION])
@pytest.mark.parametrize("unprivileged", [False, True])
def test_recovery_absent_state_requires_pending_only_for_guarded_commit(
    fake: Fake,
    op: str,
    transaction: str | None,
    unprivileged: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if unprivileged:
        monkeypatch.setattr(edge, "_euid", lambda: UID or 1000)
    args = [op, str(UID), *(["--transaction", transaction] if transaction is not None else [])]
    refused = op == "commit" and transaction is not None
    assert edge.main(args) == (1 if refused else 0)
    if refused:
        assert "no pending Direct edge transaction to commit" in capsys.readouterr().err
    assert not fake.calls and not fake.sudo_calls
    assert not Path(edge.IMP_DIR).exists()


@pytest.mark.parametrize("op", ["commit", "rollback"])
@pytest.mark.parametrize("transaction", [None, TRANSACTION])
@pytest.mark.parametrize("unprivileged", [False, True])
def test_recovery_committed_state_requires_pending_only_for_guarded_commit(
    fake: Fake,
    op: str,
    transaction: str | None,
    unprivileged: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert edge.main(["enable", HOST, str(UID), "--transaction", TRANSACTION]) == 0
    assert edge.main(["commit", str(UID), "--transaction", TRANSACTION]) == 0
    assert fake.marker()["pending"] is None
    paths = [edge.MARKER, edge.CADDYFILE, edge.UNIT_PATH, *edge.STOCK_UNITS]
    before = [(Path(p).lstat().st_mtime_ns, Path(p).lstat().st_mode, Path(p).read_bytes()) for p in paths]
    state = fake.state()
    fake.calls.clear()
    if unprivileged:
        monkeypatch.setattr(edge, "_euid", lambda: UID or 1000)
    args = [op, str(UID), *(["--transaction", transaction] if transaction is not None else [])]
    refused = op == "commit" and transaction is not None
    assert edge.main(args) == (1 if refused else 0)
    if refused:
        assert "no pending Direct edge transaction to commit" in capsys.readouterr().err
    assert [
        (Path(p).lstat().st_mtime_ns, Path(p).lstat().st_mode, Path(p).read_bytes()) for p in paths
    ] == before
    assert fake.state() == state
    assert not fake.calls and not fake.sudo_calls


def test_guarded_commit_rechecks_pending_under_root_lock_after_manual_rollback(
    fake: Fake, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert edge.main(["enable", HOST, str(UID), "--transaction", TRANSACTION]) == 0
    monkeypatch.setattr(edge, "_euid", lambda: UID or 1000)
    paths = [edge.MARKER, edge.CADDYFILE, edge.UNIT_PATH, *edge.STOCK_UNITS]
    expected: list[tuple[int, int, bytes]] = []
    lock = edge._lock
    locked = False

    @contextlib.contextmanager
    def tracked_lock(*, create: bool) -> Any:
        nonlocal locked
        with lock(create=create):
            locked = True
            try:
                yield
            finally:
                locked = False

    inspect = edge._inspect

    def inspect_locked(uid: int) -> tuple[str, dict[str, Any] | None]:
        assert locked
        return inspect(uid)

    requested = ["commit", str(UID), "--transaction", TRANSACTION]

    def sudo(args: list[str], *, interactive: bool) -> int:
        assert args == requested and not locked and not interactive
        fake.sudo_calls.append((args, interactive))
        monkeypatch.setattr(edge, "_euid", lambda: 0)
        assert edge.main(["rollback", str(UID)]) == 0
        assert fake.marker()["pending"] is None
        assert fake.state() == (False, False)
        expected.extend(
            (Path(p).lstat().st_mtime_ns, Path(p).lstat().st_mode, Path(p).read_bytes()) for p in paths
        )
        fake.calls.clear()
        return edge.main(args)

    monkeypatch.setattr(edge, "_lock", tracked_lock)
    monkeypatch.setattr(edge, "_inspect", inspect_locked)
    monkeypatch.setattr(edge, "_sudo", sudo)
    assert edge.main(requested) == 1
    assert "no pending Direct edge transaction to commit" in capsys.readouterr().err
    assert fake.sudo_calls == [(requested, False)]
    assert [
        (Path(p).lstat().st_mtime_ns, Path(p).lstat().st_mode, Path(p).read_bytes()) for p in paths
    ] == expected
    assert fake.state() == (False, False) and not fake.calls


@pytest.mark.parametrize("op", ["commit", "rollback"])
def test_manual_recovery_can_finish_transaction_identified_enable(fake: Fake, op: str) -> None:
    assert edge.main(["enable", HOST, str(UID), "--transaction", TRANSACTION]) == 0
    assert edge.main([op, str(UID)]) == 0
    assert fake.marker()["pending"] is None
    assert fake.state() == ((True, True) if op == "commit" else (False, False))


def test_transaction_marker_rejects_duplicate_and_extra_keys() -> None:
    data = edge._marker_bytes(UID, HOST, edge._pending("enable", False, False, transaction=TRANSACTION))
    duplicate = data.replace(
        f'"transaction":"{TRANSACTION}"'.encode(),
        f'"transaction":"{TRANSACTION}","transaction":"{TRANSACTION}"'.encode(),
    )
    assert edge._parse_marker(duplicate, UID) is None
    pending = edge._pending("enable", False, False, transaction=TRANSACTION)
    pending["extra"] = TRANSACTION
    assert edge._parse_marker(edge._marker_bytes(UID, HOST, pending), UID) is None


@pytest.mark.parametrize("op", ["commit", "rollback"])
def test_guarded_same_owner_recovery_preserves_arguments_through_sudo(
    fake: Fake, op: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert edge.main(["enable", HOST, str(UID), "--transaction", TRANSACTION]) == 0
    monkeypatch.setattr(edge, "_euid", lambda: UID or 1000)
    requested = [op, str(UID), "--transaction", TRANSACTION]

    def sudo(args: list[str], *, interactive: bool) -> int:
        assert args == requested and not interactive
        fake.sudo_calls.append((args, interactive))
        monkeypatch.setattr(edge, "_euid", lambda: 0)
        return edge.main(args)

    monkeypatch.setattr(edge, "_sudo", sudo)
    assert edge.main(requested) == 0
    assert fake.sudo_calls == [(requested, False)]
    assert fake.marker()["pending"] is None
    assert fake.state() == ((True, True) if op == "commit" else (False, False))


def test_guarded_prepackage_failure_retains_identity_for_owned_recovery(
    fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake.fail["systemctl mask"] = True
    with monkeypatch.context() as m:
        m.setattr(edge, "_failed", lambda *a, **kw: None)
        assert edge.main(["enable", HOST, str(UID), "--transaction", TRANSACTION]) == 1
    pending = fake.marker()["pending"]
    assert pending["transaction"] == TRANSACTION and pending["prep"] is not None
    fake.fail.clear()
    assert edge.main(["rollback", str(UID), "--transaction", OTHER_TRANSACTION]) == 1
    assert fake.marker()["pending"] == pending
    assert edge.main(["rollback", str(UID), "--transaction", TRANSACTION]) == 0
    assert edge._inspect(UID) == ("absent", None)
