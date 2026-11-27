#!/usr/bin/env python3
"""Real relay and synthetic WebSocket adapter for the isolated persistent SSH probe.

Install the current services/relay package on both hosts (Python 3.12+), and keep
this script beside test-guided-relay.py. `backend` and `client PORT` keep the
29.3a CLI. For the persistent experiment start `serve [--port 8787] [--route A]`
with stdin held open. It prints READY PORT, then uses JSONL on stdin/stdout.
Stop with {"id":1,"op":"stop"} and require its terminal PASS and zero exit.

For a local, already-configured disposable sshd fixture, run `compare --authority
BINARY --config CONFIG.json --legacy-port PORT` (warm ssh -L already listening),
or `adversarial --authority BINARY --config CONFIG.json --replacement CONFIG.json
--legacy-port PORT` (old port now free), or `same-endpoint --authority BINARY
--config CONFIG.json` (a second relay replaces the first listener). Config maps
the authority establish fields host, port, username, server_key, private_key,
relay_port; test relays bind at those destination ports. Test failures exit
nonzero; compare emits a METRICS JSON line and PASS. Container drivers can
instead use the functions with already-spawned processes.
"""

import argparse
import asyncio
import json
import runpy
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import asdict, replace
from math import ceil
from pathlib import Path
from time import perf_counter_ns

from imp_relay.protocol import (
    ActionResultMessage,
    ConsumerReadyMessage,
    DispatchMessage,
    HelloMessage,
    SnapshotMessage,
    StatusMessage,
    encode_action,
    encode_consumer,
    encode_consumer_result,
    encode_publish,
    encode_select,
)
from imp_relay.server import RelayServer
from websockets.asyncio.server import ServerConnection, serve

GUIDED = runpy.run_path(str(Path(__file__).with_name("test-guided-relay.py")))
CONTEXT = GUIDED["CONTEXT"]
ACTION_COUNT = GUIDED["ACTION_COUNT"]
command, state, socket, receive, decode = (
    GUIDED[name] for name in ("command", "state", "socket", "receive", "decode")
)


def relay_name(route):
    return f"Imp persistent acceptance fixture {route}"


def reply(message):
    print(json.dumps(message, separators=(",", ":")), flush=True)


def elapsed(started):
    return (perf_counter_ns() - started) / 1_000_000


class JsonLines:
    """One reader per process; multiple outstanding IDs, collected in any order."""

    def __init__(self, process):
        assert process.stdin is not None and process.stdout is not None
        self.process = process
        self.next_id = 0
        self.pending = []

    async def line(self):
        raw = await asyncio.wait_for(self.process.stdout.readline(), 12)
        assert raw, (
            f"fixture subprocess ended before reply (exit={self.process.returncode})"
        )
        return raw.decode().rstrip("\r\n")

    async def ready(self):
        line = await self.line()
        assert line.startswith("READY "), line
        return int(line.split()[1])

    async def receive(self, predicate):
        async with asyncio.timeout(12):
            for index, message in enumerate(self.pending):
                if predicate(message):
                    return self.pending.pop(index)
            while True:
                message = json.loads(await self.line())
                if predicate(message):
                    return message
                self.pending.append(message)

    async def send(self, *requests):
        ids = []
        frames = []
        for op, fields in requests:
            self.next_id += 1
            ids.append(self.next_id)
            frames.append(
                json.dumps({"id": self.next_id, "op": op, **fields}).encode() + b"\n"
            )
        self.process.stdin.write(b"".join(frames))
        await self.process.stdin.drain()
        return ids

    async def start(self, op, **fields):
        (request_id,) = await self.send((op, fields))
        return request_id

    async def result(self, request_id):
        return await self.receive(
            lambda item: item.get("id") == request_id and "status" in item
        )

    async def call(self, op, **fields):
        return await self.result(await self.start(op, **fields))

    async def event(self, name, predicate=None):
        return await self.receive(
            lambda item: (
                item.get("event") == name and (predicate is None or predicate(item))
            )
        )


def ok(message):
    assert message["status"] == "ok", message
    return message


class FixtureRelay(RelayServer):
    def __init__(self, *, port, relay_name):
        super().__init__(port=port, relay_name=relay_name)
        self.upgrades = {"/state": 0, "/action": 0}
        self.stall_armed = False
        self.upgrade_waiting = asyncio.Event()
        self.release_upgrade = asyncio.Event()

    async def _process_request(self, connection, request):
        if request.path in self.upgrades:
            self.upgrades[request.path] += 1
        if request.path == "/action" and self.stall_armed:
            self.stall_armed = False
            self.upgrade_waiting.set()
            await self.release_upgrade.wait()
        return await super()._process_request(connection, request)


