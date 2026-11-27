#!/usr/bin/env python3
"""Run owned-forward Gate 1 with native Python and a disposable Docker sshd/relay.

    python scripts/run-owned-forward-fixture.py /path/to/owned-forward[.exe]

Run in a native relay venv with services/relay installed (Windows Python for
an .exe). Docker must publish SSH to that same host's IPv4 loopback.
"""

import argparse
import asyncio
import json
import math
import os
import runpy
import secrets
import shutil
import socket
import statistics
import sys
from collections import Counter
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager, suppress
from pathlib import Path
from tempfile import TemporaryDirectory

from imp_relay.protocol import ActionMessage
from imp_relay.server import MAX_FRAME_BYTES, RelayServer
from imp_relay.websocket_logging import websocket_logger
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve

prior = runpy.run_path(str(Path(__file__).with_name("run-persistent-ssh-fixture.py")))
SCRIPTS, HOST, USER, PRIMARY, RELAY = (
    prior[name] for name in ("SCRIPTS", "HOST", "USER", "PRIMARY", "RELAY")
)
container, probes, docker = (prior[name] for name in ("container", "probes", "docker"))
CONTEXT = {"session": "guided_fixture", "foreground": 7, "connection": 11}
COMMAND = "owned-forward-fixture"
PAIRS = 60
CAP_US = 100_000


def report(label, **fields):
    print(label, json.dumps(fields, sort_keys=True), flush=True)


def expect(reply, status="ok", code=None):
    assert reply.get("status") == status, reply
    if code is not None:
        assert reply.get("code") == code, reply
    return reply


class Lines:
    def __init__(self, process):
        self.process = process
        self.next_id = 0

    async def line(self):
        raw = await asyncio.wait_for(self.process.stdout.readline(), 8)
        if not raw:
            raise EOFError(
                f"fixture subprocess {self.process.pid} ended: {self.process.returncode}"
            )
        return raw.decode().strip()

    async def request(self, **fields):
        self.process.stdin.write(json.dumps(fields).encode() + b"\n")
        await self.process.stdin.drain()
        return json.loads(await self.line())

    async def call(self, cmd, **fields):
        return await self.request(cmd=cmd, **fields)

    async def control(self, op, **fields):
        self.next_id += 1
        reply = expect(await self.request(id=self.next_id, op=op, **fields))
        assert reply["id"] == self.next_id, reply
        return reply


@asynccontextmanager
async def pipe(*command, **kwargs):
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        **kwargs,
    )
    try:
        yield Lines(process)
    finally:
        process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), 2)
        except TimeoutError:
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()


@contextmanager
def local_listener(port=0):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if probes.WINDOWS:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((HOST, port))
        listener.listen()
        listener.setblocking(False)
        assert listener.getsockname()[1] not in (8787, 8789)
        yield listener


def free_port():
    with local_listener() as listener:
        return listener.getsockname()[1]


async def passive_sink(listener, phase, *, expected_accepts=0):
    loop = asyncio.get_running_loop()
    stats = {"tcp_accepts": 0, "received_bytes": 0, "upgrade_requests": 0}
    # Do not send a 101 or process input as WebSocket frames: this is a zero-byte sink.
    try:
        async with asyncio.timeout(0.15):
            while True:
                client, _ = await loop.sock_accept(listener)
                stats["tcp_accepts"] += 1
                with client:
                    request = bytearray()
                    with suppress(ConnectionResetError):
                        while data := await loop.sock_recv(client, 4096):
                            stats["received_bytes"] += len(data)
                            if len(request) < 4096:
                                request.extend(data[: 4096 - len(request)])
                            if b"\r\n\r\n" in request:
                                break
                    if (
                        request.startswith(b"GET ")
                        and b"\r\nupgrade: websocket\r\n" in request.lower()
                    ):
                        stats["upgrade_requests"] += 1
    except TimeoutError:
        pass
    report(
        "PASSIVE_SINK",
        phase=phase,
        action_frames=0 if stats["received_bytes"] == 0 else None,
        action_frames_source="derived from zero bytes; no WebSocket established",
        **stats,
    )
    assert stats.pop("tcp_accepts") == expected_accepts and not any(stats.values()), (
        "passive fixture sink saw an unexpected accept count or application bytes"
    )


