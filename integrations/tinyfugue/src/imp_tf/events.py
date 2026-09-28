"""Parser for versioned, session- and world-aware TinyFugue spool events."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

from imp_relay.protocol import LIMITS, StateContext

from imp_tf.records import MAX_EPOCH_MS, MAX_RECORD_CHARS, Record, parse_record

MAX_EVENT_CHARS: Final = MAX_RECORD_CHARS + 512
_SESSION = re.compile(r"^[A-Za-z0-9_]{1,128}$")
_INTEGER = re.compile(r"^[1-9][0-9]*$")
_TIMESTAMP = re.compile(r"^[0-9]{1,13}(?:\.[0-9]{1,6})?$")
_MAX_WORLD_CHARS = 128


@dataclass(frozen=True)
class GmcpEvent:
    session: str
    connection: int
    world: str
    record: Record


@dataclass(frozen=True)
class TextEvent:
    session: str
    connection: int
    world: str
    at: int
    text: str


@dataclass(frozen=True)
class ResetEvent:
    session: str
    connection: int
    world: str
    at: int


@dataclass(frozen=True)
class SelectEvent:
    session: str
    foreground: int
    connection: int
    world: str | None
    at: int

    @property
    def context(self) -> StateContext | None:
        if self.world is None:
            return None
        return StateContext(self.session, self.foreground, self.connection)


type FeedEvent = GmcpEvent | TextEvent | ResetEvent | SelectEvent


@dataclass(frozen=True)
class EventResult:
    event: FeedEvent | None
    error: str | None

    @property
    def ok(self) -> bool:
        return self.event is not None


def _reject_json_constant(_value: str) -> object:
    raise ValueError


def _invalid(error: str) -> EventResult:
    return EventResult(None, error)


def _decode_tf_token_value(token: str) -> str | None:
    decoded: list[str] = []
    index = 0
    while index < len(token):
        char = token[index]
        if char.isascii() and char.isalnum():
            decoded.append(char)
            index += 1
            continue
        if char != "_":
            return None
        end = token.find("_", index + 1)
        if end < 0:
            return None
        digits = token[index + 1 : end]
        if not digits.isascii() or not digits.isdigit():
            return None
        codepoint = int(digits)
        if codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
            return None
        decoded.append(chr(codepoint))
        index = end + 1

    value = "".join(decoded)
    if not value:
        return None
    if any(ord(char) <= 0x1F or 0x7F <= ord(char) <= 0x9F for char in value):
        return None
    return value


def decode_tf_token(token: str) -> str | None:
    """Decode a bounded TinyFugue textencode.tf world token."""

    value = _decode_tf_token_value(token)
    if value is None or len(value) > _MAX_WORLD_CHARS:
        return None
    return value


def decode_tf_text_token(token: str) -> str | None:
    """Decode one bounded, control-free received-text token."""

    value = _decode_tf_token_value(token)
    if value is None:
        return None
    utf16_length = len(value.encode("utf-16-le", errors="surrogatepass")) // 2
    if utf16_length > LIMITS["maxTextEventChars"]:
        return None
    return value


def _timestamp(value: str) -> int | None:
    if _TIMESTAMP.fullmatch(value) is None:
        return None
    try:
        timestamp = int(Decimal(value) * 1000)
    except (InvalidOperation, ValueError):
        return None
    return timestamp if 0 <= timestamp <= MAX_EPOCH_MS else None


def _positive_integer(value: str) -> int | None:
    return int(value) if _INTEGER.fullmatch(value) is not None else None


def _header(session: str, connection: str, world_token: str, timestamp: str) -> tuple[int, str, int] | None:
    if _SESSION.fullmatch(session) is None:
        return None
    generation = _positive_integer(connection)
    world = decode_tf_token(world_token)
    at = _timestamp(timestamp)
    if generation is None or world is None or at is None:
        return None
    return generation, world, at


def parse_tf_event(line: str) -> EventResult:
    raw = line.removesuffix("\n").removesuffix("\r")
    if len(raw) > MAX_EVENT_CHARS:
        return _invalid("raw_record_too_large")
    fields = raw.split(" ", 6)
    if len(fields) < 2 or fields[0] != "IMP2":
        return _invalid("invalid_event_version")

    kind = fields[1]
    if kind == "G":
        if len(fields) != 7:
            return _invalid("invalid_gmcp_event")
        _, _, session, connection, world_token, timestamp, gmcp = fields
        header = _header(session, connection, world_token, timestamp)
        if header is None:
            return _invalid("invalid_gmcp_event")
        generation, world, at = header
        package, separator, payload_text = gmcp.partition(" ")
        if not package:
            return _invalid("invalid_gmcp_event")
        if separator:
            try:
                payload: object = json.loads(payload_text, parse_constant=_reject_json_constant)
            except (TypeError, ValueError, json.JSONDecodeError):
                return _invalid("invalid_raw_json")
        else:
            payload = None
        envelope = json.dumps(
            {"at": at, "package": package, "payload": payload}, ensure_ascii=True, separators=(",", ":")
        )
        parsed = parse_record(envelope)
        if not parsed.ok or parsed.record is None:
            return _invalid(parsed.error or "invalid_raw_event")
        return EventResult(GmcpEvent(session, generation, world, parsed.record), None)

    if kind == "T":
        if len(fields) != 7:
            return _invalid("invalid_text_event")
        _, _, session, connection, world_token, timestamp, text_token = fields
        header = _header(session, connection, world_token, timestamp)
        if header is None:
            return _invalid("invalid_text_event")
        generation, world, at = header
        text = decode_tf_text_token(text_token)
        if text is None:
            return _invalid("invalid_text_event")
        return EventResult(TextEvent(session, generation, world, at, text), None)

    if kind == "R":
        if len(fields) != 6:
            return _invalid("invalid_reset_event")
        _, _, session, connection, world_token, timestamp = fields
        header = _header(session, connection, world_token, timestamp)
        if header is None:
            return _invalid("invalid_reset_event")
        generation, world, at = header
        return EventResult(ResetEvent(session, generation, world, at), None)

    if kind == "S":
        if len(fields) != 7:
            return _invalid("invalid_select_event")
        _, _, selected_session, foreground_text, connection_text, selected_world_token, selected_timestamp = (
            fields
        )
        if _SESSION.fullmatch(selected_session) is None:
            return _invalid("invalid_select_event")
        foreground = _positive_integer(foreground_text)
        selected_at = _timestamp(selected_timestamp)
        if foreground is None or selected_at is None:
            return _invalid("invalid_select_event")
        if selected_world_token == "-":
            if connection_text != "0":
                return _invalid("invalid_select_event")
            return EventResult(SelectEvent(selected_session, foreground, 0, None, selected_at), None)
        selected_connection = _positive_integer(connection_text)
        selected_world = decode_tf_token(selected_world_token)
        if selected_connection is None or selected_world is None:
            return _invalid("invalid_select_event")
        return EventResult(
            SelectEvent(selected_session, foreground, selected_connection, selected_world, selected_at), None
        )

    return _invalid("invalid_event_type")