async def fixture_control(relay, route):
    async with socket(relay.port, "ingest") as producer:
        async with socket(relay.port, "state") as observer:
            await producer.send(encode_select(CONTEXT, state(0)))
            # Selection retains a snapshot; a publish is what marks the feed live.
            await producer.send(encode_publish(CONTEXT, state(0)))
            assert (await receive(observer, HelloMessage)).relay.name == relay_name(
                route
            )
            await snapshot(observer, 0)
            async with asyncio.timeout(3):
                while (await receive(observer, StatusMessage)).feed != "live":
                    pass
        print(f"READY {relay.port}", flush=True)
        reader = asyncio.StreamReader()
        transport, _ = await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer
        )
        consumer = None
        dispatch = None
        auto = None
        seen_ids = set()

        async def take(expected):
            message = await receive(consumer, DispatchMessage)
            assert (message.context, message.command) == (CONTEXT, expected), message
            assert message.id not in seen_ids, message
            seen_ids.add(message.id)
            return message

        async def answer(message, status):
            if status is not None:
                await consumer.send(encode_consumer_result(message.id, status))
                await consumer.wait_closed()
            else:
                await consumer.close()

        async def acknowledge(expected, index):
            await answer(await take(expected), "forwarded")
            await producer.send(encode_publish(CONTEXT, state(index)))

        try:
            while raw := await reader.readline():
                request = json.loads(raw)
                op = request["op"]
                result = {"id": request["id"], "status": "ok"}
                async with asyncio.timeout(8):
                    if op == "arm":
                        assert consumer is None, "previous consumer still armed"
                        consumer = await socket(relay.port, "action-consumer")
                        await consumer.send(encode_consumer(CONTEXT))
                        assert (
                            await receive(consumer, ConsumerReadyMessage)
                        ).context == CONTEXT
                        if "index" in request:
                            auto = asyncio.create_task(
                                acknowledge(request["command"], request["index"])
                            )
                    elif op == "take":
                        assert (
                            consumer is not None and auto is None and dispatch is None
                        )
                        dispatch = await take(request["command"])
                    elif op == "finish":
                        assert consumer is not None
                        if auto is not None:
                            await auto
                        else:
                            assert dispatch is not None
                            await answer(dispatch, request["result"])
                        consumer = auto = dispatch = None
                    elif op == "quiet":
                        assert (
                            consumer is not None and auto is None and dispatch is None
                        )
                        try:
                            frame = await asyncio.wait_for(
                                consumer.recv(), timeout=0.25
                            )
                        except TimeoutError:
                            pass
                        else:
                            raise AssertionError(
                                f"unexpected adapter delivery: {frame!r}"
                            )
                        await consumer.close()
                        consumer = None
                    elif op == "stall_action":
                        assert (
                            not relay.stall_armed and not relay.upgrade_waiting.is_set()
                        )
                        relay.stall_armed = True
                    elif op == "await_stalled":
                        await relay.upgrade_waiting.wait()
                    elif op == "release_action":
                        assert relay.upgrade_waiting.is_set()
                        relay.release_upgrade.set()
                    elif op == "drain_listener":
                        # Keep accepted WS peers alive while freeing the destination port.
                        server = relay._server
                        assert server is not None
                        server.close(close_connections=False)
                        while server.is_serving():
                            await asyncio.sleep(0)
                    elif op == "publish":
                        await producer.send(
                            encode_publish(CONTEXT, state(request["index"]))
                        )
                    elif op == "upgrades":
                        result["upgrades"] = relay.upgrades
                    else:
                        assert op == "stop" and consumer is None, request
                    reply(result)
                    if op == "stop":
                        return len(seen_ids)
            raise AssertionError("expected stop before backend stdin EOF")
        finally:
            relay.release_upgrade.set()
            if auto is not None:
                auto.cancel()
                await asyncio.gather(auto, return_exceptions=True)
            if consumer is not None:
                await consumer.close()
            transport.close()


async def backend(port, route):
    relay = FixtureRelay(port=port, relay_name=relay_name(route))
    await relay.start()
    try:
        count = await fixture_control(relay, route)
    finally:
        await relay.close()
    print(
        f"PASS backend {route}: {count} distinct exact adapter dispatches", flush=True
    )


