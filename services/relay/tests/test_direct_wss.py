from __future__ import annotations

import asyncio
import base64
import errno
import grp
import hashlib
import http.server
import io
import json
import logging
import os
import pwd
import secrets
import shlex
import shutil
import signal
import socket
import ssl
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.http11 import Request, Response

import imp_relay.direct_wss as direct_wss
from imp_relay.gateway import token_digest
from imp_relay.websocket_logging import websocket_logger

TOKEN = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode("ascii")
OLD_TOKEN = base64.urlsafe_b64encode(bytes(range(1, 33))).rstrip(b"=").decode("ascii")


class TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(direct_wss, "_supported_platform", lambda: None)
    monkeypatch.setattr(direct_wss, "_user_home", lambda: (os.geteuid(), str(home)))
    return home


def _config_dir(home: Path) -> Path:
    path = home / ".config" / "imp"
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    os.chmod(path, 0o700)
    return path


def _write_config(home: Path, data: bytes) -> Path:
    path = _config_dir(home) / "gateway.env"
    path.write_bytes(data)
    os.chmod(path, 0o600)
    return path


def _install_unit(home: Path) -> None:
    unit_dir = home / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    for directory in (unit_dir.parent, unit_dir):
        os.chmod(directory, 0o700)
    unit = unit_dir / direct_wss._UNIT
    unit.write_bytes(direct_wss._SHIPPED_UNIT)
    os.chmod(unit, 0o644)


def _tty_streams(monkeypatch: pytest.MonkeyPatch) -> tuple[TTY, io.StringIO]:
    monkeypatch.setattr(sys, "stdin", TTY())
    stdout = TTY()
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    return stdout, stderr


class FakeSystemctl:
    def __init__(
        self,
        home: Path,
        *,
        active: bool = False,
        enabled: bool = False,
        properties: dict[str, str] | None = None,
        failed_restarts: set[int] | None = None,
    ) -> None:
        self.home = home
        self.active = active
        self.enabled = enabled
        self.properties = properties or {}
        self.failed_restarts = failed_restarts or set()
        self.restart_count = 0
        self.calls: list[tuple[str, ...]] = []
        self.actions: list[tuple[str, ...]] = []

    def _show(self) -> str:
        binary = str(self.home / ".local/share/imp/current/bin/imp-server")
        argv = (
            binary,
            "gateway",
            "--host",
            "127.0.0.1",
            "--port",
            "8788",
            "--relay-url",
            "ws://127.0.0.1:8787",
        )
        exec_start = (
            f"{{ path={binary} ; argv[]={shlex.join(argv)} ; ignore_errors=no ; "
            "start_time=[n/a] ; stop_time=[n/a] ; pid=0 }"
        )
        environment = f"{self.home}/.config/imp/gateway.env (ignore_errors=no)"
        values = {
            "LoadState": "loaded",
            "FragmentPath": str(self.home / ".config/systemd/user/imp-gateway.service"),
            "DropInPaths": "",
            "NeedDaemonReload": "no",
            "Type": "simple",
            "ExecStart": exec_start,
            "EnvironmentFiles": environment,
            "Restart": "on-failure",
            "RestartUSec": "2s",
            "NoNewPrivileges": "yes",
            "UnitFileState": "enabled" if self.enabled else "disabled",
            "ActiveState": "active" if self.active else "inactive",
        }
        values.update(self.properties)
        return "".join(f"{key}={value}\n" for key, value in values.items())

    def __call__(self, *args: str) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        op = args[0]
        if op == "show":
            return subprocess.CompletedProcess(args, 0, self._show(), "")
        if op == "is-active":
            return subprocess.CompletedProcess(args, 0 if self.active else 3, "", "")
        if op == "is-enabled":
            return subprocess.CompletedProcess(args, 0 if self.enabled else 1, "", "")
        self.actions.append(args)
        if op == "enable" and "--now" in args:
            self.active = True
            self.enabled = True
        elif op == "enable":
            self.enabled = True
        elif op == "disable":
            self.enabled = False
        elif op == "restart":
            self.restart_count += 1
            if self.restart_count in self.failed_restarts:
                return subprocess.CompletedProcess(args, 1, "", "private service error")
            self.active = True
        elif op == "stop":
            self.active = False
        else:
            return subprocess.CompletedProcess(args, 1, "", "unsupported fake command")
        return subprocess.CompletedProcess(args, 0, "", "")


def _fake_systemctl(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    **kwargs: Any,
) -> FakeSystemctl:
    _install_unit(home)
    fake = FakeSystemctl(home, **kwargs)
    monkeypatch.setattr(direct_wss, "_systemctl", fake)

    async def relay_ready(port: int, token: str | None = None) -> bool:
        return True

    monkeypatch.setattr(direct_wss, "_state_hello", relay_ready)
    return fake


def test_config_parser_accepts_only_one_digest_with_whitespace_and_comments() -> None:
    digest = token_digest(TOKEN)
    assert (
        direct_wss._parse_config(
            f"  # supported comment\n\n  {_config_line(digest.hex().upper())}  \n".encode()
        )
        == digest
    )

    for content in (
        b"",
        f"{_config_line(digest.hex())}\n{_config_line(digest.hex())}\n".encode(),
        f"{_config_line(digest.hex())}\nUNSUPPORTED=value\n".encode(),
        f"export {_config_line(digest.hex())}\n".encode(),
        f'{direct_wss._CONFIG_KEY}="{digest.hex()}"\n'.encode(),
        f"{_config_line(digest.hex())} # inline comment\n".encode(),
        b"IMP_GATEWAY_TOKEN_SHA256=not-a-digest\n",
        b"IMP_GATEWAY_TOKEN_SHA256=\xff\n",
        f"\u00a0{_config_line(digest.hex())}\n".encode(),
        f"# comment\u2028{_config_line(digest.hex())}\n".encode(),
    ):
        with pytest.raises(direct_wss.DirectWssError):
            direct_wss._parse_config(content)


def _config_line(digest: str) -> str:
    return f"{direct_wss._CONFIG_KEY} = {digest}"


def test_setup_persists_only_digest_with_private_modes_and_hands_token_off_after_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    service = _fake_systemctl(home, monkeypatch)
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: TOKEN)
    verified: list[str] = []
    monkeypatch.setattr(direct_wss, "_verify_gateway", lambda token: verified.append(token))
    stdout, stderr = _tty_streams(monkeypatch)

    assert direct_wss.main(["setup"]) == 0

    config = home / ".config/imp/gateway.env"
    assert config.read_bytes() == f"{direct_wss._CONFIG_KEY}={token_digest(TOKEN).hex()}\n".encode()
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert stat.S_IMODE(config.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE((config.parent / direct_wss._LOCK_NAME).stat().st_mode) == 0o600
    assert verified == [TOKEN]
    assert stdout.getvalue().endswith(TOKEN + "\n")
    assert TOKEN not in config.read_text()
    assert all(TOKEN not in argument for call in service.calls for argument in call)
    assert TOKEN not in stderr.getvalue()
    assert TOKEN not in " ".join(os.environ.values())
    assert TOKEN not in " ".join(sys.argv)
    assert service.active and service.enabled
    assert not list(config.parent.glob(".gateway.env.tmp.*"))


def test_setup_idempotent_does_not_generate_lock_or_call_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    config = _write_config(home, f"{_config_line(token_digest(TOKEN).hex())}\n".encode())
    original = config.read_bytes()
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: pytest.fail("generated token"))
    monkeypatch.setattr(direct_wss, "_systemctl", lambda *args: pytest.fail("called systemctl"))
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    assert direct_wss.main(["setup"]) == 0
    assert config.read_bytes() == original
    assert not (config.parent / direct_wss._LOCK_NAME).exists()


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"IMP_GATEWAY_TOKEN_SHA256=" + b"a" * 64 + b"\nIMP_GATEWAY_TOKEN_SHA256=" + b"b" * 64 + b"\n",
        b"IMP_GATEWAY_TOKEN_SHA256=" + b"a" * 64 + b"\nUNKNOWN=value\n",
        b"export IMP_GATEWAY_TOKEN_SHA256=" + b"a" * 64 + b"\n",
        b"IMP_GATEWAY_TOKEN_SHA256='" + b"a" * 64 + b"'\n",
    ],
)
def test_setup_rejects_unsupported_config_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: bytes
) -> None:
    home = _home(tmp_path, monkeypatch)
    config = _write_config(home, content)
    _tty_streams(monkeypatch)
    monkeypatch.setattr(direct_wss, "_systemctl", lambda *args: pytest.fail("called systemctl"))

    assert direct_wss.main(["setup"]) == 1
    assert config.read_bytes() == content
    assert not (config.parent / direct_wss._LOCK_NAME).exists()


@pytest.mark.parametrize("operation", ["setup", "rotate", "status"])
@pytest.mark.parametrize("nul_at", ["comment", "assignment_end", "assignment_inside"])
def test_nul_in_config_is_invalid_for_every_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, nul_at: str
) -> None:
    home = _home(tmp_path, monkeypatch)
    digest = token_digest(TOKEN).hex()
    line = _config_line(digest)
    content = {
        "comment": f"# note\x00\n{line}\n",
        "assignment_end": f"{line}\x00\n",
        "assignment_inside": f"{line[:-1]}\x00{line[-1]}\n",
    }[nul_at].encode()
    config = _write_config(home, content)
    before = config.stat()
    service = _fake_systemctl(home, monkeypatch)
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: pytest.fail("generated token"))
    stdout, stderr = _tty_streams(monkeypatch)

    assert direct_wss.main([operation]) == (0 if operation == "status" else 1)
    output = stdout.getvalue() + stderr.getvalue()
    if operation == "status":
        assert "gateway.env syntax: invalid" in output
    for secret in (digest, TOKEN, "\x00", "# note"):
        assert secret not in output
    after = config.stat()
    assert config.read_bytes() == content
    assert (after.st_ino, after.st_mtime_ns, stat.S_IMODE(after.st_mode)) == (
        before.st_ino,
        before.st_mtime_ns,
        stat.S_IMODE(before.st_mode),
    )
    assert not (config.parent / direct_wss._LOCK_NAME).exists()
    assert not service.actions