class LocalRelay(RelayServer):
    def __init__(self):
        super().__init__(port=0, relay_name="Imp isolated local refusal fixture")
        self.stats = {
            "tcp_accepts": 0,
            "received_bytes": 0,
            "upgrade_requests": 0,
            "action_frames": 0,
        }

    async def start_on(self, listener):
        stats = self.stats

        class ObservedConnection(ServerConnection):
            def connection_made(self, transport):
                stats["tcp_accepts"] += 1
                super().connection_made(transport)

            def data_received(self, data):
                stats["received_bytes"] += len(data)
                super().data_received(data)

        self._server = await serve(
            self._handle_connection,
            sock=listener,
            process_request=self._process_request,
            max_size=MAX_FRAME_BYTES,
            logger=websocket_logger(),
            open_timeout=2,
            close_timeout=1,
            create_connection=ObservedConnection,
        )
        self._stale_task = asyncio.create_task(self._watch_staleness())

    async def _process_request(self, connection, request):
        self.stats["upgrade_requests"] += any(
            token.strip().lower() == "websocket"
            for value in request.headers.get_all("Upgrade")
            for token in value.split(",")
        )
        return await super()._process_request(connection, request)

    async def _decode_frame(self, connection, raw_frame):
        message = await super()._decode_frame(connection, raw_frame)
        if isinstance(message, ActionMessage):
            self.stats["action_frames"] += 1
        return message


@asynccontextmanager
async def healthy_local_relay(phase, port=0):
    with local_listener(port) as listener:
        relay = LocalRelay()
        await relay.start_on(listener)
        baseline = None
        try:
            async with connect(
                f"ws://{HOST}:{relay.port}/state", proxy=None, open_timeout=3
            ) as client:
                hello = json.loads(await asyncio.wait_for(client.recv(), 3))
                assert (
                    hello["type"] == "hello"
                    and hello["relay"]["name"] == "Imp isolated local refusal fixture"
                ), hello
            baseline = relay.stats.copy()
            assert (
                baseline["tcp_accepts"] == baseline["upgrade_requests"] == 1
                and baseline["received_bytes"] > 0
            )
            yield relay.port
        finally:
            await relay.close()
            if baseline is not None:
                delta = {
                    field: value - baseline[field]
                    for field, value in relay.stats.items()
                }
                report("LOCAL_RELAY", phase=phase, **delta)
                assert not any(delta.values()), (
                    "fixture refusal admitted traffic to the host-local relay"
                )


class Fixture:
    def __init__(self, name, binary, root, env, keygen):
        self.name, self.binary, self.root, self.env = name, binary, root, env
        keys = prior["install_keys"](name, root, keygen, env)
        for script in ("test-guided-relay.py", "test-persistent-relay.py"):
            docker("cp", str(SCRIPTS / script), f"{name}:/run/{script}")
        prior["start_sshd"](name, PRIMARY)
        self.ssh_port = container.published_port(name, PRIMARY)
        algorithm, blob = keys["host"].read_text(encoding="ascii").split()[:2]
        assert algorithm == "ssh-ed25519"
        pin = f"[{HOST}]:{self.ssh_port} {algorithm} {blob}\n"
        wrong_key = keys["untrusted"].with_suffix(".pub").read_text().split()[1]
        self.configs = {}
        for label, identity, trust in (
            ("allowed", "allowed", pin),
            ("unknown", "allowed", ""),
            (
                "changed",
                "allowed",
                f"[{HOST}]:{self.ssh_port} {algorithm} {wrong_key}\n",
            ),
            ("untrusted", "untrusted", pin),
            ("denied", "denied", pin),
        ):
            known_hosts = root / (label + ".known_hosts")
            known_hosts.write_text(trust, encoding="ascii")
            config = root / (label + ".config")
            config.write_text(
                f'IdentityFile "{keys[identity].as_posix()}"\n'
                f'UserKnownHostsFile "{known_hosts.as_posix()}"\n'
                "GlobalKnownHostsFile none\n"
                "StrictHostKeyChecking yes\n"
                "HostKeyAlgorithms ssh-ed25519\n"
                "VerifyHostKeyDNS no\n"
                "UpdateHostKeys no\n"
                "IdentitiesOnly yes\n"
                "IdentityAgent none\n"
                "BatchMode yes\n"
                "PreferredAuthentications publickey\n"
                "ConnectTimeout 4\n"
                "ConnectionAttempts 1\n"
                "ControlMaster no\n"
                "ControlPath none\n",
                encoding="utf-8",
            )
            self.configs[label] = config

    async def ssh_log(self):
        return await asyncio.to_thread(prior["ssh_log"], self.name, PRIMARY)

    @asynccontextmanager
    async def owner(self, label="allowed", *, local=None, remote=RELAY, control=False):
        if local is None:
            local = free_port()
        async with pipe(
            str(self.binary),
            *(["--control"] if control else []),
            str(self.configs[label]),
            HOST,
            USER,
            str(self.ssh_port),
            str(local),
            str(remote),
            cwd=self.root,
            env=self.env,
        ) as owner:
            reply = expect(json.loads(await owner.line()), "ready")
            owner.ssh_pid = int(reply["pid"])
            owner.port = local
            assert reply["control"] is control and owner.ssh_pid > 0, reply
            yield owner

    @asynccontextmanager
    async def relay(self):
        async with pipe(
            "docker",
            "exec",
            "-i",
            self.name,
            "python3",
            "-u",
            "/run/test-persistent-relay.py",
            "serve",
        ) as relay:
            assert await relay.line() == f"READY {RELAY}"
            yield relay
            await relay.control("stop")
            tail = await relay.line()
            assert tail.startswith("PASS backend A:"), tail
            assert await asyncio.wait_for(relay.process.wait(), 5) == 0


