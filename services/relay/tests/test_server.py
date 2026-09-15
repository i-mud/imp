from __future__ import annotations

import asyncio
import json
from typing import cast

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from tinyscry_relay.protocol import Character, GameState, Vital, encode_publish
from tinyscry_relay.server import POLICY_VIOLATION_CLOSE_CODE, RelayServer


def _state(name: str = "Ada") -> GameState:
    return GameState(character=Character(name, Vital(9, 10), None, None), target=None)


def _character_name(message: dict[str, object]) -> str:
    state = message["state"]
    assert isinstance(state, dict)
    character = state["character"]
    assert isinstance(character, dict)
    name = character["name"]
    assert isinstance(name, str)
    return name


async def _receive_type(connection: ClientConnection, expected_type: str) -> dict[str, object]:
    while True:
        frame = await connection.recv()
        assert isinstance(frame, str)
        message = cast(dict[str, object], json.loads(frame))
        if message["type"] == expected_type:
            return message


async def _receive_feed(connection: ClientConnection, expected_feed: str) -> dict[str, object]:
    while True:
        frame = await connection.recv()
        assert isinstance(frame, str)
        message = cast(dict[str, object], json.loads(frame))
        if message["type"] == "status" and message["feed"] == expected_feed:
            return message


def test_subscriber_receives_hello_snapshot_then_status() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        relay.state.apply_publish(_state(), now=1.0)
        await relay.start()
        try:
            async with connect(f"ws://127.0.0.1:{relay.port}/state") as subscriber:
                hello = json.loads(await subscriber.recv())
                snapshot = json.loads(await subscriber.recv())
                status = json.loads(await subscriber.recv())
                assert [hello["type"], snapshot["type"], status["type"]] == ["hello", "snapshot", "status"]
                assert snapshot["state"]["character"]["name"] == "Ada"
        finally:
            await relay.close()

    asyncio.run(scenario())


def test_late_subscriber_receives_retained_snapshot() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        relay.state.apply_publish(_state("Retained"), now=1.0)
        await relay.start()
        try:
            async with connect(f"ws://127.0.0.1:{relay.port}/state") as late_subscriber:
                snapshot = await _receive_type(late_subscriber, "snapshot")
                assert snapshot["seq"] == 1
                assert _character_name(snapshot) == "Retained"
        finally:
            await relay.close()

    asyncio.run(scenario())


def test_publish_broadcasts_to_multiple_subscribers() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()
        try:
            async with (
                connect(f"ws://127.0.0.1:{relay.port}/state") as first,
                connect(f"ws://127.0.0.1:{relay.port}/state") as second,
                connect(f"ws://127.0.0.1:{relay.port}/ingest") as producer,
            ):
                await producer.send(encode_publish(_state("Broadcast")))
                first_snapshot = await _receive_type(first, "snapshot")
                second_snapshot = await _receive_type(second, "snapshot")
                assert first_snapshot["state"] == second_snapshot["state"]
                assert _character_name(first_snapshot) == "Broadcast"
        finally:
            await relay.close()

    asyncio.run(scenario())


def test_staleness_watcher_broadcasts_live_to_stale_transition() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0, stale_after=0.02)
        await relay.start()
        try:
            async with (
                connect(f"ws://127.0.0.1:{relay.port}/state") as subscriber,
                connect(f"ws://127.0.0.1:{relay.port}/ingest") as producer,
            ):
                await producer.send(encode_publish(_state()))
                await _receive_type(subscriber, "snapshot")
                status = await asyncio.wait_for(_receive_feed(subscriber, "stale"), timeout=0.5)
                assert status["feed"] == "stale"
        finally:
            await relay.close()

    asyncio.run(scenario())


def test_malformed_publish_closes_producer_without_mutating_retained_state() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        original = relay.state.apply_publish(_state("Original"), now=1.0)
        await relay.start()
        try:
            producer = await connect(f"ws://127.0.0.1:{relay.port}/ingest")
            await producer.send(
                '{"type":"publish","protocol":1,"state":{"character":{"name":"broken","hp":{"current":1}}}}'
            )
            with pytest.raises(ConnectionClosed):
                await producer.recv()
            assert producer.close_code == POLICY_VIOLATION_CLOSE_CODE
            assert relay.state.snapshot() == original
        finally:
            await relay.close()

    asyncio.run(scenario())


def test_healthz_returns_json_status() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", relay.port)
            writer.write(b"GET /healthz HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            await writer.drain()
            response = await reader.read()
            writer.close()
            await writer.wait_closed()
            headers, body = response.split(b"\r\n\r\n", 1)
            assert b" 200 " in headers.splitlines()[0]
            assert json.loads(body) == {
                "feed": "down",
                "producer_count": 0,
                "has_snapshot": False,
                "seq": None,
            }
        finally:
            await relay.close()

    asyncio.run(scenario())


def test_disconnect_of_one_subscriber_does_not_stop_other_delivery() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()
        try:
            first = await connect(f"ws://127.0.0.1:{relay.port}/state")
            second = await connect(f"ws://127.0.0.1:{relay.port}/state")
            try:
                await first.close()
                async with connect(f"ws://127.0.0.1:{relay.port}/ingest") as producer:
                    await producer.send(encode_publish(_state("Survivor")))
                    snapshot = await _receive_type(second, "snapshot")
                    assert _character_name(snapshot) == "Survivor"
            finally:
                await second.close()
        finally:
            await relay.close()

    asyncio.run(scenario())