def test_setup_rejects_permissive_config_file_mode_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    config = _write_config(home, f"{_config_line(token_digest(TOKEN).hex())}\n".encode())
    os.chmod(config, 0o644)
    before = config.read_bytes()
    _tty_streams(monkeypatch)
    monkeypatch.setattr(direct_wss, "_systemctl", lambda *args: pytest.fail("called systemctl"))

    assert direct_wss.main(["setup"]) == 1
    assert config.read_bytes() == before
    assert stat.S_IMODE(config.stat().st_mode) == 0o644


def test_setup_rejects_symlink_config_and_preserves_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    config_dir = _config_dir(home)
    target = tmp_path / "external.env"
    target.write_bytes(b"external bytes\n")
    (config_dir / "gateway.env").symlink_to(target)
    _tty_streams(monkeypatch)
    monkeypatch.setattr(direct_wss, "_systemctl", lambda *args: pytest.fail("called systemctl"))

    assert direct_wss.main(["setup"]) == 1
    assert target.read_bytes() == b"external bytes\n"
    assert (config_dir / "gateway.env").is_symlink()


def test_setup_rejects_symlink_config_ancestor_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    _write_config(outside, f"{_config_line(token_digest(TOKEN).hex())}\n".encode())
    (home / ".config").symlink_to(outside / ".config", target_is_directory=True)
    _tty_streams(monkeypatch)
    monkeypatch.setattr(direct_wss, "_systemctl", lambda *args: pytest.fail("called systemctl"))

    assert direct_wss.main(["setup"]) == 1
    assert not (outside / ".config/imp/gateway.env.lock").exists()


def test_setup_rejects_wrong_file_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _home(tmp_path, monkeypatch)
    config = _write_config(home, f"{_config_line(token_digest(TOKEN).hex())}\n".encode())
    original_fstat = os.fstat
    target = config.stat()

    def foreign_owner(fd: int) -> Any:
        info = original_fstat(fd)
        if (info.st_dev, info.st_ino) == (target.st_dev, target.st_ino):
            return SimpleNamespace(
                st_mode=info.st_mode,
                st_uid=os.geteuid() + 1,
                st_nlink=info.st_nlink,
            )
        return info

    monkeypatch.setattr(os, "fstat", foreign_owner)
    _tty_streams(monkeypatch)
    monkeypatch.setattr(direct_wss, "_systemctl", lambda *args: pytest.fail("called systemctl"))

    assert direct_wss.main(["setup"]) == 1
    assert config.exists()


def test_rotate_rejects_unsafe_lock_without_touching_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    config = _write_config(home, f"{_config_line(token_digest(OLD_TOKEN).hex())}\n".encode())
    target = tmp_path / "outside.lock"
    target.write_bytes(b"leave alone")
    (config.parent / direct_wss._LOCK_NAME).symlink_to(target)
    _tty_streams(monkeypatch)
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: pytest.fail("generated token"))

    assert direct_wss.main(["rotate"]) == 1
    assert target.read_bytes() == b"leave alone"
    assert token_digest(OLD_TOKEN).hex().encode() in config.read_bytes()


def test_atomic_replace_is_private_and_failure_keeps_previous_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    config = _write_config(home, f"{_config_line(token_digest(OLD_TOKEN).hex())}\n".encode())
    old_data = config.read_bytes()
    service = _fake_systemctl(home, monkeypatch, active=False, enabled=False)
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: TOKEN)
    _tty_streams(monkeypatch)

    def fail_replace(*args: Any, **kwargs: Any) -> None:
        raise OSError("private replacement failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    assert direct_wss.main(["rotate"]) == 1
    assert config.read_bytes() == old_data
    assert not list(config.parent.glob(".gateway.env.tmp.*"))
    assert not service.actions


def test_setup_failure_restores_missing_config_and_inactive_disabled_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    service = _fake_systemctl(home, monkeypatch)
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: TOKEN)
    monkeypatch.setattr(
        direct_wss,
        "_verify_gateway",
        lambda token: (_ for _ in ()).throw(direct_wss.DirectWssError("readiness failed")),
    )
    stdout, stderr = _tty_streams(monkeypatch)

    assert direct_wss.main(["setup"]) == 1
    assert not (home / ".config/imp/gateway.env").exists()
    assert not stdout.getvalue()
    assert TOKEN not in stderr.getvalue()
    assert not service.active and not service.enabled
    assert service.actions[-2:] == [("stop", direct_wss._UNIT), ("disable", direct_wss._UNIT)]


def test_active_rotate_readiness_failure_restores_exact_config_and_service_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    old_data = b"# keep comments\n" + f"{_config_line(token_digest(OLD_TOKEN).hex())}\n".encode()
    config = _write_config(home, old_data)
    service = _fake_systemctl(home, monkeypatch, active=True, enabled=True)
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: TOKEN)
    monkeypatch.setattr(
        direct_wss,
        "_verify_gateway",
        lambda token: (_ for _ in ()).throw(direct_wss.DirectWssError("gateway readiness failed")),
    )
    stdout, stderr = _tty_streams(monkeypatch)

    assert direct_wss.main(["rotate"]) == 1
    assert config.read_bytes() == old_data
    assert service.active and service.enabled
    assert service.restart_count == 2
    assert not stdout.getvalue()
    assert TOKEN not in stderr.getvalue()


def test_restore_failure_is_explicit_and_gateway_is_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    config = _write_config(home, f"{_config_line(token_digest(OLD_TOKEN).hex())}\n".encode())
    service = _fake_systemctl(home, monkeypatch, active=True, enabled=True, failed_restarts={2})
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: TOKEN)
    monkeypatch.setattr(
        direct_wss,
        "_verify_gateway",
        lambda token: (_ for _ in ()).throw(direct_wss.DirectWssError("readiness failed")),
    )
    _, stderr = _tty_streams(monkeypatch)

    assert direct_wss.main(["rotate"]) == 1
    assert config.read_bytes() == f"{_config_line(token_digest(OLD_TOKEN).hex())}\n".encode()
    assert "restoration failed" in stderr.getvalue()
    assert TOKEN not in stderr.getvalue()
    assert not service.active
    assert service.actions[-1] == ("stop", direct_wss._UNIT)


@pytest.mark.parametrize("enabled", [False, True])
def test_inactive_rotate_preserves_enabled_state_without_activation_or_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    home = _home(tmp_path, monkeypatch)
    config = _write_config(home, f"{_config_line(token_digest(OLD_TOKEN).hex())}\n".encode())
    service = _fake_systemctl(home, monkeypatch, active=False, enabled=enabled)
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: TOKEN)
    monkeypatch.setattr(direct_wss, "_verify_gateway", lambda token: pytest.fail("readiness must be skipped"))
    stdout, _ = _tty_streams(monkeypatch)

    assert direct_wss.main(["rotate"]) == 0
    assert direct_wss._parse_config(config.read_bytes()) == token_digest(TOKEN)
    assert not service.active and service.enabled is enabled
    assert not service.actions
    assert stdout.getvalue().endswith(TOKEN + "\n")


def test_rotate_requires_supported_existing_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _home(tmp_path, monkeypatch)
    _config_dir(home)
    _tty_streams(monkeypatch)
    monkeypatch.setattr(direct_wss, "_systemctl", lambda *args: pytest.fail("called systemctl"))

    assert direct_wss.main(["rotate"]) == 1
    assert not (home / ".config/imp/gateway.env").exists()
    assert not (home / ".config/imp/gateway.env.lock").exists()


def test_tty_refusal_happens_before_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _home(tmp_path, monkeypatch)
    _fake_systemctl(home, monkeypatch)
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: pytest.fail("generated token"))

    assert direct_wss.main(["setup"]) == 1
    assert not (home / ".config/imp/gateway.env").exists()
    assert not (home / ".config/imp/gateway.env.lock").exists()
    assert not (home / ".config/imp").exists()


def test_status_is_read_only_and_separates_syntax_from_file_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    config = _write_config(home, f"{_config_line(token_digest(TOKEN).hex())}\n".encode())
    os.chmod(config, 0o644)
    service = _fake_systemctl(home, monkeypatch, active=False, enabled=True)
    before = config.read_bytes()
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)

    assert direct_wss.main(["status"]) == 0
    assert "gateway.env syntax: valid" in out.getvalue()
    assert "gateway.env mode: 0644 not private" in out.getvalue()
    assert "gateway.env directory mode: 0700 private" in out.getvalue()
    assert "gateway service enabled: yes" in out.getvalue()
    assert "gateway service active: no" in out.getvalue()
    assert "gateway health: unavailable" in out.getvalue()
    assert token_digest(TOKEN).hex() not in out.getvalue()
    assert TOKEN not in out.getvalue()
    assert config.read_bytes() == before
    assert not (config.parent / direct_wss._LOCK_NAME).exists()
    assert not service.actions