async def first(owner, cmd="begin", **fields):
    # `ready` is a spawn notification, not an SSH authentication/listen barrier.
    deadline = asyncio.get_running_loop().time() + 5
    while True:
        reply = await owner.call(cmd, **fields)
        if (
            reply.get("code") != "connect_failed"
            or asyncio.get_running_loop().time() >= deadline
        ):
            return reply
        await asyncio.sleep(0.025)


async def graceful(owner):
    expect(await owner.call("quit"))
    assert await asyncio.wait_for(owner.process.wait(), 5) == 0


async def identities(owner, label):
    table = await asyncio.to_thread(probes.process_table)
    owned = {owner.process.pid}
    while children := {pid for pid, row in table.items() if row[0] in owned} - owned:
        owned.update(children)
    child = table[owner.ssh_pid]
    assert owner.ssh_pid in owned and child[3].lower() in ("ssh", "ssh.exe"), child
    rows = [table[pid] for pid in owned]
    ssh_count = sum(row[3].lower() in ("ssh", "ssh.exe") for row in rows)
    report(
        "RESOURCES",
        phase=label,
        processes=len(rows),
        ssh_processes=ssh_count,
        threads=sum(row[1] for row in rows),
        resident_kib=sum(row[2] for row in rows),
    )
    assert ssh_count == 1
    return {pid: table[pid][4] for pid in owned}


async def released(owner, owned):
    exited, bound = await asyncio.gather(
        asyncio.to_thread(probes.wait_for_exit, owned),
        asyncio.to_thread(probes.assert_released, owner.port, "owned SSH cleanup"),
        return_exceptions=True,
    )
    # Report both observations even when cleanup is incomplete.
    table = await asyncio.to_thread(probes.process_table)
    remaining = [
        pid
        for pid, creation in owned.items()
        if pid in table and table[pid][4] == creation
    ]
    closed = await asyncio.to_thread(probes.listener_closed, owner.port)
    report(
        "CLEANUP",
        port=owner.port,
        remaining=remaining,
        listener_closed=closed,
        bindable=not isinstance(bound, Exception),
    )
    if isinstance(exited, Exception):
        raise exited
    if isinstance(bound, Exception):
        raise bound
    assert closed, "owned listener did not close"


def proof(reply, *, warm=False):
    attempts, elapsed, search = (
        reply[key] for key in ("attempts", "proof_us", "search_us")
    )
    assert all(type(value) is int for value in (attempts, elapsed, search)), reply
    if warm:
        assert attempts == elapsed == search == 0, reply
    else:
        assert attempts > 0 and 0 <= search <= elapsed < CAP_US, reply