def check_snapshot(message, index):
    assert (message.seq, message.context, message.state) == (
        index + 1,
        CONTEXT,
        state(index),
    ), message
    return message


async def snapshot(connection, index):
    return check_snapshot(await receive(connection, SnapshotMessage), index)


async def native_snapshot(native, state_id, index):
    event = await native.event("state", lambda item: item.get("id") == state_id)
    check_snapshot(decode(json.dumps(event["snapshot"])), index)
    context = event["context"]
    assert context is not None and all(
        context[key] == value for key, value in asdict(CONTEXT).items()
    ), event
    return context


async def live_native(native, generation):
    started = perf_counter_ns()
    opened = ok(await native.call("state", generation=generation))
    context = await native_snapshot(native, opened["id"], 0)
    retained_ms = elapsed(started)
    assert context["generation"] == generation, context
    await native.event(
        "status",
        lambda item: (
            item.get("generation") == generation and item.get("feed") == "live"
        ),
    )
    return opened["id"], context, retained_ms


def action_fields(context, text, wire_context=CONTEXT):
    return {"context": context, "action": json.loads(encode_action(wire_context, text))}


def verify_result(message, status, detail=None):
    if isinstance(message, dict):
        message = decode(json.dumps(ok(message)["result"]))
    assert isinstance(message, ActionResultMessage), message
    assert (message.status, message.detail) == (status, detail), message


def verify_error(message, delivery):
    assert (message["status"], message.get("delivery")) == ("error", delivery), message


async def compare(
    backend, native, config, legacy_port, sample=None, native_first=False
):
    """Pair warm -L vs persistent by round, alternating the order, with held state on both."""
    async with asyncio.timeout(60):
        started = perf_counter_ns()
        generation = ok(await native.call("establish", **config))["generation"]
        establish_ms = elapsed(started)
        state_id, context, native_retained_ms = await live_native(native, generation)
        started = perf_counter_ns()
        async with socket(legacy_port, "state") as warm:
            assert (await receive(warm, HelloMessage)).relay.name == relay_name("A")
            await snapshot(warm, 0)
            warm_retained_ms = elapsed(started)
            if sample is not None:
                sample()
            wrong = replace(CONTEXT, connection=CONTEXT.connection + 1)
            ok(await backend.call("arm"))
            rejected, _ = await GUIDED["action"](
                legacy_port, wrong, "persistent-wrong-context"
            )
            verify_result(rejected, "rejected", "context is not active")
            verify_error(
                await native.call(
                    "action",
                    **action_fields(context, "persistent-wrong-context", wrong),
                ),
                "not-dispatched",
            )
            verify_error(
                await native.call(
                    "action",
                    **action_fields(
                        {**context, "connection": wrong.connection},
                        "persistent-wrong-handle",
                        wrong,
                    ),
                ),
                "not-dispatched",
            )
            ok(await backend.call("quiet"))
            samples = {"warm": [], "native": []}
            index = 0
            for pair in range(ACTION_COUNT):
                order = (
                    ("native", "warm")
                    if native_first ^ bool(pair % 2)
                    else ("warm", "native")
                )
                for method in order:
                    index += 1
                    text = command(index)
                    ok(await backend.call("arm", command=text, index=index))
                    started = perf_counter_ns()
                    if method == "warm":
                        result, elapsed_ms = await GUIDED["action"](
                            legacy_port, CONTEXT, text
                        )
                    else:
                        result = await native.call(
                            "action", **action_fields(context, text)
                        )
                        elapsed_ms = elapsed(started)
                    samples[method].append(elapsed_ms)
                    verify_result(result, "forwarded")
                    ok(await backend.call("finish"))
                    await snapshot(warm, index)
                    context = await native_snapshot(native, state_id, index)
            ok(await backend.call("arm"))
            ok(await backend.call("quiet"))
    metrics = {
        "native_establish_ms": establish_ms,
        "warm_retained_ms": warm_retained_ms,
        "native_retained_ms": native_retained_ms,
        "action_n": ACTION_COUNT,
    }
    for method, timings in samples.items():
        ordered = sorted(timings)
        metrics[f"{method}_samples_ms"] = timings
        for percent in (50, 95):
            metrics[f"{method}_p{percent}_ms"] = ordered[
                ceil(percent * len(ordered) / 100) - 1
            ]
    print("METRICS " + json.dumps(metrics, separators=(",", ":")), flush=True)
    print(
        "PASS compare: held WS state, wrong-context rejection, 20 exact once-only actions per route, no replay to replacement consumer",
        flush=True,
    )
    return metrics


