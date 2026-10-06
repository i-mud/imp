"""Provision the installed loopback gateway without exporting or storing its pairing token."""

from __future__ import annotations

import asyncio
import http.client
import json
import os
import re
import secrets
import shlex
import stat
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from websockets.asyncio.client import connect

from .gateway import token_digest, valid_pairing_token
from .protocol import HelloMessage, decode_server_message
from .websocket_logging import websocket_logger

try:
    import fcntl
except ImportError:  # Keep desktop imports/builds working outside Linux.
    fcntl = None  # type: ignore[assignment]

_UNIT = "imp-gateway.service"
_CONFIG_NAME = "gateway.env"
_LOCK_NAME = "gateway.env.lock"
_CONFIG_KEY = "IMP_GATEWAY_TOKEN_SHA256"
_GATEWAY_HOST = "127.0.0.1"
_GATEWAY_PORT = 8788
_RELAY_PORT = 8787
_TIMEOUT = 5
_MAX_CONFIG_BYTES = 4096
_DIGEST_LINE = re.compile(r"^IMP_GATEWAY_TOKEN_SHA256[ \t]*=[ \t]*([0-9a-fA-F]{64})$")
_PROPERTIES = (
    "LoadState",
    "FragmentPath",
    "DropInPaths",
    "NeedDaemonReload",
    "Type",
    "ExecStart",
    "EnvironmentFiles",
    "Restart",
    "RestartUSec",
    "NoNewPrivileges",
    "UnitFileState",
    "ActiveState",
)
_SHIPPED_UNIT = (
    "[Unit]\n"
    "Description=Imp authenticated remote gateway\n"
    "After=network.target imp-relay.service\n"
    "Wants=imp-relay.service\n"
    "\n"
    "[Service]\n"
    "Type=simple\n"
    "EnvironmentFile=%h/.config/imp/gateway.env\n"
    "ExecStart=%h/.local/share/imp/current/.venv/bin/imp-gateway --host 127.0.0.1 --port 8788 --relay-url ws://127.0.0.1:8787\n"
    "Restart=on-failure\n"
    "RestartSec=2\n"
    "NoNewPrivileges=true\n"
    "\n"
    "[Install]\n"
    "WantedBy=default.target\n"
).encode("ascii")


class DirectWssError(Exception):
    """A concise, credential-safe command failure."""


class AtomicWriteError(DirectWssError):
    def __init__(self, *, replaced: bool) -> None:
        super().__init__("could not atomically write gateway.env")
        self.replaced = replaced


@dataclass(frozen=True)
class ServiceState:
    active: bool
    enabled: bool


@dataclass(frozen=True)
class ConfigInspection:
    syntax: str
    mode: str
    directory_mode: str
    digest: bytes | None
    data: bytes | None
    exists: bool


def _supported_platform() -> None:
    if not sys.platform.startswith("linux") or fcntl is None:
        raise DirectWssError("Direct WSS requires Linux user-systemd")
    if os.geteuid() == 0:
        raise DirectWssError("run Direct WSS as the installed target user, not root")


def _user_home() -> tuple[int, str]:
    uid = os.geteuid()
    home = str(Path.home())
    if not home.startswith("/") or home == "/":
        raise DirectWssError("the target user's home directory is unavailable")
    return uid, home


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW


def _open_home_component(parent_fd: int, name: str, uid: int) -> int:
    try:
        fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
    except OSError:
        raise DirectWssError("the target user's home directory is unsafe") from None
    try:
        info = os.fstat(fd)
    except OSError:
        os.close(fd)
        raise DirectWssError("the target user's home directory is unsafe") from None
    mode = stat.S_IMODE(info.st_mode)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid not in {0, uid}
        or (mode & 0o022 and not (info.st_uid == 0 and mode & stat.S_ISVTX))
    ):
        os.close(fd)
        raise DirectWssError("the target user's home directory is unsafe")
    return fd


def _open_directory(parent_fd: int, name: str, uid: int, *, create: bool = False) -> int:
    created = False
    try:
        fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            created = True
        except FileExistsError:
            pass
        try:
            fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
        except OSError:
            raise DirectWssError("configuration path contains an unsafe directory") from None
    except OSError:
        raise DirectWssError("configuration path contains an unsafe directory") from None
    try:
        info = os.fstat(fd)
    except OSError:
        os.close(fd)
        raise DirectWssError("configuration path contains an unsafe directory") from None
    mode = stat.S_IMODE(info.st_mode)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or mode & 0o022:
        os.close(fd)
        raise DirectWssError("configuration path contains an unsafe directory")
    if created:
        try:
            os.fchmod(fd, 0o700)
        except OSError:
            os.close(fd)
            raise DirectWssError("could not secure the configuration directory") from None
    return fd