def snapshot(message, index):
    expected = {
        "character": {
            "name": "GuidedFixture",
            "hp": {"current": 41 + index, "max": 100},
            "mana": None,
            "moves": None,
        },
        "target": None,
    }
    assert (
        message["type"],
        message["protocol"],
        message["seq"],
        message["context"],
        message["state"],
    ) == ("snapshot", 2, index + 1, CONTEXT, expected), message


def retained(messages, index):
    hello, *rest = messages
    assert (hello["type"], hello["protocol"], hello["relay"]["name"]) == (
        "hello",
        2,
        "Imp persistent acceptance fixture A",
    ), hello
    (retained_snapshot,) = (
        message for message in rest if message["type"] == "snapshot"
    )
    snapshot(retained_snapshot, index)


def action_message(context=CONTEXT):
    return {"type": "action", "protocol": 2, "context": context, "command": COMMAND}


def action_result(message, accepted):
    status, detail = (
        ("forwarded", None) if accepted else ("rejected", "context is not active")
    )
    assert (
        message["type"],
        message["protocol"],
        message["status"],
        message["detail"],
    ) == ("action-result", 2, status, detail), message


async def upgrades(relay):
    return (await relay.control("upgrades"))["upgrades"]


async def positive(fixture):
    async with fixture.relay() as relay, fixture.owner() as owner:
        state = expect(await first(owner, "bench", slot="state"))
        proof(state)
        retained(state["messages"], 0)
        owned = await identities(owner, "held-state")
        await relay.control("arm")
        wrong = dict(CONTEXT, connection=CONTEXT["connection"] + 1)
        rejected = expect(
            await owner.call("bench", slot="action", action=action_message(wrong))
        )
        proof(rejected)
        (reply,) = rejected["messages"]
        action_result(reply, False)
        expect(await owner.call("close", slot="action"))
        await relay.control("quiet")
        for index in range(1, 21):
            await relay.control("arm", command=COMMAND, index=index)
            dispatched = expect(
                await owner.call("bench", slot="action", action=action_message())
            )
            proof(dispatched)
            (reply,) = dispatched["messages"]
            action_result(reply, True)
            await relay.control("finish")
            expect(await owner.call("close", slot="action"))
            for _ in range(8):
                message = expect(await owner.call("read", slot="state"))["message"]
                if message["type"] == "snapshot":
                    snapshot(message, index)
                    break
                assert message["type"] == "status", message
            else:
                raise AssertionError("held /state never observed the published update")
        await relay.control("arm")
        await relay.control("quiet")
        counts = await upgrades(relay)
        assert counts == {"/state": 2, "/action": 21}, (
            counts
        )  # includes the backend readiness observer
        await graceful(owner)
        await released(owner, owned)


async def dead_child(owner, *, slot="state"):
    reply = await first(owner, slot=slot)
    report("REFUSAL", phase="start", response=reply)
    expect(reply, "error", "child_exited")


async def auth_refusal(fixture, label):
    before = await fixture.ssh_log()
    async with fixture.owner(label) as owner:
        await dead_child(owner)
        await graceful(owner)
    new_log = (await fixture.ssh_log())[len(before) :]
    assert (
        "Connection from" in new_log and f"Accepted publickey for {USER}" not in new_log
    ), new_log
    if label == "untrusted":
        assert f"Failed publickey for {USER}" in new_log, new_log


async def port_conflict(fixture, *, healthy=False):
    async with AsyncExitStack() as stack:
        if healthy:
            local = await stack.enter_async_context(
                healthy_local_relay("live-relay-port-conflict")
            )
        else:
            sink = stack.enter_context(local_listener())
            local = sink.getsockname()[1]
        before = (await fixture.ssh_log()).count(f"Accepted publickey for {USER}")
        async with fixture.owner(local=local) as owner:
            # Wait for bind failure, not just spawn; the conflicting listener stays passive.
            try:
                await asyncio.to_thread(
                    probes.wait_until,
                    lambda: owner.ssh_pid not in probes.process_table(),
                    "own OpenSSH to exit on fixture port conflict",
                )
                await dead_child(owner)
            finally:
                if not healthy:
                    await passive_sink(sink, "local-port-conflict")
            await graceful(owner)
        assert (await fixture.ssh_log()).count(
            f"Accepted publickey for {USER}"
        ) == before + 1


