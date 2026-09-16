"""Live feed: drain TinyFugue's private spool, normalize GMCP, publish to relay.

The spool reader and relay publisher are deliberately decoupled. A relay outage
must not stop the feed from draining/bounding TinyFugue's raw runtime spool, and
SIGTERM must not wait for RelayPublisher's reconnect loop. At most one pending
normalized state is queued while a publish is in flight; newer state replaces
that pending state so outage memory remains bounded.

A private normalized checkpoint in $XDG_RUNTIME_DIR lets a supervised feed
restart recover identity/vitals even after old raw spool generations have been
retired. The checkpoint contains only TinyScry's validated GameState, never raw
GMCP, and disappears with the user's runtime directory.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Protocol

from tinyscry_relay.protocol import GameState, decode_game_state

from tinyscry_tf.capture import parse_raw_gmcp
from tinyscry_tf.diagnostics import DiagnosticCapture
from tinyscry_tf.normalize import Normalizer
from tinyscry_tf.publisher import DEFAULT_RELAY_URL, RelayPublisher, state_to_wire
from tinyscry_tf.records import JsonValue, Record
from tinyscry_tf.spool import ProducerAlreadyRunning, RuntimeLayout, SpoolReader, acquire_producer_lock

LOGGER = logging.getLogger(__name__)
DEFAULT_POLL_INTERVAL_SECONDS = 0.2
_CHECKPOINT_MODE = 0o600


class SpoolSource(Protocol):
    dropped: int

    def read_lines(self) -> list[str]: ...


class StatePublisher(Protocol):
    async def publish(self, state: GameState) -> None: ...


def _seed_normalizer(initial_state: GameState | None) -> Normalizer:
    """Reconstruct Normalizer's accumulated fields from a validated checkpoint."""

    normalizer = Normalizer()
    if initial_state is None:
        return normalizer

    payload: dict[str, JsonValue] = {}
    character = initial_state.character
    if character is not None:
        payload["character_name"] = character.name
        if character.hp is not None:
            payload["health"] = character.hp.current
            payload["health_max"] = character.hp.max
        if character.mana is not None:
            payload["mana"] = character.mana.current
            payload["mana_max"] = character.mana.max
        if character.moves is not None:
            payload["movement"] = character.moves.current
            payload["movement_max"] = character.moves.max

    target = initial_state.target
    if target is not None:
        payload["opponent_name"] = target.name
        if target.health_percent is not None:
            payload["opponent_health"] = target.health_percent

    if payload:
        normalizer.apply(Record(at=0, package="Char.Status", payload=payload))
    return normalizer


