"""Bridge TF adapter records from stdin or a FIFO to relay ingest."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TextIO

from tinyscry_relay.protocol import GameState

from tinyscry_tf.normalize import Normalizer
from tinyscry_tf.publisher import DEFAULT_RELAY_URL, RelayPublisher
from tinyscry_tf.records import parse_record

LOGGER = logging.getLogger(__name__)


class StatePublisher(Protocol):
    async def publish(self, state: GameState) -> None: ...


@dataclass(frozen=True)
class BridgeStats:
    received: int
    rejected: int
    published: int


async def process_lines(stream: TextIO, publisher: StatePublisher) -> BridgeStats:
    """Normalize and publish records until a blocking text stream reaches EOF.

    ``readline`` runs off the event-loop thread so the WebSocket client can
    process close frames and keepalive traffic while stdin or a FIFO is idle.
    """

    normalizer = Normalizer()
    received = 0
    rejected = 0
    published = 0
    last_published: GameState | None = None

    while True:
        line = await asyncio.to_thread(stream.readline)
        if line == "":
            break
        received += 1
        parsed = parse_record(line)
        if not parsed.ok:
            rejected += 1
            # Never include raw MUD/TF input in logs. It may contain control text.
            LOGGER.warning("skipped malformed adapter record %d (%s)", received, parsed.error)
            continue

        assert parsed.record is not None

        previous = normalizer.state
        state = normalizer.apply(parsed.record)
        if state != previous and state != last_published:
            await publisher.publish(state)
            last_published = state
            published += 1

    return BridgeStats(received=received, rejected=rejected, published=published)


async def _run(stream: TextIO, publisher: RelayPublisher) -> BridgeStats:
    try:
        return await process_lines(stream, publisher)
    finally:
        await publisher.close()


def _arguments() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Publish TinyFugue GMCP adapter records to TinyScry relay")
    parser.add_argument(
        "--fifo",
        type=Path,
        help="read newline-delimited adapter records from this named pipe",
    )
    parser.add_argument("--relay-url", default=DEFAULT_RELAY_URL, help="relay ingest WebSocket URL")
    parser.add_argument(
        "--allow-non-loopback",
        action="store_true",
        help="explicitly permit a relay URL outside the loopback and SSH boundary",
    )
    return parser


def main() -> None:
    args = _arguments().parse_args()
    publisher = RelayPublisher(url=args.relay_url, allow_non_loopback=args.allow_non_loopback)

    # Record data is never interpolated into a shell command: no shell=True,
    # os.system, or subprocess call exists anywhere in this data path.
    if args.fifo is None:
        stats = asyncio.run(_run(sys.stdin, publisher))
    else:
        with args.fifo.open(encoding="utf-8") as stream:
            stats = asyncio.run(_run(stream, publisher))
    LOGGER.info(
        "bridge stopped: received=%d rejected=%d published=%d",
        stats.received,
        stats.rejected,
        stats.published,
    )


if __name__ == "__main__":
    main()
