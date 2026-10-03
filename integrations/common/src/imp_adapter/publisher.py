"""Loopback-only publisher for the relay ingest socket."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Final
from urllib.parse import urlsplit

from imp_relay.protocol import (
    Character,
    GameState,
    StateContext,
    Target,
    Vital,
    decode_client_message,
    decode_game_state,
    encode_publish,
    encode_select,
    encode_text,
)
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, WebSocketException

DEFAULT_RELAY_URL: Final = "ws://127.0.0.1:8787/ingest"
_INITIAL_BACKOFF_SECONDS: Final = 0.25
_MAX_BACKOFF_SECONDS: Final = 10.0


def _validate_relay_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "ws"
        or parsed.path != "/ingest"
        or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
    ):
        raise ValueError("relay URL must be a loopback ws:// URL with the /ingest path")


def _vital_to_wire(vital: Vital | None) -> dict[str, int] | None:
    return None if vital is None else {"current": vital.current, "max": vital.max}


def _character_to_wire(character: Character | None) -> dict[str, object] | None:
    if character is None:
        return None
    return {
        "name": character.name,
        "hp": _vital_to_wire(character.hp),
        "mana": _vital_to_wire(character.mana),
        "moves": _vital_to_wire(character.moves),
    }


def _target_to_wire(target: Target | None) -> dict[str, object] | None:
    return None if target is None else {"name": target.name, "healthPercent": target.health_percent}


def state_to_wire(state: GameState) -> dict[str, object]:
    return {"character": _character_to_wire(state.character), "target": _target_to_wire(state.target)}


def _validated_text_frame(context: StateContext, at: int, text: str) -> str:
    frame = encode_text(context, at, text)
    decoded = decode_client_message(frame)
    if not decoded.ok:
        error = decoded.error
        assert error is not None
        raise ValueError(f"text event rejected: {error.code} at {error.path}")
    return frame


def _validated(state: GameState) -> GameState:
    decoded = decode_game_state(state_to_wire(state))
    if not decoded.ok:
        error = decoded.error
        assert error is not None
        raise ValueError(f"normalized state rejected: {error.code} at {error.path}")
    assert decoded.value is not None
    return decoded.value


@dataclass
class RelayPublisher:
    """Keeps ingest connected and reasserts only the retained selection after reconnect."""

    url: str = DEFAULT_RELAY_URL
    _connection: ClientConnection | None = field(default=None, init=False, repr=False)
    _selected_context: StateContext | None = field(default=None, init=False, repr=False)
    _selected_state: GameState | None = field(default=None, init=False, repr=False)
    _selection_revision: int = field(default=0, init=False, repr=False)
    _connection_revision: int = field(default=-1, init=False, repr=False)
    _condition: asyncio.Condition = field(default_factory=asyncio.Condition, init=False, repr=False)
    _reconnect_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        _validate_relay_url(self.url)

    async def select(self, context: StateContext | None, state: GameState) -> None:
        checked = _validated(state)
        async with self._condition:
            if self._closed:
                raise RuntimeError("publisher is closed")
            self._selected_context = context
            self._selected_state = checked
            self._selection_revision += 1
            revision = self._selection_revision
        await self._send_selection(revision)

    async def publish(self, context: StateContext, state: GameState) -> None:
        checked = _validated(state)
        async with self._condition:
            if self._closed:
                raise RuntimeError("publisher is closed")
            if context != self._selected_context:
                raise ValueError("publish context is not the publisher's selected context")
            if checked != self._selected_state:
                self._selected_state = checked
                self._selection_revision += 1
            revision = self._selection_revision
            connection = self._connection
        # Retain state for selection recovery, not observation evidence for replay.
        if connection is None:
            return
        try:
            await connection.send(encode_publish(context, checked))
        except (ConnectionClosed, OSError, WebSocketException):
            await self._discard_connection(connection)
            return
        async with self._condition:
            if connection is not self._connection:
                return
            if revision == self._selection_revision:
                self._connection_revision = revision
                return
            self._connection_revision = -1
            current_revision = self._selection_revision
        await self._send_selection(current_revision)

    async def text(self, context: StateContext, at: int, text: str) -> bool:
        """Attempt one transient send on the current selected connection.

        This method never opens a connection, waits for reconnect, retries, or
        retains the text. A missing or not-yet-selected connection means drop.
        """

        frame = _validated_text_frame(context, at, text)

        async with self._condition:
            if self._closed or context != self._selected_context:
                return False
            connection = self._connection
            if connection is None or self._connection_revision != self._selection_revision:
                return False

        try:
            await connection.send(frame)
        except (ConnectionClosed, OSError, WebSocketException):
            await self._discard_connection(connection)
            return False
        return True

    async def _send_selection(self, requested_revision: int) -> None:
        while True:
            connection = await self._connection_for_send()
            async with self._condition:
                if connection is not self._connection:
                    continue
                if self._connection_revision >= requested_revision:
                    return
                revision = self._selection_revision
                context = self._selected_context
                state = self._selected_state
                assert state is not None
            try:
                await connection.send(encode_select(context, state))
            except (ConnectionClosed, OSError, WebSocketException):
                await self._discard_connection(connection)
                continue
            async with self._condition:
                if connection is not self._connection:
                    continue
                if revision == self._selection_revision:
                    self._connection_revision = revision
                    if revision >= requested_revision:
                        return
                else:
                    self._connection_revision = -1

    async def _connection_for_send(self) -> ClientConnection:
        async with self._condition:
            if self._closed:
                raise RuntimeError("publisher is closed")
            if self._reconnect_task is None:
                self._reconnect_task = asyncio.create_task(self._connection_loop())
            while self._connection is None and not self._closed:
                await self._condition.wait()
            if self._closed:
                raise RuntimeError("publisher is closed")
            assert self._connection is not None
            return self._connection

    async def _connection_loop(self) -> None:
        connection: ClientConnection | None = None
        backoff = _INITIAL_BACKOFF_SECONDS
        try:
            while True:
                async with self._condition:
                    if self._closed:
                        return
                try:
                    connection = await connect(self.url, proxy=None)
                    if not await self._activate_connection(connection):
                        return
                    backoff = _INITIAL_BACKOFF_SECONDS
                    await connection.wait_closed()
                except (ConnectionClosed, OSError, WebSocketException):
                    pass
                finally:
                    if connection is not None:
                        await self._discard_connection(connection)
                        connection = None
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
        finally:
            async with self._condition:
                if self._reconnect_task is asyncio.current_task():
                    self._reconnect_task = None
                self._condition.notify_all()

    async def _activate_connection(self, connection: ClientConnection) -> bool:
        while True:
            async with self._condition:
                if self._closed:
                    return False
                revision = self._selection_revision
                context = self._selected_context
                state = self._selected_state
                assert state is not None
            await connection.send(encode_select(context, state))
            async with self._condition:
                if revision != self._selection_revision:
                    continue
                self._connection = connection
                self._connection_revision = revision
                self._condition.notify_all()
                return True

    async def _discard_connection(self, connection: ClientConnection) -> None:
        async with self._condition:
            if self._connection is connection:
                self._connection = None
                self._connection_revision = -1
                self._condition.notify_all()
        try:
            await connection.close()
        except (ConnectionClosed, OSError, WebSocketException):
            pass

    async def close(self) -> None:
        async with self._condition:
            if self._closed:
                return
            self._closed = True
            self._selection_revision += 1
            task = self._reconnect_task
            self._connection = None
            self._connection_revision = -1
            self._condition.notify_all()
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
