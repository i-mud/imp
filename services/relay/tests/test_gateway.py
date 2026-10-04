from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import socket
from typing import Any, cast

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.typing import Origin

import imp_relay.gateway as gateway_module
from imp_relay.gateway import (
    AUTH_POLICY_CLOSE_CODE,
    GatewayServer,
    token_digest,
    valid_pairing_token,
    validate_relay_url,
)
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
from imp_relay.server import RelayServer

TOKEN = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode("ascii")
TOKEN_DIGEST = hashlib.sha256(TOKEN.encode("ascii")).digest()
WRONG_TOKEN = base64.urlsafe_b64encode(bytes(range(1, 33))).rstrip(b"=").decode("ascii")

CONTEXT = StateContext("session1", 1, 1)


def _auth(token: str = TOKEN) -> str:
    return json.dumps({"type": "auth", "token": token}, separators=(",", ":"))


def _state(name: str = "Ada") -> GameState:
    return GameState(
        character=Character(name, Vital(9, 10), None, None),
        target=None,
    )


async def _receive_type(connection: ClientConnection, expected_type: str) -> dict[str, object]:
    while True:
        frame = await connection.recv()
        assert isinstance(frame, str)
        message = cast(dict[str, object], json.loads(frame))
        if message["type"] == expected_type:
            return message


def _reserve_unavailable_port() -> tuple[socket.socket, int]:
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    address = cast(tuple[str, int], blocker.getsockname())
    return blocker, address[1]


def test_pairing_token_validation_is_canonical_and_bounded() -> None:
    assert valid_pairing_token(TOKEN)
    assert token_digest(TOKEN) == TOKEN_DIGEST

    assert not valid_pairing_token("")
    assert not valid_pairing_token(TOKEN[:-1])
    assert not valid_pairing_token(TOKEN + "A")
    assert not valid_pairing_token("*" + TOKEN[1:])

    with pytest.raises(ValueError, match="pairing token"):
        token_digest("not-a-token")


@pytest.mark.parametrize(
    "url",
    [
        "ws://example.com:8787",
        "ws://localhost:8787",
        "wss://127.0.0.1:8787",
        "ws://user@127.0.0.1:8787",
        "ws://127.0.0.1",
        "ws://127.0.0.1:8787/state",
        "ws://127.0.0.1:8787?token=secret",
    ],
)
def test_gateway_relay_url_rejects_non_loopback_or_non_base_urls(url: str) -> None:
    with pytest.raises(ValueError):
        validate_relay_url(url)


def test_gateway_relay_url_accepts_explicit_loopback_ws() -> None:
    assert validate_relay_url("ws://127.0.0.1:8787") == "ws://127.0.0.1:8787"
    assert validate_relay_url("ws://[::1]:8787/") == "ws://[::1]:8787"


@pytest.mark.parametrize("auth_timeout", [float("inf"), float("-inf"), float("nan"), 0.0, -1.0])
def test_gateway_rejects_nonpositive_or_nonfinite_auth_timeout(auth_timeout: float) -> None:
    with pytest.raises(ValueError):
        GatewayServer(TOKEN_DIGEST, auth_timeout=auth_timeout)


def test_gateway_listener_rejects_non_loopback_binding() -> None:
    with pytest.raises(ValueError, match="loopback"):
        GatewayServer(TOKEN_DIGEST, host="0.0.0.0")


