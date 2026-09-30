from __future__ import annotations

import asyncio
import json
from typing import cast

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.typing import Origin

from imp_relay.protocol import (
    Character,
    GameState,
    StateContext,
    Vital,
    encode_action,
    encode_consumer,
    encode_consumer_result,
    encode_publish,
    encode_select,
    encode_text,
)
from imp_relay.server import POLICY_VIOLATION_CLOSE_CODE, RelayServer

CONTEXT = StateContext("session1", 1, 1)
OTHER_CONTEXT = StateContext("session1", 2, 1)


def _state(name: str = "Ada") -> GameState:
    return GameState(character=Character(name, Vital(9, 10), None, None), target=None)


def _character_name(message: dict[str, object]) -> str:
    state = cast(dict[str, object], message["state"])
    character = cast(dict[str, object], state["character"])
    return cast(str, character["name"])


async def _receive_type(connection: ClientConnection, expected_type: str) -> dict[str, object]:
    while True:
        frame = await connection.recv()
        assert isinstance(frame, str)
        message = cast(dict[str, object], json.loads(frame))
        if message["type"] == expected_type:
            return message


async def _select(relay: RelayServer, context: StateContext | None = CONTEXT) -> None:
    async with connect(f"ws://127.0.0.1:{relay.port}/ingest") as producer:
        await producer.send(encode_select(context, _state("Selected")))


def test_subscriber_receives_contextual_retained_snapshot() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        relay.state.apply_select(CONTEXT, _state(), now=1.0)
        await relay.start()
        try:
            async with connect(f"ws://127.0.0.1:{relay.port}/state") as subscriber:
                hello = json.loads(await subscriber.recv())
                snapshot = json.loads(await subscriber.recv())
                status = json.loads(await subscriber.recv())
                assert [hello["type"], snapshot["type"], status["type"]] == [
                    "hello",
                    "snapshot",
                    "status",
                ]
                assert snapshot["context"] == {
                    "session": "session1",
                    "foreground": 1,
                    "connection": 1,
                }
                assert _character_name(snapshot) == "Ada"
        finally:
            await relay.close()

    asyncio.run(scenario())


def test_publish_broadcasts_only_for_selected_context() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()
        try:
            async with (
                connect(f"ws://127.0.0.1:{relay.port}/state") as subscriber,
                connect(f"ws://127.0.0.1:{relay.port}/ingest") as producer,
            ):
                await producer.send(encode_select(CONTEXT, _state("Selected")))
                assert _character_name(await _receive_type(subscriber, "snapshot")) == "Selected"
                await producer.send(encode_publish(OTHER_CONTEXT, _state("Wrong")))
                await producer.send(encode_publish(CONTEXT, _state("Current")))
                assert _character_name(await _receive_type(subscriber, "snapshot")) == "Current"
        finally:
            await relay.close()

    asyncio.run(scenario())


def test_text_is_transient_and_broadcast_only_for_active_context() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()
        try:
            async with connect(f"ws://127.0.0.1:{relay.port}/ingest") as producer:
                await producer.send(encode_select(CONTEXT, _state("Selected")))

                async with connect(f"ws://127.0.0.1:{relay.port}/state") as subscriber:
                    await _receive_type(subscriber, "snapshot")
                    await _receive_type(subscriber, "status")

                    before = relay.state.snapshot()
                    await producer.send(encode_text(CONTEXT, 1234, "The troll snarls."))

                    event = await _receive_type(subscriber, "text")
                    assert event == {
                        "type": "text",
                        "protocol": 2,
                        "context": {
                            "session": "session1",
                            "foreground": 1,
                            "connection": 1,
                        },
                        "at": 1234,
                        "text": "The troll snarls.",
                    }

                    # A transient event does not advance or replace retained state.
                    assert relay.state.snapshot() == before

                    await producer.send(encode_text(OTHER_CONTEXT, 1235, "wrong world"))
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(_receive_type(subscriber, "text"), timeout=0.05)

                # A subscriber arriving later receives retained state/status,
                # but never the earlier text event.
                async with connect(f"ws://127.0.0.1:{relay.port}/state") as later:
                    await _receive_type(later, "snapshot")
                    await _receive_type(later, "status")
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(_receive_type(later, "text"), timeout=0.05)
        finally:
            await relay.close()

    asyncio.run(scenario())


def test_malformed_ingest_frame_closes_without_mutating_state() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        original = relay.state.apply_select(CONTEXT, _state("Original"), now=1.0)
        await relay.start()
        try:
            producer = await connect(f"ws://127.0.0.1:{relay.port}/ingest")
            await producer.send('{"type":"publish","protocol":2,"context":null,"state":{}}')
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


@pytest.mark.parametrize("path", ["/state", "/action"])
@pytest.mark.parametrize("origin", [Origin("http://localhost:1420"), Origin("http://tauri.localhost")])
def test_browser_endpoints_accept_trusted_origins(path: str, origin: Origin) -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()
        try:
            connection = await connect(f"ws://127.0.0.1:{relay.port}{path}", origin=origin)
            await connection.close()
        finally:
            await relay.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("path", ["/state", "/action", "/ingest", "/action-consumer"])
@pytest.mark.parametrize("origin", [Origin("null"), Origin("https://evil.example")])
def test_untrusted_origins_are_rejected(path: str, origin: Origin) -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()
        try:
            with pytest.raises(InvalidStatus) as error:
                await connect(f"ws://127.0.0.1:{relay.port}{path}", origin=origin)
            assert error.value.response.status_code == 403
        finally:
            await relay.close()

    asyncio.run(scenario())