async def simultaneous_owners(fixture):
    async with fixture.relay() as relay, AsyncExitStack() as stack:
        local = free_port()
        before = await upgrades(relay)
        contenders = await asyncio.gather(
            *(stack.enter_async_context(fixture.owner(local=local)) for _ in range(2)),
            return_exceptions=True,
        )
        for contender in contenders:
            if isinstance(contender, Exception):
                raise contender

        def sole_survivor():
            table = probes.process_table()
            alive = [owner for owner in contenders if owner.ssh_pid in table]
            return alive[0] if len(alive) == 1 else None

        try:
            winner = await asyncio.to_thread(
                probes.wait_until,
                sole_survivor,
                "exactly one fixture OpenSSH contender to own the port",
            )
        except AssertionError:
            if probes.WINDOWS:
                # A LISTEN row diagnoses bind contention; it never authorizes application bytes.
                netstat = Path(os.environ["SystemRoot"]) / "System32/netstat.exe"
                query = await asyncio.create_subprocess_exec(
                    netstat,
                    "-ano",
                    "-p",
                    "tcp",
                    stdout=asyncio.subprocess.PIPE,
                )
                output, _ = await asyncio.wait_for(query.communicate(), 8)
                assert query.returncode == 0
                listeners = []
                for line in output.splitlines():
                    cols = line.split()
                    if (
                        len(cols) == 5
                        and cols[1] == f"{HOST}:{local}".encode()
                        and cols[2] == b"0.0.0.0:0"
                    ):
                        listeners.append(int(cols[-1]))
                for owner in contenders:
                    dial = await owner.call("begin", slot="state")
                    observed = (
                        await owner.call("prove", slot="state")
                        if dial["status"] == "ok"
                        else dial
                    )
                    report(
                        "CONTENTION",
                        pid=owner.ssh_pid,
                        local_port=local,
                        listening_rows=listeners.count(owner.ssh_pid),
                        evidence=observed,
                    )
                    expect(await owner.call("close", slot="state"))
                assert await upgrades(relay) == before
            raise
        loser = next(owner for owner in contenders if owner is not winner)
        winner_ids = await identities(winner, "same-port-winner")
        await dead_child(loser)
        await graceful(loser)
        table = await asyncio.to_thread(probes.process_table)
        assert all(
            pid in table and table[pid][4] == start for pid, start in winner_ids.items()
        )
        assert await upgrades(relay) == before, (
            "losing contender admitted a relay upgrade"
        )
        first_winner = await first(winner, "bench", slot="state")
        report("PORT_RACE", phase="surviving-owner", response=first_winner)
        await graceful(winner)
        await released(winner, winner_ids)

        async with fixture.owner(local=local) as fresh:
            fresh_result = await first(fresh, "bench", slot="state")
            report("PORT_RACE", phase="fresh-owner", response=fresh_result)
            fresh_ids = await identities(fresh, "same-port-fresh-owner")
            await graceful(fresh)
            await released(fresh, fresh_ids)
        for result in (first_winner, fresh_result):
            proof(expect(result))
            retained(result["messages"], 0)


async def unavailable_route(fixture, *, denied):
    before_auth = (await fixture.ssh_log()).count(f"Accepted publickey for {USER}")
    async with (
        fixture.relay() as relay,
        fixture.owner(
            "denied" if denied else "allowed",
            remote=RELAY if denied else prior["REPLACEMENT_RELAY"],
        ) as owner,
    ):
        before = await upgrades(relay)
        reply = await first(owner, "bench", slot="state")
        report(
            "REFUSAL",
            phase="forwarding-denied" if denied else "relay-absent",
            response=reply,
        )
        expect(reply, "error")
        assert reply["code"] in {"proof_timeout", "proof_failed", "upgrade_failed"}, (
            reply
        )
        assert await upgrades(relay) == before
        # An unavailable relay/denied channel must not be mistaken for failed SSH login.
        authenticated = (await fixture.ssh_log()).count(
            f"Accepted publickey for {USER}"
        )
        assert authenticated == before_auth + 1
        owned = await identities(owner, "unavailable-route")
        await graceful(owner)
        await released(owner, owned)


