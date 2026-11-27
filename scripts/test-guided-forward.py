#!/usr/bin/env python3
"""Compare isolated real OpenSSH -L and guided -W; use native Python for Win32.

The fixture requires disposable sshd, an authorized synthetic user/key, a second
key that may authenticate but not forward to 127.0.0.1:8787, the sshd public key,
and this script's relay mode in sshd's network namespace. Publish fixture ports
to client loopback only. On Windows use native Python, OpenSSH and the .exe
harness; WSL interop may launch Python but both ports must reach the fixture
from Windows loopback. No installed Imp or personal SSH files are used.
"""

import argparse
import contextlib
import errno
import hashlib
import json
import math
import os
import pathlib
import re
import shutil
import socket
import socketserver
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor


HOST = "127.0.0.1"
RELAY_PORT = 8787
TIMEOUT = 8
ACTION_COUNT = 20
UPLOAD = b"fixture upload!\n" * 65536
DOWNLOAD = b"fixture response" * 65536
WINDOWS = sys.platform == "win32"


def receive(sock, length):
    data = bytearray()
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise AssertionError(f"unexpected EOF after {len(data)} of {length} bytes")
        data.extend(chunk)
    return data


def connect(port, *, small_window=False):
    sock = socket.socket()
    try:
        sock.settimeout(TIMEOUT)
        if small_window:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        sock.connect((HOST, port))
        return sock
    except BaseException:
        sock.close()
        raise


class RelayHandler(socketserver.BaseRequestHandler):
    def handle(self):
        sock = self.request
        sock.settimeout(TIMEOUT * 2)
        with self.server.lock:
            self.server.active += 1
            self.server.accepted += 1
        try:
            command = sock.recv(1)
            if command == b"E":
                sock.settimeout(None)
                while data := sock.recv(65536):
                    sock.sendall(data)
            elif command in (b"H", b"U"):
                if command == b"U":
                    self.server.upload_resumed.clear()
                    sock.sendall(b"R")
                    assert self.server.upload_resumed.wait(TIMEOUT * 2)
                digest = hashlib.sha256()
                while data := sock.recv(65536):
                    digest.update(data)
                sock.sendall(digest.digest())
            elif command == b"F":
                sock.sendall(b"remote FIN\n")
                sock.shutdown(socket.SHUT_WR)
                with self.server.lock:
                    self.server.remote_fin += 1
                digest = hashlib.sha256(receive(sock, len(UPLOAD))).hexdigest()
                with self.server.lock:
                    self.server.remote_digest = digest
            elif command == b"B":
                with ThreadPoolExecutor(max_workers=1) as executor:
                    sender = executor.submit(sock.sendall, DOWNLOAD)
                    digest = hashlib.sha256(receive(sock, len(UPLOAD))).digest()
                    sender.result()
                sock.sendall(digest)
            elif command == b"D":
                with self.server.lock:
                    self.server.stall_started += 1
                for _ in range(32):
                    sock.sendall(DOWNLOAD)
                with self.server.lock:
                    self.server.stall_finished += 1
        except (OSError, AssertionError):
            # The cancellation and failed-handshake cases deliberately cut streams.
            pass
        finally:
            with self.server.lock:
                self.server.active -= 1


class Relay(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, port):
        super().__init__((HOST, port), RelayHandler)
        self.lock = threading.Lock()
        self.upload_resumed = threading.Event()
        self.active = self.accepted = self.stall_started = self.stall_finished = 0
        self.remote_fin = 0
        self.remote_digest = None

    def snapshot(self):
        with self.lock:
            return {
                "active": self.active,
                "accepted": self.accepted,
                "stall_started": self.stall_started,
                "stall_finished": self.stall_finished,
                "remote_fin": self.remote_fin,
                "remote_digest": self.remote_digest,
            }


@contextlib.contextmanager
def relay_listener(port):
    with Relay(port) as relay:
        thread = threading.Thread(target=relay.serve_forever, args=(0.02,), daemon=True)
        thread.start()
        try:
            yield relay
        finally:
            relay.shutdown()
            thread.join()


