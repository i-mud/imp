"""Convert versioned TinyFugue GMCP events into adapter JSONL."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from imp_adapter.records import ParseResult

from imp_tf.events import GmcpEvent, parse_tf_event

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CaptureStats:
    received: int
    rejected: int
    written: int


def parse_raw_gmcp(line: str) -> ParseResult:
    """Parse one ``IMP2 G`` spool event and return its checked GMCP record."""

    parsed = parse_tf_event(line)
    if not parsed.ok:
        return ParseResult(record=None, error=parsed.error)
    if not isinstance(parsed.event, GmcpEvent):
        return ParseResult(record=None, error="not_gmcp_event")
    return ParseResult(record=parsed.event.record, error=None)


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