def test_status_reports_malformed_config_without_exposing_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    config = _write_config(home, b"SECRETISH unsupported content\n")
    _fake_systemctl(home, monkeypatch)
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)

    assert direct_wss.main(["status"]) == 0
    assert "gateway.env path: supported" in out.getvalue()
    assert "gateway.env syntax: invalid" in out.getvalue()
    assert "SECRETISH" not in out.getvalue()
    assert not (config.parent / direct_wss._LOCK_NAME).exists()


SHARED_GROUP = "is group-writable by a group not shown private to the target user"
ACL = "is group-writable and has an extended access ACL that could not be excluded"


def _accounts(
    monkeypatch: pytest.MonkeyPatch,
    gid: int,
    *,
    primary_gid: int | None = None,
    others: tuple[tuple[str, int, int], ...] = (),
    members: tuple[str, ...] = ("imp-user",),
    listed: bool = True,
    resolvable: bool = True,
    aliases: tuple[tuple[str, tuple[str, ...]], ...] = (),
    groups_enumerable: bool = True,
) -> None:
    """Replace the account database deterministically; the runner's passwd/group never matter."""
    uid = os.geteuid()
    user = pwd.struct_passwd(
        ("imp-user", "x", uid, gid if primary_gid is None else primary_gid, "", "/", "/bin/sh")
    )
    accounts = [user] if listed else []
    accounts += [
        pwd.struct_passwd((name, "x", other_uid, other_gid, "", "/", "/bin/sh"))
        for name, other_uid, other_gid in others
    ]

    def getpwuid(value: int) -> pwd.struct_passwd:
        if not resolvable or value != uid:
            raise KeyError(value)
        return user

    def getgrgid(value: int) -> grp.struct_group:
        if not resolvable or value != gid:
            raise KeyError(value)
        return grp.struct_group(("imp-user", "x", gid, list(members)))

    def getgrall() -> list[grp.struct_group]:
        if not groups_enumerable:
            raise OSError("group enumeration unavailable")
        records = [("imp-user", members), *aliases]
        return [grp.struct_group((name, "x", gid, list(names))) for name, names in records]

    monkeypatch.setattr(direct_wss, "pwd", SimpleNamespace(getpwuid=getpwuid, getpwall=lambda: accounts))
    monkeypatch.setattr(direct_wss, "grp", SimpleNamespace(getgrgid=getgrgid, getgrall=getgrall))


@pytest.mark.parametrize(
    ("mode", "allow", "accounts", "problem"),
    [
        (0o755, True, {"resolvable": False}, None),
        (0o775, True, {}, None),
        (0o777, True, {}, "is world-writable"),
        (0o775, True, {"primary_gid": -1}, SHARED_GROUP),
        (0o775, True, {"others": (("other", 4242, None),)}, SHARED_GROUP),
        (0o775, True, {"members": ("imp-user", "other")}, SHARED_GROUP),
        (0o775, True, {"resolvable": False}, SHARED_GROUP),
        (0o775, True, {"listed": False}, SHARED_GROUP),
        (0o775, True, {"aliases": (("shared-alias", ("other",)),)}, SHARED_GROUP),
        (0o775, True, {"groups_enumerable": False}, SHARED_GROUP),
        (0o775, False, {}, "must be owned by the target user with mode 0700"),
    ],
    ids=[
        "0755",
        "0775-private-primary-group",
        "world-writable",
        "non-primary-group",
        "other-primary-account",
        "other-supplementary-member",
        "lookup-failure",
        "target-not-enumerated",
        "duplicate-gid-record-with-other-member",
        "group-enumeration-failure",
        "strict-directory",
    ],
)
def test_ancestor_group_write_requires_proven_private_primary_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: int,
    allow: bool,
    accounts: dict[str, Any],
    problem: str | None,
) -> None:
    directory = tmp_path / "ancestor"
    directory.mkdir()
    os.chmod(directory, mode)
    gid = directory.stat().st_gid
    if "others" in accounts:
        accounts["others"] = tuple((name, other_uid, gid) for name, other_uid, _ in accounts["others"])
    if accounts.get("primary_gid") == -1:
        accounts["primary_gid"] = gid + 1
    _accounts(monkeypatch, gid, **accounts)
    parent = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        if problem is None:
            os.close(
                direct_wss._open_directory(
                    parent, "ancestor", os.geteuid(), display="~/a", allow_private_group=allow
                )
            )
        else:
            with pytest.raises(direct_wss.DirectWssError, match=f"^~/a {problem}$"):
                direct_wss._open_directory(
                    parent, "ancestor", os.geteuid(), display="~/a", allow_private_group=allow
                )
    finally:
        os.close(parent)


def test_ancestor_owned_by_another_user_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "ancestor").mkdir(mode=0o755)
    _accounts(monkeypatch, (tmp_path / "ancestor").stat().st_gid)
    parent = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(direct_wss.DirectWssError, match="^~/a is not owned by the target user$"):
            direct_wss._open_directory(
                parent, "ancestor", os.geteuid() + 1, display="~/a", allow_private_group=True
            )
    finally:
        os.close(parent)


@pytest.mark.parametrize(
    ("error", "rejected"),
    [
        (None, True),
        (errno.ENODATA, False),
        (errno.ENOTSUP, False),
        (errno.EOPNOTSUPP, False),
        (errno.EACCES, True),
        (errno.EIO, True),
    ],
    ids=["acl-present", "no-acl", "enotsup", "eopnotsupp", "eacces", "eio"],
)
def test_private_group_exception_rejects_present_or_uninspectable_access_acl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: int | None, rejected: bool
) -> None:
    directory = tmp_path / "ancestor"
    directory.mkdir()
    os.chmod(directory, 0o775)
    _accounts(monkeypatch, directory.stat().st_gid)
    inspected: list[Any] = []

    def getxattr(target: Any, name: str) -> bytes:
        inspected.append((type(target), name))
        if error is not None:
            raise OSError(error, os.strerror(error))
        return b"acl"

    monkeypatch.setattr(os, "getxattr", getxattr)
    parent = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        if rejected:
            with pytest.raises(direct_wss.DirectWssError, match=f"^~/a {ACL}$"):
                direct_wss._open_directory(
                    parent, "ancestor", os.geteuid(), display="~/a", allow_private_group=True
                )
        else:
            os.close(
                direct_wss._open_directory(
                    parent, "ancestor", os.geteuid(), display="~/a", allow_private_group=True
                )
            )
    finally:
        os.close(parent)
    assert inspected == [(int, "system.posix_acl_access")]


def test_named_user_write_acl_rejects_private_group_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if shutil.which("setfacl") is None:
        pytest.skip("setfacl unavailable")
    directory = tmp_path / "ancestor"
    directory.mkdir()
    os.chmod(directory, 0o775)
    _accounts(monkeypatch, directory.stat().st_gid)
    parent = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.close(
            direct_wss._open_directory(
                parent, "ancestor", os.geteuid(), display="~/a", allow_private_group=True
            )
        )
        if subprocess.run(["setfacl", "-m", "u:nobody:rwx", str(directory)], capture_output=True).returncode:
            pytest.skip("filesystem does not support POSIX ACLs")
        assert stat.S_IMODE(directory.stat().st_mode) == 0o775
        with pytest.raises(direct_wss.DirectWssError, match=f"^~/a {ACL}$"):
            direct_wss._open_directory(
                parent, "ancestor", os.geteuid(), display="~/a", allow_private_group=True
            )
    finally:
        os.close(parent)


def _ubuntu_layout(home: Path) -> int:
    for relative in (".config", ".config/systemd", ".config/systemd/user"):
        os.chmod(home / relative, 0o775)
    return (home / ".config").stat().st_gid


def test_status_and_rotate_accept_ubuntu_private_group_ancestors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    config = _write_config(home, f"{_config_line(token_digest(OLD_TOKEN).hex())}\n".encode())
    service = _fake_systemctl(home, monkeypatch, active=False, enabled=True)
    _accounts(monkeypatch, _ubuntu_layout(home))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)

    assert direct_wss.main(["status"]) == 0
    for line in (
        "gateway.env path: supported",
        "gateway.env syntax: valid",
        "gateway.env mode: 0600 private",
        "gateway.env directory mode: 0700 private",
        "gateway unit policy: supported",
    ):
        assert line + "\n" in out.getvalue()
    assert token_digest(OLD_TOKEN).hex() not in out.getvalue()

    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: TOKEN)
    stdout, _ = _tty_streams(monkeypatch)
    assert direct_wss.main(["rotate"]) == 0
    assert direct_wss._parse_config(config.read_bytes()) == token_digest(TOKEN)
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert stat.S_IMODE(config.parent.stat().st_mode) == 0o700
    assert not service.actions


@pytest.mark.parametrize("operation", ["status", "setup", "rotate"])
def test_shared_group_config_ancestor_is_a_path_failure_not_syntax(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    home = _home(tmp_path, monkeypatch)
    content = f"{_config_line(token_digest(OLD_TOKEN).hex())}\n".encode()
    config = _write_config(home, content)
    service = _fake_systemctl(home, monkeypatch, active=True, enabled=True)
    _accounts(monkeypatch, _ubuntu_layout(home), members=("imp-user", "other"))
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: pytest.fail("generated token"))
    stdout, stderr = _tty_streams(monkeypatch)

    assert direct_wss.main([operation]) == (0 if operation == "status" else 1)
    output = stdout.getvalue() + stderr.getvalue()
    reason = f"~/.config {SHARED_GROUP}"
    if operation == "status":
        assert f"gateway.env path: unsafe ({reason})\n" in output
        assert "gateway.env syntax: not inspected\n" in output
        assert f"gateway unit policy: unsupported ({reason})\n" in output
    else:
        assert output == f"error: {reason}\n"
    assert token_digest(OLD_TOKEN).hex() not in output and OLD_TOKEN not in output
    assert config.read_bytes() == content
    assert not (config.parent / direct_wss._LOCK_NAME).exists()
    assert not service.actions