def serve_relay(port):
    # This control socket lives only in the disposable container; publish it on client loopback.
    with (
        socket.create_server(("0.0.0.0", port)) as control,
        contextlib.ExitStack() as data,
    ):
        relay = data.enter_context(relay_listener(RELAY_PORT))
        enabled = True
        while True:
            client, _ = control.accept()
            with client:
                client.settimeout(2)
                try:
                    with client.makefile("rb") as request:
                        command = request.readline(16).strip()
                    if command == b"down" and enabled:
                        data.close()
                        enabled = False
                    elif command == b"up" and not enabled:
                        relay = data.enter_context(relay_listener(RELAY_PORT))
                        enabled = True
                    elif command == b"resume-upload":
                        relay.upload_resumed.set()
                    if command in (b"up", b"down", b"stats", b"resume-upload"):
                        client.sendall(json.dumps(relay.snapshot()).encode() + b"\n")
                except OSError:
                    pass


def relay_control(port, command="stats"):
    with connect(port) as client, client.makefile("rb") as response:
        client.sendall(command.encode() + b"\n")
        return json.loads(response.readline(1024))


def wait_until(predicate, description, timeout=TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {description}")


def free_port():
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def listener_closed(port):
    try:
        with connect(port):
            return False
    except ConnectionRefusedError:
        return True


def can_bind(port, reuse):
    with socket.socket() as sock:
        if reuse:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((HOST, port))
        except OSError as exc:
            if exc.errno in (errno.EADDRINUSE, errno.EACCES) or getattr(
                exc, "winerror", None
            ) in (10013, 10048):
                return False
            raise
        return True


def assert_exclusive(port, phase):
    with ThreadPoolExecutor(max_workers=2) as executor:
        plain = executor.submit(can_bind, port, False)
        reusable = executor.submit(can_bind, port, True)
        assert not plain.result() and not reusable.result(), (
            f"{phase}: competing socket bound"
        )


def assert_released(port, phase):
    assert listener_closed(port), f"{phase}: guided listener still accepting"
    wait_until(lambda: can_bind(port, True), f"{phase}: port to become bindable again")


class FixtureProcess:
    def __init__(self, root, label, command, env, stack):
        self.out = root / (label + ".out")
        self.err = root / (label + ".err")
        self.start = time.perf_counter()
        with self.out.open("wb") as out, self.err.open("wb") as err:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=out,
                stderr=err,
                cwd=root,
                env=env,
            )
        stack.callback(self.cleanup)

    def cleanup(self):
        with contextlib.suppress(BrokenPipeError):
            self.process.stdin.close()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)

    def ready(self, probe):
        def check():
            if self.process.poll() is not None:
                raise AssertionError(f"{self.out.stem} exited: {self.diagnostics()}")
            return probe()

        try:
            result = wait_until(check, self.out.stem + " readiness")
        except AssertionError as exc:
            raise AssertionError(f"{exc}: {self.diagnostics()}") from exc
        return result, (time.perf_counter() - self.start) * 1000

    def diagnostics(self):
        return self.err.read_text(errors="replace").strip()[-2000:]

    def guided_ready(self):
        def probe():
            data = self.out.read_bytes()
            if b"\n" not in data:
                return None
            match = re.fullmatch(rb"READY ([1-9][0-9]{0,4})", data.splitlines()[0])
            assert match and int(match[1]) <= 65535, data
            return int(match[1])

        return self.ready(probe)

    def stop_guided(self, command):
        if command:
            self.process.stdin.write(b"stop\n")
            self.process.stdin.flush()
        else:
            self.process.stdin.close()
        assert self.process.wait(timeout=TIMEOUT) == 0, self.diagnostics()


