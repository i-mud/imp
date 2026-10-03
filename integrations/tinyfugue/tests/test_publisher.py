from __future__ import annotations

import asyncio
import json
from typing import cast

import imp_adapter.publisher as publisher_module
import pytest
from imp_adapter.publisher import DEFAULT_RELAY_URL, RelayPublisher, state_to_wire
from imp_relay.protocol import Character, GameState, StateContext, Target, Vital
from imp_relay.server import RelayServer
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as websocket_connect

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


@pytest.mark.parametrize("observed_name", ["First", "Changed"])
def test_observation_on_lost_transport_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, observed_name: str
) -> None:
    async def scenario() -> None:
        now = [0.0]
        relay = RelayServer(port=0, stale_after=10.0, clock=lambda: now[0])
        await relay.start()
        port = relay.port
        publisher = RelayPublisher(url=f"ws://127.0.0.1:{port}/ingest")
        started = asyncio.Event()
        release = asyncio.Event()
        observation: asyncio.Task[None] | None = None
        restarted = RelayServer(port=port, stale_after=10.0, clock=lambda: now[0])

        async def marker(subscriber: ClientConnection, text: str) -> None:
            assert await publisher.text(CONTEXT, 1, text)
            while True:
                raw = await asyncio.wait_for(subscriber.recv(), timeout=1)
                assert isinstance(raw, str)
                message = json.loads(raw)
                if message["type"] == "text":
                    assert message["text"] == text
                    return

        try:
            await publisher.select(CONTEXT, _state("First"))
            await publisher.publish(CONTEXT, _state("First"))
            async with websocket_connect(f"ws://127.0.0.1:{port}/state") as subscriber:
                await marker(subscriber, "initial")
                assert relay.state.health()["feed"] == "live"

            connection = publisher._connection
            assert connection is not None
            original_send = connection.send

            async def held_send(frame: str) -> None:
                if json.loads(frame)["type"] == "publish":
                    started.set()
                    await release.wait()
                await original_send(frame)

            monkeypatch.setattr(connection, "send", held_send)
            observation = asyncio.create_task(publisher.publish(CONTEXT, _state(observed_name)))
            await asyncio.wait_for(started.wait(), timeout=1)
            await relay.close()
            now[0] = 11.0
            await restarted.start()

            async with websocket_connect(f"ws://127.0.0.1:{port}/state") as subscriber:
                while True:
                    raw = await asyncio.wait_for(subscriber.recv(), timeout=1)
                    assert isinstance(raw, str)
                    if json.loads(raw)["type"] == "snapshot":
                        break
                retained = restarted.state.snapshot()
                assert retained is not None
                assert retained.context == CONTEXT
                assert retained.state == _state(observed_name)
                assert restarted.state.health()["feed"] == "stale"

                release.set()
                await asyncio.wait_for(observation, timeout=1)
                await marker(subscriber, "old observation finished")
                assert restarted.state.snapshot() is retained
                assert restarted.state.health()["feed"] == "stale"

                await publisher.publish(CONTEXT, _state(observed_name))
                await marker(subscriber, "new observation")
                assert restarted.state.snapshot() is retained
                assert restarted.state.health()["feed"] == "live"
        finally:
            release.set()
            if observation is not None:
                await asyncio.wait_for(observation, timeout=1)
            await publisher.close()
            await relay.close()
            await restarted.close()

    asyncio.run(scenario())