def test_unsafe_systemd_ancestor_is_reported_as_unit_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    _write_config(home, f"{_config_line(token_digest(OLD_TOKEN).hex())}\n".encode())
    _fake_systemctl(home, monkeypatch)
    _accounts(monkeypatch, _ubuntu_layout(home))
    os.chmod(home / ".config/systemd/user", 0o777)
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)

    assert direct_wss.main(["status"]) == 0
    assert "gateway.env path: supported\n" in out.getvalue()
    assert "gateway.env syntax: valid\n" in out.getvalue()
    assert "gateway unit policy: unsupported (~/.config/systemd/user is world-writable)\n" in out.getvalue()
    assert token_digest(OLD_TOKEN).hex() not in out.getvalue()


def test_private_group_exception_never_applies_to_imp_config_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    config = _write_config(home, f"{_config_line(token_digest(OLD_TOKEN).hex())}\n".encode())
    _fake_systemctl(home, monkeypatch)
    _accounts(monkeypatch, _ubuntu_layout(home))
    os.chmod(config.parent, 0o770)
    stdout, stderr = _tty_streams(monkeypatch)

    assert direct_wss.main(["status"]) == 0
    assert "gateway.env path: unsafe (~/.config/imp must be owned by the target user with mode 0700)\n" in (
        stdout.getvalue()
    )
    assert direct_wss.main(["setup"]) == 1
    assert stderr.getvalue() == "error: ~/.config/imp must be owned by the target user with mode 0700\n"


def test_systemd_preflight_accepts_only_shipped_manager_and_disk_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    service = _fake_systemctl(home, monkeypatch, active=True, enabled=False)
    with direct_wss._open_home() as (_, path, home_fd):
        assert direct_wss._service_preflight(home_fd, path) == direct_wss.ServiceState(True, False)

    for name, value in (
        ("NeedDaemonReload", "yes"),
        ("FragmentPath", "/etc/systemd/user/imp-gateway.service"),
        ("DropInPaths", "/tmp/override.conf"),
        ("Restart", "always"),
        ("EnvironmentFiles", "/tmp/other.env (ignore_errors=no)"),
        ("ExecStart", "{ path=/usr/bin/other ; argv[]=/usr/bin/other }"),
    ):
        service.properties[name] = value
        with direct_wss._open_home() as (_, path, home_fd), pytest.raises(direct_wss.DirectWssError):
            direct_wss._service_preflight(home_fd, path)
        service.properties.clear()


def test_systemd_preflight_rejects_disk_drift_dropins_and_symlink_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    _fake_systemctl(home, monkeypatch)
    unit_dir = home / ".config/systemd/user"
    unit = unit_dir / direct_wss._UNIT
    unit.write_bytes(direct_wss._SHIPPED_UNIT + b"# changed\n")
    with direct_wss._open_home() as (_, path, home_fd), pytest.raises(direct_wss.DirectWssError):
        direct_wss._service_preflight(home_fd, path)
    unit.write_bytes(direct_wss._SHIPPED_UNIT)
    (unit_dir / f"{direct_wss._UNIT}.d").mkdir()
    with direct_wss._open_home() as (_, path, home_fd), pytest.raises(direct_wss.DirectWssError):
        direct_wss._service_preflight(home_fd, path)
    (unit_dir / f"{direct_wss._UNIT}.d").rmdir()
    unit.unlink()
    unit.symlink_to(tmp_path / "missing-unit")
    with direct_wss._open_home() as (_, path, home_fd), pytest.raises(direct_wss.DirectWssError):
        direct_wss._service_preflight(home_fd, path)


def test_concurrent_setup_issues_one_credential_without_overwriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    service = _fake_systemctl(home, monkeypatch)
    _tty_streams(monkeypatch)
    # This lock regression uses threads; native signal handling is covered by CLI subprocesses below.
    monkeypatch.setattr(direct_wss, "_interrupt_signals", lambda: nullcontext(lambda: None))
    entered = threading.Event()
    second_waiting = threading.Event()
    release = threading.Event()
    generated: list[str] = []
    outputs = [TTY(), TTY()]
    failures: list[BaseException] = []
    original_lock = direct_wss._lock_mutations

    def lock(dir_fd: int, uid: int) -> int:
        if threading.current_thread().name == "second-setup":
            second_waiting.set()
        return original_lock(dir_fd, uid)

    def generate(length: int) -> str:
        generated.append(TOKEN)
        entered.set()
        assert release.wait(timeout=3)
        return TOKEN

    def setup(output: TTY) -> None:
        try:
            direct_wss._mutate("setup", output)
        except BaseException as error:
            failures.append(error)

    monkeypatch.setattr(direct_wss, "_lock_mutations", lock)
    monkeypatch.setattr(secrets, "token_urlsafe", generate)
    monkeypatch.setattr(direct_wss, "_verify_gateway", lambda token: None)
    first = threading.Thread(target=setup, args=(outputs[0],))
    second = threading.Thread(target=setup, args=(outputs[1],), name="second-setup")
    first.start()
    try:
        assert entered.wait(timeout=3)
        second.start()
        assert second_waiting.wait(timeout=3)
    finally:
        release.set()
        first.join(timeout=3)
        if second.ident is not None:
            second.join(timeout=3)
    assert not failures and not first.is_alive() and not second.is_alive()
    assert generated == [TOKEN]
    assert outputs[0].getvalue().count(TOKEN) == 1
    assert TOKEN not in outputs[1].getvalue()
    assert direct_wss._parse_config((home / ".config/imp/gateway.env").read_bytes()) == token_digest(TOKEN)
    assert service.actions == [("enable", "--now", direct_wss._UNIT)]


def test_created_modes_ignore_a_permissive_umask(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _home(tmp_path, monkeypatch)
    service = _fake_systemctl(home, monkeypatch)
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: TOKEN)
    monkeypatch.setattr(direct_wss, "_verify_gateway", lambda token: None)
    _tty_streams(monkeypatch)
    old_umask = os.umask(0)
    try:
        assert direct_wss.main(["setup"]) == 0
    finally:
        os.umask(old_umask)

    config_dir = home / ".config/imp"
    assert stat.S_IMODE(config_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((config_dir / "gateway.env").stat().st_mode) == 0o600
    assert stat.S_IMODE((config_dir / direct_wss._LOCK_NAME).stat().st_mode) == 0o600
    assert service.active and service.enabled


def test_setup_handles_stdout_failure_without_handing_off_or_retaining_new_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    service = _fake_systemctl(home, monkeypatch)
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: TOKEN)
    monkeypatch.setattr(direct_wss, "_verify_gateway", lambda token: None)

    class BrokenTTY(TTY):
        def write(self, value: str) -> int:
            if value.startswith("Direct WSS pairing token"):
                return super().write(value)
            raise BrokenPipeError

    monkeypatch.setattr(sys, "stdin", TTY())
    stdout = BrokenTTY()
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    assert direct_wss.main(["setup"]) == 1
    assert TOKEN not in stdout.getvalue()
    assert TOKEN not in stderr.getvalue()
    assert not (home / ".config/imp/gateway.env").exists()
    assert not service.active and not service.enabled


def test_staged_file_sync_failure_leaves_previous_bytes_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    old_data = f"{_config_line(token_digest(OLD_TOKEN).hex())}\n".encode()
    config = _write_config(home, old_data)
    _fake_systemctl(home, monkeypatch, active=False, enabled=True)
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: TOKEN)
    _tty_streams(monkeypatch)

    def fail_sync(fd: int) -> None:
        raise OSError("sync failure")

    monkeypatch.setattr(os, "fsync", fail_sync)

    assert direct_wss.main(["rotate"]) == 1
    assert config.read_bytes() == old_data
    assert not list(config.parent.glob(".gateway.env.tmp.*"))


def test_config_restore_failure_is_explicit_and_stops_active_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    old_data = f"{_config_line(token_digest(OLD_TOKEN).hex())}\n".encode()
    config = _write_config(home, old_data)
    service = _fake_systemctl(home, monkeypatch, active=True, enabled=True)
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: TOKEN)
    monkeypatch.setattr(
        direct_wss,
        "_verify_gateway",
        lambda token: (_ for _ in ()).throw(direct_wss.DirectWssError("readiness failed")),
    )
    _, stderr = _tty_streams(monkeypatch)
    original_replace = os.replace
    replacements = 0

    def fail_rollback_replace(source: str, destination: str, **kwargs: Any) -> None:
        nonlocal replacements
        replacements += 1
        if replacements == 2:
            raise OSError("rollback sync failed")
        original_replace(source, destination, **kwargs)

    monkeypatch.setattr(os, "replace", fail_rollback_replace)

    assert direct_wss.main(["rotate"]) == 1
    assert replacements == 2
    assert "restoration failed" in stderr.getvalue()
    assert "gateway stop was attempted" in stderr.getvalue()
    assert not service.active and service.enabled
    assert token_digest(TOKEN).hex().encode() in config.read_bytes()


