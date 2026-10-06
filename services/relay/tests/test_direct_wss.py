from __future__ import annotations

import base64
import errno
import grp
import hashlib
import http.server
import io
import os
import pwd
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import imp_relay.direct_wss as direct_wss
from imp_relay.gateway import token_digest

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
        binary = str(self.home / ".local/share/imp/current/.venv/bin/imp-gateway")
        argv = (
            binary,
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
    binary = f"{home}/.local/share/imp/current/.venv/bin/imp-gateway"
    argv = (
        binary,
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