async def barrier_stop(fixture, *, proved):
    async with fixture.relay() as relay, fixture.owner() as owner:
        before = await upgrades(relay)
        owned = await identities(owner, "barrier")
        expect(await first(owner, slot="state"))
        observation = await owner.call("prove", slot="state") if proved else None
        if observation is not None:
            report("BARRIER_OBSERVATION", response=observation)
        expect(await owner.call("stopchild"))
        phase = "after-proof" if proved else "before-proof"
        cmd = "upgrade" if proved else "prove"
        reply = None
        try:
            async with healthy_local_relay(phase, owner.port):
                reply = await owner.call(cmd, slot="state")
                report("REFUSAL", phase=phase, response=reply)
                expect(reply, "error", "child_exited")
                expect(
                    await owner.call("begin", slot="action"), "error", "child_exited"
                )
        except OSError as error:
            if reply is None:
                reply = await owner.call(cmd, slot="state")
                report(
                    "REFUSAL", phase=phase, response=reply, local_relay_error=str(error)
                )
            raise
        assert await upgrades(relay) == before, (
            "application bytes crossed a stopped-child barrier"
        )
        await graceful(owner)
        await released(owner, owned)
        if observation is not None:
            proof(expect(observation))


async def stop_in_connect_gap(fixture):
    async with fixture.owner() as owner:
        await asyncio.to_thread(
            probes.wait_until,
            lambda: not probes.can_bind(owner.port, False),
            "owned forward listener to reserve its disposable endpoint",
        )
        expect(
            await owner.call("begin", slot="state", pause_before_connect=True),
            "before_connect",
        )
        expect(await owner.call("stopchild"))
        # If bind fails, exiting the pipe sends EOF, so the paused command never dials.
        with local_listener(owner.port) as sink:
            try:
                reply = await owner.call("resume")
                report("REFUSAL", phase="pre-connect-gap", response=reply)
                expect(reply, "error", "child_exited")
            finally:
                await passive_sink(sink, "pre-connect-gap", expected_accepts=1)
        await graceful(owner)


async def retired(fixture):
    before = (await fixture.ssh_log()).count(f"Accepted publickey for {USER}")
    async with fixture.owner() as owner:
        deadline = asyncio.get_running_loop().time() + 5
        while (await fixture.ssh_log()).count(
            f"Accepted publickey for {USER}"
        ) != before + 1:
            assert asyncio.get_running_loop().time() < deadline, (
                "retired owner never authenticated"
            )
            await asyncio.sleep(0.025)
        delayed = {"cmd": "begin", "slot": "action"}
        expect(await owner.call("retire"))
        # The retired command is sent only after OpenSSH has been reaped by its owner.
        async with healthy_local_relay("retired-generation", owner.port):
            reply = await owner.request(**delayed)
            report("REFUSAL", phase="retired-generation", response=reply)
            expect(reply, "error", "retired")
            expect(await owner.call("prove", slot="action"), "error", "retired")
        await graceful(owner)


async def owner_death(fixture):
    async with fixture.relay() as relay, fixture.owner() as sentinel:
        expect(await first(sentinel, slot="state"))
        sentinel_ids = await identities(sentinel, "independent-owner")
        async with fixture.owner() as owner:
            assert owner.port != sentinel.port
            expect(await first(owner, slot="state"))
            owned = await identities(owner, "before-owner-death")
            before = await upgrades(relay)
            # Kill the exact subprocess we launched, never a process looked up by name/PID.
            owner.process.kill()
            assert await asyncio.wait_for(owner.process.wait(), 5) != 0
            cleanup = await asyncio.gather(
                released(owner, owned), return_exceptions=True
            )
        table = await asyncio.to_thread(probes.process_table)
        assert all(
            pid in table and table[pid][4] == start
            for pid, start in sentinel_ids.items()
        ), "independently launched fixture owner or OpenSSH died"
        expect(await sentinel.call("close", slot="state"))
        expect(await sentinel.call("begin", slot="state"))
        assert await upgrades(relay) == before, (
            "unproved sockets wrote relay application bytes"
        )
        await graceful(sentinel)
        await released(sentinel, sentinel_ids)
        if isinstance(cleanup[0], Exception):
            raise cleanup[0]


def p95(values):
    return sorted(values)[math.ceil(0.95 * len(values)) - 1]