def test_unsafe_gateway_env_file_types_and_config_directory_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    config_dir = _config_dir(home)
    config = config_dir / "gateway.env"
    config.mkdir()
    _tty_streams(monkeypatch)
    monkeypatch.setattr(direct_wss, "_systemctl", lambda *args: pytest.fail("called systemctl"))

    assert direct_wss.main(["setup"]) == 1
    assert config.is_dir()
    config.rmdir()
    os.mkfifo(config)
    assert direct_wss.main(["setup"]) == 1
    assert config.is_fifo()
    config.unlink()
    config.write_bytes(f"{_config_line(token_digest(TOKEN).hex())}\n".encode())
    os.chmod(config, 0o600)
    os.chmod(config_dir, 0o755)
    assert direct_wss.main(["setup"]) == 1
    assert stat.S_IMODE(config_dir.stat().st_mode) == 0o755
    assert config.exists()


def test_systemd_preflight_rejects_masked_linked_and_mismatched_unit_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    service = _fake_systemctl(home, monkeypatch)
    for name, value in (("LoadState", "masked"), ("UnitFileState", "linked"), ("ActiveState", "failed")):
        service.properties[name] = value
        with direct_wss._open_home() as (_, path, home_fd), pytest.raises(direct_wss.DirectWssError):
            direct_wss._service_preflight(home_fd, path)
        service.properties.clear()


def test_symlinked_home_is_rejected_before_any_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    actual_home = tmp_path / "actual-home"
    actual_home.mkdir()
    alias = tmp_path / "home-link"
    alias.symlink_to(actual_home, target_is_directory=True)
    monkeypatch.setattr(direct_wss, "_supported_platform", lambda: None)
    monkeypatch.setattr(direct_wss, "_user_home", lambda: (os.geteuid(), str(alias)))
    _tty_streams(monkeypatch)

    assert direct_wss.main(["setup"]) == 1
    assert list(actual_home.iterdir()) == []


def test_status_command_never_creates_config_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    _install_unit(home)
    fake = FakeSystemctl(home)
    monkeypatch.setattr(direct_wss, "_systemctl", fake)
    monkeypatch.setattr(direct_wss, "_gateway_health", lambda: False)
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    assert direct_wss.main(["status"]) == 0
    assert not (home / ".config/imp").exists()


def test_setup_refuses_active_gateway_without_generation_or_service_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    service = _fake_systemctl(home, monkeypatch, active=True, enabled=True)
    _tty_streams(monkeypatch)
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: pytest.fail("generated token"))

    assert direct_wss.main(["setup"]) == 1
    assert not (home / ".config/imp/gateway.env").exists()
    assert not service.actions


def test_stdout_token_handoff_failure_rolls_back_active_rotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    old_data = f"{_config_line(token_digest(OLD_TOKEN).hex())}\n".encode()
    config = _write_config(home, old_data)
    service = _fake_systemctl(home, monkeypatch, active=True, enabled=False)
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: TOKEN)
    monkeypatch.setattr(direct_wss, "_verify_gateway", lambda token: None)

    class BrokenTTY(TTY):
        def write(self, value: str) -> int:
            if value.startswith("Direct WSS pairing token"):
                return super().write(value)
            raise BrokenPipeError

    monkeypatch.setattr(sys, "stdin", TTY())
    monkeypatch.setattr(sys, "stdout", BrokenTTY())
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)

    assert direct_wss.main(["rotate"]) == 1
    assert config.read_bytes() == old_data
    assert service.active and not service.enabled
    assert TOKEN not in stderr.getvalue()


def test_readiness_interrupt_restores_config_and_service_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    old_data = f"{_config_line(token_digest(OLD_TOKEN).hex())}\n".encode()
    config = _write_config(home, old_data)
    service = _fake_systemctl(home, monkeypatch, active=True, enabled=True)
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: TOKEN)
    monkeypatch.setattr(
        direct_wss,
        "_verify_gateway",
        lambda token: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    stdout, stderr = _tty_streams(monkeypatch)

    assert direct_wss.main(["rotate"]) == 1
    assert config.read_bytes() == old_data
    assert service.active and service.enabled
    assert not stdout.getvalue()
    assert "interrupted" in stderr.getvalue()
    assert TOKEN not in stderr.getvalue()


def test_captured_systemctl_errors_never_echo_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    home = _home(tmp_path, monkeypatch)
    service = _fake_systemctl(home, monkeypatch)
    original = service

    def failing_start(*args: str) -> subprocess.CompletedProcess[str]:
        result = original(*args)
        if args[0] == "enable" and "--now" in args:
            return subprocess.CompletedProcess(args, 1, "", TOKEN)
        return result

    monkeypatch.setattr(direct_wss, "_systemctl", failing_start)
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: TOKEN)
    monkeypatch.setattr(direct_wss, "_verify_gateway", lambda token: None)
    stdout, stderr = _tty_streams(monkeypatch)

    assert direct_wss.main(["setup"]) == 1
    assert not stdout.getvalue()
    assert TOKEN not in stderr.getvalue()
    assert TOKEN not in caplog.text
    assert TOKEN not in " ".join(os.environ.values())
    assert not (home / ".config/imp/gateway.env").exists()
    assert not original.active and not original.enabled


def test_cli_platform_guard_rejects_windows_and_root(monkeypatch: pytest.MonkeyPatch) -> None:
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    assert direct_wss.main(["status"]) == 1

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert direct_wss.main(["status"]) == 1


def test_systemd_execstart_uses_one_quoted_argv_field_and_exact_environment_file() -> None:
    home = "/home/test user"
    binary = f"{home}/.local/share/imp/current/bin/imp-server"
    argv = (
        binary,
        "gateway",
        "--host",
        "127.0.0.1",
        "--port",
        "8788",
        "--relay-url",
        "ws://127.0.0.1:8787",
    )
    property_value = f"{{ path={binary} ; argv[]={shlex.join(argv)} ; ignore_errors=no }}"

    assert direct_wss._expected_exec_start(property_value, home)
    assert direct_wss._expected_environment_files(f"{home}/.config/imp/gateway.env (ignore_errors=no)", home)
    repeated_fields = f"{{ path={binary} ; argv[]={binary} ; argv[]=--host ; ignore_errors=no }}"
    assert not direct_wss._expected_exec_start(repeated_fields, home)
    legacy = f"{home}/.local/share/imp/current/.venv/bin/imp-gateway"
    legacy_property = f"{{ path={legacy} ; argv[]={shlex.join((legacy, *argv[2:]))} ; ignore_errors=no }}"
    assert not direct_wss._expected_exec_start(legacy_property, home)


def test_frozen_systemctl_gets_original_library_path(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, str]] = []

    def run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append(kwargs["env"])
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/host/lib")
    direct_wss._systemctl("show")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG")
    direct_wss._systemctl("show")

    assert seen[0]["LD_LIBRARY_PATH"] == "/host/lib"
    assert "LD_LIBRARY_PATH" not in seen[1]


def test_gateway_health_requires_exact_minimal_response(monkeypatch: pytest.MonkeyPatch) -> None:
    class Handler(http.server.BaseHTTPRequestHandler):
        body = b'{"status":"ok"}'
        status = 200

        def do_GET(self) -> None:
            self.send_response(self.status)
            self.send_header("Content-Length", str(len(self.body)))
            self.end_headers()
            self.wfile.write(self.body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    monkeypatch.setattr(direct_wss, "_GATEWAY_PORT", server.server_address[1])
    thread.start()
    try:
        assert direct_wss._gateway_health()
        Handler.body = b'{"status":"ok","extra":true}'
        assert not direct_wss._gateway_health()
        Handler.body = b'{"status":"bad","status":"ok"}'
        assert not direct_wss._gateway_health()
        Handler.status = 503
        Handler.body = b'{"status":"ok"}'
        assert not direct_wss._gateway_health()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_unavailable_relay_aborts_before_token_generation_or_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    service = _fake_systemctl(home, monkeypatch)
    _tty_streams(monkeypatch)

    async def unavailable(port: int, token: str | None = None) -> bool:
        return False

    monkeypatch.setattr(direct_wss, "_state_hello", unavailable)
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: pytest.fail("generated token"))
    assert direct_wss.main(["setup"]) == 1
    assert not service.actions
    assert not (home / ".config/imp/gateway.env").exists()


def test_generated_token_has_canonical_256_bit_encoding_and_textual_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    _fake_systemctl(home, monkeypatch)
    monkeypatch.setattr(direct_wss, "_verify_gateway", lambda token: None)
    stdout, _ = _tty_streams(monkeypatch)
    assert direct_wss.main(["setup"]) == 0
    token = stdout.getvalue().splitlines()[-1]
    raw = base64.urlsafe_b64decode(token + "=")
    assert len(token) == 43 and len(raw) == 32
    assert base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") == token
    config = (home / ".config/imp/gateway.env").read_bytes()
    assert direct_wss._parse_config(config) == hashlib.sha256(token.encode("ascii")).digest()
    assert token.encode("ascii") not in config


@pytest.mark.parametrize(
    "url", ["wss://imp.example/state", "wss://imp.example:443/state", "wss://[::1]:8443/state"]
)
def test_public_url_accepts_exact_state_endpoint(url: str) -> None:
    assert direct_wss._public_url(url).geturl() == url


def test_loopback_verification_endpoint_is_fixed_local_https() -> None:
    assert direct_wss._LOOPBACK_ENDPOINT == ("127.0.0.1", 443)


