#!/usr/bin/env python3
"""Exercise the managed structured SSH harness with native OpenSSH and a fixture relay."""

import argparse
import ctypes
import http.client
import json
import os
import runpy
import secrets
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


HOST = "127.0.0.1"


def openssh(tool):
    if os.name == "nt":
        return Path(os.environ["SystemRoot"]) / "System32" / "OpenSSH" / f"{tool}.exe"
    path = shutil.which(tool)
    if path is None:
        raise RuntimeError(f"OpenSSH {tool} not found")
    return Path(path)


def keygen(path):
    subprocess.run(
        [openssh("ssh-keygen"), "-q", "-t", "ed25519", "-N", "", "-f", path],
        check=True,
        timeout=10,
    )


@contextmanager
def retained_client(pid):
    # Hold the identity across harness shutdown; don't poll or signal a recycled PID.
    if os.name != "nt":
        fd = os.pidfd_open(pid)
        try:
            assert Path(f"/proc/{pid}/exe").samefile(openssh("ssh"))
            yield lambda: bool(select.select([fd], [], [], 0)[0])
        finally:
            os.close(fd)
        return

    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = wintypes.DWORD, wintypes.BOOL, wintypes.DWORD
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel.WaitForSingleObject.argtypes = wintypes.HANDLE, wintypes.DWORD
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(0x101000, False, pid)  # synchronize, query limited info
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        name = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(name))
        if not kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(length)):
            raise ctypes.WinError(ctypes.get_last_error())
        assert Path(name.value).samefile(openssh("ssh")), name.value
        yield lambda: kernel.WaitForSingleObject(handle, 0) == 0
    finally:
        kernel.CloseHandle(handle)


@contextmanager
def reserve_port():
    with socket.socket() as sock:
        if os.name == "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind((HOST, 0))
        yield sock.getsockname()[1]


def closed(port):
    try:
        with socket.create_connection((HOST, port), timeout=4 if os.name == "nt" else 0.2):
            return False
    except ConnectionRefusedError:
        return True


def healthy(port):
    connection = http.client.HTTPConnection(HOST, port, timeout=0.5)
    try:
        connection.request("GET", "/healthz")
        response = connection.getresponse()
        if response.status != 200:
            return False
        health = json.loads(response.read())
        return (
            isinstance(health, dict)
            and health.get("has_snapshot") is True
            and health.get("seq") == health.get("producer_count") == 1
            and health.get("feed") in ("live", "stale")
        )
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def wait_until(predicate, label, process=None):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise AssertionError(f"process exited before {label}: {process.returncode}")
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {label}")


def exercise(harness, root, username, server_port):
    work = root / "structured"
    work.mkdir()
    keygen(work / "wrong_key")
    host_key = (root / "host_key.pub").read_text().strip()
    wrong_key = (work / "wrong_key.pub").read_text().strip()
    env = {**os.environ, "SSH_AUTH_SOCK": "", "LC_ALL": "C"}
    assert openssh("ssh").is_file()
    if os.name == "nt":
        env["PATH"] = str(openssh("ssh").parent)

    def case(
        label, port_arg, config_port, pinned_key, *,
        error=None, action=False, legacy=False, abrupt=False,
    ):
        config = work / f"{label}.config"
        known_hosts = work / f"{label}.known-hosts"
        known_hosts.write_text(
            f"[{HOST}]:{server_port} {pinned_key}\n" if pinned_key else ""
        )
        original_hosts = known_hosts.read_bytes()
        identity = (
            work / "wrong_key" if label == "structured-authentication" else root / "client_key"
        )
        identity_path = identity.as_posix().replace("%", "%%")
        hosts_path = known_hosts.as_posix().replace("%", "%%")
        target = "fixture-alias" if legacy else HOST
        host_name = f" HostName {HOST}\n" if legacy else ""
        config.write_text(
            f"""Host {target}
{host_name} Port {config_port}
 User {username if legacy else "imp_fixture_wrong"}
 IdentityFile "{identity_path}"
 IdentityAgent none
 UserKnownHostsFile "{hosts_path}"
 GlobalKnownHostsFile none
 StrictHostKeyChecking {"yes" if legacy else "no"}
 NoHostAuthenticationForLocalhost {"no" if legacy else "yes"}
 UpdateHostKeys no
 HostKeyAlgorithms ssh-ed25519
"""
        )
        original_config = config.read_bytes()
        with reserve_port() as local_port:
            pass
        assert closed(local_port), f"native port {local_port} is already in use"
        record = work / f"{label}.pid"
        log = work / f"{label}.log"
        if legacy:
            args = [harness, "ssh", config, target]
        else:
            args = [harness, "ssh-structured", config, target, username, port_arg]
        with log.open("wb") as stderr:
            process = subprocess.Popen(
                [*args, str(local_port), record, work],
                cwd=work,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
            )
            try:
                wait_until(
                    lambda: record.exists() and record.stat().st_size,
                    f"{label} child PID",
                    process,
                )
                if error is None:
                    wait_until(
                        lambda: healthy(local_port),
                        f"{label} authenticated relay",
                        process,
                    )
                    with retained_client(int(record.read_text())) as exited:
                        assert not exited(), f"{label} OpenSSH exited before cleanup"
                        if action:
                            subprocess.run(
                                [
                                    sys.executable,
                                    Path(__file__).with_name("test-guided-relay.py"),
                                    "client",
                                    str(local_port),
                                ],
                                cwd=work,
                                env=env,
                                check=True,
                                timeout=35,
                            )
                        if abrupt:
                            process.kill()
                            process.wait(timeout=10)
                        else:
                            process.stdin.close()
                            assert process.wait(timeout=10) == 0
                        wait_until(exited, f"{label} owned SSH exit")
                else:
                    wait_until(
                        lambda: error in log.read_text(errors="replace"),
                        f"{label} OpenSSH refusal",
                        process,
                    )
                    assert closed(local_port), f"{label} opened a forwarding listener"
                    process.stdin.close()
                    assert process.wait(timeout=10) == 0
                wait_until(lambda: closed(local_port), f"{label} listener release")
            except BaseException:
                print(log.read_text(errors="replace"), file=sys.stderr)
                raise
            finally:
                process.stdin.close()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        assert config.read_bytes() == original_config, f"{label} changed SSH config"
        assert known_hosts.read_bytes() == original_hosts, f"{label} changed known_hosts"
        print(f"{sys.platform} {label}: PASS", flush=True)

    if os.name == "nt":
        case("trusted-legacy-alias", None, server_port, host_key, legacy=True)

    # With no explicit port, OpenSSH must honor the config. With one, it must
    # override a reserved non-listening port, never accidentally probe real SSH.
    case("structured-configured-port", "default", server_port, host_key, abrupt=True)
    with reserve_port() as unused_port:
        port = str(server_port)
        case(
            "structured-unknown-key", port, unused_port, None,
            error="you have requested strict checking",
        )
        case(
            "structured-changed-key", port, unused_port, wrong_key,
            error="REMOTE HOST IDENTIFICATION HAS CHANGED",
        )
        case(
            "structured-authentication", port, unused_port, host_key,
            error="Permission denied (publickey)",
        )
        case("structured-explicit-port", port, unused_port, host_key, action=True)