def timing(samples, observations, failed, workload, warm_available):
    paired = [sample for sample in samples if "warm" in sample and "proved" in sample]
    counts = Counter(row["attempts"] for row in observations if "attempts" in row)
    proof_times = [row["proof_us"] for row in observations if "proof_us" in row]
    metrics = {
        "workload": workload,
        "pairs_requested": PAIRS,
        "pairs_complete": len(paired),
        "warm_available": warm_available,
        "failures": dict(failed),
        "total_failures": sum(failed.values()),
        "timeouts": sum(n for key, n in failed.items() if "timeout" in key),
        "observations_per_proof": dict(counts),
        "observations_total": sum(count * number for count, number in counts.items()),
        "post_connect_trials_completed": len(proof_times),
        "proof_median_us": statistics.median(proof_times) if proof_times else None,
        "proof_p95_us": p95(proof_times) if proof_times else None,
        "proof_max_us": max(proof_times, default=None),
        "search_max_us": max(
            (row["search_us"] for row in observations if "search_us" in row),
            default=None,
        ),
        "samples_valid": {
            mode: sum(mode in sample for sample in samples)
            for mode in ("warm", "proved")
        },
    }
    passing = True
    if paired:
        warm = [sample["warm"]["elapsed_us"] / 1_000 for sample in paired]
        proved = [sample["proved"]["elapsed_us"] / 1_000 for sample in paired]
        delta = [candidate - baseline for candidate, baseline in zip(proved, warm)]
        for label, quantile in (("median", statistics.median), ("p95", p95)):
            warm_ms, proved_ms, delta_ms = map(quantile, (warm, proved, delta))
            threshold = max(5.0, 0.25 * warm_ms)
            metrics.update(
                {
                    f"warm_{label}_ms": warm_ms,
                    f"proved_{label}_ms": proved_ms,
                    f"paired_delta_{label}_ms": delta_ms,
                    f"threshold_{label}_ms": threshold,
                }
            )
            passing &= delta_ms <= threshold
    report("METRICS", **metrics)
    assert len(paired) == PAIRS and not failed and passing, (
        f"{workload} Gate 1 measurement did not pass"
    )


async def benchmark(fixture, workload):
    samples, observations, failed = [], [], Counter()
    warm_available = False
    try:
        before_auth = (await fixture.ssh_log()).count(f"Accepted publickey for {USER}")
        async with fixture.relay() as relay, fixture.owner(control=True) as owner:
            init = await first(owner, "bench", slot="state")
            report("BENCH_INIT", workload=workload, response=init)
            warm_available = init["status"] == "ok"
            if warm_available:
                proof(init)
                retained(init["messages"], 0)
                if workload == "state":
                    expect(await owner.call("close", slot="state"))
            else:
                report(
                    "BENCH_INIT_REFUSED",
                    workload=workload,
                    response=init,
                    reason="warm comparator requires a strict successful upgrade on this owner",
                )
            owned = await identities(owner, "same-forward-" + workload)
            state_index = 0
            for index in range(PAIRS):
                sample = {}
                samples.append(sample)
                for warm in (False, True) if index % 2 == 0 else (True, False):
                    mode = "warm" if warm else "proved"
                    if warm and not warm_available:
                        continue
                    fields = {"slot": workload, "warm": warm}
                    armed = workload == "action" and warm_available
                    if workload == "action":
                        if armed:
                            await relay.control(
                                "arm", command=COMMAND, index=state_index + 1
                            )
                        fields["action"] = action_message()
                    try:
                        reply = await owner.call("bench", **fields)
                    except TimeoutError:
                        failed[f"{mode}:ipc_timeout"] += 1
                        raise  # a late reply would be misattributed on this pipe
                    if not warm:
                        observations.append(reply)
                    if reply["status"] != "ok":
                        key = f"{mode}:{reply.get('code', 'unknown')}"
                        failed[key] += 1
                        if failed[key] == 1:
                            report(
                                "BENCH_REFUSED",
                                workload=workload,
                                mode=mode,
                                response=reply,
                            )
                        if armed:
                            # The auto consumer cannot be reset without an actual dispatch.
                            raise AssertionError(
                                "forwarded action sample failed with consumer armed"
                            )
                        continue
                    if armed:
                        await relay.control("finish")
                        state_index += 1
                    expect(await owner.call("close", slot=workload))
                    try:
                        proof(reply, warm=warm)
                        assert reply["elapsed_us"] >= reply["proof_us"], reply
                        if workload == "action":
                            (message,) = reply["messages"]
                            action_result(message, True)
                        else:
                            retained(reply["messages"], state_index)
                    except (AssertionError, KeyError, ValueError):
                        failed[f"{mode}:invalid_sample"] += 1
                        if failed[f"{mode}:invalid_sample"] == 1:
                            report(
                                "BENCH_INVALID",
                                workload=workload,
                                mode=mode,
                                response=reply,
                            )
                    else:
                        sample[mode] = reply
            await graceful(owner)
            await released(owner, owned)
        assert (await fixture.ssh_log()).count(
            f"Accepted publickey for {USER}"
        ) == before_auth + 1
    except Exception:
        with suppress(AssertionError):
            timing(samples, observations, failed, workload, warm_available)
        raise
    timing(samples, observations, failed, workload, warm_available)


