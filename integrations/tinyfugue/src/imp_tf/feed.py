"""Drain versioned TinyFugue events, isolate per-world state, and publish the foreground."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
from collections.abc import Callable, Coroutine
from contextlib import suppress
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Protocol

from imp_adapter.normalize import Normalizer
from imp_adapter.publisher import DEFAULT_RELAY_URL, RelayPublisher, state_to_wire
from imp_adapter.records import JsonValue, Record
from imp_relay.protocol import GameState, StateContext, decode_game_state

from imp_tf.context import write_context_marker
from imp_tf.diagnostics import DiagnosticCapture
from imp_tf.events import GmcpEvent, ResetEvent, SelectEvent, TextEvent, parse_tf_event
from imp_tf.spool import ProducerAlreadyRunning, RuntimeLayout, SpoolReader, acquire_producer_lock

LOGGER = logging.getLogger(__name__)
DEFAULT_POLL_INTERVAL_SECONDS = 0.2
_CHECKPOINT_MODE = 0o600
EMPTY_STATE = GameState(character=None, target=None)


class SpoolSource(Protocol):
    dropped: int

    def read_lines(self) -> list[str]: ...


class StatePublisher(Protocol):
    async def select(self, context: StateContext | None, state: GameState) -> None: ...

    async def publish(self, context: StateContext, state: GameState) -> None: ...

    async def text(self, context: StateContext, at: int, text: str) -> bool: ...


@dataclass(frozen=True)
class WorldCheckpoint:
    connection: int
    state: GameState


@dataclass(frozen=True)
class FeedCheckpoint:
    session: str
    worlds: dict[str, WorldCheckpoint]


@dataclass
class _WorldRuntime:
    connection: int
    normalizer: Normalizer


def _seed_normalizer(initial_state: GameState | None) -> Normalizer:
    if initial_state is None:
        return Normalizer()
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
    normalizer = Normalizer(seeded_target=initial_state.target)
    if payload:
        normalizer.apply(Record(at=0, package="Char.Status", payload=payload))
    return normalizer


def load_checkpoint(path: Path) -> FeedCheckpoint | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        LOGGER.warning("ignored unreadable Imp runtime checkpoint")
        return None
    if not isinstance(raw, dict) or raw.get("version") != 2 or not isinstance(raw.get("session"), str):
        LOGGER.warning("ignored invalid Imp runtime checkpoint")
        return None
    session = raw["session"]
    if not session or len(session) > 128 or not session.replace("_", "a").isalnum():
        LOGGER.warning("ignored invalid Imp runtime checkpoint")
        return None
    raw_worlds = raw.get("worlds")
    if not isinstance(raw_worlds, dict):
        LOGGER.warning("ignored invalid Imp runtime checkpoint")
        return None
    worlds: dict[str, WorldCheckpoint] = {}
    for world, value in raw_worlds.items():
        if not isinstance(world, str) or not world or len(world) > 128 or not isinstance(value, dict):
            LOGGER.warning("ignored invalid Imp runtime checkpoint")
            return None
        connection = value.get("connection")
        if isinstance(connection, bool) or not isinstance(connection, int) or connection < 1:
            LOGGER.warning("ignored invalid Imp runtime checkpoint")
            return None
        decoded = decode_game_state(value.get("state"))
        if not decoded.ok or decoded.value is None:
            LOGGER.warning("ignored invalid Imp runtime checkpoint")
            return None
        worlds[world] = WorldCheckpoint(connection, decoded.value)
    return FeedCheckpoint(session, worlds)


def store_checkpoint(path: Path, checkpoint: FeedCheckpoint) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{os.getpid()}")
    payload = {
        "version": 2,
        "session": checkpoint.session,
        "worlds": {
            world: {"connection": item.connection, "state": state_to_wire(item.state)}
            for world, item in checkpoint.worlds.items()
        },
    }
    fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _CHECKPOINT_MODE)
    try:
        os.fchmod(fd, _CHECKPOINT_MODE)
        with os.fdopen(fd, "w", encoding="utf-8", closefd=False) as handle:
            json.dump(payload, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(staged, path)


def _checkpoint(session: str, worlds: dict[str, _WorldRuntime]) -> FeedCheckpoint:
    return FeedCheckpoint(
        session,
        {
            world: WorldCheckpoint(runtime.connection, runtime.normalizer.state)
            for world, runtime in worlds.items()
        },
    )


async def _replace_delivery(
    previous: asyncio.Task[None] | None,
    operation: Callable[[], Coroutine[object, object, None]],
    published_count: list[int],
) -> asyncio.Task[None]:
    if previous is not None:
        if not previous.done():
            previous.cancel()
        with suppress(asyncio.CancelledError):
            await previous

    async def deliver() -> None:
        await operation()
        published_count[0] += 1

    current = asyncio.create_task(deliver())
    await asyncio.sleep(0)
    if current.done():
        await current
    return current


async def run_feed(
    source: SpoolSource,
    publisher: StatePublisher,
    *,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    diagnostics: DiagnosticCapture | None = None,
    stop: asyncio.Event | None = None,
    initial_checkpoint: FeedCheckpoint | None = None,
    checkpoint: Callable[[FeedCheckpoint], None] | None = None,
    context_marker: Callable[[StateContext | None], None] | None = None,
) -> None:
    session = initial_checkpoint.session if initial_checkpoint is not None else None
    worlds = {
        world: _WorldRuntime(item.connection, _seed_normalizer(item.state))
        for world, item in (initial_checkpoint.worlds.items() if initial_checkpoint is not None else ())
    }
    selected_context: StateContext | None = None
    selected_world: str | None = None
    received = rejected = 0
    published_count = [0]
    text_forwarded = 0
    text_dropped = 0
    delivery: asyncio.Task[None] | None = None

    try:
        while stop is None or not stop.is_set():
            for line in source.read_lines():
                received += 1

                # Received MUD text is deliberately transient. Even explicit
                # diagnostic capture must never persist IMP2 T payloads.
                if diagnostics is not None and not line.startswith("IMP2 T "):
                    diagnostics.write(line)

                parsed = parse_tf_event(line)
                if not parsed.ok or parsed.event is None:
                    rejected += 1
                    LOGGER.warning("skipped malformed TinyFugue event %d (%s)", received, parsed.error)
                    continue
                event = parsed.event
                if event.session != session:
                    session = event.session
                    worlds.clear()
                    selected_context = None
                    selected_world = None
                    if context_marker is not None:
                        context_marker(None)

                if isinstance(event, SelectEvent):
                    selected_context = event.context
                    selected_world = event.world
                    if context_marker is not None:
                        context_marker(selected_context)
                    if event.world is None:
                        state = EMPTY_STATE
                    else:
                        runtime = worlds.get(event.world)
                        if runtime is None or runtime.connection != event.connection:
                            runtime = _WorldRuntime(event.connection, Normalizer())
                            worlds[event.world] = runtime
                        state = runtime.normalizer.state
                    delivery = await _replace_delivery(
                        delivery,
                        partial(publisher.select, selected_context, state),
                        published_count,
                    )

                elif isinstance(event, ResetEvent):
                    existing = worlds.get(event.world)
                    if existing is not None and event.connection < existing.connection:
                        continue
                    runtime = _WorldRuntime(event.connection, Normalizer())
                    worlds[event.world] = runtime
                    if selected_world == event.world and selected_context is not None:
                        selected_context = StateContext(
                            event.session, selected_context.foreground, event.connection
                        )
                        if context_marker is not None:
                            context_marker(selected_context)
                        delivery = await _replace_delivery(
                            delivery,
                            partial(publisher.select, selected_context, EMPTY_STATE),
                            published_count,
                        )

                elif isinstance(event, TextEvent):
                    if (
                        selected_world == event.world
                        and selected_context is not None
                        and selected_context.connection == event.connection
                    ):
                        if await publisher.text(selected_context, event.at, event.text):
                            text_forwarded += 1
                        else:
                            text_dropped += 1
                    else:
                        text_dropped += 1

                elif isinstance(event, GmcpEvent):
                    runtime = worlds.get(event.world)
                    if runtime is not None and event.connection < runtime.connection:
                        continue
                    if runtime is None or runtime.connection != event.connection:
                        runtime = _WorldRuntime(event.connection, Normalizer())
                        worlds[event.world] = runtime
                    previous = runtime.normalizer.state
                    state = runtime.normalizer.apply(event.record)
                    if (
                        state != previous
                        and selected_world == event.world
                        and selected_context is not None
                        and selected_context.connection == event.connection
                    ):
                        current_context = selected_context
                        delivery = await _replace_delivery(
                            delivery,
                            partial(publisher.publish, current_context, state),
                            published_count,
                        )

                if checkpoint is not None and session is not None and not isinstance(event, TextEvent):
                    checkpoint(_checkpoint(session, worlds))

            if stop is None:
                await asyncio.sleep(poll_interval)
            else:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=poll_interval)
    finally:
        if delivery is not None:
            delivery.cancel()
            with suppress(asyncio.CancelledError):
                await delivery
        LOGGER.info(
            "feed stopped: received=%d rejected=%d published=%d text_forwarded=%d text_dropped=%d dropped=%d",
            received,
            rejected,
            published_count[0],
            text_forwarded,
            text_dropped,
            source.dropped,
        )


def _arguments() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Drain TinyFugue's event spool and publish to the relay")
    parser.add_argument("--relay-url", default=DEFAULT_RELAY_URL, help="loopback relay ingest WebSocket URL")
    parser.add_argument(
        "--diagnostic-capture",
        action="store_true",
        help="opt-in: persist raw TinyFugue event lines to bounded private diagnostics",
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
    initial_checkpoint: FeedCheckpoint | None,
    layout: RuntimeLayout,
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
            initial_checkpoint=initial_checkpoint,
            checkpoint=lambda value: store_checkpoint(layout.checkpoint, value),
            context_marker=lambda value: write_context_marker(layout.context, value),
        )
    finally:
        write_context_marker(layout.context, None)
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
    publisher = RelayPublisher(url=args.relay_url)
    try:
        write_context_marker(layout.context, None)
        reader.open()
        initial_checkpoint = load_checkpoint(layout.checkpoint)
        if diagnostics is not None:
            diagnostics.open()
            LOGGER.info("diagnostic raw capture enabled at %s", layout.diagnostics)
        LOGGER.info("feed started: spool=%s lock=%s", layout.spool, layout.lock)
        asyncio.run(_serve(reader, publisher, args.poll_interval, diagnostics, initial_checkpoint, layout))
    finally:
        write_context_marker(layout.context, None)
        reader.close()
        if diagnostics is not None:
            diagnostics.close()
        os.close(lock_fd)


if __name__ == "__main__":
    main()