@pytest.mark.parametrize(
    "first_frame",
    [
        "{not json",
        json.dumps({"type": "auth"}),
        json.dumps({"type": "auth", "token": WRONG_TOKEN}),
        json.dumps({"type": "auth", "token": TOKEN, "extra": True}),
        b'{"type":"auth"}',
    ],
)
def test_failed_authentication_never_opens_upstream(
    monkeypatch: pytest.MonkeyPatch,
    first_frame: str | bytes,
) -> None:
    async def scenario() -> None:
        attempts = 0

        def forbidden_connect(*args: object, **kwargs: object) -> object:
            nonlocal attempts
            attempts += 1
            raise AssertionError("gateway contacted relay before authenticating")

        monkeypatch.setattr(gateway_module, "connect", forbidden_connect)

        gateway = GatewayServer(TOKEN_DIGEST, port=0)
        await gateway.start()
        try:
            connection = await connect(f"ws://127.0.0.1:{gateway.port}/state")
            await connection.send(first_frame)

            with pytest.raises(ConnectionClosed):
                await connection.recv()

            assert connection.close_code == AUTH_POLICY_CLOSE_CODE
            assert attempts == 0
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_authentication_timeout_never_opens_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        attempts = 0

        def forbidden_connect(*args: object, **kwargs: object) -> object:
            nonlocal attempts
            attempts += 1
            raise AssertionError("gateway contacted relay before authenticating")

        monkeypatch.setattr(gateway_module, "connect", forbidden_connect)

        gateway = GatewayServer(TOKEN_DIGEST, port=0, auth_timeout=0.02)
        await gateway.start()
        try:
            connection = await connect(f"ws://127.0.0.1:{gateway.port}/state")

            with pytest.raises(ConnectionClosed):
                await connection.recv()

            assert connection.close_code == AUTH_POLICY_CLOSE_CODE
            assert attempts == 0
        finally:
            await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "path",
    [
        "/ingest",
        "/action-consumer",
        "/unknown",
        "/state?token=secret",
    ],
)
def test_gateway_rejects_non_remote_routes_before_auth(path: str) -> None:
    async def scenario() -> None:
        gateway = GatewayServer(TOKEN_DIGEST, port=0)
        await gateway.start()
        try:
            with pytest.raises(InvalidStatus) as error:
                await connect(f"ws://127.0.0.1:{gateway.port}{path}")
            assert error.value.response.status_code == 404
        finally:
            await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("path", ["/state", "/action"])
