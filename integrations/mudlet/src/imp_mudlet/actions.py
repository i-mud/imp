"""Consume trusted Imp actions for one active Mudlet context."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from typing import Final
from urllib.parse import urlsplit

from imp_relay.protocol import (
    ConsumerReadyMessage,
    ConsumerStatus,
    DispatchMessage,
    StateContext,
    decode_server_message,
    encode_consumer,
    encode_consumer_result,
)
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, WebSocketException

DEFAULT_CONSUMER_URL: Final = "ws://127.0.0.1:8787/action-consumer"
_RETRY_SECONDS: Final = 0.05

type ActionEmitter = Callable[[DispatchMessage], None]


def _validate_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "ws"
        or parsed.path != "/action-consumer"
        or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
    ):
        raise ValueError("consumer URL must be a loopback ws:// URL with the /action-consumer path")


class RelayActionConsumer:
    """Register one exact context and hand dispatches to Mudlet one at a time."""

    def __init__(
        self,
        context: StateContext,
        emit: ActionEmitter,
        *,
        url: str = DEFAULT_CONSUMER_URL,
    ) -> None:
        _validate_url(url)
        self.context = context
        self.url = url
        self._emit = emit
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._pending_id: str | None = None
        self._pending_result: asyncio.Future[ConsumerStatus] | None = None

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("action consumer is already started")
        self._task = asyncio.create_task(self._run())

    async def wait_ready(self) -> None:
        await self._ready.wait()

    def resolve(self, correlation: str, status: ConsumerStatus) -> bool:
        result = self._pending_result
        if result is None or result.done() or correlation != self._pending_id:
            return False
        result.set_result(status)
        return True

    async def close(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _run(self) -> None:
        try:
            while True:
                connection: ClientConnection | None = None
                result: asyncio.Future[ConsumerStatus] | None = None

                try:
                    connection = await connect(self.url, proxy=None)
                    await connection.send(encode_consumer(self.context))

                    raw = await connection.recv()
                    if not isinstance(raw, str):
                        return

                    decoded = decode_server_message(raw)
                    if (
                        not decoded.ok
                        or not isinstance(decoded.value, ConsumerReadyMessage)
                        or decoded.value.context != self.context
                    ):
                        return

                    self._ready.set()

                    raw = await connection.recv()
                    if not isinstance(raw, str):
                        return

                    decoded = decode_server_message(raw)
                    if (
                        not decoded.ok
                        or not isinstance(decoded.value, DispatchMessage)
                        or decoded.value.context != self.context
                    ):
                        return

                    dispatch = decoded.value
                    loop = asyncio.get_running_loop()
                    result = loop.create_future()
                    self._pending_id = dispatch.id
                    self._pending_result = result

                    self._emit(dispatch)
                    status = await result

                    await connection.send(encode_consumer_result(dispatch.id, status))

                except (ConnectionClosed, OSError, WebSocketException):
                    pass
                finally:
                    self._ready.clear()

                    if self._pending_result is result:
                        self._pending_result = None
                        self._pending_id = None

                    if result is not None and not result.done():
                        result.cancel()

                    if connection is not None:
                        with suppress(
                            ConnectionClosed,
                            OSError,
                            WebSocketException,
                        ):
                            await connection.close()

                await asyncio.sleep(_RETRY_SECONDS)
        finally:
            self._ready.clear()
            result = self._pending_result
            self._pending_result = None
            self._pending_id = None
            if result is not None and not result.done():
                result.cancel()