async def matrix(fixture, contention_only):
    rows = [
        ("independent-state-and-actions", lambda: positive(fixture)),
        *(
            (label + "-key", lambda label=label: auth_refusal(fixture, label))
            for label in ("unknown", "changed", "untrusted")
        ),
        ("local-port-conflict", lambda: port_conflict(fixture)),
        ("healthy-relay-port-conflict", lambda: port_conflict(fixture, healthy=True)),
        ("simultaneous-owners-same-port", lambda: simultaneous_owners(fixture)),
        ("forwarding-denied", lambda: unavailable_route(fixture, denied=True)),
        ("relay-absent", lambda: unavailable_route(fixture, denied=False)),
        ("stop-in-pre-connect-gap", lambda: stop_in_connect_gap(fixture)),
        ("stop-before-proof", lambda: barrier_stop(fixture, proved=False)),
        ("stop-after-proof", lambda: barrier_stop(fixture, proved=True)),
        ("retired-no-dial", lambda: retired(fixture)),
        ("owner-death-and-independent-owner", lambda: owner_death(fixture)),
        ("paired-state", lambda: benchmark(fixture, "state")),
        ("paired-action", lambda: benchmark(fixture, "action")),
    ]
    failed = []
    for label, run in rows:
        if contention_only and label != "simultaneous-owners-same-port":
            continue
        try:
            await run()
        except Exception as error:  # noqa: BLE001 - retain independent matrix rows after a failure
            failed.append(label)
            report("FAIL", row=label, error=f"{type(error).__name__}: {error}")
        else:
            report("PASS", row=label)
    report(
        "GATE_1",
        failed=failed,
        platform=sys.platform,
        unsupported=[
            "same-process generation replacement (harness supports permanent retirement only)"
        ],
    )
    return bool(failed)


def main():
    if not __debug__:
        raise RuntimeError("acceptance requires assertions (do not use Python -O)")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path, help="native owned-forward executable")
    parser.add_argument(
        "--contention-only",
        action="store_true",
        help="isolate the native bind decision without benchmarking",
    )
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    if probes.WINDOWS:
        if binary.suffix.lower() != ".exe":
            parser.error("native Windows Python requires the native .exe binary")
        keygen = Path(os.environ["SystemRoot"]) / "System32/OpenSSH/ssh-keygen.exe"
    elif sys.platform.startswith("linux"):
        keygen = shutil.which("ssh-keygen")
    else:
        parser.error("only Linux (including WSL) and native Windows are supported")
    if not keygen or not Path(keygen).is_file():
        parser.error("native OpenSSH ssh-keygen is required")
    image = container.build_image(SCRIPTS.parent / "services" / "relay")
    name = "imp-owned-" + secrets.token_hex(8)
    with TemporaryDirectory(prefix="imp-owned-") as temp:
        env = dict(os.environ, HOME=temp, USERPROFILE=temp, LC_ALL="C")
        for var in (
            "SSH_AUTH_SOCK",
            "SSH_ASKPASS",
            "SSH_ASKPASS_REQUIRE",
            "SSH_SK_PROVIDER",
        ):
            env.pop(var, None)
        docker(
            "run",
            "--rm",
            "--init",
            "--detach",
            "--name",
            name,
            "-p",
            f"{HOST}::{PRIMARY}",
            image,
            "sleep",
            "900",
        )
        try:
            fixture = Fixture(name, binary, Path(temp), env, keygen)
            raise SystemExit(asyncio.run(matrix(fixture, args.contention_only)))
        finally:
            docker("rm", "-f", name)


if __name__ == "__main__":
    main()
