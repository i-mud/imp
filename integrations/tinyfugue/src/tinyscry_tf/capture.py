"""Convert TinyFugue's direct GMCP hook output into adapter JSONL."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TextIO

from tinyscry_tf.records import MAX_EPOCH_MS, MAX_RECORD_CHARS, ParseResult, parse_record

LOGGER = logging.getLogger(__name__)
_TIMESTAMP = re.compile(r"^[0-9]{1,13}(?:\.[0-9]{1,6})?$")


@dataclass(frozen=True)
class CaptureStats:
    received: int
    rejected: int
    written: int


def _invalid(error: str) -> ParseResult:
    return ParseResult(record=None, error=error)


def _reject_json_constant(_value: str) -> object:
    raise ValueError("non-standard JSON constant")


def parse_raw_gmcp(line: str) -> ParseResult:
    """Parse ``<epoch-seconds> <package> [JSON]`` emitted by the TF hook."""
    raw = line.removesuffix("\n").removesuffix("\r")
    if len(raw) > MAX_RECORD_CHARS:
        return _invalid("raw_record_too_large")

    timestamp_text, separator, event = raw.partition(" ")
    if not separator or not event or _TIMESTAMP.fullmatch(timestamp_text) is None:
        return _invalid("invalid_raw_event")

    try:
        at = int(Decimal(timestamp_text) * 1000)
    except (InvalidOperation, ValueError):
        return _invalid("invalid_raw_timestamp")
    if not 0 <= at <= MAX_EPOCH_MS:
        return _invalid("invalid_raw_timestamp")

    package, payload_separator, payload_text = event.partition(" ")
    if not package:
        return _invalid("invalid_raw_event")

    if payload_separator:
        try:
            payload: object = json.loads(payload_text, parse_constant=_reject_json_constant)
        except (TypeError, ValueError, json.JSONDecodeError):
            return _invalid("invalid_raw_json")
    else:
        payload = None

    envelope = json.dumps(
        {"at": at, "package": package, "payload": payload},
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return parse_record(envelope)


def encode_record(parsed: ParseResult) -> str:
    if parsed.record is None:
        raise ValueError("cannot encode a rejected raw record")
    return json.dumps(
        {
            "at": parsed.record.at,
            "package": parsed.record.package,
            "payload": parsed.record.payload,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )


def convert_lines(lines: Iterable[str], output: TextIO) -> CaptureStats:
    received = 0
    rejected = 0
    written = 0

    for line in lines:
        received += 1
        parsed = parse_raw_gmcp(line)
        if not parsed.ok:
            rejected += 1
            LOGGER.warning("skipped malformed raw GMCP record %d (%s)", received, parsed.error)
            continue

        output.write(encode_record(parsed))
        output.write("\n")
        output.flush()
        written += 1

    return CaptureStats(received=received, rejected=rejected, written=written)


def _arguments() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert TinyFugue raw GMCP capture to adapter JSONL")
    parser.add_argument("input", nargs="?", type=Path, help="raw capture file; omit to read standard input")
    parser.add_argument("--output", type=Path, help="adapter JSONL file; omit to write standard output")
    return parser


def main() -> None:
    args = _arguments().parse_args()
    input_stream = sys.stdin if args.input is None else args.input.open(encoding="utf-8")
    output_stream = sys.stdout if args.output is None else args.output.open("w", encoding="utf-8")

    try:
        stats = convert_lines(input_stream, output_stream)
    finally:
        if input_stream is not sys.stdin:
            input_stream.close()
        if output_stream is not sys.stdout:
            output_stream.close()

    LOGGER.info(
        "capture conversion stopped: received=%d rejected=%d written=%d",
        stats.received,
        stats.rejected,
        stats.written,
    )


if __name__ == "__main__":
    main()