def process_table():
    if WINDOWS:
        powershell = pathlib.Path(os.environ["SystemRoot"]) / (
            "System32/WindowsPowerShell/v1.0/powershell.exe"
        )
        data = subprocess.check_output(
            [
                str(powershell),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); "
                "$p = Get-CimInstance Win32_Process -ErrorAction Stop | "
                "Select-Object ProcessId,ParentProcessId,ThreadCount,WorkingSetSize,Name,CreationDate; "
                "ConvertTo-Json -InputObject @($p) -Compress",
            ],
            timeout=TIMEOUT,
        )
        return {
            row["ProcessId"]: (
                row["ParentProcessId"],
                row["ThreadCount"] or 0,
                int(row["WorkingSetSize"] or 0) // 1024,
                row["Name"],
                row["CreationDate"],
            )
            for row in json.loads(data)
        }
    page_kib = os.sysconf("SC_PAGE_SIZE") // 1024
    table = {}
    for path in pathlib.Path("/proc").glob("[0-9]*/stat"):
        try:
            stat = path.read_text(errors="replace")
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        pid_name, fields = stat.rsplit(")", 1)
        pid, name = pid_name.split(" (", 1)
        fields = fields.split()
        if fields[0] != "Z":
            table[int(pid)] = (
                int(fields[1]),
                int(fields[17]),
                int(fields[21]) * page_kib,
                name,
                fields[19],
            )
    return table


def sample(process, label, min_ssh=1):
    table = process_table()
    root = process.process.pid
    assert root in table, f"{label}: process exited before resource sample"
    visited = {root}
    while (
        children := {pid for pid, row in table.items() if row[0] in visited} - visited
    ):
        visited.update(children)
    rows = [table[pid] for pid in visited]
    metrics = {
        "processes": len(rows),
        "threads": sum(row[1] for row in rows),
        "resident_kib": sum(row[2] for row in rows),
        "ssh_processes": sum(row[3].lower() in ("ssh", "ssh.exe") for row in rows),
    }
    print(f"{label}: {json.dumps(metrics)}")
    assert metrics["ssh_processes"] >= min_ssh, (label, metrics)
    return {pid: table[pid][4] for pid in visited}


def wait_for_exit(identities):
    def exited():
        table = process_table()
        return all(
            pid not in table or table[pid][4] != start
            for pid, start in identities.items()
        )

    wait_until(exited, f"sampled owned processes to exit ({sorted(identities)})")


def echo(sock, data):
    sock.sendall(data)
    assert receive(sock, len(data)) == data


def open_echo(port):
    sock = connect(port)
    try:
        sock.sendall(b"E")
        echo(sock, b"fixture open\n")
        return sock
    except BaseException:
        sock.close()
        raise


def action(port, data):
    start = time.perf_counter()
    with connect(port) as sock:
        sock.sendall(b"E" + data)
        assert receive(sock, len(data)) == data
    return (time.perf_counter() - start) * 1000


def workload(label, owner, port, setup, stats, concurrent_ssh):
    baseline = stats()["accepted"]
    first = action(port, b"first response\n")
    with open_echo(port) as state:
        sample(owner, f"{label} retained state")
        durations = []
        for index in range(ACTION_COUNT):
            durations.append(action(port, f"action {index}\n".encode()))
            echo(state, f"state {index}\n".encode())
        with open_echo(port):
            sample(owner, f"{label} retained state + action", min_ssh=concurrent_ssh)
    assert stats()["accepted"] - baseline == ACTION_COUNT + 3, label
    p95 = sorted(durations)[math.ceil(0.95 * len(durations)) - 1]
    print(
        f"{label}: local_ready_ms={setup:.1f}, first_response_ms={first:.1f}, "
        f"reconnect_samples={len(durations)}, p50_ms={statistics.median(durations):.1f}, "
        f"p95_ms={p95:.1f}, relay_streams={ACTION_COUNT + 3}"
    )


def expect_stream_failure(port):
    with connect(port) as sock:
        try:
            sock.sendall(b"Eshould not reach relay\n")
            assert sock.recv(1) == b"", "rejected stream delivered data"
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


def remote_first(label, port, stats, *, candidate=False):
    before = stats()["remote_fin"]
    payload = (b"W" if candidate else b"L") + UPLOAD[1:]
    expected = hashlib.sha256(payload).hexdigest()
    with connect(port) as sock:
        sock.sendall(b"F")
        sentinel = b"remote FIN\n"
        assert receive(sock, len(sentinel)) == sentinel
        wait_until(lambda: stats()["remote_fin"] == before + 1, f"{label}: relay FIN")
        try:
            sock.settimeout(2)
            assert sock.recv(1) == b"", "received data after the relay's FIN"
            sock.settimeout(TIMEOUT)
            sock.sendall(payload)
            sock.shutdown(socket.SHUT_WR)
            wait_until(
                lambda: stats()["remote_digest"] == expected,
                f"{label}: relay to receive the post-FIN upload",
                timeout=2,
            )
        except (TimeoutError, ConnectionError, AssertionError) as exc:
            if not candidate:
                raise
            print(
                f"CANDIDATE LIMIT: {label} remote-first half-close not observed ({exc})"
            )
            return False
    print(f"{label}: remote-first half-close verified by relay digest")
    return True


