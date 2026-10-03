"""Loopback WebSocket relay for normalized state and trusted outbound actions."""

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

from .action import ActionBroker
from .protocol import (
    ActionMessage,
    ConsumerMessage,
    ConsumerResultMessage,
    FeedStatus,
    PublishMessage,
    RelayInfo,
    SelectMessage,
    TextMessage,
    decode_client_message,
    encode_action_result,
    encode_consumer_ready,
    encode_hello,
    encode_snapshot,
    encode_status,
    encode_text,
)
from .state import RelayState, Snapshot

LOGGER = logging.getLogger(__name__)
MAX_FRAME_BYTES: Final = 65_536
POLICY_VIOLATION_CLOSE_CODE: Final = 1008
_BROWSER_ORIGINS: Final = frozenset({"http://localhost:1420", "http://tauri.localhost"})
_BROWSER_ENDPOINTS: Final = frozenset({"/state", "/action", "/healthz"})
_PRIVILEGED_ENDPOINTS: Final = frozenset({"/ingest", "/action-consumer"})


class RelayServer:
    """Owns network connections while RelayState owns feed semantics."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8787,
        stale_after: float = 10.0,
        relay_name: str = "Imp relay",
        relay_version: str = "0.1.0",
        clock: Callable[[], float] = time.time,
    ) -> None:
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("relay binding is restricted to loopback")
        self._host = host
        self._port = port
        self._clock = clock
        self._relay = RelayInfo(relay_name, relay_version)
        self._state = RelayState(stale_after=stale_after, clock=clock)
        self._actions = ActionBroker()
        self._subscribers: set[ServerConnection] = set()
        self._server: Server | None = None
        self._stale_task: asyncio.Task[None] | None = None
        self._last_feed: FeedStatus = self._state.feed_status(self._clock())

    @property
    def port(self) -> int:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("relay server is not running")
        return int(self._server.sockets[0].getsockname()[1])

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
        path = request.path
        if path not in _BROWSER_ENDPOINTS | _PRIVILEGED_ENDPOINTS:
            return connection.respond(HTTPStatus.NOT_FOUND, "Not found\n")
        origins = request.headers.get_all("Origin")
        if len(origins) > 1:
            return connection.respond(HTTPStatus.FORBIDDEN, "Forbidden\n")
        origin = origins[0] if origins else None
        if path in _BROWSER_ENDPOINTS:
            if origin is not None and origin not in _BROWSER_ORIGINS:
                return connection.respond(HTTPStatus.FORBIDDEN, "Forbidden\n")
        elif origin is not None:
            return connection.respond(HTTPStatus.FORBIDDEN, "Forbidden\n")
        if path == "/healthz":
            return connection.respond(HTTPStatus.OK, json.dumps(self._state.health(), separators=(",", ":")))
        return None

    async def _handle_connection(self, connection: ServerConnection) -> None:
        request = connection.request
        if request is None:
            return
        if request.path == "/state":
            await self._handle_subscriber(connection)
        elif request.path == "/ingest":
            await self._handle_producer(connection)
        elif request.path == "/action":
            await self._handle_action(connection)
        elif request.path == "/action-consumer":
            await self._handle_action_consumer(connection)

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
                message = await self._decode_frame(connection, raw_frame)
                if message is None:
                    return
                snapshot: Snapshot | None
                if isinstance(message, SelectMessage):
                    snapshot = self._state.apply_select(message.context, message.state, self._clock())
                elif isinstance(message, PublishMessage):
                    snapshot = self._state.apply_publish(message.context, message.state, self._clock())
                elif isinstance(message, TextMessage):
                    if message.context == self._state.active_context:
                        await self._broadcast(encode_text(message.context, message.at, message.text))
                    continue
                else:
                    await self._close_invalid(connection)
                    return
                if snapshot is not None:
                    await self._broadcast_snapshot(snapshot)
                await self._announce_feed_if_changed()
        except ConnectionClosed:
            pass
        finally:
            self._state.producer_disconnected()
            await self._announce_feed_if_changed()

    async def _handle_action(self, connection: ServerConnection) -> None:
        try:
            raw_frame = await connection.recv()
        except ConnectionClosed:
            return
        message = await self._decode_frame(connection, raw_frame)
        if not isinstance(message, ActionMessage):
            if message is not None:
                await self._close_invalid(connection)
            return
        status, detail = await self._actions.forward(
            message.context, message.command, self._state.active_context
        )
        await self._send(connection, encode_action_result(status, detail))

    async def _handle_action_consumer(self, connection: ServerConnection) -> None:
        try:
            raw_frame = await connection.recv()
        except ConnectionClosed:
            return
        message = await self._decode_frame(connection, raw_frame)
        if not isinstance(message, ConsumerMessage):
            if message is not None:
                await self._close_invalid(connection)
            return
        registration = self._actions.register(connection, message.context, self._state.active_context)
        if registration is None:
            await connection.close(POLICY_VIOLATION_CLOSE_CODE, "consumer context is not eligible")
            return
        if not await self._send(connection, encode_consumer_ready(message.context)):
            self._actions.unregister(registration)
            return
        try:
            raw_frame = await connection.recv()
            result = await self._decode_frame(connection, raw_frame)
            if result is not None and (
                not isinstance(result, ConsumerResultMessage)
                or not self._actions.resolve(registration, result.id, result.status)
            ):
                await self._close_invalid(connection)
        except ConnectionClosed:
            pass
        finally:
            self._actions.unregister(registration)

    async def _decode_frame(self, connection: ServerConnection, raw_frame: str | bytes) -> object | None:
        if not isinstance(raw_frame, str):
            await self._close_invalid(connection)
            return None
        decoded = decode_client_message(raw_frame)
        if not decoded.ok:
            error = decoded.error
            assert error is not None
            LOGGER.warning("rejecting frame: code=%s path=%s", error.code, error.path)
            await self._close_invalid(connection)
            return None
        return decoded.value

    async def _close_invalid(self, connection: ServerConnection) -> None:
        await connection.close(POLICY_VIOLATION_CLOSE_CODE, "invalid protocol frame")

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
        await self._broadcast(encode_snapshot(snapshot.seq, snapshot.at, snapshot.context, snapshot.state))

    async def _broadcast_status(self, feed: FeedStatus) -> None:
        await self._broadcast(encode_status(self._now_millis(), feed, None))

    async def _send_snapshot(self, connection: ServerConnection, snapshot: Snapshot) -> bool:
        return await self._send(
            connection, encode_snapshot(snapshot.seq, snapshot.at, snapshot.context, snapshot.state)
        )

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
    relay_name: str = "Imp relay",
    relay_version: str = "0.1.0",
    clock: Callable[[], float] = time.time,
) -> RelayServer:
    """Start an in-process relay; primarily useful for integration tests."""

    relay = RelayServer(host, port, stale_after, relay_name, relay_version, clock)
    await relay.start()
    return relay
