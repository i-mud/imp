"""Fail-closed parsing for newline-delimited GMCP adapter records."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final

MAX_RECORD_CHARS: Final = 16_384
MAX_PACKAGE_CHARS: Final = 128
MAX_PAYLOAD_DEPTH: Final = 32
MAX_EPOCH_MS: Final = 9_007_199_254_740_991

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]


@dataclass(frozen=True)
class Record:
    """One client-emitted GMCP record, after envelope validation."""

    at: int
    package: str
    payload: JsonValue


@dataclass(frozen=True)
class ParseResult:
    """A parsed record or a bounded, input-safe rejection code."""

    record: Record | None
    error: str | None

    @property
    def ok(self) -> bool:
        return self.record is not None


def _invalid(error: str) -> ParseResult:
    return ParseResult(record=None, error=error)


def _is_safe_package(value: str) -> bool:
    return not any(
        ord(character) <= 0x1F or 0x7F <= ord(character) <= 0x9F or 0xD800 <= ord(character) <= 0xDFFF
        for character in value
    )


def _reject_json_constant(_value: str) -> object:
    raise ValueError("non-standard JSON constant")


def _is_json_value(value: object, depth: int = 0) -> bool:
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if depth >= MAX_PAYLOAD_DEPTH:
        return False
    if isinstance(value, list):
        return all(_is_json_value(item, depth + 1) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item, depth + 1) for key, item in value.items())
    return False


def parse_record(line: str) -> ParseResult:
    """Parse one record without ever returning attacker-controlled diagnostics.

    The line cap is checked before JSON decoding to bound work.  Callers log only
    ``error``; they must never log the supplied line.
    """

    if len(line) > MAX_RECORD_CHARS:
        return _invalid("record_too_large")

    try:
        parsed: object = json.loads(line, parse_constant=_reject_json_constant)
    except (TypeError, ValueError, RecursionError):
        return _invalid("invalid_json")

    if not isinstance(parsed, dict):
        return _invalid("record_not_object")

    at = parsed.get("at")
    if isinstance(at, bool) or not isinstance(at, int) or not 0 <= at <= MAX_EPOCH_MS:
        return _invalid("invalid_at")

    package = parsed.get("package")
    if (
        not isinstance(package, str)
        or not package
        or len(package) > MAX_PACKAGE_CHARS
        or not _is_safe_package(package)
    ):
        return _invalid("invalid_package")

    if "payload" not in parsed or not _is_json_value(parsed["payload"]):
        return _invalid("invalid_payload")

    return ParseResult(record=Record(at=at, package=package, payload=parsed["payload"]), error=None)
