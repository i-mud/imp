#!/usr/bin/env python3
"""Compare isolated native persistent SSH with warm system OpenSSH against a disposable sshd."""

import argparse
import asyncio
import contextlib
import hashlib
import importlib.util
import json
import math
import os
import shutil
import socket
import statistics
import struct
import subprocess
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

SCRIPTS = Path(__file__).resolve().parent
HOST = "127.0.0.1"
USER = "impfixture"
PRIMARY = 2222
SECONDARY = 2223
RELAY = 8787
REPLACEMENT_RELAY = 8788
CONTROL = 8790
RUNS = 3


def sibling(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


container = sibling("run-guided-forward-fixture")
probes = sibling("test-guided-forward")
docker = container.docker


def install_keys(name, root, keygen, env):
    keys = {label: root / label for label in ("allowed", "denied", "untrusted")}
    for key in keys.values():
        subprocess.run(
            [keygen, "-q", "-t", "ed25519", "-N", "", "-f", key],
            check=True,
            timeout=15,
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
        )
    destinations = (
        f'permitopen="{HOST}:{RELAY}",permitopen="{HOST}:{REPLACEMENT_RELAY}"'
    )
    authorized = "".join(
        policy + " " + keys[label].with_suffix(".pub").read_text().strip() + "\n"
        for label, policy in (
            ("allowed", f"restrict,port-forwarding,{destinations}"),
            ("denied", "restrict"),
        )
    )
    server = (
        container.SERVER.replace(
            f"PermitOpen {HOST}:{RELAY}",
            f"PermitOpen {HOST}:{RELAY} {HOST}:{REPLACEMENT_RELAY}",
        )
        + "LogLevel VERBOSE\n"
    )
    for path, content in (("imp-authorized", authorized), ("imp-sshd.conf", server)):
        docker(
            "exec", "-i", name, "sh", "-ec", f"cat >/run/{path}", data=content.encode()
        )
    docker(
        "exec",
        name,
        "ssh-keygen",
        "-q",
        "-t",
        "ed25519",
        "-N",
        "",
        "-f",
        "/run/imp-host",
    )
    # The server pin comes from the container control plane, not a network scan.
    keys["host"] = root / "host.pub"
    keys["host"].write_text(
        docker("exec", name, "cat", "/run/imp-host.pub") + "\n", encoding="ascii"
    )
    return keys


def start_sshd(name, port):
    docker(
        "exec",
        "-d",
        name,
        "sh",
        "-ec",
        'echo $$ >"/run/imp-sshd-$1.pid"; exec /usr/sbin/sshd -D -f /run/imp-sshd.conf '
        '-p "$1" -o "PidFile=/run/imp-sshd-$1.openssh.pid" -E "/run/imp-sshd-$1.log"',
        "sshd",
        str(port),
    )
    probes.wait_until(
        lambda: "Server listening on" in ssh_log(name, port), f"sshd {port} readiness"
    )


def ssh_log(name, port):
    return docker(
        "exec",
        name,
        "sh",
        "-ec",
        f"[ ! -f /run/imp-sshd-{port}.log ] || cat /run/imp-sshd-{port}.log",
        timeout=5,
    )


def auth_count(name, port=PRIMARY):
    return ssh_log(name, port).count(f"Accepted publickey for {USER} from ")


def sever_sshd(name, port):
    # SIGTERM of the listener alone leaves authenticated OpenSSH children alive.
    docker(
        "exec",
        name,
        "python3",
        "-c",
        """
import os
import signal
import sys
from pathlib import Path

pid = int(Path(f'/run/imp-sshd-{int(sys.argv[1])}.pid').read_text())
def descendants(parent):
    try:
        children = Path(f'/proc/{parent}/task/{parent}/children').read_text().split()
    except FileNotFoundError:
        return
    for child in map(int, children):
        yield child
        yield from descendants(child)

owned = [pid, *descendants(pid)]
for process in owned:
    try:
        os.kill(process, signal.SIGKILL)
    except ProcessLookupError:
        pass
""",
        str(port),
        timeout=8,
    )


def warm_ssh(root, stack, env, ssh, keys, ssh_port, label):
    algorithm, blob = keys["host"].read_text(encoding="ascii").split()[:2]
    assert algorithm == "ssh-ed25519"
    host = HOST if ssh_port == 22 else f"[{HOST}]:{ssh_port}"
    config = probes.client_config(
        root, label, keys["allowed"], ssh_port, f"{host} {algorithm} {blob}\n"
    )
    port = probes.free_port()
    owner = probes.FixtureProcess(
        root,
        label,
        [
            ssh,
            "-F",
            config,
            "-T",
            "-l",
            USER,
            "-v",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "HostKeyAlgorithms=ssh-ed25519",
            "-N",
            "-L",
            f"{HOST}:{port}:{HOST}:{RELAY}",
            "--",
            HOST,
        ],
        env,
        stack,
    )
    _, setup_ms = owner.ready(
        lambda: (
            f"Local forwarding listening on {HOST} port {port}." in owner.diagnostics()
        )
    )
    return owner, port, setup_ms


def read_closed(sock):
    with contextlib.suppress(
        ConnectionResetError, ConnectionAbortedError, BrokenPipeError
    ):
        assert sock.recv(1) == b"", "a cancelled channel still returned data"


def raw_checks(name, root, stack, env, binary, ssh, keys, ssh_port, control):
    docker(
        "exec",
        "-d",
        name,
        "python3",
        "/run/test-guided-forward.py",
        "relay",
        "--control-port",
        str(CONTROL),
    )

    def stats():
        return probes.relay_control(control)

    def ready():
        try:
            return stats()["active"] == 0
        except (OSError, ValueError):
            return False

    probes.wait_until(ready, "synthetic raw relay readiness")
    stack.callback(probes.relay_control, control, "down")
    limits = []
    pinned = keys["host"].read_bytes()
    missing = root / "missing-host.pub"
    wrong = keys["untrusted"].with_suffix(".pub")
    wrong_pin = wrong.read_bytes()

    def spawn(label, *, key="allowed", pin=None, listen=0):
        if pin is None:
            pin = keys["host"]
        return probes.FixtureProcess(
            root,
            label,
            [
                binary,
                "raw",
                f"{HOST}:{ssh_port}",
                USER,
                keys[key],
                pin,
                str(RELAY),
                str(listen),
            ],
            env,
            stack,
        )

    def start_blocked_upload(sock, executor):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
        sock.sendall(b"U")
        assert probes.receive(sock, 1) == b"R"

        def upload():
            for _ in range(16):
                sock.sendall(probes.UPLOAD)
            sock.shutdown(socket.SHUT_WR)

        return executor.submit(upload)

    baseline, baseline_port, _ = warm_ssh(
        root, stack, env, ssh, keys, ssh_port, "unrelated-warm"
    )
    survivor = stack.enter_context(probes.open_echo(baseline_port))
    before_auth = auth_count(name)
    candidate = spawn("raw")
    port, setup = candidate.guided_ready()
    initial = stats()
    probes.assert_exclusive(port, "raw candidate listener")
    with probes.open_echo(port) as held:
        with probes.connect(port) as half:
            half.sendall(b"H" + probes.UPLOAD)
            half.shutdown(socket.SHUT_WR)
            assert probes.receive(half, 32) == hashlib.sha256(probes.UPLOAD).digest()
            assert half.recv(1) == b""
        if not probes.remote_first("native raw", port, stats, candidate=True):
            limits.append("remote-first half-close")
        with (
            probes.connect(port) as duplex,
            ThreadPoolExecutor(max_workers=1) as executor,
        ):
            duplex.sendall(b"B")
            sender = executor.submit(duplex.sendall, probes.UPLOAD)
            got = hashlib.sha256(probes.receive(duplex, len(probes.DOWNLOAD))).digest()
            assert got == hashlib.sha256(probes.DOWNLOAD).digest()
            sender.result(timeout=probes.TIMEOUT)
            assert probes.receive(duplex, 32) == hashlib.sha256(probes.UPLOAD).digest()

        with (
            probes.connect(port) as blocked,
            ThreadPoolExecutor(max_workers=1) as executor,
        ):
            pending = start_blocked_upload(blocked, executor)
            try:
                probes.action(port, b"sibling action under upload stall\n")
                probes.echo(held, b"held channel under upload stall\n")
                assert not pending.done(), "fixture did not create upload backpressure"
            finally:
                probes.relay_control(control, "resume-upload")
            pending.result(timeout=probes.TIMEOUT)
            digest = hashlib.sha256()
            for _ in range(16):
                digest.update(probes.UPLOAD)
            assert probes.receive(blocked, 32) == digest.digest()

        probes.wait_until(
            lambda: stats()["active"] == 2, "only held candidate and unrelated streams"
        )
        with probes.connect(port, small_window=True) as stalled:
            stalled.sendall(b"D")
            probes.wait_until(
                lambda: stats()["stall_started"] > initial["stall_started"],
                "download stall",
            )
            try:
                probes.action(port, b"sibling action under download stall\n")
                probes.echo(held, b"held channel under download stall\n")
            except TimeoutError as error:
                limits.append("sibling progress under stalled download")
                print(
                    "LIMIT RAW sibling echo timed out while a separate download reader was stalled",
                    flush=True,
                )
                traceback.print_exception(error)
            assert stats()["stall_finished"] == initial["stall_finished"], (
                "fixture did not create download backpressure"
            )
            # Abort the local reader rather than waiting for its never-read TCP receive queue.
            linger = struct.pack("HH" if probes.WINDOWS else "ii", 1, 0)
            stalled.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
        try:
            probes.wait_until(
                lambda: stats()["active"] == 2,
                "cancelled stalled channel to drain remotely",
            )
        except AssertionError:
            limits.append("remote drain after local download reset")
            print(
                "LIMIT RAW local download reset did not drain the stalled remote application",
                flush=True,
            )
        probes.echo(held, b"held channel survived sibling cancellation\n")
        assert auth_count(name) - before_auth == 1, (
            "raw channel workload did not use exactly one authenticated SSH session"
        )
        print(
            f"RAW duplex, upload stall progress, and post-reset recovery: PASS; setup_ms={setup:.2f}; ssh_authentications=1",
            flush=True,
        )

        probes.relay_control(control, "down")
        restarting = perf_counter()
        try:
            probes.expect_stream_failure(port)
            assert "raw forwarding failed" in candidate.diagnostics(), (
                candidate.diagnostics()
            )
        finally:
            probes.relay_control(control, "up")
        first_ms = probes.action(
            port, b"relay restored on same authenticated session\n"
        )
        recovery_ms = (perf_counter() - restarting) * 1000
        assert auth_count(name) - before_auth == 1
        probes.echo(held, b"held channel survived relay listener restart\n")
        current_survivor = stack.enter_context(probes.open_echo(baseline_port))
        with (
            probes.connect(port) as blocked,
            ThreadPoolExecutor(max_workers=1) as executor,
        ):
            pending = start_blocked_upload(blocked, executor)
            probes.action(
                port, b"upload cancelled by stopping owner, not by resuming relay\n"
            )
            assert not pending.done(), (
                "fixture did not create upload backpressure before stop"
            )
            stopping = perf_counter()
            try:
                candidate.stop_guided(command=True)
                try:
                    pending.result(timeout=probes.TIMEOUT)
                except ConnectionError:
                    pass
                else:
                    raise AssertionError(
                        "paused upload completed despite stopping its owner"
                    )
                read_closed(held)
                probes.assert_released(port, "raw stop")
                local_cleanup_ms = (perf_counter() - stopping) * 1000
            finally:
                # The synthetic server deliberately does not poll its upload socket before this.
                probes.relay_control(control, "resume-upload")
        probes.wait_until(
            lambda: stats()["active"] == 1, "application drain after stopped upload"
        )
        drain_ms = (perf_counter() - stopping) * 1000
        probes.action(baseline_port, b"unrelated OpenSSH still accepts new streams\n")
        probes.echo(survivor, b"unrelated OpenSSH survived native stop\n")
        print(
            f"RAW relay refused/recovered on same session and stopped blocked upload: PASS; "
            f"recovery_ms={recovery_ms:.2f}, first_roundtrip_ms={first_ms:.2f}, "
            f"local_cleanup_ms={local_cleanup_ms:.2f}, relay_application_drain_ms={drain_ms:.2f} (not an SSH CLOSE ack)",
            flush=True,
        )

    for label, options, diagnostic in (
        ("missing-host-pin", {"pin": missing}, "host key"),
        ("host-key-mismatch", {"pin": wrong}, "host key"),
        ("authentication-rejected", {"key": "untrusted"}, "auth"),
        ("occupied-endpoint", {"listen": baseline_port}, "bind"),
    ):
        before = stats()["accepted"]
        failed = spawn(label, **options)
        assert failed.process.wait(timeout=probes.TIMEOUT) != 0, label
        assert b"READY" not in failed.out.read_bytes(), label
        assert diagnostic in failed.diagnostics().lower(), (label, failed.diagnostics())
        assert stats()["accepted"] == before, label
        assert keys["host"].read_bytes() == pinned and wrong.read_bytes() == wrong_pin
        assert not missing.exists(), "candidate populated an absent trust pin (TOFU)"
        probes.echo(survivor, f"unrelated channel survived {label}\n".encode())
        print(f"RAW {label}: PASS", flush=True)

    before_auth = auth_count(name)
    denied = spawn("forwarding-rejected", key="denied")
    rejected_port, _ = denied.guided_ready()
    assert auth_count(name) - before_auth == 1, (
        "restricted identity did not authenticate"
    )
    before = stats()["accepted"]
    probes.expect_stream_failure(rejected_port)
    denied.stop_guided(command=False)
    assert "raw forwarding failed" in denied.diagnostics(), denied.diagnostics()
    probes.assert_released(rejected_port, "raw stdin EOF")
    assert stats()["accepted"] == before, "server-denied channel reached raw relay"
    print(
        "RAW authenticated forwarding denial and stdin EOF shutdown: PASS", flush=True
    )

    doomed = spawn("abrupt")
    doomed_port, _ = doomed.guided_ready()
    with probes.open_echo(doomed_port) as held:
        owned = probes.sample(
            doomed, "native raw live before abrupt owner death", min_ssh=0
        )
        killing = perf_counter()
        doomed.process.kill()
        assert doomed.process.wait(timeout=probes.TIMEOUT) != 0
        read_closed(held)
    probes.assert_released(doomed_port, "raw abrupt owner death")
    probes.wait_for_exit(owned)
    probes.wait_until(
        lambda: stats()["active"] == 1,
        "only the unrelated current relay channel after abrupt owner death",
    )
    probes.echo(
        current_survivor,
        b"unrelated current relay stream survived abrupt native death\n",
    )
    probes.echo(
        survivor, b"unrelated pre-restart OpenSSH survived abrupt native death\n"
    )
    kill_ms = (perf_counter() - killing) * 1000
    baseline.cleanup()
    print(
        f"RAW abrupt owner death, listener release and unrelated SSH survival: PASS; "
        f"local_process_and_relay_application_cleanup_ms={kill_ms:.2f} (not an SSH CLOSE ack)",
        flush=True,
    )
    before_auth, before_accept = auth_count(name), stats()["accepted"]
    measured = subprocess.run(
        [
            binary,
            "measure",
            f"{HOST}:{ssh_port}",
            USER,
            keys["allowed"],
            keys["host"],
            str(RELAY),
        ],
        cwd=root,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=True,
    )
    print(measured.stdout.rstrip(), flush=True)
    header, *samples = measured.stdout.splitlines()
    assert header.startswith("ssh_auth_us=") and len(samples) == 20, measured.stdout
    for index, sample in enumerate(samples, 1):
        channel, opened, closed = sample.split()
        assert channel == f"channel={index}", sample
        assert opened.startswith("open_confirmed_us=") and closed.startswith(
            "eof_to_peer_close_us="
        ), sample
    assert auth_count(name) - before_auth == 1, (
        "channel measurement did not reuse one SSH authentication"
    )
    probes.wait_until(
        lambda: stats()["accepted"] - before_accept == 20, "20 measured relay accepts"
    )
    return limits


def paired_result(runs):
    def quantile(values, p):
        return (
            sorted(values)[math.ceil(p * len(values)) - 1]
            if p == 0.95
            else statistics.median(values)
        )

    for i, run in enumerate(runs, 1):
        warm, native = run["warm_samples_ms"], run["native_samples_ms"]
        assert len(warm) == len(native) == 20, (i, len(warm), len(native))
        print(
            "PAIRS "
            + json.dumps(
                {
                    "run": i,
                    "warm_ms": warm,
                    "native_ms": native,
                    "delta_ms": [n - w for w, n in zip(warm, native)],
                }
            ),
            flush=True,
        )
    result = {}
    for percentile in (0.50, 0.95):
        warm_runs = [quantile(run["warm_samples_ms"], percentile) for run in runs]
        warm = statistics.median(warm_runs)
        variation = statistics.median(abs(v - warm) for v in warm_runs)
        deltas = [
            n - w
            for run in runs
            for w, n in zip(run["warm_samples_ms"], run["native_samples_ms"])
        ]
        overhead = quantile(deltas, percentile)
        limit = max(5, variation, 0.25 * warm)
        result[f"p{int(percentile * 100)}"] = {
            "warm_runs_ms": warm_runs,
            "warm_ms": warm,
            "warm_inter_run_mad_ms": variation,
            "paired_delta_ms": overhead,
            "overhead_limit_ms": limit,
            "within_limit": overhead <= limit,
        }
    print("COMPARISON " + json.dumps(result), flush=True)
    if not all(item["within_limit"] for item in result.values()):
        print(
            "LIMIT latency: candidate missed the predeclared paired comparison criterion",
            flush=True,
        )


async def authority_checks(name, root, stack, env, binary, ssh, keys, ssh_ports, relay):

    @contextlib.asynccontextmanager
    async def child(*command, ready_port=None):
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            cwd=root,
            env=env if ready_port is None else os.environ,
        )
        try:
            wire = relay.JsonLines(process)
            if ready_port is not None:
                assert await wire.ready() == ready_port
            yield wire
        finally:
            if process.returncode is None:
                process.stdin.close()
                try:
                    await asyncio.wait_for(process.wait(), 3)
                except TimeoutError:
                    process.kill()
                    await process.wait()

    def backend(port=RELAY, route="A"):
        return child(
            "docker",
            "exec",
            "-i",
            name,
            "python3",
            "-u",
            "/run/test-persistent-relay.py",
            "serve",
            "--port",
            str(port),
            "--route",
            route,
            ready_port=port,
        )

    def authority():
        return child(binary, "authority")

    def config(ssh_port, relay_port):
        return {
            "host": HOST,
            "port": ssh_port,
            "username": USER,
            "server_key": str(keys["host"]),
            "private_key": str(keys["allowed"]),
            "relay_port": relay_port,
        }

    async def finish_native(wire, *, already_shutdown=False):
        if not already_shutdown:
            relay.ok(await wire.call("shutdown"))
        wire.process.stdin.close()
        assert await asyncio.wait_for(wire.process.wait(), probes.TIMEOUT) == 0

    a = config(ssh_ports[PRIMARY], RELAY)
    b = config(ssh_ports[SECONDARY], REPLACEMENT_RELAY)
    print(
        "COMPARISON criterion: paired delta p50/p95 <= "
        "max(5 ms, median absolute deviation of warm run quantiles, 25% of warm quantile)",
        flush=True,
    )
    before_warm = auth_count(name)
    warm, warm_port, setup = warm_ssh(
        root, stack, env, ssh, keys, ssh_ports[PRIMARY], "comparison-warm"
    )
    assert auth_count(name) - before_warm == 1
    print(f"WARM -L authenticated setup_ms={setup:.2f}", flush=True)
    runs = []
    for i in range(RUNS):
        before_auth = auth_count(name)
        async with backend() as fixture, authority() as native:

            def sample(run=i + 1):
                table = probes.process_table()
                for label, pid, expected_ssh in (
                    ("warm -L", warm.process.pid, 1),
                    ("native", native.process.pid, 0),
                ):
                    owned = {pid}
                    assert pid in table, (label, pid)
                    while (
                        children := {p for p, row in table.items() if row[0] in owned}
                        - owned
                    ):
                        owned.update(children)
                    rows = [table[p] for p in owned]
                    metrics = {
                        "run": run,
                        "method": label,
                        "processes": len(rows),
                        "threads": sum(row[1] for row in rows),
                        "resident_kib": sum(row[2] for row in rows),
                        "ssh_processes": sum(
                            row[3].lower() in {"ssh", "ssh.exe"} for row in rows
                        ),
                    }
                    assert metrics["ssh_processes"] == expected_ssh, metrics
                    print("HELD RESOURCES " + json.dumps(metrics), flush=True)

            runs.append(
                await relay.compare(
                    fixture,
                    native,
                    a,
                    warm_port,
                    sample=sample,
                    native_first=bool(i % 2),
                )
            )
            assert auth_count(name) - before_auth == 1, (
                "one new native auth expected across all held and action streams"
            )
            print(f"AUTH compare run {i + 1}: new_native=1 new_warm=0", flush=True)
            await finish_native(native)
            await relay.stop(fixture)
    paired_result(runs)
    warm.cleanup()
    probes.assert_released(warm_port, "warm comparison cleanup")

    async with (
        backend() as fixture_a,
        backend(REPLACEMENT_RELAY, "B") as fixture_b,
        authority() as native,
    ):
        await relay.adversarial(fixture_a, fixture_b, native, a, b, warm_port)
        await finish_native(native, already_shutdown=True)
        await relay.stop(fixture_b)
        await relay.stop(fixture_a)

    async with contextlib.AsyncExitStack() as endpoints:
        fixture = await endpoints.enter_async_context(backend())
        native = await endpoints.enter_async_context(authority())
        replacement = None

        async def launch_replacement():
            nonlocal replacement
            replacement = await endpoints.enter_async_context(backend(RELAY, "B"))
            return replacement

        before_auth = auth_count(name)
        await relay.same_endpoint(fixture, native, a, launch_replacement)
        assert auth_count(name) - before_auth == 1, (
            "same-endpoint relay replacement opened another SSH session"
        )
        await finish_native(native, already_shutdown=True)
        assert replacement is not None
        await relay.stop(replacement)
        await relay.stop(fixture)

    async with (
        backend() as fixture_a,
        backend(REPLACEMENT_RELAY, "B") as fixture_b,
        authority() as native,
    ):
        before_a, before_b = auth_count(name), auth_count(name, SECONDARY)
        generation_a = relay.ok(await native.call("establish", **a))["generation"]
        _, old_context, _ = await relay.live_native(native, generation_a)
        assert auth_count(name) - before_a == 1
        dispatched = "persistent-in-flight-at-ssh-loss"
        relay.ok(await fixture_a.call("arm"))
        relay.ok(await fixture_b.call("arm"))
        pending = await native.start(
            "action", **relay.action_fields(old_context, dispatched)
        )
        relay.ok(await fixture_a.call("take", command=dispatched))
        losing = perf_counter()
        await asyncio.to_thread(sever_sshd, name, PRIMARY)
        await native.event(
            "invalidated", lambda event: event.get("generation") == generation_a
        )
        relay.verify_error(await native.result(pending), "unknown")
        invalidation_ms = (perf_counter() - losing) * 1000
        relay.ok(await fixture_a.call("finish", result="forwarded"))
        relay.verify_error(
            await native.call(
                "action", **relay.action_fields(old_context, "persistent-while-lost")
            ),
            "not-dispatched",
        )

        generation_b = relay.ok(await native.call("replace", **b))["generation"]
        assert generation_b != generation_a
        _, new_context, _ = await relay.live_native(native, generation_b)
        relay.ok(await fixture_b.call("quiet"))
        relay.ok(await fixture_b.call("arm"))
        relay.verify_error(
            await native.call(
                "action",
                **relay.action_fields(old_context, "persistent-stale-after-loss"),
            ),
            "not-dispatched",
        )
        command = "persistent-recovered-after-ssh-loss"
        action = await native.start(
            "action", **relay.action_fields(new_context, command)
        )
        relay.ok(await fixture_b.call("take", command=command))
        relay.ok(await fixture_b.call("finish", result="forwarded"))
        relay.verify_result(await native.result(action), "forwarded")
        assert auth_count(name, SECONDARY) - before_b == 1
        recovery_ms = (perf_counter() - losing) * 1000
        await finish_native(native)
        await relay.stop(fixture_b)
        await relay.stop(fixture_a)
    print(
        "AUTH actual sshd loss invalidated the generation and left the dispatched action unknown; "
        f"no replay, fresh state and action recovered: PASS; invalidation_ms={invalidation_ms:.2f}, "
        f"loss_to_verified_recovery_ms={recovery_ms:.2f}",
        flush=True,
    )


