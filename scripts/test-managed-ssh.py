#!/usr/bin/env python3
"""Check owned foreground SSH using disposable keys, sshd, and the frozen node."""

import ctypes
import os
import pathlib
import pwd
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


def run(args, **kwargs):
    return subprocess.run(
        args, check=True, timeout=5, capture_output=True, text=True, **kwargs
    )


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    if sys.platform != "linux" or not hasattr(os, "pidfd_open"):
        raise RuntimeError("this authenticated fixture requires Linux pidfds")
    if len(sys.argv) != 2:
        raise RuntimeError(
            "usage: test-managed-ssh.py /path/to/runtime-ownership-harness"
        )
    harness = pathlib.Path(sys.argv[1]).resolve()
    node = (
        pathlib.Path(__file__).resolve().parents[1]
        / "apps/desktop/src-tauri/binaries/imp-node-x86_64-unknown-linux-gnu"
    )
    sshd = pathlib.Path("/usr/sbin/sshd")
    for executable in (harness, node, sshd):
        if not executable.is_file():
            raise RuntimeError(f"required fixture executable not found: {executable}")
    with socket.socket() as check:
        check.bind(("127.0.0.1", 8787))
    # Adopt only descendants of this disposable observer, including daemonized masters.
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "PR_SET_CHILD_SUBREAPER")

    handles = {}
    processes = []
    with tempfile.TemporaryDirectory(prefix="imp-managed-ssh-") as directory:
        root = pathlib.Path(directory)
        log = (root / "fixture.log").open("ab")
        home = root / "home"
        (home / ".ssh").mkdir(parents=True)
        config = home / ".ssh/config"
        env = {
            **os.environ,
            "HOME": str(home),
            "USERPROFILE": str(home),
            "SSH_AUTH_SOCK": "",
        }

        def alive(pid):
            return pid in handles and not select.select([handles[pid]], [], [], 0)[0]

        def children(parent):
            found = set()
            try:
                pids = (
                    pathlib.Path(f"/proc/{parent}/task/{parent}/children")
                    .read_text()
                    .split()
                )
            except FileNotFoundError:
                return found
            for value in pids:
                pid = int(value)
                if not alive(pid):
                    try:
                        fd = os.pidfd_open(pid)
                        try:
                            status = (
                                pathlib.Path(f"/proc/{pid}/status")
                                .read_text()
                                .splitlines()
                            )
                            ppid = next(
                                int(line.split()[1])
                                for line in status
                                if line.startswith("PPid:")
                            )
                            if ppid != parent or (
                                parent != os.getpid() and not alive(parent)
                            ):
                                os.close(fd)
                                continue
                        except BaseException:
                            os.close(fd)
                            raise
                    except (ProcessLookupError, FileNotFoundError):
                        continue
                    old = handles.pop(pid, None)
                    if old is not None:
                        os.close(old)
                    handles[pid] = fd
                found.add(pid)
                if alive(pid):
                    found.update(children(pid))
            return found

        def capture():
            return children(os.getpid())

        def wait_until(predicate, description, timeout=10):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                capture()
                result = predicate()
                if result:
                    return result
                time.sleep(0.02)
            raise AssertionError(f"timed out waiting for {description}")

        def spawn(args, **kwargs):
            process = subprocess.Popen(
                args, stdout=subprocess.DEVNULL, stderr=log, **kwargs
            )
            processes.append(process)
            capture()
            if process.pid not in handles:
                raise RuntimeError(
                    "fixture process exited before identity registration"
                )
            return process

        def health(port):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/healthz", timeout=1
                ) as response:
                    return response.status == 200 and b'"feed"' in response.read()
            except OSError:
                return False

        def listener_closed(port):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    return False
            except OSError:
                return True

        def effective(alias, path=config, owned=True):
            args = ["ssh", "-G", "-N", "-F", str(path)]
            if owned:
                args += ["-S", "none", "-o", "ForkAfterAuthentication=no"]
            args.append(alias)
            result = run(args, cwd=home, env=env)
            return dict(
                line.split(None, 1)
                for line in result.stdout.splitlines()
                if " " in line
            )

        def master_pid(path, alias):
            result = subprocess.run(
                [
                    "ssh",
                    "-F",
                    str(config),
                    "-S",
                    str(path),
                    "-o",
                    "ForkAfterAuthentication=no",
                    "-O",
                    "check",
                    alias,
                ],
                cwd=home,
                env=env,
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if result.returncode:
                return None
            text = result.stderr + result.stdout
            return int(text.split("pid=", 1)[1].split(")", 1)[0])

        try:
            host_key = root / "host_key"
            client_key = root / "client_key"
            for key in (host_key, client_key):
                run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)])
            (root / "authorized_keys").write_text((root / "client_key.pub").read_text())
            server_port = free_port()
            known_hosts = root / "known_hosts"
            known_hosts.write_text(
                f"[127.0.0.1]:{server_port} " + (root / "host_key.pub").read_text()
            )
            marker = root / "local-command-complete"
            config.write_text(
                f"""Host fixture-jump
 ControlPath none
 ForkAfterAuthentication no

Host fixture fixture-jump via-jump via-command
 HostName 127.0.0.1
 Port {server_port}
 User {pwd.getpwuid(os.getuid()).pw_name}
 IdentityFile {client_key}
 IdentitiesOnly yes
 UserKnownHostsFile {known_hosts}
 StrictHostKeyChecking yes
 ControlMaster auto
 ControlPersist yes
 ControlPath {root}/control-%n
 ForkAfterAuthentication yes

Host fixture via-jump via-command
 PermitLocalCommand yes
 LocalCommand dd if=/dev/zero bs=1048576 count=1 2>/dev/null; printf complete > {marker}

Host via-jump
 ProxyJump fixture-jump

Host via-command
 ProxyCommand ssh -F {config} -S none -o ForkAfterAuthentication=no -W %h:%p fixture-jump
"""
            )
            initial_config = config.read_bytes()
            sshd_config = root / "sshd_config"
            sshd_config.write_text(
                f"""Port {server_port}
ListenAddress 127.0.0.1
HostKey {host_key}
PidFile {root}/sshd.pid
AuthorizedKeysFile {root}/authorized_keys
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
PermitRootLogin prohibit-password
UsePAM no
AllowUsers {pwd.getpwuid(os.getuid()).pw_name}
StrictModes no
"""
            )
            run([str(sshd), "-t", "-f", str(sshd_config)])
            spawn([str(sshd), "-D", "-e", "-f", str(sshd_config)])
            backend = spawn([str(node), "--host", "127.0.0.1", "--port", "8787"])
            wait_until(lambda: health(8787), "frozen node backend")

            for master in ("auto", "yes"):
                path = root / f"master-{master}.config"
                path.write_text(
                    config.read_text().replace(
                        "ControlMaster auto", f"ControlMaster {master}"
                    )
                )
                settings = effective("fixture", path)
                expected = "auto" if master == "auto" else "true"
                assert (
                    settings["controlmaster"] == expected
                    and settings["controlpersist"] == "yes"
                )
                assert (
                    settings["forkafterauthentication"] == "no"
                    and "controlpath" not in settings
                )
                assert settings["stricthostkeychecking"] == "true"
                assert settings["userknownhostsfile"] == str(known_hosts)
                assert settings["hostname"] == "127.0.0.1" and settings["port"] == str(
                    server_port
                )
            jump_settings = effective("fixture-jump", owned=False)
            assert (
                "controlpath" not in jump_settings
                and jump_settings["forkafterauthentication"] == "no"
            )
            print(
                "effective config: sharing disabled, foreground, host verification/custom port preserved",
                flush=True,
            )

            def managed_case(alias, port, label, abrupt, external=None):
                marker.unlink(missing_ok=True)
                record = root / f"{label}.pid"
                process = spawn(
                    [
                        str(harness),
                        "ssh",
                        str(config),
                        alias,
                        str(port),
                        str(record),
                        str(home),
                    ],
                    stdin=subprocess.PIPE,
                )
                wait_until(record.exists, f"{label} child registration")
                child = int(record.read_text())
                wait_until(
                    lambda: child in handles and marker.exists(),
                    f"{label} 1 MiB LocalCommand completion",
                )
                wait_until(
                    lambda: health(port), f"{label} usable authenticated forward"
                )
                assert alive(child), "owned target client detached"
                tree = children(process.pid)
                if alias in ("via-command", "via-jump"):
                    assert children(child), (
                        "authenticated proxy was not present in the owned tree"
                    )
                if external:
                    path, pid, before, external_port = external
                    assert (
                        master_pid(path, "fixture") == pid
                        and alive(pid)
                        and health(external_port)
                    )
                    after = path.stat()
                    assert (before.st_ino, before.st_mtime_ns) == (
                        after.st_ino,
                        after.st_mtime_ns,
                    )
                else:
                    assert not list(root.glob("control-*")), (
                        "owned SSH created a multiplex socket"
                    )
                if abrupt:
                    signal.pidfd_send_signal(handles[process.pid], signal.SIGKILL)
                else:
                    process.stdin.close()
                process.wait(timeout=10)
                wait_until(
                    lambda: all(not alive(pid) for pid in tree),
                    f"{label} root/proxy/guardian cleanup",
                )
                wait_until(lambda: listener_closed(port), f"{label} listener release")
                assert alive(backend.pid) and health(8787), (
                    "external backend was affected"
                )
                if external:
                    assert alive(external[1]) and health(external[3]), (
                        "external master/forward was affected"
                    )
                else:
                    assert not list(root.glob("control-*")), (
                        "detached multiplex master socket survived"
                    )
                print(
                    f"{label}: root={child} owned_tree={sorted(tree)} forward_healthy=true cleanup=true",
                    flush=True,
                )

            for alias in ("fixture", "via-command", "via-jump"):
                port = free_port()
                for label, abrupt in (
                    ("normal", False),
                    ("hard", True),
                    ("relaunch", False),
                ):
                    managed_case(alias, port, f"{alias}-{label}", abrupt)

            external_port = free_port()
            external_path = pathlib.Path(
                effective("fixture", owned=False)["controlpath"]
            )
            spawn(
                [
                    "ssh",
                    "-F",
                    str(config),
                    "-o",
                    "PermitLocalCommand=no",
                    "-o",
                    "ForkAfterAuthentication=no",
                    "-M",
                    "-N",
                    "-T",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ExitOnForwardFailure=yes",
                    "-L",
                    f"127.0.0.1:{external_port}:127.0.0.1:8787",
                    "--",
                    "fixture",
                ],
                cwd=home,
                env=env,
            )
            wait_until(external_path.exists, "pre-existing external master socket")
            external_pid = wait_until(
                lambda: master_pid(external_path, "fixture"), "external master identity"
            )
            capture()
            assert external_pid in handles and alive(external_pid), (
                "external master is not an exact owned fixture descendant"
            )
            wait_until(lambda: health(external_port), "external master forward")
            external = (
                external_path,
                external_pid,
                external_path.stat(),
                external_port,
            )
            for abrupt in (False, True):
                managed_case(
                    "fixture",
                    free_port(),
                    f"beside-external-{'hard' if abrupt else 'normal'}",
                    abrupt,
                    external,
                )
            assert config.read_bytes() == initial_config, (
                "managed lifecycle modified SSH configuration"
            )
            print(
                f"external master: pid={external_pid} socket/config unchanged; forward survives both lifecycles",
                flush=True,
            )
        finally:
            failure = sys.exception()
            errors = [failure] if failure else []
            try:
                capture()
            except (OSError, ValueError, StopIteration) as error:
                errors.append(error)
            for fd in handles.values():
                try:
                    signal.pidfd_send_signal(fd, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError as error:
                    errors.append(error)
            for process in processes:
                try:
                    if process.stdin and not process.stdin.closed:
                        process.stdin.close()
                    process.wait(timeout=10)
                except (OSError, subprocess.SubprocessError) as error:
                    errors.append(error)
            for fd in handles.values():
                try:
                    assert select.select([fd], [], [], 10)[0], (
                        "fixture cleanup left a live process"
                    )
                    try:
                        os.waitid(os.P_PIDFD, fd, os.WEXITED | os.WNOHANG)
                    except ChildProcessError:
                        pass
                except (OSError, ValueError, AssertionError) as error:
                    errors.append(error)
                finally:
                    try:
                        os.close(fd)
                    except OSError as error:
                        errors.append(error)
            log.close()
            if errors:
                print(
                    (root / "fixture.log").read_text(errors="replace"), file=sys.stderr
                )
                raise BaseExceptionGroup(
                    "authenticated SSH fixture failure (after exact cleanup)", errors
                )
            print(
                "fixture cleanup: all exact pidfds exited/reaped; no PID/name/port scavenging",
                flush=True,
            )
    print("managed SSH authenticated fixture: PASS")


if __name__ == "__main__":
    main()