@asynccontextmanager
async def old_port_competitor(port):
    requests = []

    class ProbeConnection(ServerConnection):
        def connection_made(self, transport):
            requests.append(transport.get_extra_info("peername"))
            super().connection_made(transport)

    async def reject(connection):
        await connection.close()

    async with serve(
        reject, "127.0.0.1", port, create_connection=ProbeConnection
    ) as competitor:
        assert competitor.sockets
        yield requests


async def adversarial(backend_a, backend_b, native, config_a, config_b, old_port):
    """Verify explicit replacement and shutdown across real, independent relay routes."""
    assert config_a["relay_port"] != config_b["relay_port"]
    async with asyncio.timeout(35), old_port_competitor(old_port) as requests:
        generation_a = ok(await native.call("establish", **config_a))["generation"]
        _, context_a, _ = await live_native(native, generation_a)
        ok(await backend_a.call("arm"))
        unknown = await native.start(
            "action", **action_fields(context_a, "persistent-unknown")
        )
        ok(await backend_a.call("take", command="persistent-unknown"))
        ok(await backend_a.call("finish", result=None))
        verify_result(
            await native.result(unknown),
            "unknown",
            "consumer disconnected after dispatch",
        )
        ok(await backend_a.call("arm"))
        ok(await backend_a.call("quiet"))

        ok(await backend_a.call("arm"))
        delayed = await native.start(
            "action", **action_fields(context_a, "persistent-delayed-a")
        )
        ok(await backend_a.call("take", command="persistent-delayed-a"))
        ok(await backend_a.call("stall_action"))
        queued = await native.start(
            "action", **action_fields(context_a, "persistent-queued-a")
        )
        ok(await backend_a.call("await_stalled"))
        generation_b = ok(await native.call("replace", **config_b))["generation"]
        assert generation_b != generation_a
        await native.event(
            "invalidated", lambda item: item["generation"] == generation_a
        )
        verify_error(await native.result(queued), "not-dispatched")
        ok(await backend_a.call("release_action"))
        stale_state = await native.call("state", generation=generation_a)
        assert stale_state["status"] == "error", stale_state
        copied = {**context_a, "generation": generation_b}
        verify_error(
            await native.call(
                "action", **action_fields(copied, "persistent-before-fresh-state")
            ),
            "not-dispatched",
        )
        assert ok(await backend_b.call("upgrades"))["upgrades"] == {
            "/state": 1,
            "/action": 0,
        }
        _, context_b, _ = await live_native(native, generation_b)
        assert all(context_a[key] == context_b[key] for key in asdict(CONTEXT))
        ok(await backend_b.call("arm"))
        verify_error(
            await native.call(
                "action", **action_fields(context_a, "persistent-stale-a")
            ),
            "not-dispatched",
        )
        current = await native.start(
            "action", **action_fields(context_b, "persistent-current-b")
        )
        ok(await backend_b.call("take", command="persistent-current-b"))
        ok(await backend_a.call("finish", result="rejected"))
        verify_error(await native.result(delayed), "unknown")
        ok(await backend_b.call("finish", result="forwarded"))
        verify_result(await native.result(current), "forwarded")
        for relay in (backend_a, backend_b):
            ok(await relay.call("arm"))
            ok(await relay.call("quiet"))

        ok(await backend_b.call("arm"))
        closing = await native.start(
            "action", **action_fields(context_b, "persistent-sent-before-shutdown")
        )
        ok(await backend_b.call("take", command="persistent-sent-before-shutdown"))
        shutdown, _ = await native.send(
            ("shutdown", {}),
            ("action", action_fields(context_b, "persistent-after-shutdown")),
        )
        ok(await native.result(shutdown))
        verify_error(await native.result(closing), "unknown")
        assert await asyncio.wait_for(native.process.wait(), 10) == 0
        ok(await backend_b.call("finish", result="forwarded"))
        ok(await backend_b.call("arm"))
        ok(await backend_b.call("quiet"))
        assert ok(await backend_a.call("upgrades"))["upgrades"] == {
            "/state": 2,
            "/action": 3,
        }
        assert ok(await backend_b.call("upgrades"))["upgrades"] == {
            "/state": 2,
            "/action": 2,
        }
        assert not requests, requests
    print(
        "PASS adversarial: old-port decoy unused; queued handshake not dispatched; sent/unknown without replay; matching context on replaced route needs a new state/action generation; late result isolated; shutdown rejects",
        flush=True,
    )


