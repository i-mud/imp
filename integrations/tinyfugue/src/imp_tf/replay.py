"""Replay adapter fixtures through normalization, with an offline verification mode."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TextIO

from imp_relay.protocol import GameState, decode_game_state

from imp_tf.normalize import Normalizer
from imp_tf.publisher import DEFAULT_RELAY_URL, RelayPublisher, state_to_wire
from imp_tf.records import parse_record


class StatePublisher(Protocol):
    async def select(self, context: None, state: GameState) -> None: ...


@dataclass(frozen=True)
class ReplayStats:
    records: int
    rejected: int
    states: int


def _state_json(state: GameState) -> str:
    validated = decode_game_state(state_to_wire(state))
    if not validated.ok:
        error = validated.error
        assert error is not None
        raise RuntimeError(f"normalization produced invalid state: {error.code} at {error.path}")
    assert validated.value is not None
    import json

    return json.dumps(state_to_wire(validated.value), separators=(",", ":"), ensure_ascii=False)


async def replay_lines(
    lines: Iterable[str],
    *,
    interval: float,
    publisher: StatePublisher | None,
    write_state: Callable[[str], None],
) -> ReplayStats:
    """Replay finite input, outputting or publishing only complete state changes."""

    normalizer = Normalizer()
    records = 0
    rejected = 0
    states = 0
    previous: GameState | None = None

    for line in lines:
        records += 1
        parsed = parse_record(line)
        if not parsed.ok:
            rejected += 1
            continue
        assert parsed.record is not None
        before = normalizer.state
        state = normalizer.apply(parsed.record)
        if state == before or state == previous:
            continue

        if states and interval:
            await asyncio.sleep(interval)
        if publisher is None:
            write_state(_state_json(state))
        else:
            await publisher.select(None, state)
        previous = state
        states += 1

    return ReplayStats(records=records, rejected=rejected, states=states)


def _nonnegative_interval(value: str) -> float:
    interval = float(value)
    if interval < 0:
        raise argparse.ArgumentTypeError("interval must be non-negative")
    return interval


def _arguments() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replay TinyFugue JSONL adapter fixtures")
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--relay-url", default=DEFAULT_RELAY_URL)
    parser.add_argument("--interval", type=_nonnegative_interval, default=0.25)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print normalized states without opening a network socket",
    )
    return parser


async def _run(args: argparse.Namespace, output: TextIO) -> ReplayStats:
    publisher: RelayPublisher | None = None
    if not args.dry_run:
        publisher = RelayPublisher(url=args.relay_url)

    try:
        with args.fixture.open(encoding="utf-8") as stream:
            return await replay_lines(
                stream,
                interval=args.interval,
                publisher=publisher,
                write_state=lambda line: print(line, file=output),
            )
    finally:
        if publisher is not None:
            await publisher.close()


def main() -> None:
    args = _arguments().parse_args()
    try:
        stats = asyncio.run(_run(args, sys.stdout))
    except (OSError, RuntimeError, ValueError) as error:
        print(f"replay failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    print(
        f"replay complete: records={stats.records} rejected={stats.rejected} states={stats.states}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