@contextmanager
def _open_home() -> Iterator[tuple[int, str, int]]:
    uid, home = _user_home()
    fd = os.open("/", _directory_flags())
    try:
        for component in home.split("/")[1:]:
            if not component or component in {".", ".."}:
                raise DirectWssError("the target user's home directory is unsafe")
            next_fd = _open_home_component(fd, component, uid)
            os.close(fd)
            fd = next_fd
        if os.fstat(fd).st_uid != uid:
            raise DirectWssError("the target user's home directory is not owned by that user")
        yield uid, home, fd
    finally:
        os.close(fd)


def _config_directory(home_fd: int, uid: int, *, create: bool, private: bool = True) -> int:
    config_fd = _open_directory(home_fd, ".config", uid, create=create)
    try:
        imp_fd = _open_directory(config_fd, "imp", uid, create=create)
    finally:
        os.close(config_fd)
    if private and stat.S_IMODE(os.fstat(imp_fd).st_mode) != 0o700:
        os.close(imp_fd)
        raise DirectWssError("~/.config/imp must be owned by the target user with mode 0700")
    return imp_fd


def _read_regular_file(dir_fd: int, name: str, uid: int, *, max_bytes: int) -> tuple[bytes, os.stat_result]:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    except FileNotFoundError:
        raise
    except OSError:
        raise DirectWssError("configuration file is not a safe regular file") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1:
            raise DirectWssError("configuration file is not a safe regular file")
        chunks: list[bytes] = []
        size = 0
        while size <= max_bytes:
            chunk = os.read(fd, min(1024, max_bytes + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        if size > max_bytes:
            raise DirectWssError("configuration file is too large")
        return b"".join(chunks), info
    finally:
        os.close(fd)


def _parse_config(data: bytes) -> bytes:
    if b"\x00" in data:
        raise DirectWssError("gateway.env has unsupported or malformed content")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise DirectWssError("gateway.env has unsupported or malformed content") from None
    digests: list[str] = []
    for line in text.split("\n"):
        stripped = line.removesuffix("\r").strip(" \t")
        if not stripped or stripped.startswith("#"):
            continue
        match = _DIGEST_LINE.fullmatch(stripped)
        if match is None:
            raise DirectWssError("gateway.env has unsupported or malformed content")
        digests.append(match.group(1))
    if len(digests) != 1:
        raise DirectWssError("gateway.env must contain exactly one token digest assignment")
    return bytes.fromhex(digests[0])


def _inspect_config(dir_fd: int, uid: int, *, require_private: bool) -> ConfigInspection:
    try:
        data, info = _read_regular_file(dir_fd, _CONFIG_NAME, uid, max_bytes=_MAX_CONFIG_BYTES)
    except FileNotFoundError:
        return ConfigInspection("missing", "unavailable", "0700 private", None, None, False)
    mode = stat.S_IMODE(info.st_mode)
    mode_text = f"{mode:04o} {'private' if mode == 0o600 else 'not private'}"
    if require_private and mode != 0o600:
        raise DirectWssError("gateway.env must be owned by the target user with mode 0600")
    digest = _parse_config(data)
    return ConfigInspection("valid", mode_text, "0700 private", digest, data, True)


def _lock_mutations(dir_fd: int, uid: int) -> int:
    flags = os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    created = False
    try:
        fd = os.open(_LOCK_NAME, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=dir_fd)
        created = True
    except FileExistsError:
        try:
            fd = os.open(_LOCK_NAME, flags, dir_fd=dir_fd)
        except OSError:
            raise DirectWssError("could not safely open the Direct WSS mutation lock") from None
    except OSError:
        raise DirectWssError("could not safely open the Direct WSS mutation lock") from None
    if created:
        try:
            os.fchmod(fd, 0o600)
        except OSError:
            os.close(fd)
            raise DirectWssError("could not secure the Direct WSS mutation lock") from None
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != uid
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        os.close(fd)
        raise DirectWssError("Direct WSS mutation lock has unsafe ownership or permissions")
    assert fcntl is not None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
    except OSError:
        os.close(fd)
        raise DirectWssError("could not acquire the Direct WSS mutation lock") from None
    return fd


def _atomic_replace(dir_fd: int, data: bytes) -> None:
    staged = f".gateway.env.tmp.{secrets.token_hex(8)}"
    fd: int | None = None
    staged_created = False
    replaced = False
    try:
        fd = os.open(
            staged,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=dir_fd,
        )
        staged_created = True
        os.fchmod(fd, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(staged, _CONFIG_NAME, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        replaced = True
    except BaseException:
        if staged_created and not replaced:
            try:
                os.stat(staged, dir_fd=dir_fd, follow_symlinks=False)
            except FileNotFoundError:
                replaced = True
            except OSError:
                replaced = True
        raise AtomicWriteError(replaced=replaced) from None
    finally:
        if fd is not None:
            with suppress(OSError):
                os.close(fd)
        if staged_created:
            try:
                os.unlink(staged, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def _restore_config(dir_fd: int, previous: bytes | None) -> None:
    if previous is None:
        try:
            os.unlink(_CONFIG_NAME, dir_fd=dir_fd)
        except FileNotFoundError:
            return
        except OSError:
            raise DirectWssError("could not remove the generated gateway.env during rollback") from None
    else:
        _atomic_replace(dir_fd, previous)


def _systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ("systemctl", "--user", *args),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise DirectWssError("the user systemd manager could not be reached") from None


def _property_map(output: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            result[key] = value
    return result


def _unit_file(home_fd: int, uid: int, home: str) -> str:
    try:
        config_fd = _open_directory(home_fd, ".config", uid)
        try:
            systemd_fd = _open_directory(config_fd, "systemd", uid)
        finally:
            os.close(config_fd)
        try:
            user_fd = _open_directory(systemd_fd, "user", uid)
        finally:
            os.close(systemd_fd)
    except FileNotFoundError:
        raise DirectWssError("the installed gateway unit path is missing") from None
    try:
        try:
            data, info = _read_regular_file(user_fd, _UNIT, uid, max_bytes=16_384)
        except FileNotFoundError:
            raise DirectWssError("the installed gateway unit is missing") from None
        if stat.S_IMODE(info.st_mode) & 0o022:
            raise DirectWssError("the installed gateway unit has unsafe permissions")
        if data != _SHIPPED_UNIT:
            raise DirectWssError("the installed gateway unit differs from the supported bundle unit")
        try:
            os.stat(f"{_UNIT}.d", dir_fd=user_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise DirectWssError("gateway unit drop-ins are not supported")
        return os.path.join(home, ".config/systemd/user", _UNIT)
    finally:
        os.close(user_fd)


def _expected_exec_start(value: str, home: str) -> bool:
    binary = os.path.join(home, ".local/share/imp/current/.venv/bin/imp-gateway")
    paths = [path.strip() for path in re.findall(r"(?:^|\{ )path=([^;}]*)", value)]
    raw_argv = re.findall(r"argv\[\]=(.*?)(?=\s*;\s*|\s*\})", value)
    if len(raw_argv) != 1:
        return False
    try:
        argv = shlex.split(raw_argv[0])
    except ValueError:
        return False
    return paths == [binary] and argv == [
        binary,
        "--host",
        _GATEWAY_HOST,
        "--port",
        str(_GATEWAY_PORT),
        "--relay-url",
        f"ws://{_GATEWAY_HOST}:{_RELAY_PORT}",
    ]


def _expected_environment_files(value: str, home: str) -> bool:
    expected = os.path.join(home, ".config/imp", _CONFIG_NAME)
    return value.strip() == f"{expected} (ignore_errors=no)"


def _service_preflight(home_fd: int, home: str) -> ServiceState:
    expected_fragment = _unit_file(home_fd, os.geteuid(), home)
    shown = _systemctl("show", _UNIT, "--property=" + ",".join(_PROPERTIES))
    if shown.returncode != 0:
        raise DirectWssError("the gateway unit is not available in the user systemd manager")
    props = _property_map(shown.stdout)
    expected_values = {
        "LoadState": "loaded",
        "FragmentPath": expected_fragment,
        "DropInPaths": "",
        "NeedDaemonReload": "no",
        "Type": "simple",
        "Restart": "on-failure",
        "RestartUSec": "2s",
        "NoNewPrivileges": "yes",
    }
    if any(props.get(key) != value for key, value in expected_values.items()):
        raise DirectWssError("the manager's loaded gateway unit is unsupported or differs from disk")
    if not _expected_exec_start(props.get("ExecStart", ""), home):
        raise DirectWssError("the manager's loaded gateway command is not the supported loopback command")
    if not _expected_environment_files(props.get("EnvironmentFiles", ""), home):
        raise DirectWssError("the manager's loaded gateway environment file is unsupported")
    unit_state = props.get("UnitFileState")
    active_state = props.get("ActiveState")
    if unit_state not in {"enabled", "disabled"} or active_state not in {"active", "inactive"}:
        raise DirectWssError("the gateway must be in a normal enabled or disabled, active or inactive state")
    active_check = _systemctl("is-active", "--quiet", _UNIT)
    enabled_check = _systemctl("is-enabled", "--quiet", _UNIT)
    active = active_state == "active"
    enabled = unit_state == "enabled"
    if active_check.returncode != (0 if active else 3) or enabled_check.returncode != (0 if enabled else 1):
        raise DirectWssError("the gateway manager state is inconsistent")
    return ServiceState(active, enabled)


def _systemctl_action(*args: str) -> None:
    if _systemctl(*args).returncode != 0:
        raise DirectWssError("a gateway systemd operation failed")


def _restore_service(
    before: ServiceState,
    *,
    operation_attempted: bool,
    config_restored: bool,
) -> list[str]:
    if not operation_attempted and config_restored:
        return []
    failures: list[str] = []
    active_after = before.active and config_restored
    if operation_attempted or not config_restored:
        try:
            _systemctl_action("restart" if active_after else "stop", _UNIT)
        except DirectWssError:
            failures.append("restore the previous gateway active state")
    if operation_attempted:
        try:
            _systemctl_action("enable" if before.enabled else "disable", _UNIT)
        except DirectWssError:
            failures.append("restore the previous gateway enabled state")
    try:
        active_check = _systemctl("is-active", "--quiet", _UNIT)
        if active_check.returncode != (0 if active_after else 3):
            failures.append("verify the previous gateway active state")
    except DirectWssError:
        failures.append("verify the previous gateway active state")
    if operation_attempted:
        try:
            enabled_check = _systemctl("is-enabled", "--quiet", _UNIT)
            if enabled_check.returncode != (0 if before.enabled else 1):
                failures.append("verify the previous gateway enabled state")
        except DirectWssError:
            failures.append("verify the previous gateway enabled state")
    if failures:
        try:
            _systemctl_action("stop", _UNIT)
        except DirectWssError:
            failures.append("stop the gateway")
    return failures


def _gateway_health() -> bool:
    connection = http.client.HTTPConnection(_GATEWAY_HOST, _GATEWAY_PORT, timeout=_TIMEOUT)
    try:
        connection.request("GET", "/healthz")
        response = connection.getresponse()
        body = response.read(1024)
        if response.status != 200:
            return False
        try:
            return bool(json.loads(body, object_pairs_hook=lambda pairs: pairs) == [("status", "ok")])
        except (json.JSONDecodeError, UnicodeDecodeError):
            return False
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


async def _state_hello(port: int, token: str | None = None) -> bool:
    try:
        async with connect(
            f"ws://{_GATEWAY_HOST}:{port}/state",
            open_timeout=_TIMEOUT,
            close_timeout=2,
            proxy=None,
            logger=websocket_logger(),
        ) as connection:
            if token is not None:
                await connection.send(json.dumps({"type": "auth", "token": token}, separators=(",", ":")))
            frame = await asyncio.wait_for(connection.recv(), timeout=_TIMEOUT)
    except Exception:
        return False
    if not isinstance(frame, str):
        return False
    decoded = decode_server_message(frame)
    return decoded.ok and isinstance(decoded.value, HelloMessage) and decoded.value.protocol == 2


def _verify_gateway(token: str) -> None:
    deadline = time.monotonic() + _TIMEOUT
    while time.monotonic() < deadline:
        if _gateway_health() and asyncio.run(_state_hello(_GATEWAY_PORT, token)):
            return
        time.sleep(0.1)
    raise DirectWssError("gateway did not become healthy and authenticate within the readiness timeout")


def _require_tty() -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise DirectWssError("setup and rotate require interactive stdin and stdout")


def _write_pairing_token(token: str, stdout: IO[str]) -> None:
    try:
        stdout.write("Direct WSS pairing token (shown once; not stored):\n")
        stdout.write(token + "\n")
        stdout.flush()
    except OSError:
        if stdout is sys.stdout:
            with suppress(OSError, ValueError):
                fd = os.open(os.devnull, os.O_WRONLY)
                try:
                    os.dup2(fd, stdout.fileno())
                finally:
                    os.close(fd)
        raise


def _mutate(command: str, stdout: IO[str]) -> None:
    _supported_platform()
    with _open_home() as (uid, home, home_fd):
        try:
            config_fd = _config_directory(home_fd, uid, create=False)
        except FileNotFoundError:
            config_fd = None
        if config_fd is None:
            if command == "rotate":
                raise DirectWssError("rotate requires an existing supported gateway.env")
        else:
            try:
                existing = _inspect_config(config_fd, uid, require_private=True)
                if command == "setup" and existing.exists:
                    stdout.write("Direct WSS is already configured; no changes made.\n")
                    return
                if command == "rotate" and not existing.exists:
                    raise DirectWssError("rotate requires an existing supported gateway.env")
            finally:
                os.close(config_fd)
        _require_tty()
        config_fd = _config_directory(home_fd, uid, create=True)
        lock_fd: int | None = None
        try:
            lock_fd = _lock_mutations(config_fd, uid)
            existing = _inspect_config(config_fd, uid, require_private=True)
            if command == "setup" and existing.exists:
                stdout.write("Direct WSS is already configured; no changes made.\n")
                return
            if command == "rotate" and not existing.exists:
                raise DirectWssError("rotate requires an existing supported gateway.env")
            before = _service_preflight(home_fd, home)
            if command == "setup" and before.active:
                raise DirectWssError("setup cannot replace credentials while the gateway is already active")
            if (command == "setup" or before.active) and not asyncio.run(_state_hello(_RELAY_PORT)):
                raise DirectWssError("the local relay is unavailable or did not send a protocol-v2 hello")
            token = secrets.token_urlsafe(32)
            if not valid_pairing_token(token):
                raise DirectWssError("secure pairing token generation failed")
            digest = token_digest(token)
            old_data = existing.data
            new_data = f"{_CONFIG_KEY}={digest.hex()}\n".encode("ascii")
            config_replaced = False
            service_operation_attempted = False
            try:
                _atomic_replace(config_fd, new_data)
                config_replaced = True
                if command == "setup":
                    service_operation_attempted = True
                    _systemctl_action("enable", "--now", _UNIT)
                elif before.active:
                    service_operation_attempted = True
                    _systemctl_action("restart", _UNIT)
                if command == "setup" or before.active:
                    if _systemctl("is-active", "--quiet", _UNIT).returncode != 0:
                        raise DirectWssError("the gateway service did not become active")
                    expected_enabled = command == "setup" or before.enabled
                    if _systemctl("is-enabled", "--quiet", _UNIT).returncode != (
                        0 if expected_enabled else 1
                    ):
                        raise DirectWssError("the gateway enabled state changed unexpectedly")
                    _verify_gateway(token)
                    stdout.write(
                        "Gateway: locally verified; enabled for boot.\n"
                        if expected_enabled
                        else "Gateway: locally verified; boot enablement remains disabled.\n"
                    )
                else:
                    stdout.write("Credential rotated; gateway remains stopped and enablement is unchanged.\n")
                stdout.write(
                    "Public state URL: wss://<your-public-host>/state\n"
                    "Configure your TLS proxy to forward approved routes to 127.0.0.1:8788.\n"
                    "Paste the URL and token separately into Settings -> Connection -> Direct.\n"
                    "Save and restart Imp.\n"
                )
                _write_pairing_token(token, stdout)
            except BaseException as error:
                rollback_failures: list[str] = []
                config_may_be_replaced = config_replaced or (
                    isinstance(error, AtomicWriteError) and error.replaced
                )
                config_restored = not config_may_be_replaced
                if config_may_be_replaced:
                    try:
                        _restore_config(config_fd, old_data)
                        config_restored = True
                    except DirectWssError:
                        rollback_failures.append("restore the previous gateway.env bytes")
                rollback_failures.extend(
                    _restore_service(
                        before,
                        operation_attempted=service_operation_attempted,
                        config_restored=config_restored,
                    )
                )
                if rollback_failures:
                    raise DirectWssError(
                        "operation failed; restoration failed ("
                        + ", ".join(rollback_failures)
                        + "); gateway stop was attempted"
                    ) from None
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise DirectWssError(
                        "operation interrupted; previous configuration and service state restored"
                    ) from None
                if isinstance(error, DirectWssError):
                    raise DirectWssError(
                        f"{error}; previous configuration and service state restored"
                    ) from None
                raise DirectWssError(
                    "operation failed; previous configuration and service state restored"
                ) from None
        finally:
            if lock_fd is not None:
                os.close(lock_fd)
            os.close(config_fd)


def _status_config(home_fd: int, uid: int) -> ConfigInspection:
    try:
        config_fd = _config_directory(home_fd, uid, create=False, private=False)
    except FileNotFoundError:
        return ConfigInspection("missing", "unavailable", "unavailable", None, None, False)
    except DirectWssError:
        return ConfigInspection("unsafe", "unavailable", "unavailable", None, None, False)
    try:
        directory_value = stat.S_IMODE(os.fstat(config_fd).st_mode)
        directory_mode = f"{directory_value:04o} {'private' if directory_value == 0o700 else 'not private'}"
        try:
            data, info = _read_regular_file(config_fd, _CONFIG_NAME, uid, max_bytes=_MAX_CONFIG_BYTES)
        except FileNotFoundError:
            return ConfigInspection("missing", "unavailable", directory_mode, None, None, False)
        except DirectWssError:
            try:
                info = os.stat(_CONFIG_NAME, dir_fd=config_fd, follow_symlinks=False)
                mode = stat.S_IMODE(info.st_mode)
                mode_text = f"{mode:04o} {'private' if mode == 0o600 else 'not private'}"
            except OSError:
                mode_text = "unavailable"
            return ConfigInspection("unsafe", mode_text, directory_mode, None, None, True)
        mode = stat.S_IMODE(info.st_mode)
        mode_text = f"{mode:04o} {'private' if mode == 0o600 else 'not private'}"
        try:
            digest = _parse_config(data)
        except DirectWssError:
            return ConfigInspection("invalid", mode_text, directory_mode, None, data, True)
        return ConfigInspection("valid", mode_text, directory_mode, digest, data, True)
    finally:
        os.close(config_fd)


def _status_service(home_fd: int, home: str) -> tuple[str, str, str]:
    enabled = "unknown"
    active = "unknown"
    policy = "unsupported"
    try:
        state = _service_preflight(home_fd, home)
        enabled = "yes" if state.enabled else "no"
        active = "yes" if state.active else "no"
        policy = "supported"
    except DirectWssError:
        try:
            shown = _systemctl("show", _UNIT, "--property=" + ",".join(_PROPERTIES))
        except DirectWssError:
            return enabled, active, policy
        if shown.returncode == 0:
            props = _property_map(shown.stdout)
            enabled_state = props.get("UnitFileState")
            active_state = props.get("ActiveState")
            if enabled_state in {"enabled", "disabled"}:
                enabled = "yes" if enabled_state == "enabled" else "no"
            if active_state in {"active", "inactive"}:
                active = "yes" if active_state == "active" else "no"
    return enabled, active, policy


def _status(stdout: IO[str]) -> None:
    _supported_platform()
    with _open_home() as (uid, home, home_fd):
        inspection = _status_config(home_fd, uid)
        enabled, active, policy = _status_service(home_fd, home)
        health = "healthy" if active == "yes" and _gateway_health() else "unavailable"
        stdout.write(f"gateway.env syntax: {inspection.syntax}\n")
        stdout.write(f"gateway.env mode: {inspection.mode}\n")
        stdout.write(f"gateway.env directory mode: {inspection.directory_mode}\n")
        stdout.write(f"gateway service enabled: {enabled}\n")
        stdout.write(f"gateway service active: {active}\n")
        stdout.write(f"gateway unit policy: {policy}\n")
        stdout.write(f"gateway health: {health}\n")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] not in {"setup", "rotate", "status"}:
        with suppress(BaseException):
            print("usage: imp-direct-wss {setup|rotate|status}", file=sys.stderr)
        return 2
    try:
        command = args[0]
        if command == "status":
            _status(sys.stdout)
        else:
            _mutate(command, sys.stdout)
        return 0
    except DirectWssError as error:
        message = f"error: {error}"
    except KeyboardInterrupt:
        return 130
    except Exception:
        message = "error: Direct WSS operation failed"
    with suppress(BaseException):
        print(message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