@pytest.mark.parametrize(
    "url",
    [
        "ws://imp.example/state",
        "https://imp.example/state",
        "wss:///state",
        f"wss://{TOKEN}@imp.example/state",
        f"wss://user:{TOKEN}@imp.example/state",
        f"wss://imp.example/state?token={TOKEN}",
        f"wss://imp.example/state#{TOKEN}",
        "wss://imp.example/state?",
        "wss://imp.example/state#",
        "wss://imp.example/prefix/state",
        "wss://imp.example/state/",
        "wss://imp.example/action",
        "wss://imp.example:0/state",
        "wss://imp.example:65536/state",
        "wss://imp.example:bad/state",
        "wss://imp.example:/state",
        "wss://imp.example/\nstate",
        " wss://imp.example/state",
        "wss://imp.example\\evil/state",
        "wss://imp.example/%73tate",
        "wss://[broken/state",
    ],
)
def test_public_url_rejection_never_echoes_input(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    stdout, stderr = _tty_streams(monkeypatch)
    assert direct_wss.main(["setup", "--public-url", url]) == 1
    assert not stdout.getvalue()
    assert url not in stderr.getvalue()
    assert TOKEN not in stderr.getvalue()


PUBLIC_URL = "wss://imp.example.test/state"


@pytest.fixture
def public_edge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,DNS:imp.example.test",
        ],
        check=True,
        capture_output=True,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    # Add only this disposable certificate to the normal trust store; never disable TLS validation.
    monkeypatch.setenv("SSL_CERT_FILE", str(cert))
    real_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        # Public test names are intentionally unresolvable; any lookup is a public dial attempt.
        if isinstance(host, str) and host.endswith(".example.test"):
            edge.lookups.append(host)
            raise socket.gaierror(socket.EAI_NONAME, "intentionally unreachable")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    for name in ("https_proxy", "HTTPS_PROXY", "wss_proxy", "WSS_PROXY", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    edge = SimpleNamespace(
        health=200,
        body='{"status":"ok"}',
        auth=True,
        hello=2,
        forbidden=None,
        websocket_only=False,
        redirect=False,
        requests=[],
        hosts=[],
        sni=[],
        lookups=[],
    )

    def request(connection: ServerConnection, request: Request) -> Response | None:
        upgrade = request.headers.get("Upgrade") == "websocket"
        edge.requests.append((request.path, upgrade))
        edge.hosts.append(request.headers.get("Host"))
        if request.path == "/state":
            if edge.redirect:
                response = connection.respond(302, "redirect")
                response.headers["Location"] = edge.url
                return response
            return None
        if request.path == "/healthz":
            return connection.respond(edge.health, edge.body)
        if request.path == edge.forbidden and (not edge.websocket_only or upgrade):
            return connection.respond(200, TOKEN)
        return connection.respond(404, "Not found")

    async def state(connection: ServerConnection) -> None:
        auth = json.loads(await asyncio.wait_for(connection.recv(), timeout=2))
        assert auth == {"type": "auth", "token": TOKEN}
        if not edge.auth:
            await connection.close(1008, TOKEN)
            return
        await connection.send(
            json.dumps(
                {
                    "type": "hello",
                    "protocol": edge.hello,
                    "at": 0,
                    "relay": {"name": "imp-relay", "version": "0.0.0"},
                }
            )
        )

    context.sni_callback = lambda sock, name, ctx: edge.sni.append(name)
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    async def start() -> Server:
        return await serve(
            state, "127.0.0.1", 0, ssl=context, process_request=request, logger=websocket_logger()
        )

    server = asyncio.run_coroutine_threadsafe(start(), loop).result(timeout=5)
    edge.url = f"wss://localhost:{server.sockets[0].getsockname()[1]}/state"
    # Test-only: production is fixed 127.0.0.1:443; the ephemeral port stands in for local Caddy.
    monkeypatch.setattr(direct_wss, "_LOOPBACK_ENDPOINT", ("127.0.0.1", server.sockets[0].getsockname()[1]))
    try:
        yield edge
    finally:

        async def stop() -> None:
            server.close()
            await server.wait_closed()

        asyncio.run_coroutine_threadsafe(stop(), loop).result(timeout=5)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=3)
        loop.close()


@pytest.mark.parametrize("loopback", [False, True])
@pytest.mark.parametrize(
    "command,enabled,health", [("setup", False, 200), ("rotate", False, 404), ("rotate", True, 200)]
)
def test_public_verification_precedes_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    public_edge: SimpleNamespace,
    command: str,
    enabled: bool,
    health: int,
    loopback: bool,
) -> None:
    home = _home(tmp_path, monkeypatch)
    if command == "rotate":
        _write_config(home, f"{_config_line(token_digest(OLD_TOKEN).hex())}\n".encode())
    service = _fake_systemctl(home, monkeypatch, active=command == "rotate", enabled=enabled)
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: TOKEN)
    monkeypatch.setattr(direct_wss, "_verify_gateway", lambda token: None)
    stdout, stderr = _tty_streams(monkeypatch)
    public_edge.health = health
    handoff = direct_wss._write_pairing_token

    def checked_handoff(token: str, output: io.StringIO) -> None:
        assert ("/healthz", False) in public_edge.requests
        assert TOKEN not in output.getvalue()
        handoff(token, output)

    monkeypatch.setattr(direct_wss, "_write_pairing_token", checked_handoff)
    url, flags = (PUBLIC_URL, ["--verify-via-loopback"]) if loopback else (public_edge.url, [])
    assert direct_wss.main([command, "--public-url", url, *flags]) == 0, stderr.getvalue()
    assert stdout.getvalue().endswith(TOKEN + "\n")
    assert not stderr.getvalue()
    assert service.active and service.enabled == (command == "setup" or enabled)
    assert TOKEN not in repr(service.calls)
    assert TOKEN not in repr(dict(os.environ))
    assert public_edge.requests == [
        ("/state", True),
        ("/ingest", False),
        ("/ingest", True),
        ("/action-consumer", False),
        ("/action-consumer", True),
        ("/unknown", False),
        ("/unknown", True),
        ("/healthz", False),
    ]
    if loopback:
        # Public name is the TLS identity and Host but is never resolved or dialed.
        assert set(public_edge.sni) == set(public_edge.hosts) == {"imp.example.test"}
        assert public_edge.lookups == []
        assert stdout.getvalue().index("Public WSS endpoint: verified") < stdout.getvalue().index(TOKEN)


def test_default_public_verification_still_dials_public_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, public_edge: SimpleNamespace
) -> None:
    home = _home(tmp_path, monkeypatch)
    old_data = f"{_config_line(token_digest(OLD_TOKEN).hex())}\n".encode()
    config = _write_config(home, old_data)
    service = _fake_systemctl(home, monkeypatch, active=True, enabled=False)
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: TOKEN)
    monkeypatch.setattr(direct_wss, "_verify_gateway", lambda token: None)
    stdout, stderr = _tty_streams(monkeypatch)
    assert direct_wss.main(["rotate", "--public-url", PUBLIC_URL]) == 1
    assert set(public_edge.lookups) == {"imp.example.test"}
    assert public_edge.requests == []
    assert config.read_bytes() == old_data
    assert service.active and not service.enabled
    assert not stdout.getvalue()
    assert TOKEN not in stderr.getvalue()


@pytest.mark.parametrize("url", ["wss://imp.example.test:8443/state", "wss://imp.example.test:443x/state"])
def test_loopback_mode_rejects_non_default_public_port_before_any_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, public_edge: SimpleNamespace, url: str
) -> None:
    home = _home(tmp_path, monkeypatch)
    monkeypatch.setattr(direct_wss, "_systemctl", lambda *args: pytest.fail("called systemctl"))
    stdout, stderr = _tty_streams(monkeypatch)
    assert direct_wss.main(["setup", "--public-url", url, "--verify-via-loopback"]) == 1
    assert not stdout.getvalue()
    assert url not in stderr.getvalue()
    assert public_edge.requests == [] and public_edge.lookups == []
    assert not (home / ".config/imp/gateway.env").exists()


@pytest.mark.parametrize(
    "failure",
    [
        "tls",
        "hostname",
        "auth",
        "hello",
        "redirect",
        "http-ingest",
        "ws-ingest",
        "http-action-consumer",
        "ws-action-consumer",
        "http-unknown",
        "ws-unknown",
        "health",
        "health-body",
    ],
)
@pytest.mark.parametrize("loopback", [False, True])
@pytest.mark.parametrize("command,enabled", [("setup", False), ("rotate", False), ("rotate", True)])
def test_public_failure_restores_state_without_disclosing_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    public_edge: SimpleNamespace,
    caplog: pytest.LogCaptureFixture,
    failure: str,
    command: str,
    enabled: bool,
    loopback: bool,
) -> None:
    home = _home(tmp_path, monkeypatch)
    old_data = f"# preserve these bytes\n{_config_line(token_digest(OLD_TOKEN).hex())}\n".encode()
    if command == "rotate":
        _write_config(home, old_data)
    service = _fake_systemctl(home, monkeypatch, active=command == "rotate", enabled=enabled)
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: TOKEN)
    monkeypatch.setattr(direct_wss, "_verify_gateway", lambda token: None)
    stdout, stderr = _tty_streams(monkeypatch)
    caplog.set_level(logging.DEBUG)
    url, flags = public_edge.url, []
    if loopback:
        url, flags = PUBLIC_URL, ["--verify-via-loopback"]
    if failure == "tls":
        monkeypatch.delenv("SSL_CERT_FILE")
    elif failure == "hostname":
        url = "wss://other.example.test/state" if loopback else url.replace("localhost", "127.0.0.1")
    elif failure == "auth":
        public_edge.auth = False
    elif failure == "hello":
        public_edge.hello = 1
    elif failure == "redirect":
        public_edge.redirect = True
    elif failure.startswith(("http-", "ws-")):
        public_edge.forbidden = "/" + failure.split("-", 1)[1]
        public_edge.websocket_only = failure.startswith("ws-")
    elif failure == "health":
        public_edge.health = 503
    else:
        public_edge.body = '{"status":"ok","extra":true}'

    assert direct_wss.main([command, "--public-url", url, *flags]) == 1
    config = home / ".config/imp/gateway.env"
    if command == "rotate":
        assert config.read_bytes() == old_data
    else:
        assert not config.exists()
    assert service.active == (command == "rotate")
    assert service.enabled == enabled
    assert "restored" in stderr.getvalue()
    assert TOKEN not in stdout.getvalue() + stderr.getvalue() + caplog.text
    assert TOKEN not in repr(service.calls)
    assert TOKEN not in repr(dict(os.environ))


