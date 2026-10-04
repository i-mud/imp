from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.asyncio.server import ServerConnection
from websockets.exceptions import ConnectionClosed

from imp_relay import server as server_module
from imp_relay.protocol import (
    Character,
    GameState,
    StateContext,
    Vital,
    encode_publish,
    encode_select,
    encode_text,
)
from imp_relay.server import RelayServer

CONTEXT = StateContext("session1", 1, 1)
OTHER_CONTEXT = StateContext("session1", 2, 1)


def _state(name: str) -> GameState:
    return GameState(character=Character(name, Vital(9, 10), None, None), target=None)


async def _receive(connection: ClientConnection, timeout: float = 1.0) -> dict[str, Any]:
    frame = await asyncio.wait_for(connection.recv(), timeout)
    assert isinstance(frame, str)
    return cast(dict[str, Any], json.loads(frame))


async def _handshake(connection: ClientConnection) -> list[dict[str, Any]]:
    messages = [await _receive(connection) for _ in range(3)]
    assert [message["type"] for message in messages] == ["hello", "snapshot", "status"]
    return messages


async def _wait_for(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(1.0):
        while not predicate():
            await asyncio.sleep(0)


async def _select_live(relay: RelayServer, producer: ClientConnection) -> None:
    await producer.send(encode_select(CONTEXT, _state("Selected")))
    await producer.send(encode_publish(CONTEXT, _state("Live")))
    await _wait_for(lambda: relay._last_feed == "live")


@asynccontextmanager
async def _capture_loop_errors() -> AsyncIterator[None]:
    loop = asyncio.get_running_loop()
    errors: list[dict[str, Any]] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: errors.append(context))
    try:
        yield
    finally:
        await asyncio.sleep(0)
        loop.set_exception_handler(previous)
    assert not errors


def _track_writer_tasks(monkeypatch: pytest.MonkeyPatch) -> list[asyncio.Task[None]]:
    original_create_task = asyncio.create_task
    writers: list[asyncio.Task[None]] = []

    def create_task(
        coro: Coroutine[Any, Any, None], *, name: str | None = None, context: Any = None
    ) -> asyncio.Task[None]:
        task = original_create_task(coro, name=name, context=context)
        if getattr(coro, "__qualname__", "") == "RelayServer._write_subscriber":
            writers.append(task)
        return task

    monkeypatch.setattr(asyncio, "create_task", create_task)
    return writers


def _assert_writers_done(writers: list[asyncio.Task[None]]) -> None:
    assert writers
    assert all(task.done() for task in writers)
    assert all(task.cancelled() or task.exception() is None for task in writers)


