"""Fail-closed JSONL protocol between Mudlet Lua and its local helper."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final, cast

from imp_adapter.records import MAX_RECORD_CHARS, Record, parse_record
from imp_relay.protocol import LIMITS, ConsumerStatus

MAX_FRAME_CHARS: Final = MAX_RECORD_CHARS + 1024
MAX_PROFILE_CHARS: Final = 128
MAX_PROTOCOL_CHARS: Final = 32


@dataclass(frozen=True)
class InitMessage:
    profile: str
    connected: bool
    focused: bool


@dataclass(frozen=True)
class ConnectedMessage:
    pass


@dataclass(frozen=True)
class DisconnectedMessage:
    pass


@dataclass(frozen=True)
class FocusMessage:
    focused: bool


@dataclass(frozen=True)
class ProtocolMessage:
    name: str
    enabled: bool


@dataclass(frozen=True)
class GmcpMessage:
    record: Record


@dataclass(frozen=True)
class ActionResultMessage:
    id: str
    status: ConsumerStatus


LuaMessage = (
    InitMessage
    | ConnectedMessage
    | DisconnectedMessage
    | FocusMessage
    | ProtocolMessage
    | GmcpMessage
    | ActionResultMessage
)


@dataclass(frozen=True)
class DecodeResult:
    message: LuaMessage | None
    error: str | None

    @property
    def ok(self) -> bool:
        return self.message is not None


def _invalid(error: str) -> DecodeResult:
    return DecodeResult(None, error)


def _safe_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str) or not value or len(value) > limit:
        return None
    if any(ord(char) <= 0x1F or 0x7F <= ord(char) <= 0x9F or 0xD800 <= ord(char) <= 0xDFFF for char in value):
        return None
    return value


def _reject_json_constant(_value: str) -> object:
    raise ValueError("non-standard JSON constant")


def decode_lua_message(line: str) -> DecodeResult:
    raw = line.removesuffix("\n").removesuffix("\r")
    if len(raw) > MAX_FRAME_CHARS:
        return _invalid("frame_too_large")

    try:
        parsed: object = json.loads(raw, parse_constant=_reject_json_constant)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _invalid("invalid_json")

    if not isinstance(parsed, dict):
        return _invalid("frame_not_object")

    body = cast(dict[str, object], parsed)
    message_type = body.get("type")
    if not isinstance(message_type, str):
        return _invalid("invalid_type")

    if message_type == "init":
        profile = _safe_text(body.get("profile"), MAX_PROFILE_CHARS)
        connected = body.get("connected")
        focused = body.get("focused")
        if profile is None or not isinstance(connected, bool) or not isinstance(focused, bool):
            return _invalid("invalid_init")
        return DecodeResult(InitMessage(profile, connected, focused), None)

    if message_type == "connected":
        return DecodeResult(ConnectedMessage(), None)

    if message_type == "disconnected":
        return DecodeResult(DisconnectedMessage(), None)

    if message_type == "focus":
        focused = body.get("focused")
        if not isinstance(focused, bool):
            return _invalid("invalid_focus")
        return DecodeResult(FocusMessage(focused), None)

    if message_type == "protocol":
        name = _safe_text(body.get("name"), MAX_PROTOCOL_CHARS)
        enabled = body.get("enabled")
        if name is None or not isinstance(enabled, bool):
            return _invalid("invalid_protocol")
        return DecodeResult(ProtocolMessage(name, enabled), None)

    if message_type == "gmcp":
        if "payload" not in body:
            return _invalid("invalid_gmcp")

        envelope = json.dumps(
            {
                "at": body.get("at"),
                "package": body.get("package"),
                "payload": body["payload"],
            },
            separators=(",", ":"),
            ensure_ascii=True,
        )
        record = parse_record(envelope)
        if not record.ok or record.record is None:
            return _invalid(record.error or "invalid_gmcp")
        return DecodeResult(GmcpMessage(record.record), None)

    if message_type == "action-result":
        correlation = _safe_text(
            body.get("id"),
            LIMITS["maxCorrelationChars"],
        )
        status = body.get("status")
        if (
            correlation is None
            or not correlation.isascii()
            or not all(char.isalnum() or char == "_" for char in correlation)
            or status not in ("forwarded", "rejected")
        ):
            return _invalid("invalid_action_result")
        return DecodeResult(
            ActionResultMessage(
                correlation,
                status,
            ),
            None,
        )

    return _invalid("unknown_type")