def test_public_setup_is_idempotent_without_existing_plaintext(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = _home(tmp_path, monkeypatch)
    old_data = f"{_config_line(token_digest(OLD_TOKEN).hex())}\n".encode()
    config = _write_config(home, old_data)
    stdout, stderr = _tty_streams(monkeypatch)
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: pytest.fail("generated token"))
    monkeypatch.setattr(direct_wss, "_systemctl", lambda *args: pytest.fail("called systemctl"))
    monkeypatch.setattr(direct_wss, "_verify_public", lambda *args: pytest.fail("called network"))
    assert direct_wss.main(["setup", "--public-url", "wss://imp.example/state"]) == 0
    assert config.read_bytes() == old_data
    assert "Public verification not performed" in stdout.getvalue()
    assert not stderr.getvalue()
    assert not (config.parent / direct_wss._LOCK_NAME).exists()


def test_public_rotation_of_stopped_gateway_refuses_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = _home(tmp_path, monkeypatch)
    old_data = f"{_config_line(token_digest(OLD_TOKEN).hex())}\n".encode()
    config = _write_config(home, old_data)
    service = _fake_systemctl(home, monkeypatch, enabled=True)
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: pytest.fail("generated token"))
    stdout, stderr = _tty_streams(monkeypatch)
    assert direct_wss.main(["rotate", "--public-url", "wss://imp.example/state"]) == 1
    assert config.read_bytes() == old_data
    assert not service.actions and not service.active and service.enabled
    assert not stdout.getvalue()
    assert "already active" in stderr.getvalue()


@pytest.mark.parametrize(
    "args",
    [
        ["status", "--public-url", "wss://imp.example/state"],
        ["setup", "--public-url"],
        ["setup", "--public-url", "wss://imp.example/state", "--public-url", "wss://other/state"],
        ["setup", "--verify-via-loopback"],
        ["setup", "--verify-via-loopback", "--public-url", "wss://imp.example/state"],
        ["status", "--verify-via-loopback"],
        [
            "setup",
            "--public-url",
            "wss://imp.example/state",
            "--verify-via-loopback",
            "--verify-via-loopback",
        ],
        ["rotate", "--public-url", "wss://imp.example/state", "--verify-via-loopback", "127.0.0.1:1"],
        ["rotate", "--public-url", "wss://imp.example/state", "--other"],
    ],
)
def test_cli_rejects_unsupported_public_option_forms(
    args: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    stdout, stderr = _tty_streams(monkeypatch)
    assert direct_wss.main(args) == 2
    assert not stdout.getvalue()
    assert "usage:" in stderr.getvalue()


@pytest.mark.parametrize(
    ("url", "loopback", "destination", "tls_host", "authority"),
    [
        (
            "wss://[2606:4700:4700::1111]/state",
            False,
            ("2606:4700:4700::1111", 443),
            "2606:4700:4700::1111",
            "[2606:4700:4700::1111]",
        ),
        (
            "wss://[2606:4700:4700::1111]:8443/state",
            False,
            ("2606:4700:4700::1111", 8443),
            "2606:4700:4700::1111",
            "[2606:4700:4700::1111]:8443",
        ),
        (
            "wss://imp.example.test/state",
            False,
            ("imp.example.test", 443),
            "imp.example.test",
            "imp.example.test",
        ),
        (
            "wss://imp.example.test/state",
            True,
            ("127.0.0.1", 443),
            "imp.example.test",
            "imp.example.test",
        ),
    ],
)
def test_public_http_preserves_dial_tls_and_authority(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    loopback: bool,
    destination: tuple[str, int],
    tls_host: str,
    authority: str,
) -> None:
    addresses: list[tuple[str, int]] = []
    tls_hosts: list[str] = []
    requests: list[bytes] = []
    connection = SimpleNamespace(
        setsockopt=lambda *args: None,
        sendall=requests.append,
        makefile=lambda *args: io.BytesIO(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n"),
        close=lambda: None,
    )

    def dial(address: tuple[str, int], *args: Any, **kwargs: Any) -> Any:
        addresses.append(address)
        return connection

    def wrap_socket(context: ssl.SSLContext, sock: Any, **kwargs: Any) -> Any:
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        tls_hosts.append(kwargs["server_hostname"])
        return sock

    # Keep the real HTTPSConnection's host parsing, TLS identity and HTTP authority generation.
    monkeypatch.setattr(socket, "create_connection", dial)
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", wrap_socket)
    parts = direct_wss._public_url(url, loopback)
    assert direct_wss._public_http_get(parts, "/unknown", ssl.create_default_context(), loopback) == (
        404,
        b"",
    )
    assert addresses == [destination]
    assert tls_hosts == [tls_host]
    assert f"\r\nHost: {authority}\r\n".encode() in b"".join(requests)


def test_public_http_deadline_bounds_drip_fed_body_and_restores_timer(
    tmp_path: Path,
    public_edge: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                for byte in body:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(0.04)
            except OSError:
                pass

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path / "cert.pem", tmp_path / "key.pem")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(direct_wss, "_TIMEOUT", 0.2)
    previous_handler = signal.getsignal(signal.SIGALRM)
    try:
        url = direct_wss._public_url(f"wss://localhost:{server.server_address[1]}/state")
        with pytest.raises(TimeoutError):
            direct_wss._public_http_get(url, "/healthz", ssl.create_default_context())
        assert signal.getsignal(signal.SIGALRM) == previous_handler
        assert signal.getitimer(signal.ITIMER_REAL) == (0, 0)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_public_verification_interrupt_uses_rotation_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = _home(tmp_path, monkeypatch)
    old_data = f"{_config_line(token_digest(OLD_TOKEN).hex())}\n".encode()
    config = _write_config(home, old_data)
    service = _fake_systemctl(home, monkeypatch, active=True, enabled=False)
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: TOKEN)
    monkeypatch.setattr(direct_wss, "_verify_gateway", lambda token: None)
    monkeypatch.setattr(
        direct_wss, "_verify_public", lambda *args: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    stdout, stderr = _tty_streams(monkeypatch)
    assert direct_wss.main(["rotate", "--public-url", "wss://imp.example/state"]) == 1
    assert config.read_bytes() == old_data
    assert service.active and not service.enabled
    assert not stdout.getvalue()
    assert "interrupted" in stderr.getvalue()
    assert TOKEN not in stderr.getvalue()


_SIGNAL_CHILD = """
import json, os, sys, time
from pathlib import Path
import pytest
sys.path.insert(0, sys.argv[1])
import test_direct_wss as t
import imp_relay.direct_wss as d

home, command, ready, result = Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4]), Path(sys.argv[5])
mp = pytest.MonkeyPatch()
mp.setattr(d, "_supported_platform", lambda: None)
mp.setattr(d, "_user_home", lambda: (os.geteuid(), str(home)))
service = t._fake_systemctl(home, mp, active=sys.argv[6] == "1", enabled=sys.argv[7] == "1")
mp.setattr(d.secrets, "token_urlsafe", lambda length: t.TOKEN)
config = home / ".config/imp/gateway.env"


def state():
    return {
        "config": config.read_text() if config.exists() else None,
        "active": service.active,
        "enabled": service.enabled,
    }


def wait_for_signal():
    staged = ready.with_name("ready.tmp")
    staged.write_text(json.dumps(state()))
    staged.replace(ready)
    while not ready.with_suffix(".release").exists():
        time.sleep(0.02)


def block(token):
    if sys.argv[8] == "1":
        try:
            raise RuntimeError("verification retry")
        except RuntimeError:
            wait_for_signal()
    else:
        wait_for_signal()


mp.setattr(d, "_verify_gateway", block)
stdout, stderr = t._tty_streams(mp)
rc = d.main([command])
result.write_text(json.dumps({**state(), "stdout": stdout.getvalue(), "stderr": stderr.getvalue()}))
sys.exit(rc)
"""


def _interrupted_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    signum: signal.Signals,
    *,
    active: bool,
    enabled: bool,
    old_data: bytes | None,
    in_exception: bool = False,
) -> tuple[Path, dict[str, Any], dict[str, Any], subprocess.Popen[str]]:
    home = _home(tmp_path, monkeypatch)
    if old_data is not None:
        _write_config(home, old_data)
    ready, result = tmp_path / "ready.json", tmp_path / "result.json"
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _SIGNAL_CHILD,
            str(Path(__file__).parent),
            str(home),
            command,
            str(ready),
            str(result),
            str(int(active)),
            str(int(enabled)),
            str(int(in_exception)),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        while not ready.exists():
            if child.poll() is not None or time.monotonic() > deadline:
                pytest.fail(f"child never reached the blocking boundary: {child.communicate(timeout=5)}")
            time.sleep(0.02)
        replaced = json.loads(ready.read_text())
        child.send_signal(signum)
        ready.with_suffix(".release").touch()
        out, err = child.communicate(timeout=30)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
    final = json.loads(result.read_text())
    assert TOKEN not in out + err + ready.read_text() + result.read_text()
    return home / ".config/imp/gateway.env", replaced, final, child


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP, signal.SIGINT])
@pytest.mark.parametrize("enabled", [False, True])
def test_real_signal_during_setup_restores_absent_config_and_service_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, signum: signal.Signals, enabled: bool
) -> None:
    config, replaced, final, child = _interrupted_child(
        tmp_path, monkeypatch, "setup", signum, active=False, enabled=enabled, old_data=None
    )
    assert token_digest(TOKEN).hex() in replaced["config"]
    assert replaced["active"] and replaced["enabled"]
    assert child.returncode == 1
    assert "interrupted" in final["stderr"]
    assert not config.exists()
    assert not final["active"] and final["enabled"] is enabled
    assert not final["stdout"]


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP, signal.SIGINT])
@pytest.mark.parametrize("enabled", [False, True])
def test_real_signal_during_rotate_keeps_old_credential_and_service_authoritative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, signum: signal.Signals, enabled: bool
) -> None:
    old_data = f"{_config_line(token_digest(OLD_TOKEN).hex())}\n".encode()
    config, replaced, final, child = _interrupted_child(
        tmp_path, monkeypatch, "rotate", signum, active=True, enabled=enabled, old_data=old_data
    )
    assert token_digest(TOKEN).hex() in replaced["config"]
    assert child.returncode == 1
    assert "interrupted" in final["stderr"]
    assert config.read_bytes() == old_data
    assert final["active"] and final["enabled"] is enabled
    assert not final["stdout"]


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP])
def test_real_signal_inside_verification_exception_is_deferred_before_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, signum: signal.Signals
) -> None:
    config, replaced, final, child = _interrupted_child(
        tmp_path,
        monkeypatch,
        "setup",
        signum,
        active=False,
        enabled=False,
        old_data=None,
        in_exception=True,
    )
    assert token_digest(TOKEN).hex() in replaced["config"]
    assert child.returncode == 1 and "interrupted" in final["stderr"]
    assert not config.exists() and not final["active"] and not final["enabled"]
    assert TOKEN not in final["stdout"]