def load_checkpoint(path: Path) -> GameState | None:
    """Load a private normalized runtime checkpoint; fail closed if malformed."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        LOGGER.warning("ignored unreadable TinyScry runtime checkpoint")
        return None

    decoded = decode_game_state(raw)
    if not decoded.ok or decoded.value is None:
        code = decoded.error.code if decoded.error is not None else "invalid_state"
        LOGGER.warning("ignored invalid TinyScry runtime checkpoint (%s)", code)
        return None
    return decoded.value


def store_checkpoint(path: Path, state: GameState) -> None:
    """Atomically replace the normalized runtime checkpoint with mode 0600."""

    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{os.getpid()}")
    fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _CHECKPOINT_MODE)
    try:
        os.fchmod(fd, _CHECKPOINT_MODE)
        with os.fdopen(fd, "w", encoding="utf-8", closefd=False) as handle:
            json.dump(state_to_wire(state), handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(staged, path)


def _offer_latest(queue: asyncio.Queue[GameState], state: GameState) -> None:
    """Keep at most one not-yet-started publish while another may be blocked."""

    if queue.full():
        with suppress(asyncio.QueueEmpty):
            queue.get_nowait()
            queue.task_done()
    queue.put_nowait(state)


async def _publish_worker(
    queue: asyncio.Queue[GameState],
    publisher: StatePublisher,
    published_count: list[int],
) -> None:
    while True:
        state = await queue.get()
        try:
            await publisher.publish(state)
            published_count[0] += 1
        finally:
            queue.task_done()


async def run_feed(
    source: SpoolSource,
    publisher: StatePublisher,
    *,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    diagnostics: DiagnosticCapture | None = None,
    stop: asyncio.Event | None = None,
    initial_state: GameState | None = None,
    checkpoint: Callable[[GameState], None] | None = None,
) -> None:
    """Drain continuously; publish asynchronously so relay outages cannot stall it."""

    normalizer = _seed_normalizer(initial_state)
    last_queued = initial_state
    received = rejected = 0
    published_count = [0]
    publish_queue: asyncio.Queue[GameState] = asyncio.Queue(maxsize=1)
    publish_task = asyncio.create_task(_publish_worker(publish_queue, publisher, published_count))

    try:
        while stop is None or not stop.is_set():
            for line in source.read_lines():
                received += 1
                if diagnostics is not None:
                    diagnostics.write(line)

                parsed = parse_raw_gmcp(line)
                if not parsed.ok:
                    rejected += 1
                    LOGGER.warning("skipped malformed raw GMCP record %d (%s)", received, parsed.error)
                    continue
                assert parsed.record is not None

                previous = normalizer.state
                state = normalizer.apply(parsed.record)
                if state == previous:
                    continue

                if checkpoint is not None:
                    checkpoint(state)

                if state != last_queued:
                    _offer_latest(publish_queue, state)
                    last_queued = state

            if stop is None:
                await asyncio.sleep(poll_interval)
            else:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=poll_interval)
    finally:
        publish_task.cancel()
        with suppress(asyncio.CancelledError):
            await publish_task
        LOGGER.info(
            "feed stopped: received=%d rejected=%d published=%d dropped=%d",
            received,
            rejected,
            published_count[0],
            source.dropped,
        )


def _arguments() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Drain TinyFugue's GMCP spool and publish to the relay")
    parser.add_argument("--relay-url", default=DEFAULT_RELAY_URL, help="relay ingest WebSocket URL")
    parser.add_argument(
        "--allow-non-loopback",
        action="store_true",
        help="explicitly permit a relay URL outside the loopback and SSH boundary",
    )
    parser.add_argument(
        "--diagnostic-capture",
        action="store_true",
        help="opt-in: persist raw GMCP lines to a bounded, private diagnostics directory",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        metavar="SECONDS",
        help="spool poll interval",
    )
    return parser


async def _serve(
    reader: SpoolReader,
    publisher: RelayPublisher,
    poll_interval: float,
    diagnostics: DiagnosticCapture | None,
    initial_state: GameState | None,
    checkpoint_path: Path,
) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    try:
        await run_feed(
            reader,
            publisher,
            poll_interval=poll_interval,
            diagnostics=diagnostics,
            stop=stop,
            initial_state=initial_state,
            checkpoint=lambda state: store_checkpoint(checkpoint_path, state),
        )
    finally:
        await publisher.close()


def main() -> None:
    args = _arguments().parse_args()
    if args.poll_interval <= 0:
        raise SystemExit("--poll-interval must be positive")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    layout = RuntimeLayout.resolve()
    try:
        lock_fd = acquire_producer_lock(layout.lock)
    except ProducerAlreadyRunning as error:
        raise SystemExit(str(error)) from None

    reader = SpoolReader(layout)
    diagnostics = DiagnosticCapture(layout.diagnostics) if args.diagnostic_capture else None
    publisher = RelayPublisher(url=args.relay_url, allow_non_loopback=args.allow_non_loopback)
    try:
        reader.open()
        initial_state = load_checkpoint(layout.checkpoint)
        if diagnostics is not None:
            diagnostics.open()
            LOGGER.info("diagnostic raw capture enabled at %s", layout.diagnostics)
        LOGGER.info("feed started: spool=%s lock=%s", layout.spool, layout.lock)
        asyncio.run(
            _serve(
                reader,
                publisher,
                args.poll_interval,
                diagnostics,
                initial_state,
                layout.checkpoint,
            )
        )
    finally:
        reader.close()
        if diagnostics is not None:
            diagnostics.close()
        os.close(lock_fd)


if __name__ == "__main__":
    main()
