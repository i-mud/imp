#!/usr/bin/env python3
"""Probe the real Imp relay through an already-running native guided forward.

Run `backend` inside the SSH fixture with stdin held open. After `READY 8787`,
run `client PORT` on the native host using the harness's forwarded port. After
client success, send `stop\n` to the backend and check both exit statuses.
Both sides use the current services/relay package and its websocket dependency.
"""

import argparse
import asyncio
from dataclasses import replace
import math
import sys
from time import perf_counter_ns

from websockets.asyncio.client import ClientConnection, connect

from imp_relay.protocol import (
    ActionResultMessage,
    Character,
    ConsumerReadyMessage,
    DispatchMessage,
    GameState,
    HelloMessage,
    ServerMessage,
    SnapshotMessage,
    StateContext,
    StatusMessage,
    Vital,
    decode_server_message,
    encode_action,
    encode_consumer,
    encode_consumer_result,
    encode_publish,
    encode_select,
)
from imp_relay.server import RelayServer

RELAY_NAME = "Imp guided acceptance fixture"
RELAY_PORT = 8787
CONTEXT = StateContext("guided_fixture", 7, 11)
ACTION_COUNT = 20


def command(index: int) -> str:
    return f"fixture-acknowledge-{index:02d}"


def state(completed: int) -> GameState:
    return GameState(
        Character("GuidedFixture", Vital(41 + completed, 100), None, None), None
    )


def socket(port: int, path: str) -> connect:
    return connect(
        f"ws://127.0.0.1:{port}/{path}", proxy=None, open_timeout=3, close_timeout=1
    )


def decode(frame: str | bytes) -> ServerMessage:
    assert isinstance(frame, str), "expected a text websocket frame"
    decoded = decode_server_message(frame)
    assert decoded.ok and decoded.value is not None, decoded.error
    return decoded.value


async def receive[T: ServerMessage](connection: ClientConnection, kind: type[T]) -> T:
    while True:
        message = decode(await connection.recv())
        if isinstance(message, kind):
            return message
        assert kind is SnapshotMessage and isinstance(message, StatusMessage), (
            kind,
            message,
        )


async def subscribe(connection: ClientConnection) -> SnapshotMessage:
    hello = await receive(connection, HelloMessage)
    assert hello.relay.name == RELAY_NAME, hello
    first = await receive(connection, SnapshotMessage)
    assert (first.seq, first.context, first.state) == (1, CONTEXT, state(0)), first
    return first


async def register(consumer: ClientConnection) -> None:
    await consumer.send(encode_consumer(CONTEXT))
    ready = await receive(consumer, ConsumerReadyMessage)
    assert ready.context == CONTEXT, ready


async def exercise_backend() -> None:
    async with socket(RELAY_PORT, "ingest") as producer:
        # Wait for the real relay to observe selection before registering the consumer.
        async with socket(RELAY_PORT, "state") as observer:
            await producer.send(encode_select(CONTEXT, state(0)))
            await subscribe(observer)
        seen_ids: set[str] = set()
        for index in range(1, ACTION_COUNT + 1):
            async with socket(RELAY_PORT, "action-consumer") as consumer:
                await register(consumer)
                if index == 1:
                    print(f"READY {RELAY_PORT}", flush=True)
                else:
                    # Fence the next action on this registration via the held state peer.
                    await producer.send(encode_publish(CONTEXT, state(index - 1)))
                dispatch = await receive(consumer, DispatchMessage)
                assert (dispatch.context, dispatch.command) == (
                    CONTEXT,
                    command(index),
                ), dispatch
                assert dispatch.id not in seen_ids, dispatch
                seen_ids.add(dispatch.id)
                await consumer.send(encode_consumer_result(dispatch.id, "forwarded"))
                await consumer.wait_closed()
        async with socket(RELAY_PORT, "action-consumer") as replacement:
            await register(replacement)
            try:
                replay = await asyncio.wait_for(replacement.recv(), timeout=0.25)
            except TimeoutError:
                pass
            else:
                raise AssertionError(
                    f"unexpected delivery to replacement consumer: {replay!r}"
                )
        await producer.send(encode_publish(CONTEXT, state(ACTION_COUNT)))
        # Don't stop the relay before the native state peer has received the update.
        async with asyncio.timeout(15):
            reader = asyncio.StreamReader()
            transport, _ = await asyncio.get_running_loop().connect_read_pipe(
                lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer
            )
            try:
                assert await reader.readline() == b"stop\n", (
                    "expected stop after the client succeeds"
                )
            finally:
                transport.close()


async def backend() -> None:
    relay = RelayServer(port=RELAY_PORT, relay_name=RELAY_NAME)
    await relay.start()
    try:
        async with asyncio.timeout(45):
            await exercise_backend()
    finally:
        await relay.close()
    print(
        f"PASS: {ACTION_COUNT} exact synthetic commands delivered once; no replay to replacement consumer",
        flush=True,
    )


async def action(
    port: int, context: StateContext, text: str
) -> tuple[ActionResultMessage, float]:
    started = perf_counter_ns()
    async with socket(port, "action") as connection:
        await connection.send(encode_action(context, text))
        frame = await connection.recv()
        elapsed_ms = (perf_counter_ns() - started) / 1_000_000
        result = decode(frame)
        assert isinstance(result, ActionResultMessage), result
        return result, elapsed_ms


async def client(port: int) -> None:
    samples: list[float] = []
    started = perf_counter_ns()
    async with asyncio.timeout(30), socket(port, "state") as subscriber:
        first = await subscribe(subscriber)
        retained_ms = (perf_counter_ns() - started) / 1_000_000
        assert first.context is not None
        wrong = replace(first.context, connection=first.context.connection + 1)
        rejected, _ = await action(port, wrong, "fixture-must-not-dispatch")
        assert (rejected.status, rejected.detail) == (
            "rejected",
            "context is not active",
        ), rejected
        for index in range(1, ACTION_COUNT + 1):
            forwarded, elapsed_ms = await action(port, first.context, command(index))
            assert (forwarded.status, forwarded.detail) == ("forwarded", None), (
                forwarded
            )
            samples.append(elapsed_ms)
            update = await receive(subscriber, SnapshotMessage)
            assert (update.seq, update.context, update.state) == (
                first.seq + index,
                first.context,
                state(index),
            ), update
    samples.sort()
    n = len(samples)
    p50, p95 = (samples[math.ceil(percent * n / 100) - 1] for percent in (50, 95))
    print(
        f"METRICS retained_snapshot_ms={retained_ms:.3f} action_n={n} action_p50_ms={p50:.3f} action_p95_ms={p95:.3f}"
    )
    print(
        "PASS: held /state survived transient /action; wrong context rejected and exact context forwarded"
    )


def main() -> None:
    if not __debug__:
        raise RuntimeError(
            "run the relay acceptance probe with Python assertions enabled"
        )
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    modes.add_parser("backend")
    modes.add_parser("client").add_argument("port", type=int)
    args = parser.parse_args()
    if args.mode == "backend":
        asyncio.run(backend())
    else:
        if args.port not in range(1, 65536):
            parser.error("port must be in 1..65535")
        asyncio.run(client(args.port))


if __name__ == "__main__":
    main()