def test_identical_observation_does_not_block_transient_text(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        now = [0.0]
        relay = RelayServer(port=0, stale_after=10.0, clock=lambda: now[0])
        await relay.start()
        publisher = RelayPublisher(url=f"ws://127.0.0.1:{relay.port}/ingest")
        started = asyncio.Event()
        release = asyncio.Event()
        observation: asyncio.Task[None] | None = None
        try:
            await publisher.select(CONTEXT, _state())
            await publisher.publish(CONTEXT, _state())
            async with websocket_connect(f"ws://127.0.0.1:{relay.port}/state") as subscriber:
                assert await publisher.text(CONTEXT, 1, "initial")
                while (
                    json.loads(cast(str, await asyncio.wait_for(subscriber.recv(), timeout=1)))["type"]
                    != "text"
                ):
                    pass
                retained = relay.state.snapshot()
                assert retained is not None

                connection = publisher._connection
                assert connection is not None
                original_send = connection.send

                async def held_send(frame: str) -> None:
                    if json.loads(frame)["type"] == "publish":
                        started.set()
                        await release.wait()
                    await original_send(frame)

                monkeypatch.setattr(connection, "send", held_send)
                observation = asyncio.create_task(publisher.publish(CONTEXT, _state()))
                await asyncio.wait_for(started.wait(), timeout=1)
                now[0] = 11.0
                assert await publisher.text(CONTEXT, 11000, "during identical observation")
                while True:
                    raw = await asyncio.wait_for(subscriber.recv(), timeout=1)
                    assert isinstance(raw, str)
                    message = json.loads(raw)
                    if message["type"] == "text":
                        assert message["text"] == "during identical observation"
                        break
                assert relay.state.snapshot() is retained
                assert relay.state.health()["feed"] == "stale"

                release.set()
                await asyncio.wait_for(observation, timeout=1)
        finally:
            release.set()
            if observation is not None:
                await asyncio.wait_for(observation, timeout=1)
            await publisher.close()
            await relay.close()

    asyncio.run(scenario())


def test_transient_text_uses_current_connection_without_mutating_selection() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()
        publisher = RelayPublisher(url=f"ws://127.0.0.1:{relay.port}/ingest")
        try:
            await publisher.select(CONTEXT, _state("Selected"))

            async with websocket_connect(f"ws://127.0.0.1:{relay.port}/state") as subscriber:
                # hello, retained snapshot, status
                await subscriber.recv()
                await subscriber.recv()
                await subscriber.recv()

                before = relay.state.snapshot()
                assert await publisher.text(CONTEXT, 1234, "Incoming line")

                while True:
                    raw = await asyncio.wait_for(subscriber.recv(), timeout=1)
                    assert isinstance(raw, str)
                    message = cast(dict[str, object], json.loads(raw))
                    if message["type"] == "text":
                        break

                assert message["text"] == "Incoming line"
                assert message["at"] == 1234
                assert relay.state.snapshot() == before

                assert not await publisher.text(OTHER_CONTEXT, 1235, "Wrong context")
        finally:
            await publisher.close()
            await relay.close()

    asyncio.run(scenario())


def test_transient_text_drops_while_reconnecting_and_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()
        port = relay.port
        publisher = RelayPublisher(url=f"ws://127.0.0.1:{port}/ingest")
        await publisher.select(CONTEXT, _state("Selected"))

        real_connect = websocket_connect
        reconnect_started = asyncio.Event()
        reconnect_release = asyncio.Event()

        async def gated_connect(uri: str, *, proxy: None) -> ClientConnection:
            reconnect_started.set()
            await reconnect_release.wait()
            return await real_connect(uri, proxy=proxy)

        monkeypatch.setattr(publisher_module, "connect", gated_connect)

        await relay.close()
        await asyncio.wait_for(reconnect_started.wait(), timeout=1)

        assert not await asyncio.wait_for(
            publisher.text(CONTEXT, 2000, "Must be dropped"),
            timeout=0.1,
        )

        restarted = RelayServer(port=port)
        await restarted.start()
        try:
            async with real_connect(f"ws://127.0.0.1:{port}/state") as subscriber:
                # Initial subscriber handshake before publisher reconnects.
                await subscriber.recv()  # hello
                await subscriber.recv()  # status

                reconnect_release.set()

                # Publisher reasserts retained selection after reconnect.
                while True:
                    raw = await asyncio.wait_for(subscriber.recv(), timeout=1)
                    assert isinstance(raw, str)
                    message = cast(dict[str, object], json.loads(raw))
                    if message["type"] == "snapshot":
                        break

                with pytest.raises(TimeoutError):
                    while True:
                        raw = await asyncio.wait_for(subscriber.recv(), timeout=0.05)
                        assert isinstance(raw, str)
                        if json.loads(raw)["type"] == "text":
                            raise AssertionError("dropped text was replayed")
        finally:
            reconnect_release.set()
            await publisher.close()
            await restarted.close()

    asyncio.run(scenario())


def test_transient_text_is_validated_before_send() -> None:
    async def scenario() -> None:
        publisher = RelayPublisher(url="ws://127.0.0.1:1/ingest")
        with pytest.raises(ValueError, match="text"):
            await publisher.text(CONTEXT, 1, "bad\nline")
        await publisher.close()

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
