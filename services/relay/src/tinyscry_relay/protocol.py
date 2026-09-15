"""Canonical codec for TinyScry protocol version 1."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Final, Literal, Never

PROTOCOL_VERSION: Final = 1
MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991
MAX_TYPE_CHARS: Final = 32
LIMITS: Final = {
    "maxFrameChars": 16_384,
    "maxNameChars": 64,
    "maxDetailChars": 256,
    "maxRelayIdentChars": 64,
    "maxVitalValue": 1_000_000_000,
}

type FeedStatus = Literal["live", "stale", "down"]


@dataclass(frozen=True)
class Vital:
    current: int
    max: int


@dataclass(frozen=True)
class Character:
    name: str
    hp: Vital | None
    mana: Vital | None
    moves: Vital | None


@dataclass(frozen=True)
class Target:
    name: str
    health_percent: float | None


@dataclass(frozen=True)
class GameState:
    character: Character | None
    target: Target | None


@dataclass(frozen=True)
class RelayInfo:
    name: str
    version: str


@dataclass(frozen=True)
class HelloMessage:
    type: Literal["hello"]
    protocol: int
    at: int
    relay: RelayInfo


@dataclass(frozen=True)
class SnapshotMessage:
    type: Literal["snapshot"]
    protocol: int
    seq: int
    at: int
    state: GameState


@dataclass(frozen=True)
class StatusMessage:
    type: Literal["status"]
    protocol: int
    at: int
    feed: FeedStatus
    detail: str | None


@dataclass(frozen=True)
class PublishMessage:
    type: Literal["publish"]
    protocol: int
    state: GameState


type ServerMessage = HelloMessage | SnapshotMessage | StatusMessage
type ClientMessage = PublishMessage


@dataclass(frozen=True)
class ProtocolError:
    code: str
    path: str
    message: str


@dataclass(frozen=True)
class DecodeResult[T]:
    ok: bool
    value: T | None = None
    error: ProtocolError | None = None


def _ok[T](value: T) -> DecodeResult[T]:
    return DecodeResult(ok=True, value=value)


def _fail(code: str, path: str, message: str) -> DecodeResult[Never]:
    return DecodeResult(ok=False, error=ProtocolError(code, path, message))


def _propagate(result: DecodeResult[object]) -> DecodeResult[Never]:
    assert result.error is not None
    return DecodeResult(ok=False, error=result.error)


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le", "surrogatepass")) // 2


def _is_safe_text(value: str) -> bool:
    return all(
        not (code_point <= 0x1F or 0x7F <= code_point <= 0x9F or 0xD800 <= code_point <= 0xDFFF)
        for code_point in map(ord, value)
    )


def _read_object(value: object, path: str) -> DecodeResult[dict[str, object]]:
    if not isinstance(value, dict):
        return _fail("invalid_field", path, "expected an object")
    if not all(isinstance(key, str) for key in value):
        return _fail("invalid_field", path, "expected an object")
    return _ok(value)


def _read_text(value: object, path: str, max_chars: int) -> DecodeResult[str]:
    if not isinstance(value, str):
        return _fail("invalid_field", path, "expected a string")
    if value == "":
        return _fail("invalid_field", path, "expected a non-empty string")
    if _utf16_length(value) > max_chars:
        return _fail("invalid_field", path, f"longer than {max_chars} characters")
    if not _is_safe_text(value):
        return _fail("invalid_field", path, "contains control or malformed characters")
    return _ok(value)


def _read_integer(value: object, path: str, minimum: int, maximum: int) -> DecodeResult[int]:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or (isinstance(value, float) and not value.is_integer())
    ):
        return _fail("invalid_field", path, "expected an integer")
    if value < minimum or value > maximum:
        return _fail("invalid_field", path, f"outside {minimum}..{maximum}")
    return _ok(int(value))


def _read_vital(value: object, path: str) -> DecodeResult[Vital | None]:
    if value is None:
        return _ok(None)
    object_result = _read_object(value, path)
    if not object_result.ok:
        return _propagate(object_result)
    object_value = object_result.value
    assert object_value is not None
    current = _read_integer(object_value.get("current"), f"{path}.current", 0, LIMITS["maxVitalValue"])
    if not current.ok:
        return _propagate(current)
    maximum = _read_integer(object_value.get("max"), f"{path}.max", 0, LIMITS["maxVitalValue"])
    if not maximum.ok:
        return _propagate(maximum)
    assert current.value is not None and maximum.value is not None
    return _ok(Vital(current.value, maximum.value))


def _read_character(value: object, path: str) -> DecodeResult[Character | None]:
    if value is None:
        return _ok(None)
    object_result = _read_object(value, path)
    if not object_result.ok:
        return _propagate(object_result)
    object_value = object_result.value
    assert object_value is not None
    name = _read_text(object_value.get("name"), f"{path}.name", LIMITS["maxNameChars"])
    if not name.ok:
        return _propagate(name)
    hp = _read_vital(object_value.get("hp"), f"{path}.hp")
    if not hp.ok:
        return _propagate(hp)
    mana = _read_vital(object_value.get("mana"), f"{path}.mana")
    if not mana.ok:
        return _propagate(mana)
    moves = _read_vital(object_value.get("moves"), f"{path}.moves")
    if not moves.ok:
        return _propagate(moves)
    assert name.value is not None
    return _ok(Character(name.value, hp.value, mana.value, moves.value))


def _read_target(value: object, path: str) -> DecodeResult[Target | None]:
    if value is None:
        return _ok(None)
    object_result = _read_object(value, path)
    if not object_result.ok:
        return _propagate(object_result)
    object_value = object_result.value
    assert object_value is not None
    name = _read_text(object_value.get("name"), f"{path}.name", LIMITS["maxNameChars"])
    if not name.ok:
        return _propagate(name)
    health_percent = object_value.get("healthPercent")
    if health_percent is None:
        assert name.value is not None
        return _ok(Target(name.value, None))
    if (
        isinstance(health_percent, bool)
        or not isinstance(health_percent, (int, float))
        or not math.isfinite(health_percent)
        or not 0 <= health_percent <= 100
    ):
        return _fail("invalid_field", f"{path}.healthPercent", "expected a finite number in 0..100")
    assert name.value is not None
    return _ok(Target(name.value, health_percent))


def _read_relay_info(value: object, path: str) -> DecodeResult[RelayInfo]:
    object_result = _read_object(value, path)
    if not object_result.ok:
        return _propagate(object_result)
    object_value = object_result.value
    assert object_value is not None
    name = _read_text(object_value.get("name"), f"{path}.name", LIMITS["maxRelayIdentChars"])
    if not name.ok:
        return _propagate(name)
    version = _read_text(object_value.get("version"), f"{path}.version", LIMITS["maxRelayIdentChars"])
    if not version.ok:
        return _propagate(version)
    assert name.value is not None and version.value is not None
    return _ok(RelayInfo(name.value, version.value))


def _read_feed_status(value: object, path: str) -> DecodeResult[FeedStatus]:
    if value in ("live", "stale", "down"):
        return _ok(value)
    return _fail("invalid_field", path, 'expected "live", "stale" or "down"')


def _read_detail(value: object, path: str) -> DecodeResult[str | None]:
    if value is None:
        return _ok(None)
    return _read_text(value, path, LIMITS["maxDetailChars"])


def decode_game_state(value: object, path: str = "state") -> DecodeResult[GameState]:
    object_result = _read_object(value, path)
    if not object_result.ok:
        return _propagate(object_result)
    object_value = object_result.value
    assert object_value is not None
    character = _read_character(object_value.get("character"), f"{path}.character")
    if not character.ok:
        return _propagate(character)
    target = _read_target(object_value.get("target"), f"{path}.target")
    if not target.ok:
        return _propagate(target)
    return _ok(GameState(character.value, target.value))


def _reject_json_constant(_: str) -> object:
    raise ValueError


def _read_envelope(raw: str) -> DecodeResult[tuple[str, int, dict[str, object]]]:
    if _utf16_length(raw) > LIMITS["maxFrameChars"]:
        return _fail("frame_too_large", "", f"frame longer than {LIMITS['maxFrameChars']} characters")
    try:
        parsed = json.loads(raw, parse_constant=_reject_json_constant)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _fail("invalid_json", "", "frame is not valid JSON")
    object_result = _read_object(parsed, "")
    if not object_result.ok:
        return _propagate(object_result)
    body = object_result.value
    assert body is not None
    protocol = _read_integer(body.get("protocol"), "protocol", 0, MAX_SAFE_INTEGER)
    if not protocol.ok:
        return _propagate(protocol)
    assert protocol.value is not None
    if protocol.value != PROTOCOL_VERSION:
        return _fail("unsupported_protocol", "protocol", f"expected version {PROTOCOL_VERSION}")
    message_type = _read_text(body.get("type"), "type", MAX_TYPE_CHARS)
    if not message_type.ok:
        return _propagate(message_type)
    assert message_type.value is not None
    return _ok((message_type.value, protocol.value, body))


def decode_server_message(raw: str) -> DecodeResult[ServerMessage]:
    envelope = _read_envelope(raw)
    if not envelope.ok:
        return _propagate(envelope)
    envelope_value = envelope.value
    assert envelope_value is not None
    message_type, protocol, body = envelope_value
    at = _read_integer(body.get("at"), "at", 0, MAX_SAFE_INTEGER)
    if not at.ok:
        return _propagate(at)
    assert at.value is not None
    if message_type == "hello":
        relay = _read_relay_info(body.get("relay"), "relay")
        if not relay.ok:
            return _propagate(relay)
        assert relay.value is not None
        return _ok(HelloMessage("hello", protocol, at.value, relay.value))
    if message_type == "snapshot":
        seq = _read_integer(body.get("seq"), "seq", 0, MAX_SAFE_INTEGER)
        if not seq.ok:
            return _propagate(seq)
        state = decode_game_state(body.get("state"))
        if not state.ok:
            return _propagate(state)
        assert seq.value is not None and state.value is not None
        return _ok(SnapshotMessage("snapshot", protocol, seq.value, at.value, state.value))
    if message_type == "status":
        feed = _read_feed_status(body.get("feed"), "feed")
        if not feed.ok:
            return _propagate(feed)
        detail = _read_detail(body.get("detail"), "detail")
        if not detail.ok:
            return _propagate(detail)
        assert feed.value is not None
        return _ok(StatusMessage("status", protocol, at.value, feed.value, detail.value))
    return _fail("unknown_type", "type", "unsupported message type")


def decode_client_message(raw: str) -> DecodeResult[ClientMessage]:
    envelope = _read_envelope(raw)
    if not envelope.ok:
        return _propagate(envelope)
    envelope_value = envelope.value
    assert envelope_value is not None
    message_type, protocol, body = envelope_value
    if message_type != "publish":
        return _fail("unknown_type", "type", "unsupported message type")
    state = decode_game_state(body.get("state"))
    if not state.ok:
        return _propagate(state)
    assert state.value is not None
    return _ok(PublishMessage("publish", protocol, state.value))


def _wire_value(value: object) -> object:
    if isinstance(value, Vital):
        return {"current": value.current, "max": value.max}
    if isinstance(value, Character):
        return {
            "name": value.name,
            "hp": _wire_value(value.hp),
            "mana": _wire_value(value.mana),
            "moves": _wire_value(value.moves),
        }
    if isinstance(value, Target):
        return {"name": value.name, "healthPercent": value.health_percent}
    if isinstance(value, GameState):
        return {"character": _wire_value(value.character), "target": _wire_value(value.target)}
    if isinstance(value, RelayInfo):
        return {"name": value.name, "version": value.version}
    if isinstance(value, HelloMessage):
        return {
            "type": value.type,
            "protocol": value.protocol,
            "at": value.at,
            "relay": _wire_value(value.relay),
        }
    if isinstance(value, SnapshotMessage):
        return {
            "type": value.type,
            "protocol": value.protocol,
            "seq": value.seq,
            "at": value.at,
            "state": _wire_value(value.state),
        }
    if isinstance(value, StatusMessage):
        return {
            "type": value.type,
            "protocol": value.protocol,
            "at": value.at,
            "feed": value.feed,
            "detail": value.detail,
        }
    if isinstance(value, PublishMessage):
        return {"type": value.type, "protocol": value.protocol, "state": _wire_value(value.state)}
    return value


def _encode(value: object) -> str:
    return json.dumps(_wire_value(value), separators=(",", ":"), ensure_ascii=True)


def encode_hello(at: int, relay: RelayInfo) -> str:
    return _encode(HelloMessage("hello", PROTOCOL_VERSION, at, relay))


def encode_snapshot(seq: int, at: int, state: GameState) -> str:
    return _encode(SnapshotMessage("snapshot", PROTOCOL_VERSION, seq, at, state))


def encode_status(at: int, feed: FeedStatus, detail: str | None) -> str:
    return _encode(StatusMessage("status", PROTOCOL_VERSION, at, feed, detail))


def encode_publish(state: GameState) -> str:
    return _encode(PublishMessage("publish", PROTOCOL_VERSION, state))
