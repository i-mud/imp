"""Authenticated-by-loopback publisher for the relay ingest socket."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Final
from urllib.parse import urlsplit

from tinyscry_relay.protocol import Character, GameState, Target, Vital, decode_game_state, encode_publish
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, WebSocketException

DEFAULT_RELAY_URL: Final = "ws://127.0.0.1:8787/ingest"
_INITIAL_BACKOFF_SECONDS: Final = 0.25
_MAX_BACKOFF_SECONDS: Final = 10.0


def _is_loopback(host: str | None) -> bool:
    return host in {"127.0.0.1", "::1", "localhost"}


def _validate_relay_url(url: str, allow_non_loopback: bool) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "ws" or parsed.path != "/ingest" or not parsed.hostname:
        raise ValueError("relay URL must be a ws:// host with the /ingest path")
    if not allow_non_loopback and not _is_loopback(parsed.hostname):
        raise ValueError("a non-loopback relay target requires --allow-non-loopback")


def _vital_to_wire(vital: Vital | None) -> dict[str, int] | None:
    if vital is None:
        return None
    return {"current": vital.current, "max": vital.max}


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
    if target is None:
        return None
    return {"name": target.name, "healthPercent": target.health_percent}


def state_to_wire(state: GameState) -> dict[str, object]:
    """Create the protocol's JSON field spelling without trusting our caller."""

    return {"character": _character_to_wire(state.character), "target": _target_to_wire(state.target)}


@dataclass
class RelayPublisher:
    """Maintains one ingest connection and retries transient transport failures."""

    url: str = DEFAULT_RELAY_URL
    allow_non_loopback: bool = False
    _connection: ClientConnection | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        _validate_relay_url(self.url, self.allow_non_loopback)

    async def publish(self, state: GameState) -> None:
        """Validate and publish a complete state, retrying until the relay returns."""

        decoded = decode_game_state(state_to_wire(state))
        if not decoded.ok:
            error = decoded.error
            assert error is not None
            raise ValueError(f"normalized state rejected: {error.code} at {error.path}")
        assert decoded.value is not None
        frame = encode_publish(decoded.value)

        backoff = _INITIAL_BACKOFF_SECONDS
        while True:
            try:
                if self._connection is None:
                    self._connection = await connect(self.url, proxy=None)
                await self._connection.send(frame)
                return
            except (ConnectionClosed, OSError, WebSocketException):
                await self._drop_connection()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)

    async def close(self) -> None:
        await self._drop_connection()

    async def _drop_connection(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                await connection.close()
            except (ConnectionClosed, OSError, WebSocketException):
                pass