def test_gateway_rejects_untrusted_browser_origins(path: str) -> None:
    async def scenario() -> None:
        gateway = GatewayServer(TOKEN_DIGEST, port=0)
        await gateway.start()
        try:
            with pytest.raises(InvalidStatus) as error:
                await connect(
                    f"ws://127.0.0.1:{gateway.port}{path}",
                    origin=Origin("https://evil.example"),
                )
            assert error.value.response.status_code == 403
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_gateway_health_contains_no_relay_or_token_data() -> None:
    async def scenario() -> None:
        gateway = GatewayServer(TOKEN_DIGEST, port=0)
        await gateway.start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", gateway.port)
            writer.write(b"GET /healthz HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            await writer.drain()
            response = await reader.read()
            writer.close()
            await writer.wait_closed()

            headers, body = response.split(b"\r\n\r\n", 1)
            assert b" 200 " in headers.splitlines()[0]
            assert json.loads(body) == {"status": "ok"}
            assert TOKEN.encode() not in response
            assert b"character" not in body
            assert b"snapshot" not in body
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_authenticated_state_stream_preserves_relay_semantics() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        relay.state.apply_select(CONTEXT, _state("Selected"), now=1.0)
        await relay.start()

        gateway = GatewayServer(
            TOKEN_DIGEST,
            port=0,
            relay_url=f"ws://127.0.0.1:{relay.port}",
        )
        await gateway.start()

        try:
            async with connect(f"ws://127.0.0.1:{gateway.port}/state") as remote:
                await remote.send(_auth())

                hello = await _receive_type(remote, "hello")
                snapshot = await _receive_type(remote, "snapshot")
                status = await _receive_type(remote, "status")

                assert hello["type"] == "hello"
                assert snapshot["context"] == {
                    "session": "session1",
                    "foreground": 1,
                    "connection": 1,
                }
                assert status["feed"] in {"live", "stale", "down"}

                async with connect(f"ws://127.0.0.1:{relay.port}/ingest") as producer:
                    await producer.send(encode_text(CONTEXT, 1234, "Transient line"))

                    text = await _receive_type(remote, "text")
                    assert text["text"] == "Transient line"
                    assert text["at"] == 1234

            async with connect(f"ws://127.0.0.1:{gateway.port}/state") as later:
                await later.send(_auth())
                await _receive_type(later, "snapshot")
                await _receive_type(later, "status")

                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(_receive_type(later, "text"), timeout=0.05)
        finally:
            await gateway.close()
            await relay.close()

    asyncio.run(scenario())


def test_authenticated_action_round_trip_preserves_relay_semantics() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        relay.state.apply_select(CONTEXT, _state("Selected"), now=1.0)
        await relay.start()

        gateway = GatewayServer(
            TOKEN_DIGEST,
            port=0,
            relay_url=f"ws://127.0.0.1:{relay.port}",
        )
        await gateway.start()

        try:
            async with connect(f"ws://127.0.0.1:{relay.port}/action-consumer") as consumer:
                await consumer.send(encode_consumer(CONTEXT))
                await _receive_type(consumer, "consumer-ready")

                async with connect(f"ws://127.0.0.1:{gateway.port}/action") as remote:
                    await remote.send(_auth())
                    await remote.send(encode_action(CONTEXT, "look"))

                    dispatch = await _receive_type(consumer, "dispatch")
                    assert dispatch["command"] == "look"

                    await consumer.send(encode_consumer_result(cast(str, dispatch["id"]), "forwarded"))

                    result = await _receive_type(remote, "action-result")
                    assert result == {
                        "type": "action-result",
                        "protocol": 2,
                        "status": "forwarded",
                        "detail": None,
                    }
        finally:
            await gateway.close()
            await relay.close()

    asyncio.run(scenario())


def test_authenticated_state_connection_closes_when_relay_is_unavailable() -> None:
    async def scenario() -> None:
        blocker, unavailable_port = _reserve_unavailable_port()

        gateway = GatewayServer(
            TOKEN_DIGEST,
            port=0,
            relay_url=f"ws://127.0.0.1:{unavailable_port}",
        )
        await gateway.start()

        try:
            connection = await connect(f"ws://127.0.0.1:{gateway.port}/state")
            await connection.send(_auth())

            with pytest.raises(ConnectionClosed):
                await connection.recv()

            assert connection.close_code == 1011
        finally:
            await gateway.close()
            blocker.close()

    asyncio.run(scenario())


def test_authenticated_state_disconnects_when_relay_drops() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        relay.state.apply_select(CONTEXT, _state("Selected"), now=1.0)
        await relay.start()

        gateway = GatewayServer(
            TOKEN_DIGEST,
            port=0,
            relay_url=f"ws://127.0.0.1:{relay.port}",
        )
        await gateway.start()

        try:
            connection = await connect(f"ws://127.0.0.1:{gateway.port}/state")
            await connection.send(_auth())

            await _receive_type(connection, "hello")
            await _receive_type(connection, "snapshot")
            await _receive_type(connection, "status")

            await relay.close()

            with pytest.raises(ConnectionClosed):
                await asyncio.wait_for(connection.recv(), timeout=1)
        finally:
            await gateway.close()
            await relay.close()

    asyncio.run(scenario())


def test_action_upstream_unavailable_is_attempted_once_without_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        blocker, unavailable_port = _reserve_unavailable_port()

        real_connect = connect
        attempts = 0
        proxy_values: list[object] = []

        def counted_connect(*args: Any, **kwargs: Any) -> Any:
            nonlocal attempts
            attempts += 1
            proxy_values.append(kwargs.get("proxy"))
            return real_connect(*args, **kwargs)

        monkeypatch.setattr(gateway_module, "connect", counted_connect)

        gateway = GatewayServer(
            TOKEN_DIGEST,
            port=0,
            relay_url=f"ws://127.0.0.1:{unavailable_port}",
        )
        await gateway.start()

        try:
            connection = await connect(f"ws://127.0.0.1:{gateway.port}/action")
            await connection.send(_auth())
            await connection.send(encode_action(CONTEXT, "look"))

            with pytest.raises(ConnectionClosed):
                await connection.recv()

            assert connection.close_code == 1011
            assert attempts == 1
            assert proxy_values == [None]
        finally:
            await gateway.close()
            blocker.close()

    asyncio.run(scenario())


def test_authenticated_gateway_upstream_isolated_from_slow_remote_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()
        relay_listener = relay._server
        assert relay_listener is not None
        gateway = GatewayServer(
            TOKEN_DIGEST,
            port=0,
            relay_url=f"ws://127.0.0.1:{relay.port}",
        )
        await gateway.start()
        connections: list[ClientConnection] = []
        tasks: list[asyncio.Task[object]] = []
        try:

            def uncompressed_upstream(*args: Any, **kwargs: Any) -> Any:
                kwargs["compression"] = None
                return connect(*args, **kwargs)

            monkeypatch.setattr(gateway_module, "connect", uncompressed_upstream)

            direct = await connect(
                f"ws://127.0.0.1:{relay.port}/state",
                compression=None,
                max_queue=1,
                ping_interval=None,
            )
            connections.append(direct)
            remote = await connect(
                f"ws://127.0.0.1:{gateway.port}/state",
                compression=None,
                max_queue=1,
                ping_interval=None,
            )
            connections.append(remote)
            await _receive_type(direct, "hello")
            await _receive_type(direct, "status")
            await remote.send(_auth())
            await _receive_type(remote, "hello")
            await _receive_type(remote, "status")

            direct_socket = cast(socket.socket, direct.transport.get_extra_info("socket"))
            direct_port = cast(tuple[str, int], direct_socket.getsockname())[1]
            direct_subscriber = next(
                connection
                for connection in relay_listener.connections
                if connection.remote_address[1] == direct_port
            )
            upstream_subscriber = next(
                connection for connection in relay_listener.connections if connection is not direct_subscriber
            )
            upstream_queue = relay._subscribers[upstream_subscriber]
            upstream_socket = cast(
                socket.socket,
                upstream_subscriber.transport.get_extra_info("socket"),
            )
            upstream_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)

            remote_socket = cast(socket.socket, remote.transport.get_extra_info("socket"))
            remote_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
            remote_port = cast(tuple[str, int], remote_socket.getsockname())[1]
            gateway_server = gateway._server
            assert gateway_server is not None
            gateway_connection = next(
                connection
                for connection in gateway_server.connections
                if connection.remote_address[1] == remote_port
            )
            gateway_socket = cast(
                socket.socket,
                gateway_connection.transport.get_extra_info("socket"),
            )
            gateway_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            remote.transport.pause_reading()

            max_backlog = 0
            max_upstream_write_buffer = 0
            max_gateway_write_buffer = 0

            async def observe_backpressure() -> None:
                nonlocal max_backlog, max_upstream_write_buffer, max_gateway_write_buffer
                while upstream_subscriber in relay_listener.connections:
                    max_backlog = max(max_backlog, upstream_queue.qsize())
                    max_upstream_write_buffer = max(
                        max_upstream_write_buffer,
                        upstream_subscriber.transport.get_write_buffer_size(),
                    )
                    max_gateway_write_buffer = max(
                        max_gateway_write_buffer,
                        gateway_connection.transport.get_write_buffer_size(),
                    )
                    await asyncio.sleep(0)

            monitor = asyncio.create_task(observe_backpressure())
            tasks.append(monitor)

            async def receive_direct() -> list[int]:
                sequences: list[int] = []
                while len(sequences) < 4_000:
                    snapshot = await _receive_type(direct, "snapshot")
                    sequence = snapshot["seq"]
                    assert isinstance(sequence, int)
                    sequences.append(sequence)
                return sequences

            reader = asyncio.create_task(receive_direct())
            tasks.append(reader)
            producer = await connect(
                f"ws://127.0.0.1:{relay.port}/ingest",
                compression=None,
                max_queue=1,
                ping_interval=None,
            )
            connections.append(producer)

            async def send_snapshots() -> None:
                await producer.send(encode_select(CONTEXT, _state("00000001" + "\u754c" * 56)))
                for sequence in range(2, 4_001):
                    name = f"{sequence:08d}" + "\u754c" * 56
                    await producer.send(encode_publish(CONTEXT, _state(name)))
                    if sequence % 32 == 0:
                        await asyncio.sleep(0)

            sender = asyncio.create_task(send_snapshots())
            tasks.append(sender)
            sequences = await asyncio.wait_for(asyncio.gather(sender, reader), timeout=12)
            assert sequences[1] == list(range(1, 4_001))

            async with asyncio.timeout(5):
                while upstream_subscriber in relay_listener.connections:
                    await asyncio.sleep(0.001)
            await asyncio.wait_for(monitor, timeout=2)

            snapshot = relay.state.snapshot()
            assert snapshot is not None and snapshot.seq == 4_000
            assert direct_subscriber in relay_listener.connections
            assert list(relay._subscribers) == [direct_subscriber]
            assert upstream_queue.maxsize == 16 and upstream_queue.qsize() <= 16
            assert 0 < max_backlog <= 16
            assert max_upstream_write_buffer > upstream_subscriber.transport.get_write_buffer_limits()[1]
            assert max_gateway_write_buffer > gateway_connection.transport.get_write_buffer_limits()[1]

            remote.transport.resume_reading()
            with pytest.raises(ConnectionClosed):
                async with asyncio.timeout(5):
                    while True:
                        await remote.recv()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for connection in connections:
                connection.transport.abort()
            await gateway.close()
            await relay.close()

    asyncio.run(scenario())