def client_config(root, label, key, port, trust):
    known_hosts = root / (label + ".known_hosts")
    known_hosts.write_text(trust, encoding="ascii")
    config = root / (label + ".config")
    config.write_text(
        f"Host {HOST}\n"
        f"  Port {port}\n"
        f'  IdentityFile "{key.as_posix().replace("%", "%%")}"\n'
        f'  UserKnownHostsFile "{known_hosts.as_posix().replace("%", "%%")}"\n'
        "  GlobalKnownHostsFile none\n"
        "  StrictHostKeyChecking yes\n"
        "  VerifyHostKeyDNS no\n"
        "  UpdateHostKeys no\n"
        "  IdentitiesOnly yes\n"
        "  IdentityAgent none\n"
        "  BatchMode yes\n"
        "  PreferredAuthentications publickey\n"
        "  ConnectTimeout 4\n"
        "  ConnectionAttempts 1\n"
        "  ControlMaster no\n"
        "  ControlPath none\n",
        encoding="utf-8",
    )
    return config


def acceptance(args):
    if WINDOWS:
        ssh_dir = pathlib.Path(os.environ["SystemRoot"]) / "System32/OpenSSH"
        ssh = str(ssh_dir / "ssh.exe")
        keygen = str(ssh_dir / "ssh-keygen.exe")
        assert pathlib.Path(ssh).is_file() and pathlib.Path(keygen).is_file(), (
            "native OpenSSH required"
        )
    else:
        ssh = shutil.which("ssh")
        keygen = shutil.which("ssh-keygen")
        assert ssh and keygen, "OpenSSH client and ssh-keygen required"
        ssh_dir = pathlib.Path(ssh).parent

    control_port = args.control_port

    def stats():
        return relay_control(control_port)

    with tempfile.TemporaryDirectory(prefix="imp-guided-forward-") as temporary:
        with contextlib.ExitStack() as stack:
            root = pathlib.Path(temporary)
            env = dict(os.environ, HOME=str(root), USERPROFILE=str(root), LC_ALL="C")
            env["PATH"] = str(ssh_dir) + os.pathsep + env["PATH"]
            env.pop("SSH_AUTH_SOCK", None)
            env.pop("SSH_ASKPASS", None)
            env.pop("SSH_ASKPASS_REQUIRE", None)
            env.pop("SSH_SK_PROVIDER", None)

            def trust(public):
                algorithm, blob = public.read_text(encoding="ascii").split()[:2]
                assert algorithm == "ssh-ed25519", "fixture requires ed25519 host key"
                host = HOST if args.ssh_port == 22 else f"[{HOST}]:{args.ssh_port}"
                return f"{host} {algorithm} {blob}\n"

            # Use the generated key in place; copying drops its private Windows ACL.
            identity = args.identity.resolve(strict=True)
            known = trust(args.host_key)
            good = client_config(root, "known", identity, args.ssh_port, known)

            def spawn(label, command):
                return FixtureProcess(root, label, command, env, stack)

            def guided(label, config=good, local_port=0, ssh_port="default"):
                return spawn(
                    label,
                    [
                        str(args.harness.resolve()),
                        "guided-forward",
                        args.ssh_host,
                        args.ssh_user,
                        str(ssh_port),
                        str(local_port),
                        str(config),
                    ],
                )

            def ssh_base(config):
                return [ssh, "-F", str(config), "-T", "-l", args.ssh_user]

            def launch_warm():
                port = free_port()
                owner = spawn(
                    "baseline",
                    ssh_base(good)
                    + [
                        "-v",
                        "-o",
                        "ExitOnForwardFailure=yes",
                        "-N",
                        "-L",
                        f"{HOST}:{port}:{HOST}:{RELAY_PORT}",
                        "--",
                        args.ssh_host,
                    ],
                )
                _, setup = owner.ready(
                    lambda: (
                        f"Local forwarding listening on {HOST} port {port}."
                        in owner.diagnostics()
                    )
                )
                return owner, port, setup

            if args.protocol_only:
                if args.protocol_only == "warm":
                    owner, port, _ = launch_warm()
                    print("Actual relay protocol via warm -L", flush=True)
                else:
                    owner = guided("protocol")
                    port, _ = owner.guided_ready()
                    print("Actual relay protocol via guided -W", flush=True)
                client = (
                    pathlib.Path(__file__).resolve().with_name("test-guided-relay.py")
                )
                subprocess.run(
                    [sys.executable, str(client), "client", str(port)],
                    check=True,
                    timeout=45,
                    cwd=root,
                    env=env,
                )
                if args.protocol_only == "warm":
                    owner.cleanup()
                else:
                    owner.stop_guided(command=True)
                assert listener_closed(port)
                return

            denied = args.denied_identity.resolve(strict=True)
            untrusted = root / "untrusted"
            subprocess.run(
                [keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(untrusted)],
                check=True,
                timeout=TIMEOUT,
                stdin=subprocess.DEVNULL,
                cwd=root,
                env=env,
            )
            negative_cases = [
                ("unknown-host", identity, "", "No ED25519 host key is known"),
                (
                    "changed-host",
                    identity,
                    trust(untrusted.with_suffix(".pub")),
                    "REMOTE HOST IDENTIFICATION HAS CHANGED",
                ),
                ("authentication-denied", untrusted, known, "Permission denied"),
                ("forwarding-denied", denied, known, "administratively prohibited"),
            ]

            def assert_rejected(label, config, port, reason, ssh_port="default"):
                # The bridge drains stderr; independently confirm OpenSSH's precise rejection.
                before = stats()["accepted"]
                port_option = [] if ssh_port == "default" else ["-p", str(ssh_port)]
                direct = subprocess.run(
                    ssh_base(config)
                    + port_option
                    + ["-W", f"{HOST}:{RELAY_PORT}", "--", args.ssh_host],
                    input=b"Efixture rejection probe\n",
                    capture_output=True,
                    timeout=TIMEOUT,
                    cwd=root,
                    env=env,
                )
                assert direct.returncode != 0 and reason.encode() in direct.stderr, (
                    label,
                    direct.returncode,
                    direct.stderr,
                )
                assert not direct.stdout, (label, direct.stdout)
                expect_stream_failure(port)
                assert stats()["accepted"] == before, label
                print(f"{label}: channel rejected; direct OpenSSH confirmed {reason!r}")

            assert relay_control(control_port, "up")["active"] == 0, (
                "fixture relay already in use"
            )
            stack.callback(relay_control, control_port, "up")
            print(
                "Latency: -L readiness includes authentication; -W authenticates per stream."
            )
            print(
                "Resources: sampled local process tree only; resident KiB sums RSS/working sets."
            )
            baseline, baseline_port, setup = launch_warm()
            workload("warm -L", baseline, baseline_port, setup, stats, concurrent_ssh=1)
            remote_first("warm -L", baseline_port, stats)
            sibling = stack.enter_context(open_echo(baseline_port))

            candidate = guided("candidate")
            port, setup = candidate.guided_ready()
            workload("guided -W", candidate, port, setup, stats, concurrent_ssh=2)
            assert_exclusive(port, "healthy guided listener")
            remote_half_close = remote_first("guided -W", port, stats, candidate=True)
            state = stack.enter_context(open_echo(port))
            with connect(port) as half:
                half.sendall(b"H" + UPLOAD)
                half.shutdown(socket.SHUT_WR)
                assert receive(half, 32) == hashlib.sha256(UPLOAD).digest()
                assert half.recv(1) == b""  # Relay responded only after observing EOF.
            with connect(port) as duplex, ThreadPoolExecutor(max_workers=1) as executor:
                duplex.sendall(b"B")
                sender = executor.submit(duplex.sendall, UPLOAD)
                assert receive(duplex, len(DOWNLOAD)) == DOWNLOAD
                sender.result()
                assert receive(duplex, 32) == hashlib.sha256(UPLOAD).digest()

            # Bound a continuously sending client against a relay deliberately not reading.
            def upload(sock):
                for _ in range(16):
                    sock.sendall(UPLOAD)
                sock.shutdown(socket.SHUT_WR)

            with (
                connect(port) as blocked,
                ThreadPoolExecutor(max_workers=1) as executor,
            ):
                blocked.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
                blocked.sendall(b"U")
                assert receive(blocked, 1) == b"R"
                sender = executor.submit(upload, blocked)
                try:
                    action(port, b"action while relay consumer is stalled\n")
                    echo(state, b"retained state while relay consumer is stalled\n")
                    assert not sender.done(), (
                        "fixture upload did not create backpressure"
                    )
                finally:
                    relay_control(control_port, "resume-upload")
                sender.result(timeout=TIMEOUT)
                digest = hashlib.sha256()
                for _ in range(16):
                    digest.update(UPLOAD)
                assert receive(blocked, 32) == digest.digest()

            stalled_before = stats()
            stalled = stack.enter_context(connect(port, small_window=True))
            stalled.sendall(b"D")
            wait_until(
                lambda: stats()["stall_started"] > stalled_before["stall_started"],
                "relay to start writing to the stalled consumer",
            )
            action(port, b"action while consumer is stalled\n")
            echo(state, b"retained state while consumer is stalled\n")
            assert stats()["stall_finished"] == stalled_before["stall_finished"], (
                "fixture download did not create backpressure"
            )

            unrelated = stack.enter_context(relay_listener(0))
            occupied = unrelated.server_address[1]
            survivor = stack.enter_context(open_echo(occupied))
            contention = guided(
                "contention", local_port=occupied, ssh_port=args.ssh_port
            )
            contention.process.stdin.close()
            assert contention.process.wait(timeout=TIMEOUT) != 0, (
                "occupied port accepted"
            )
            assert b"READY" not in contention.out.read_bytes(), (
                "occupied port advertised readiness"
            )
            echo(survivor, b"unrelated compatible listener survived contention\n")
            action(occupied, b"unrelated still owns new connections\n")

            assert stats()["active"] >= 3, (
                "stalled relay stream ended before cancellation"
            )
            identities = sample(
                candidate,
                "guided teardown with live state and stalled stream",
                min_ssh=2,
            )
            candidate.stop_guided(command=True)
            assert listener_closed(port)
            with contextlib.suppress(ConnectionResetError, ConnectionAbortedError):
                assert state.recv(1) == b"", "state stream survived cancellation"
            state.close()
            stalled.close()
            assert_released(port, "stop command")
            wait_until(
                lambda: stats()["active"] == 1,
                "only the unrelated warm -L channel to remain",
            )
            wait_for_exit(identities)
            echo(sibling, b"unrelated SSH survived guided cancellation\n")
            echo(survivor, b"unrelated local stream survived guided cancellation\n")
            print(
                "duplex, client-first half-close, backpressure, contention and stop cancellation: PASS"
            )

            for label, key, trust_line, reason in negative_cases:
                config = client_config(root, label, key, args.ssh_port, trust_line)
                rejected = guided(label, config)
                rejected_port, _ = rejected.guided_ready()
                assert_rejected(label, config, rejected_port, reason)
                rejected.stop_guided(command=False)
                assert listener_closed(rejected_port), label

            # Removing only the listener must not affect the established warm tunnel stream.
            relay_control(control_port, "down")
            expect_stream_failure(baseline_port)
            with socket.socket() as unused:
                unused.bind((HOST, 0))
                override = client_config(
                    root, "override", identity, unused.getsockname()[1], known
                )
                recovered = guided("recovered", override, ssh_port=args.ssh_port)
                port, _ = recovered.guided_ready()
                assert_exclusive(port, "guided listener while relay is unavailable")
                assert_rejected(
                    "relay-unavailable",
                    override,
                    port,
                    "Connection refused",
                    args.ssh_port,
                )
                relay_control(control_port, "up")
                with ThreadPoolExecutor(max_workers=2) as executor:
                    warm = executor.submit(
                        action, baseline_port, b"warm -L after relay recovery\n"
                    )
                    fresh = executor.submit(
                        action, port, b"guided -W after relay recovery\n"
                    )
                    warm_ms, guided_ms = warm.result(), fresh.result()
                assert_exclusive(port, "guided listener after relay recovery")
            echo(sibling, b"existing baseline stream survived relay restart\n")
            state = stack.enter_context(open_echo(port))
            identities = sample(recovered, "guided EOF with live state")
            recovered.stop_guided(command=False)
            assert listener_closed(port)
            with contextlib.suppress(ConnectionResetError, ConnectionAbortedError):
                assert state.recv(1) == b"", "state stream survived stdin EOF"
            state.close()
            assert_released(port, "stdin EOF")
            wait_until(
                lambda: stats()["active"] == 0, "recovered relay streams to drain"
            )
            wait_for_exit(identities)
            echo(sibling, b"unrelated SSH survived guided stdin EOF\n")
            echo(survivor, b"unrelated local stream survived guided stdin EOF\n")
            print(
                f"relay recovery first_response_ms: warm -L={warm_ms:.1f}, guided -W={guided_ms:.1f}; "
                "stdin EOF cancellation: PASS"
            )

            doomed = guided("abrupt", local_port=port)
            assert doomed.guided_ready()[0] == port
            with open_echo(port) as state:
                assert_exclusive(port, "guided listener before abrupt parent death")
                identities = sample(doomed, "guided abrupt death with live state")
                doomed.process.kill()
                assert doomed.process.wait(timeout=TIMEOUT) != 0
                with contextlib.suppress(ConnectionResetError, ConnectionAbortedError):
                    assert state.recv(1) == b"", "state stream survived parent death"
            assert_released(port, "abrupt parent death")
            wait_until(
                lambda: stats()["active"] == 0, "abrupt death relay streams to drain"
            )
            wait_for_exit(identities)
            echo(sibling, b"unrelated SSH survived abrupt parent death\n")
            echo(survivor, b"unrelated local stream survived abrupt parent death\n")
            print("exclusive port ownership and abrupt parent death: PASS")
    if remote_half_close:
        print("isolated real SSH guided forward: PASS")
    else:
        print(
            "isolated SSH checks completed with CANDIDATE LIMIT: remote-first half-close"
        )
        raise SystemExit(3)


