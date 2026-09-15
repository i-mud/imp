"""Loopback WebSocket relay for normalized TinyScry state."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from contextlib import suppress
from http import HTTPStatus
from typing import Final

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from .protocol import (
    FeedStatus,
    RelayInfo,
    decode_client_message,
    encode_hello,
    encode_snapshot,
    encode_status,
)
from .state import RelayState, Snapshot

LOGGER = logging.getLogger(__name__)
MAX_FRAME_BYTES: Final = 65_536
POLICY_VIOLATION_CLOSE_CODE: Final = 1008


class RelayServer:
    """Owns network connections while RelayState owns all feed semantics."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8787,
        stale_after: float = 10.0,
        relay_name: str = "TinyScry relay",
        relay_version: str = "0.1.0",
        clock: Callable[[], float] = time.time,
        allow_non_loopback: bool = False,
    ) -> None:
        if host not in {"127.0.0.1", "::1", "localhost"} and not allow_non_loopback:
            raise ValueError("non-loopback binding requires allow_non_loopback=True")
        if host not in {"127.0.0.1", "::1", "localhost"}:
            LOGGER.warning("BINDING RELAY OUTSIDE LOOPBACK: security boundary is no longer local SSH only")
        self._host = host
        self._port = port
        self._clock = clock
        self._relay = RelayInfo(relay_name, relay_version)
        self._state = RelayState(stale_after=stale_after, clock=clock)
        self._subscribers: set[ServerConnection] = set()
        self._server: Server | None = None
        self._stale_task: asyncio.Task[None] | None = None
        self._last_feed: FeedStatus = self._state.feed_status(self._clock())

    @property
    def port(self) -> int:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("relay server is not running")
        socket_name = self._server.sockets[0].getsockname()
        return int(socket_name[1])

    @property
    def state(self) -> RelayState:
        return self._state

    async def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("relay server is already running")
        self._server = await serve(
            self._handle_connection,
            self._host,
            self._port,
            process_request=self._process_request,
            max_size=MAX_FRAME_BYTES,
        )
        self._stale_task = asyncio.create_task(self._watch_staleness())

    async def close(self) -> None:
        stale_task = self._stale_task
        self._stale_task = None
        if stale_task is not None:
            stale_task.cancel()
            with suppress(asyncio.CancelledError):
                await stale_task
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()
        self._subscribers.clear()

    async def _process_request(self, connection: ServerConnection, request: Request) -> Response | None:
        if request.path == "/healthz":
            return connection.respond(HTTPStatus.OK, json.dumps(self._state.health(), separators=(",", ":")))
        if request.path not in {"/state", "/ingest"}:
            return connection.respond(HTTPStatus.NOT_FOUND, "Not found\n")
        return None

    async def _handle_connection(self, connection: ServerConnection) -> None:
        request = connection.request
        if request is None:
            return
        path = request.path
        if path == "/state":
            await self._handle_subscriber(connection)
        elif path == "/ingest":
            await self._handle_producer(connection)

    async def _handle_subscriber(self, connection: ServerConnection) -> None:
        if not await self._send(connection, encode_hello(self._now_millis(), self._relay)):
            return
        snapshot = self._state.snapshot()
        if snapshot is not None and not await self._send_snapshot(connection, snapshot):
            return
        if not await self._send_status(connection, self._state.feed_status(self._clock())):
            return
        self._subscribers.add(connection)
        try:
            await connection.wait_closed()
        finally:
            self._subscribers.discard(connection)

    async def _handle_producer(self, connection: ServerConnection) -> None:
        self._state.producer_connected()
        await self._announce_feed_if_changed()
        try:
            async for raw_frame in connection:
                if not isinstance(raw_frame, str):
                    LOGGER.warning("rejecting producer frame: code=invalid_json path=")
                    await connection.close(POLICY_VIOLATION_CLOSE_CODE, "invalid protocol frame")
                    return
                decoded = decode_client_message(raw_frame)
                if not decoded.ok:
                    error = decoded.error
                    assert error is not None
                    LOGGER.warning("rejecting producer frame: code=%s path=%s", error.code, error.path)
                    await connection.close(POLICY_VIOLATION_CLOSE_CODE, "invalid protocol frame")
                    return
                message = decoded.value
                assert message is not None
                snapshot = self._state.apply_publish(message.state, self._clock())
                await self._broadcast_snapshot(snapshot)
                await self._announce_feed_if_changed()
        except ConnectionClosed:
            pass
        finally:
            self._state.producer_disconnected()
            await self._announce_feed_if_changed()

    async def _watch_staleness(self) -> None:
        while True:
            await asyncio.sleep(min(max(self._state.stale_after / 2, 0.01), 1.0))
            await self._announce_feed_if_changed()

    async def _announce_feed_if_changed(self) -> None:
        status = self._state.feed_status(self._clock())
        if status == self._last_feed:
            return
        self._last_feed = status
        await self._broadcast_status(status)

    async def _broadcast_snapshot(self, snapshot: Snapshot) -> None:
        payload = encode_snapshot(snapshot.seq, snapshot.at, snapshot.state)
        await self._broadcast(payload)

    async def _broadcast_status(self, feed: FeedStatus) -> None:
        await self._broadcast(encode_status(self._now_millis(), feed, None))

    async def _send_snapshot(self, connection: ServerConnection, snapshot: Snapshot) -> bool:
        return await self._send(connection, encode_snapshot(snapshot.seq, snapshot.at, snapshot.state))

    async def _send_status(self, connection: ServerConnection, feed: FeedStatus) -> bool:
        return await self._send(connection, encode_status(self._now_millis(), feed, None))

    async def _broadcast(self, payload: str) -> None:
        for subscriber in tuple(self._subscribers):
            if not await self._send(subscriber, payload):
                self._subscribers.discard(subscriber)

    async def _send(self, connection: ServerConnection, payload: str) -> bool:
        try:
            await connection.send(payload)
        except ConnectionClosed:
            return False
        return True

    def _now_millis(self) -> int:
        return int(self._clock() * 1000)


async def start_relay(
    host: str = "127.0.0.1",
    port: int = 8787,
    stale_after: float = 10.0,
    relay_name: str = "TinyScry relay",
    relay_version: str = "0.1.0",
    clock: Callable[[], float] = time.time,
    allow_non_loopback: bool = False,
) -> RelayServer:
    """Start an in-process relay; primarily useful for integration tests."""

    relay = RelayServer(
        host=host,
        port=port,
        stale_after=stale_after,
        relay_name=relay_name,
        relay_version=relay_version,
        clock=clock,
        allow_non_loopback=allow_non_loopback,
    )
    await relay.start()
    return relay