def container(harness):
    scripts = Path(__file__).resolve().parent
    # Reuse only the disposable image and sshd setup, not the experiment driver.
    support = runpy.run_path(str(scripts / "run-guided-forward-fixture.py"))
    docker = support["docker"]
    image = support["build_image"](scripts.parent / "services" / "relay")
    name = "imp-structured-" + secrets.token_hex(8)
    with tempfile.TemporaryDirectory(prefix="imp-structured-") as directory:
        root = Path(directory)
        keygen(root / "client_key")
        docker(
            "run", "--rm", "--init", "--detach", "--name", name,
            "--publish", f"{HOST}::2222", image, "sleep", "600",
        )
        try:
            authorized = 'restrict,port-forwarding,permitopen="127.0.0.1:8787" ' + (
                root / "client_key.pub"
            ).read_text()
            docker(
                "exec", "-i", name, "sh", "-ec", "cat >/run/imp-authorized",
                data=authorized.encode(),
            )
            docker(
                "exec", "-i", name, "sh", "-ec", "cat >/run/imp-sshd.conf",
                data=support["SERVER"].encode(),
            )
            docker(
                "exec", name, "ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                "-f", "/run/imp-host",
            )
            (root / "host_key.pub").write_text(
                docker("exec", name, "cat", "/run/imp-host.pub") + "\n"
            )
            docker(
                "cp", str(scripts / "test-guided-relay.py"), name + ":/run/imp-relay.py"
            )
            docker("exec", name, "/usr/sbin/sshd", "-f", "/run/imp-sshd.conf")
            port = support["published_port"](name, 2222)
            log = root / "relay.log"
            with log.open("wb") as output:
                backend = subprocess.Popen(
                    [
                        "docker", "exec", "-i", name,
                        "python", "-u", "/run/imp-relay.py", "backend",
                    ],
                    stdin=subprocess.PIPE,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                )
                try:
                    wait_until(
                        lambda: b"READY 8787\n" in log.read_bytes(),
                        "synthetic relay producer and consumer",
                        backend,
                    )
                    exercise(harness, root, "impfixture", port)
                    backend.communicate(b"stop\n", timeout=10)
                    assert backend.returncode == 0, "synthetic relay failed"
                    assert b"no replay to replacement consumer" in log.read_bytes()
                finally:
                    if backend.poll() is None:
                        backend.kill()
                        backend.communicate(timeout=10)
                    print(log.read_text(errors="replace"), end="", flush=True)
        finally:
            docker("rm", "-f", name)


def main():
    if not __debug__:
        raise RuntimeError("run the SSH acceptance with Python assertions enabled")
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    modes.add_parser("container").add_argument("harness", type=Path)
    client = modes.add_parser("exercise")
    client.add_argument("harness", type=Path)
    client.add_argument("root", type=Path)
    client.add_argument("username")
    client.add_argument("server_port", type=int)
    args = parser.parse_args()
    harness = args.harness.resolve(strict=True)
    if args.mode == "container":
        container(harness)
    else:
        exercise(
            harness, args.root.resolve(strict=True), args.username, args.server_port
        )


if __name__ == "__main__":
    main()