def port(value):
    number = int(value)
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError("expected a port in 1..65535")
    return number


def main():
    if not __debug__:
        raise RuntimeError("acceptance requires assertions (do not use Python -O)")
    parser = argparse.ArgumentParser(description=__doc__)
    if sys.argv[1:2] == ["relay"]:
        parser.add_argument("--control-port", type=port, required=True)
        serve_relay(parser.parse_args(sys.argv[2:]).control_port)
        return
    parser.add_argument("--harness", type=pathlib.Path, required=True)
    parser.add_argument("--ssh-host", choices=[HOST], required=True)
    parser.add_argument("--ssh-port", type=port, required=True)
    parser.add_argument("--ssh-user", required=True)
    parser.add_argument("--identity", type=pathlib.Path, required=True)
    parser.add_argument("--denied-identity", type=pathlib.Path)
    parser.add_argument("--host-key", type=pathlib.Path, required=True)
    parser.add_argument("--relay-control", metavar="127.0.0.1:PORT")
    parser.add_argument("--protocol-only", choices=("warm", "guided"))
    args = parser.parse_args()
    args.control_port = None
    if not args.protocol_only:
        if args.relay_control is None or args.denied_identity is None:
            parser.error(
                "synthetic acceptance requires --relay-control and --denied-identity"
            )
        host, _, control = args.relay_control.partition(":")
        if host != HOST or not control.isdecimal():
            parser.error("relay control must be on client loopback (127.0.0.1:PORT)")
        args.control_port = int(control)
        if not 1 <= args.control_port <= 65535 or args.control_port == args.ssh_port:
            parser.error("relay control requires a distinct port in 1..65535")
    if WINDOWS and args.harness.suffix.lower() != ".exe":
        parser.error("native Windows Python requires the native .exe harness")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", args.ssh_user):
        parser.error("expected a synthetic sshd account name")
    acceptance(args)


if __name__ == "__main__":
    main()