async def same_endpoint(backend_a, native, config_a, launch_replacement):
    """Expose the attribution limit of a new direct-tcpip to the same SSH destination."""
    async with asyncio.timeout(30):
        generation = ok(await native.call("establish", **config_a))["generation"]
        state_id, context, _ = await live_native(native, generation)
        ok(await backend_a.call("drain_listener"))
        backend_b = await launch_replacement()
        ok(await backend_a.call("publish", index=1))
        context_a = await native_snapshot(native, state_id, 1)
        assert context_a["generation"] == context["generation"]
        ok(await backend_b.call("arm"))
        sent = await native.start(
            "action", **action_fields(context_a, "persistent-same-endpoint")
        )
        ok(await backend_b.call("take", command="persistent-same-endpoint"))
        ok(await backend_b.call("finish", result="forwarded"))
        verify_result(await native.result(sent), "forwarded")
        ok(await backend_a.call("publish", index=2))
        assert (await native_snapshot(native, state_id, 2))["generation"] == generation
        ok(await native.call("shutdown"))
    print(
        "LIMIT same endpoint: held state remained on A, but a fresh action reached B on the same SSH session and destination with an identical relay context",
        flush=True,
    )


@asynccontextmanager
async def child(*argv):
    process = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE
    )
    try:
        yield JsonLines(process)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def stop(backend):
    ok(await backend.call("stop"))
    line = await backend.line()
    assert line.startswith("PASS backend "), line
    assert await asyncio.wait_for(backend.process.wait(), 10) == 0
    print(line, flush=True)


async def standalone(args):
    config = json.loads(args.config.read_text())
    async with AsyncExitStack() as stack:

        async def launch(route, port):
            fixture = await stack.enter_async_context(
                child(
                    sys.executable,
                    "-u",
                    __file__,
                    "serve",
                    "--port",
                    str(port),
                    "--route",
                    route,
                )
            )
            assert await fixture.ready() == port
            return fixture

        backend_a = await launch("A", config["relay_port"])
        native = await stack.enter_async_context(
            child(str(args.authority.resolve()), "authority")
        )
        if args.mode == "compare":
            await compare(backend_a, native, config, args.legacy_port)
            ok(await native.call("shutdown"))
        elif args.mode == "adversarial":
            replacement = json.loads(args.replacement.read_text())
            backend_b = await launch("B", replacement["relay_port"])
            await adversarial(
                backend_a, backend_b, native, config, replacement, args.legacy_port
            )
            await stop(backend_b)
        else:
            replacement = None

            async def launch_replacement():
                nonlocal replacement
                replacement = await launch("B", config["relay_port"])
                return replacement

            await same_endpoint(backend_a, native, config, launch_replacement)
            await stop(replacement)
        assert await asyncio.wait_for(native.process.wait(), 10) == 0
        await stop(backend_a)


def main():
    if not __debug__:
        raise RuntimeError("run the relay acceptance probe with assertions enabled")
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    modes.add_parser("backend")
    modes.add_parser("client").add_argument("port", type=int)
    fixture = modes.add_parser("serve")
    fixture.add_argument("--port", type=int, default=8787)
    fixture.add_argument("--route", choices=("A", "B"), default="A")
    for mode in ("compare", "adversarial", "same-endpoint"):
        driver = modes.add_parser(mode)
        driver.add_argument("--authority", type=Path, required=True)
        driver.add_argument("--config", type=Path, required=True)
        if mode != "same-endpoint":
            driver.add_argument("--legacy-port", type=int, required=True)
        if mode == "adversarial":
            driver.add_argument("--replacement", type=Path, required=True)
    args = parser.parse_args()
    for port in (getattr(args, "port", None), getattr(args, "legacy_port", None)):
        if port is not None and port not in range(1, 65536):
            parser.error("port must be in 1..65535")
    if args.mode == "backend":
        asyncio.run(GUIDED["backend"]())
    elif args.mode == "client":
        asyncio.run(GUIDED["client"](args.port))
    elif args.mode == "serve":
        asyncio.run(backend(args.port, args.route))
    else:
        asyncio.run(standalone(args))


if __name__ == "__main__":
    main()