_FAKE_SYSTEMCTL_EXE = """
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, os.environ["FAKE_SYSTEMCTL_TESTS"])
import test_direct_wss as t

root = Path(os.environ["FAKE_SYSTEMCTL_ROOT"])
state = json.loads((root / "state.json").read_text())
fake = t.FakeSystemctl(
    Path(os.environ["FAKE_SYSTEMCTL_HOME"]), active=state["active"], enabled=state["enabled"],
)
fake.restart_count = state["restarts"]
args = tuple(sys.argv[2:])
if args[0] == "restart" and fake.restart_count == 1:
    (root / "rollback-waiting").touch()
    deadline = time.monotonic() + 30
    while not (root / "release").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
result = fake(*args)
(root / "state.json").write_text(
    json.dumps({"active": fake.active, "enabled": fake.enabled, "restarts": fake.restart_count})
)
sys.stdout.write(result.stdout)
sys.exit(result.returncode)
"""

_GROUP_SIGNAL_CHILD = """
import json, os, sys, time
from pathlib import Path
import pytest
sys.path.insert(0, sys.argv[1])
import test_direct_wss as t
import imp_relay.direct_wss as d

home, ready, result = Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
mp = pytest.MonkeyPatch()
mp.setattr(d, "_supported_platform", lambda: None)
mp.setattr(d, "_user_home", lambda: (os.geteuid(), str(home)))


async def relay_ready(port, token=None):
    return True


def block(token):
    ready.touch()
    while True:
        time.sleep(0.02)


mp.setattr(d, "_state_hello", relay_ready)
mp.setattr(d.secrets, "token_urlsafe", lambda length: t.TOKEN)
mp.setattr(d, "_verify_gateway", block)
stdout, stderr = t._tty_streams(mp)
rc = d.main(["rotate"])
result.write_text(json.dumps({"stdout": stdout.getvalue(), "stderr": stderr.getvalue()}))
sys.exit(rc)
"""


@pytest.mark.parametrize("enabled", [False, True])
def test_repeated_group_signals_do_not_kill_systemctl_during_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    home = _home(tmp_path, monkeypatch)
    old_data = f"{_config_line(token_digest(OLD_TOKEN).hex())}\n".encode()
    config = _write_config(home, old_data)
    _install_unit(home)
    root = tmp_path / "fake"
    (root / "bin").mkdir(parents=True)
    (root / "state.json").write_text(json.dumps({"active": True, "enabled": enabled, "restarts": 0}))
    exe = root / "bin" / "systemctl"
    exe.write_text(f"#!{sys.executable}\n{_FAKE_SYSTEMCTL_EXE}")
    exe.chmod(0o755)
    ready, result = tmp_path / "ready", tmp_path / "result.json"
    env = {
        **os.environ,
        "PATH": f"{root / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "FAKE_SYSTEMCTL_ROOT": str(root),
        "FAKE_SYSTEMCTL_HOME": str(home),
        "FAKE_SYSTEMCTL_TESTS": str(Path(__file__).parent),
    }
    # The child leads its own group so the test can signal the whole original group like a terminal would.
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _GROUP_SIGNAL_CHILD,
            str(Path(__file__).parent),
            str(home),
            str(ready),
            str(result),
        ],
        env=env,
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    def wait_for(path: Path) -> None:
        deadline = time.monotonic() + 30
        while not path.exists():
            if child.poll() is not None or time.monotonic() > deadline:
                pytest.fail(f"child never reached {path.name}: {child.communicate(timeout=5)}")
            time.sleep(0.02)

    try:
        wait_for(ready)
        os.killpg(child.pid, signal.SIGTERM)
        wait_for(root / "rollback-waiting")
        os.killpg(child.pid, signal.SIGTERM)
        os.killpg(child.pid, signal.SIGHUP)
        (root / "release").touch()
        out, err = child.communicate(timeout=30)
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
    assert child.returncode == 1, (out, err)
    assert "interrupted" in json.loads(result.read_text())["stderr"]
    assert config.read_bytes() == old_data
    restored = json.loads((root / "state.json").read_text())
    assert restored["active"] is True and restored["enabled"] is enabled


def test_interrupt_while_installing_handlers_restores_originals(monkeypatch: pytest.MonkeyPatch) -> None:
    signums = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    originals = [signal.getsignal(signum) for signum in signums]
    real_signal = signal.signal
    calls = 0

    def interrupted_after_first_install(signum: int, handler: Any) -> Any:
        nonlocal calls
        previous = real_signal(signum, handler)
        calls += 1
        if calls == 1:
            raise KeyboardInterrupt
        return previous

    monkeypatch.setattr(signal, "signal", interrupted_after_first_install)
    with pytest.raises(KeyboardInterrupt), direct_wss._interrupt_signals():
        pytest.fail("entered after interrupted installation")
    monkeypatch.undo()
    assert [signal.getsignal(signum) for signum in signums] == originals


def test_native_signal_during_handler_restore_cannot_leave_partial_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signums = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    real_signal = signal.signal
    saved = {signum: signal.getsignal(signum) for signum in signums}
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, [])
    real_signal(signal.SIGINT, signal.default_int_handler)
    originals = {signum: signal.getsignal(signum) for signum in signums}
    delivered = False

    def interrupted_restore(signum: int, handler: Any) -> Any:
        nonlocal delivered
        previous = real_signal(signum, handler)
        if signum == signal.SIGINT and handler is signal.default_int_handler and not delivered:
            delivered = True
            os.kill(os.getpid(), signal.SIGINT)
        return previous

    try:
        monkeypatch.setattr(signal, "signal", interrupted_restore)
        with pytest.raises(KeyboardInterrupt), direct_wss._interrupt_signals():
            pass
        assert {signum: signal.getsignal(signum) for signum in signums} == originals
        assert signal.pthread_sigmask(signal.SIG_BLOCK, []) == mask
    finally:
        for signum, handler in saved.items():
            real_signal(signum, handler)


def test_interrupt_after_replace_before_bookkeeping_still_restores_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, monkeypatch)
    old_data = f"{_config_line(token_digest(OLD_TOKEN).hex())}\n".encode()
    config = _write_config(home, old_data)
    service = _fake_systemctl(home, monkeypatch, active=False, enabled=False)
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: TOKEN)
    original = direct_wss._atomic_replace
    calls = 0

    def replace_then_interrupt(dir_fd: int, data: bytes) -> None:
        nonlocal calls
        original(dir_fd, data)
        calls += 1
        if calls == 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(direct_wss, "_atomic_replace", replace_then_interrupt)
    stdout, stderr = _tty_streams(monkeypatch)
    assert direct_wss.main(["rotate"]) == 1
    assert config.read_bytes() == old_data
    assert not service.active and not service.enabled
    assert "interrupted" in stderr.getvalue()


@pytest.mark.parametrize("fail", [False, True])
def test_mutation_restores_signal_handlers_and_mask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail: bool
) -> None:
    home = _home(tmp_path, monkeypatch)
    _fake_systemctl(home, monkeypatch)
    monkeypatch.setattr(secrets, "token_urlsafe", lambda length: TOKEN)
    monkeypatch.setattr(
        direct_wss,
        "_verify_gateway",
        lambda token: (_ for _ in ()).throw(KeyboardInterrupt()) if fail else None,
    )
    _tty_streams(monkeypatch)
    signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    handlers = [signal.getsignal(signum) for signum in signals]
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, [])
    assert direct_wss.main(["setup"]) == (1 if fail else 0)
    assert [signal.getsignal(signum) for signum in signals] == handlers
    assert signal.pthread_sigmask(signal.SIG_BLOCK, []) == mask