def _block_first_send(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[asyncio.Event, asyncio.Event, list[ServerConnection], list[str | bytes]]:
    original_send = cast(Callable[..., Awaitable[None]], ServerConnection.send)
    entered = asyncio.Event()
    release = asyncio.Event()
    connections: list[ServerConnection] = []
    payloads: list[str | bytes] = []

    async def send(connection: ServerConnection, message: Any, *args: Any, **kwargs: Any) -> None:
        if connection.request is not None and connection.request.path == "/state" and not connections:
            connections.append(connection)
            payloads.append(message)
            entered.set()
            await release.wait()
        else:
            await original_send(connection, message, *args, **kwargs)

    monkeypatch.setattr(ServerConnection, "send", send)
    return entered, release, connections, payloads


def _block_then_fail_send(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[asyncio.Event, asyncio.Event, list[ServerConnection]]:
    original_send = cast(Callable[..., Awaitable[None]], ServerConnection.send)
    entered = asyncio.Event()
    release = asyncio.Event()
    connections: list[ServerConnection] = []

    async def send(connection: ServerConnection, message: Any, *args: Any, **kwargs: Any) -> None:
        if connection.request is not None and connection.request.path == "/state":
            connections.append(connection)
            if len(connections) == 1:
                entered.set()
                await release.wait()
                return
            if len(connections) == 2:
                raise ConnectionClosed(None, None)
        await original_send(connection, message, *args, **kwargs)

    monkeypatch.setattr(ServerConnection, "send", send)
    return entered, release, connections


def test_subscriber_queue_capacity_overflow_and_healthy_handshake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with _capture_loop_errors():
            relay = RelayServer(port=0)
            await relay.start()
            writers = _track_writer_tasks(monkeypatch)
            entered, release, server_connections, payloads = _block_first_send(monkeypatch)
            slow: ClientConnection | None = None
            try:
                async with connect(f"ws://127.0.0.1:{relay.port}/ingest") as producer:
                    await _select_live(relay, producer)
                    slow = await connect(f"ws://127.0.0.1:{relay.port}/state")
                    await asyncio.wait_for(entered.wait(), 1.0)
                    assert json.loads(cast(str, payloads[0]))["type"] == "hello"
                    queue = relay._subscribers[server_connections[0]]
                    assert queue.maxsize == 16
                    assert queue.qsize() == 2  # Snapshot and status wait behind the in-flight hello.

                    async with connect(f"ws://127.0.0.1:{relay.port}/state") as healthy:
                        handshake = await _handshake(healthy)
                        assert handshake[1]["seq"] == 2
                        assert handshake[2]["feed"] == "live"
                        assert len(writers) == 2 and not writers[0].done()

                        for index in range(14):
                            await producer.send(encode_text(CONTEXT, 100 + index, f"event-{index}"))
                        await _wait_for(queue.full)
                        assert queue.qsize() == 16

                        await producer.send(encode_text(CONTEXT, 114, "overflow"))
                        await asyncio.wait_for(slow.wait_closed(), 1.0)
                        await _wait_for(lambda: server_connections[0] not in relay._subscribers)
                        await _wait_for(lambda: writers[0].done())
                        assert writers[0].cancelled()

                        events = [await _receive(healthy) for _ in range(15)]
                        assert [event["text"] for event in events] == [
                            *(f"event-{index}" for index in range(14)),
                            "overflow",
                        ]
                    await _wait_for(lambda: writers[1].done())
            finally:
                release.set()
                await relay.close()
                if slow is not None:
                    await slow.wait_closed()
            _assert_writers_done(writers)

    asyncio.run(scenario())


def test_blocked_writer_isolated_from_watchdog_and_transient_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with _capture_loop_errors():
            now = [100.0]
            relay = RelayServer(port=0, stale_after=1.0, clock=lambda: now[0])
            await relay.start()
            writers = _track_writer_tasks(monkeypatch)
            entered, release, server_connections, _ = _block_first_send(monkeypatch)
            slow: ClientConnection | None = None
            try:
                async with connect(f"ws://127.0.0.1:{relay.port}/ingest") as producer:
                    await _select_live(relay, producer)
                    slow = await connect(f"ws://127.0.0.1:{relay.port}/state")
                    await asyncio.wait_for(entered.wait(), 1.0)
                    async with (
                        connect(f"ws://127.0.0.1:{relay.port}/state") as first,
                        connect(f"ws://127.0.0.1:{relay.port}/state") as second,
                    ):
                        first_handshake, second_handshake = await _handshake(first), await _handshake(second)
                        assert first_handshake[2]["feed"] == second_handshake[2]["feed"] == "live"
                        before_text = relay.state.snapshot()
                        assert before_text is not None and before_text.seq == 2
                        now[0] = 100.75

                        await producer.send(encode_text(OTHER_CONTEXT, 200, "wrong world"))
                        await producer.send(encode_text(CONTEXT, 201, "The troll snarls."))
                        for peer in (first, second):
                            prompt = await _receive(peer)
                            assert prompt["type"] == "text"
                            assert prompt["context"] == {
                                "session": "session1",
                                "foreground": 1,
                                "connection": 1,
                            }
                            assert prompt["at"] == 201 and prompt["text"] == "The troll snarls."
                        assert relay.state.snapshot() == before_text

                        now[0] = 101.01
                        for peer in (first, second):
                            stale = await _receive(peer, timeout=2.0)
                            assert (stale["type"], stale["feed"]) == ("status", "stale")
                        assert server_connections[0] in relay._subscribers
                        assert not writers[0].done()

                        now[0] = 103.0
                        await producer.send(encode_publish(CONTEXT, _state("Fresh again")))
                        for peer in (first, second):
                            snapshot, live = await _receive(peer), await _receive(peer)
                            assert snapshot["type"] == "snapshot"
                            assert live["type"] == "status" and live["feed"] == "live"
                        fresh = relay.state.snapshot()
                        assert fresh is not None and fresh.seq == 3

                        await producer.send(encode_text(CONTEXT, 203, "A second prompt."))
                        for peer in (first, second):
                            prompt = await _receive(peer)
                            assert (prompt["type"], prompt["text"]) == ("text", "A second prompt.")
                        assert relay.state.snapshot() == fresh

                        async with connect(f"ws://127.0.0.1:{relay.port}/state") as reconnected:
                            handshake = await _handshake(reconnected)
                            assert handshake[1]["seq"] == fresh.seq
                            assert handshake[2]["feed"] == "live"
                            await producer.send(encode_text(CONTEXT, 204, "Only this new prompt."))
                            marker = await _receive(reconnected)
                            assert (marker["type"], marker["text"]) == ("text", "Only this new prompt.")
                            assert relay.state.snapshot() == fresh
                            for peer in (first, second):
                                assert (await _receive(peer))["text"] == "Only this new prompt."

                        slow.transport.abort()
                        await asyncio.wait_for(slow.wait_closed(), 1.0)
                        await _wait_for(lambda: server_connections[0] not in relay._subscribers)
                        await _wait_for(lambda: writers[0].done())
                        assert writers[0].cancelled()
            finally:
                release.set()
                await relay.close()
                if slow is not None:
                    await slow.wait_closed()
            _assert_writers_done(writers)

    asyncio.run(scenario())


def test_blocked_send_deadline_retires_subscriber(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _capture_loop_errors():
            monkeypatch.setattr(server_module, "_SUBSCRIBER_SEND_TIMEOUT", 0.02)
            relay = RelayServer(port=0)
            await relay.start()
            writers = _track_writer_tasks(monkeypatch)
            entered, release, connections, payloads = _block_first_send(monkeypatch)
            try:
                subscriber = await connect(f"ws://127.0.0.1:{relay.port}/state")
                await asyncio.wait_for(entered.wait(), 1.0)
                assert json.loads(cast(str, payloads[0]))["type"] == "hello"
                await asyncio.wait_for(subscriber.wait_closed(), 1.0)
                await _wait_for(lambda: connections[0] not in relay._subscribers)
                await _wait_for(lambda: writers[0].done())
                assert writers[0].exception() is None
            finally:
                release.set()
                await relay.close()
            _assert_writers_done(writers)

    asyncio.run(scenario())


def test_connection_closed_writer_and_relay_close_cancel_all_peers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with _capture_loop_errors():
            relay = RelayServer(port=0)
            relay.state.apply_select(CONTEXT, _state("Retained"), now=0.0)
            await relay.start()
            writers = _track_writer_tasks(monkeypatch)
            entered, release, server_connections = _block_then_fail_send(monkeypatch)
            slow: ClientConnection | None = None
            failed: ClientConnection | None = None
            healthy: ClientConnection | None = None
            retiring: ClientConnection | None = None
            try:
                slow = await connect(f"ws://127.0.0.1:{relay.port}/state")
                await asyncio.wait_for(entered.wait(), 1.0)
                failed = await connect(f"ws://127.0.0.1:{relay.port}/state")
                await asyncio.wait_for(failed.wait_closed(), 1.0)
                await _wait_for(lambda: server_connections[1] not in relay._subscribers)
                await _wait_for(lambda: writers[1].done())
                assert writers[1].exception() is None

                healthy = await connect(f"ws://127.0.0.1:{relay.port}/state")
                handshake = await _handshake(healthy)
                assert handshake[2]["feed"] == "down"
                assert len(writers) == 3 and not writers[0].done() and not writers[2].done()
                retiring = await connect(f"ws://127.0.0.1:{relay.port}/state")
                await _handshake(retiring)
                retiring_peer = next(
                    peer for peer in relay._subscribers if peer.remote_address == retiring.local_address
                )
                relay._retire_subscriber(retiring_peer)
                assert not writers[3].done()

                await relay.close()
                await asyncio.wait_for(slow.wait_closed(), 1.0)
                await asyncio.wait_for(healthy.wait_closed(), 1.0)
                await asyncio.wait_for(retiring.wait_closed(), 1.0)
                assert not relay._subscribers
                assert writers[0].cancelled()
            finally:
                release.set()
                await relay.close()
                for peer in (slow, failed, healthy, retiring):
                    if peer is not None:
                        await peer.wait_closed()
            _assert_writers_done(writers)

    asyncio.run(scenario())