def test_privileged_endpoints_reject_even_trusted_browser_origin() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()
        try:
            for path in ("/ingest", "/action-consumer"):
                with pytest.raises(InvalidStatus) as error:
                    await connect(
                        f"ws://127.0.0.1:{relay.port}{path}", origin=Origin("http://localhost:1420")
                    )
                assert error.value.response.status_code == 403
        finally:
            await relay.close()

    asyncio.run(scenario())


def test_action_is_forwarded_once_and_registration_closes() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()
        try:
            await _select(relay)
            async with connect(f"ws://127.0.0.1:{relay.port}/action-consumer") as consumer:
                await consumer.send(encode_consumer(CONTEXT))
                ready = await _receive_type(consumer, "consumer-ready")
                assert ready["context"]["foreground"] == 1  # type: ignore[index]
                async with connect(f"ws://127.0.0.1:{relay.port}/action") as action:
                    await action.send(encode_action(CONTEXT, "say hello"))
                    dispatch = await _receive_type(consumer, "dispatch")
                    assert dispatch["command"] == "say hello"
                    await consumer.send(encode_consumer_result(cast(str, dispatch["id"]), "forwarded"))
                    result = await _receive_type(action, "action-result")
                    assert result == {
                        "type": "action-result",
                        "protocol": 2,
                        "status": "forwarded",
                        "detail": None,
                    }
                await asyncio.wait_for(consumer.wait_closed(), timeout=1)
                async with connect(f"ws://127.0.0.1:{relay.port}/action") as later:
                    await later.send(encode_action(CONTEXT, "look"))
                    assert (await _receive_type(later, "action-result"))["status"] == "rejected"
        finally:
            await relay.close()

    asyncio.run(scenario())


def test_action_rejects_without_matching_consumer() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()
        try:
            await _select(relay)
            async with connect(f"ws://127.0.0.1:{relay.port}/action") as action:
                await action.send(encode_action(CONTEXT, "look"))
                result = await _receive_type(action, "action-result")
                assert result["status"] == "rejected"
                assert result["detail"] == "no matching local action consumer"
        finally:
            await relay.close()

    asyncio.run(scenario())


def test_action_rejects_wrong_context_and_busy_second_request() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()
        try:
            await _select(relay)
            async with connect(f"ws://127.0.0.1:{relay.port}/action-consumer") as consumer:
                await consumer.send(encode_consumer(CONTEXT))
                await _receive_type(consumer, "consumer-ready")

                async with connect(f"ws://127.0.0.1:{relay.port}/action") as wrong:
                    await wrong.send(encode_action(OTHER_CONTEXT, "look"))
                    assert (await _receive_type(wrong, "action-result"))["status"] == "rejected"

                async with (
                    connect(f"ws://127.0.0.1:{relay.port}/action") as first,
                    connect(f"ws://127.0.0.1:{relay.port}/action") as second,
                ):
                    await first.send(encode_action(CONTEXT, "first"))
                    dispatch = await _receive_type(consumer, "dispatch")
                    await second.send(encode_action(CONTEXT, "second"))
                    assert (await _receive_type(second, "action-result"))["status"] == "rejected"
                    await consumer.send(encode_consumer_result(cast(str, dispatch["id"]), "forwarded"))
                    assert (await _receive_type(first, "action-result"))["status"] == "forwarded"
        finally:
            await relay.close()

    asyncio.run(scenario())


def test_stale_consumer_disconnect_does_not_unregister_new_consumer() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()
        try:
            await _select(relay, CONTEXT)
            old_consumer = await connect(f"ws://127.0.0.1:{relay.port}/action-consumer")
            await old_consumer.send(encode_consumer(CONTEXT))
            await _receive_type(old_consumer, "consumer-ready")

            await _select(relay, OTHER_CONTEXT)
            async with connect(f"ws://127.0.0.1:{relay.port}/action-consumer") as new_consumer:
                await new_consumer.send(encode_consumer(OTHER_CONTEXT))
                await _receive_type(new_consumer, "consumer-ready")
                await old_consumer.close()
                await asyncio.sleep(0)

                async with connect(f"ws://127.0.0.1:{relay.port}/action") as action:
                    await action.send(encode_action(OTHER_CONTEXT, "east"))
                    dispatch = await _receive_type(new_consumer, "dispatch")
                    await new_consumer.send(encode_consumer_result(cast(str, dispatch["id"]), "forwarded"))
                    assert (await _receive_type(action, "action-result"))["status"] == "forwarded"
        finally:
            await relay.close()

    asyncio.run(scenario())


def test_consumer_disconnect_after_dispatch_reports_unknown() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()
        try:
            await _select(relay)
            consumer = await connect(f"ws://127.0.0.1:{relay.port}/action-consumer")
            await consumer.send(encode_consumer(CONTEXT))
            await _receive_type(consumer, "consumer-ready")
            async with connect(f"ws://127.0.0.1:{relay.port}/action") as action:
                await action.send(encode_action(CONTEXT, "north"))
                await _receive_type(consumer, "dispatch")
                await consumer.close()
                result = await _receive_type(action, "action-result")
                assert result["status"] == "unknown"
        finally:
            await relay.close()

    asyncio.run(scenario())


def test_action_command_with_newline_is_rejected_at_protocol_boundary() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()
        try:
            await _select(relay)
            action = await connect(f"ws://127.0.0.1:{relay.port}/action")
            await action.send(
                '{"type":"action","protocol":2,"context":{"session":"session1",'
                '"foreground":1,"connection":1},"command":"look\\nnorth"}'
            )
            with pytest.raises(ConnectionClosed):
                await action.recv()
            assert action.close_code == POLICY_VIOLATION_CLOSE_CODE
        finally:
            await relay.close()

    asyncio.run(scenario())