def main():
    if not __debug__:
        raise RuntimeError("acceptance requires assertions (do not use Python -O)")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path, help="native persistent-ssh executable")
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    if probes.WINDOWS:
        if binary.suffix.lower() != ".exe":
            parser.error("native Windows Python requires the native .exe binary")
        ssh_dir = Path(os.environ["SystemRoot"]) / "System32/OpenSSH"
        ssh, keygen = ssh_dir / "ssh.exe", ssh_dir / "ssh-keygen.exe"
        if not ssh.is_file() or not keygen.is_file():
            parser.error("native Windows OpenSSH Client is required")
    else:
        if not sys.platform.startswith("linux"):
            parser.error("this fixture supports Linux and native Windows")
        ssh, keygen = shutil.which("ssh"), shutil.which("ssh-keygen")
        if not ssh or not keygen:
            parser.error("ssh and ssh-keygen are required")
    # Fail before provisioning Docker if the host relay dependencies are unavailable.
    relay = sibling("test-persistent-relay")
    image = container.build_image(SCRIPTS.parent / "services" / "relay")
    with (
        TemporaryDirectory(prefix="imp-persistent-") as temp,
        contextlib.ExitStack() as stack,
    ):
        root = Path(temp)
        env = dict(os.environ, HOME=temp, USERPROFILE=temp, LC_ALL="C")
        for var in (
            "SSH_AUTH_SOCK",
            "SSH_ASKPASS",
            "SSH_ASKPASS_REQUIRE",
            "SSH_SK_PROVIDER",
        ):
            env.pop(var, None)
        # Keep Docker CLI context and credentials on the invoking host, never in the container.
        name = docker(
            "run",
            "--rm",
            "--init",
            "--detach",
            "-p",
            f"{HOST}::{PRIMARY}",
            "-p",
            f"{HOST}::{SECONDARY}",
            "-p",
            f"{HOST}::{CONTROL}",
            image,
            "sleep",
            "360",
        )
        stack.callback(docker, "rm", "-f", name)
        keys = install_keys(name, root, keygen, env)
        for script in (
            "test-guided-forward.py",
            "test-guided-relay.py",
            "test-persistent-relay.py",
        ):
            docker("cp", str(SCRIPTS / script), f"{name}:/run/{script}")
        for port in (PRIMARY, SECONDARY):
            start_sshd(name, port)
        ports = {
            port: container.published_port(name, port)
            for port in (PRIMARY, SECONDARY, CONTROL)
        }
        raw_failure = None
        try:
            with contextlib.ExitStack() as processes:
                raw_limits = raw_checks(
                    name,
                    root,
                    processes,
                    env,
                    binary,
                    ssh,
                    keys,
                    ports[PRIMARY],
                    ports[CONTROL],
                )
        except (AssertionError, OSError, subprocess.SubprocessError) as error:
            raw_failure = error
            print(
                f"FAIL RAW {type(error).__name__}: {error}; continuing independent real-relay matrix",
                flush=True,
            )
            traceback.print_exception(error)

        async def run():
            async with asyncio.timeout(120):
                await authority_checks(
                    name, root, stack, env, binary, ssh, keys, ports, relay
                )

        asyncio.run(run())
        if raw_failure is not None:
            raise raw_failure
        if raw_limits:
            print("LIMIT RAW isolated candidate: " + ", ".join(raw_limits), flush=True)
            raise SystemExit(3)
    print(
        "Isolated persistent SSH fixture: all asserted checks complete (see comparison and candidate limits above).",
        flush=True,
    )


if __name__ == "__main__":
    main()
