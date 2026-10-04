from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from collections.abc import Iterator

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from imp_relay.gateway import GatewayServer, token_digest
from imp_relay.server import RelayServer

TOKEN = base64.urlsafe_b64encode(hashlib.sha256(b"slice20 valid credential").digest()).rstrip(b"=").decode()
INVALID_TOKEN = (
    base64.urlsafe_b64encode(hashlib.sha256(b"slice20 invalid credential").digest()).rstrip(b"=").decode()
)
AUTH = json.dumps({"type": "auth", "token": TOKEN})
INVALID_AUTH = json.dumps({"type": "auth", "token": INVALID_TOKEN})
DEPENDENCY_LOGGERS = ("websockets", "websockets.server", "websockets.client")
CONNECTION_LOGGER = "imp_relay.websocket"


@pytest.fixture
def capture(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    names = ("", "imp_relay", CONNECTION_LOGGER, "slice20.peer", *DEPENDENCY_LOGGERS)
    loggers = [logging.getLogger(name) for name in names]
    previous = [logger.level for logger in loggers]
    previous_capture_level = caplog.handler.level
    # Capture propagated DEBUG even if the application root is CRITICAL.
    caplog.handler.setLevel(logging.NOTSET)
    for logger in loggers:
        logger.setLevel(logging.NOTSET)
    logging.getLogger("slice20.peer").setLevel(logging.INFO)
    try:
        yield caplog
    finally:
        caplog.handler.setLevel(previous_capture_level)
        for logger, level in zip(loggers, previous, strict=True):
            logger.setLevel(level)


@pytest.mark.parametrize(
    "root_level", [logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL]
)
@pytest.mark.parametrize("descendant", ["websockets.server", "websockets.client"])
def test_explicit_dependency_debug_cannot_expose_authentication(
    capture: pytest.LogCaptureFixture, root_level: int, descendant: str
) -> None:
    root = logging.getLogger()
    root.setLevel(root_level)
    logging.getLogger(descendant).setLevel(logging.DEBUG)
    before = [logging.getLogger(name).level for name in DEPENDENCY_LOGGERS]
    peer_logger = logging.getLogger("slice20.peer")

    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()
        gateway = GatewayServer(token_digest(TOKEN), port=0, relay_url=f"ws://127.0.0.1:{relay.port}")
        await gateway.start()
        try:
            logging.getLogger("imp_relay.gateway").debug("application DEBUG remains available")
            async with connect(f"ws://127.0.0.1:{gateway.port}/state", logger=peer_logger) as peer:
                await peer.send(AUTH)
                assert json.loads(await peer.recv())["type"] == "hello"
                assert json.loads(await peer.recv())["type"] == "status"
            for frame in (
                INVALID_AUTH,
                f'{{"type":"auth","token":"{TOKEN}"',
                json.dumps({"type": "auth", "token": TOKEN, "extra": INVALID_TOKEN}),
                AUTH.encode("ascii"),
            ):
                async with connect(f"ws://127.0.0.1:{gateway.port}/state", logger=peer_logger) as peer:
                    await peer.send(frame)
                    with pytest.raises(ConnectionClosed):
                        await peer.recv()
                    assert peer.close_code == 1008
            async with connect(f"ws://127.0.0.1:{gateway.port}/state", logger=peer_logger) as peer:
                await peer.close(reason=INVALID_TOKEN)
            # The action bridge forwards its next payload verbatim to the relay.
            # An auth-shaped payload here tests the upstream client's diagnostics.
            async with connect(f"ws://127.0.0.1:{gateway.port}/action", logger=peer_logger) as peer:
                await peer.send(AUTH)
                await peer.send(INVALID_AUTH)
                with pytest.raises(ConnectionClosed):
                    await peer.recv()
                assert peer.close_code == 1000
        finally:
            await gateway.close()
            await relay.close()

    asyncio.run(scenario())
    output = capture.text
    for secret in (TOKEN, INVALID_TOKEN, AUTH, INVALID_AUTH):
        assert secret not in output
        assert secret.encode().hex() not in output
    assert not any(
        record.name == CONNECTION_LOGGER and record.levelno < max(logging.INFO, root_level)
        for record in capture.records
    )
    if root_level == logging.DEBUG:
        assert "application DEBUG remains available" in output
    if root_level <= logging.INFO:
        assert "connection open" in output
    else:
        assert "connection open" not in output
    assert [logging.getLogger(name).level for name in DEPENDENCY_LOGGERS] == before


@pytest.mark.parametrize("level", [logging.WARNING, logging.ERROR, logging.CRITICAL])
@pytest.mark.parametrize("boundary", [*DEPENDENCY_LOGGERS, CONNECTION_LOGGER, "imp_relay"])
@pytest.mark.parametrize("component", ["relay", "gateway"])
def test_start_and_restart_preserve_stricter_logging_restrictions(
    capture: pytest.LogCaptureFixture, level: int, boundary: str, component: str
) -> None:
    logging.getLogger().setLevel(logging.DEBUG)
    logging.getLogger(boundary).setLevel(level)
    before = [logging.getLogger(name).level for name in DEPENDENCY_LOGGERS]

    async def scenario() -> None:
        server = RelayServer(port=0) if component == "relay" else GatewayServer(token_digest(TOKEN), port=0)
        for _ in range(2):
            await server.start()
            try:
                assert server._server is not None
                assert server._server.logger.getEffectiveLevel() >= level
                async with connect(
                    f"ws://127.0.0.1:{server.port}/state", logger=logging.getLogger("slice20.peer")
                ):
                    pass
            finally:
                await server.close()

    asyncio.run(scenario())
    assert "server listening" not in capture.text
    assert "connection open" not in capture.text
    assert [logging.getLogger(name).level for name in DEPENDENCY_LOGGERS] == before


def test_later_component_cannot_weaken_earlier_root_restriction(capture: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        root = logging.getLogger()
        root.setLevel(logging.CRITICAL)
        relay = RelayServer(port=0)
        await relay.start()
        root.setLevel(logging.DEBUG)
        gateway = GatewayServer(token_digest(TOKEN), port=0, relay_url=f"ws://127.0.0.1:{relay.port}")
        try:
            for _ in range(2):
                await gateway.start()
                assert gateway._server is not None
                assert gateway._server.logger.getEffectiveLevel() >= logging.CRITICAL
                async with connect(
                    f"ws://127.0.0.1:{gateway.port}/state", logger=logging.getLogger("slice20.peer")
                ) as peer:
                    await peer.send(AUTH)
                    assert json.loads(await peer.recv())["type"] == "hello"
                await gateway.close()
            await relay.close()
            await relay.start()
            assert relay._server is not None
            assert relay._server.logger.getEffectiveLevel() >= logging.CRITICAL
        finally:
            await gateway.close()
            await relay.close()

    asyncio.run(scenario())
    assert "server listening" not in capture.text
    assert "connection open" not in capture.text
    assert TOKEN not in capture.text
    assert all(logging.getLogger(name).level == logging.NOTSET for name in DEPENDENCY_LOGGERS)


def test_unrelated_debug_connections_remain_visible_under_critical_root(
    capture: pytest.LogCaptureFixture,
) -> None:
    logging.getLogger().setLevel(logging.CRITICAL)
    logging.getLogger("websockets.client").setLevel(logging.DEBUG)

    async def echo(peer: ServerConnection) -> None:
        await peer.send(await peer.recv())

    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()
        try:
            async with serve(echo, "127.0.0.1", 0, logger=logging.getLogger("slice20.peer")) as external:
                port = external.sockets[0].getsockname()[1]
                async with connect(f"ws://127.0.0.1:{port}") as peer:
                    await peer.send("unrelated consumer DEBUG remains visible")
                    assert await peer.recv() == "unrelated consumer DEBUG remains visible"
        finally:
            await relay.close()

    asyncio.run(scenario())
    assert any(
        record.name == "websockets.client"
        and record.levelno == logging.DEBUG
        and "unrelated consumer DEBUG remains visible" in record.getMessage()
        for record in capture.records
    )
