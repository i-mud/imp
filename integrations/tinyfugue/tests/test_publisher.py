from __future__ import annotations

import asyncio
import json
from typing import cast

import pytest
from tinyscry_relay.protocol import Character, GameState, StateContext, Target, Vital
from tinyscry_relay.server import RelayServer
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as websocket_connect
from websockets.asyncio.server import ServerConnection, serve

import tinyscry_tf.publisher as publisher_module
from tinyscry_tf.publisher import DEFAULT_RELAY_URL, RelayPublisher, state_to_wire

CONTEXT = StateContext("session1", 1, 1)
OTHER_CONTEXT = StateContext("session1", 2, 2)


def _state(name: str = "Ariadne", percent: float | None = 50.0) -> GameState:
    return GameState(
        character=Character(name=name, hp=Vital(current=10, max=20), mana=None, moves=None),
        target=Target(name="a cave troll", health_percent=percent),
    )


def test_wire_form_uses_the_protocol_field_spelling() -> None:
    assert state_to_wire(_state()) == {
        "character": {"name": "Ariadne", "hp": {"current": 10, "max": 20}, "mana": None, "moves": None},
        "target": {"name": "a cave troll", "healthPercent": 50.0},
    }


def test_invalid_state_fails_before_any_connection() -> None:
    publisher = RelayPublisher(url="ws://127.0.0.1:1/ingest")
    with pytest.raises(ValueError, match=r"state\.target\.healthPercent"):
        asyncio.run(asyncio.wait_for(publisher.select(CONTEXT, _state(percent=140.0)), timeout=2))


def test_non_loopback_target_is_always_refused() -> None:
    with pytest.raises(ValueError, match="loopback"):
        RelayPublisher(url="ws://203.0.113.7:8787/ingest")
    assert RelayPublisher().url == DEFAULT_RELAY_URL


def test_reconnect_reselects_context_before_publish() -> None:
    async def scenario() -> None:
        connections = 0
        frames: list[dict[str, object]] = []
        first_closed = asyncio.Event()
        complete = asyncio.Event()

        async def handle(connection: ServerConnection) -> None:
            nonlocal connections
            connections += 1
            if connections == 1:
                frames.append(cast(dict[str, object], json.loads(cast(str, await connection.recv()))))
                await connection.close()
                first_closed.set()
                return
            frames.append(cast(dict[str, object], json.loads(cast(str, await connection.recv()))))
            frames.append(cast(dict[str, object], json.loads(cast(str, await connection.recv()))))
            complete.set()
            await connection.wait_closed()

        async with serve(handle, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            publisher = RelayPublisher(url=f"ws://127.0.0.1:{port}/ingest")
            try:
                await publisher.select(CONTEXT, _state("First"))
                await asyncio.wait_for(first_closed.wait(), timeout=1)
                await publisher.publish(CONTEXT, _state("Second"))
                await asyncio.wait_for(complete.wait(), timeout=1)
            finally:
                await publisher.close()

        assert [frame["type"] for frame in frames] == ["select", "select", "publish"]
        assert all(
            frame.get("context") == {"session": "session1", "foreground": 1, "connection": 1}
            for frame in frames
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("retained_context", [OTHER_CONTEXT, None])
def test_idle_reconnect_reasserts_only_the_latest_selection(
    retained_context: StateContext | None,
) -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()
        port = relay.port
        publisher = RelayPublisher(url=f"ws://127.0.0.1:{port}/ingest")
        await publisher.select(CONTEXT, _state("Old"))
        await publisher.select(retained_context, _state("Retained"))
        await relay.close()

        restarted = RelayServer(port=port)
        await restarted.start()
        try:
            for _ in range(300):
                snapshot = restarted.state.snapshot()
                if snapshot is not None:
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("publisher did not restore its retained selection")

            assert snapshot.context == retained_context
            assert snapshot.state == _state("Retained")
            assert restarted.state.health()["feed"] == "stale"
        finally:
            await publisher.close()
            await restarted.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("retained_context", "retained_name"),
    [(OTHER_CONTEXT, "New"), (None, "Display only")],
    ids=["new-context", "null-context"],
)
def test_selection_change_during_reconnect_wins(
    monkeypatch: pytest.MonkeyPatch,
    retained_context: StateContext | None,
    retained_name: str,
) -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()
        port = relay.port
        publisher = RelayPublisher(url=f"ws://127.0.0.1:{port}/ingest")
        await publisher.select(CONTEXT, _state("Old"))

        real_connect = websocket_connect
        reconnect_started = asyncio.Event()
        reconnect_release = asyncio.Event()
        block_reconnect = True

        async def gated_connect(uri: str, *, proxy: None) -> ClientConnection:
            if block_reconnect:
                reconnect_started.set()
                await reconnect_release.wait()
            return await real_connect(uri, proxy=proxy)

        monkeypatch.setattr(publisher_module, "connect", gated_connect)
        await relay.close()
        await asyncio.wait_for(reconnect_started.wait(), timeout=1)

        selection = asyncio.create_task(publisher.select(retained_context, _state(retained_name)))
        await asyncio.sleep(0)
        restarted = RelayServer(port=port)
        await restarted.start()
        reconnect_release.set()
        try:
            await asyncio.wait_for(selection, timeout=1)
            for _ in range(100):
                snapshot = restarted.state.snapshot()
                if snapshot is not None:
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("publisher did not restore its retained selection")

            assert snapshot.context == retained_context
            assert snapshot.state == _state(retained_name)
            assert restarted.state.health()["feed"] == "stale"
        finally:
            await publisher.close()
            await restarted.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure_mode", ["connect", "backoff"])
def test_close_cancels_reconnect_without_later_retry(
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()
        port = relay.port
        publisher = RelayPublisher(url=f"ws://127.0.0.1:{port}/ingest")
        await publisher.select(CONTEXT, _state("Old"))

        real_connect = websocket_connect
        reconnect_started = asyncio.Event()
        reconnect_release = asyncio.Event()
        reconnect_attempts = 0

        async def gated_connect(uri: str, *, proxy: None) -> ClientConnection:
            nonlocal reconnect_attempts
            reconnect_attempts += 1
            reconnect_started.set()
            if failure_mode == "backoff":
                raise OSError("relay unavailable")
            await reconnect_release.wait()
            return await real_connect(uri, proxy=proxy)

        monkeypatch.setattr(publisher_module, "connect", gated_connect)
        await relay.close()
        await asyncio.wait_for(reconnect_started.wait(), timeout=1)
        await asyncio.sleep(0)
        await asyncio.wait_for(publisher.close(), timeout=0.2)

        restarted = RelayServer(port=port)
        await restarted.start()
        try:
            reconnect_release.set()
            await asyncio.sleep(0.3)
            assert reconnect_attempts == 1
            assert restarted.state.snapshot() is None
        finally:
            await restarted.close()

    asyncio.run(scenario())
