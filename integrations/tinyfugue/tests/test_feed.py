from __future__ import annotations

import asyncio
import stat
from collections.abc import Callable
from pathlib import Path

from tinyscry_relay.protocol import GameState, StateContext

from tinyscry_tf.diagnostics import DiagnosticCapture
from tinyscry_tf.feed import FeedCheckpoint, WorldCheckpoint, load_checkpoint, run_feed, store_checkpoint

EMPTY = GameState(character=None, target=None)


class _FakeSource:
    dropped = 0

    def __init__(self, batches: list[list[str]]) -> None:
        self.batches = list(batches)
        self.calls = 0

    def read_lines(self) -> list[str]:
        self.calls += 1
        return self.batches.pop(0) if self.batches else []


class _CollectingPublisher:
    def __init__(self) -> None:
        self.operations: list[tuple[str, StateContext | None, GameState]] = []
        self.texts: list[tuple[StateContext, int, str]] = []

    async def select(self, context: StateContext | None, state: GameState) -> None:
        self.operations.append(("select", context, state))

    async def publish(self, context: StateContext, state: GameState) -> None:
        self.operations.append(("publish", context, state))

    async def text(self, context: StateContext, at: int, text: str) -> bool:
        self.texts.append((context, at, text))
        return True


async def _run(
    source: _FakeSource,
    publisher: _CollectingPublisher,
    *,
    initial_checkpoint: FeedCheckpoint | None = None,
    checkpoint: Callable[[FeedCheckpoint], None] | None = None,
    context_marker: Callable[[StateContext | None], None] | None = None,
    diagnostics: DiagnosticCapture | None = None,
) -> None:
    stop = asyncio.Event()

    async def stop_after_drain() -> None:
        while source.batches:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.08)
        stop.set()

    await asyncio.gather(
        run_feed(
            source,
            publisher,
            poll_interval=0.005,
            stop=stop,
            initial_checkpoint=initial_checkpoint,
            checkpoint=checkpoint,
            context_marker=context_marker,
            diagnostics=diagnostics,
        ),
        stop_after_drain(),
    )


def _character_name(state: GameState) -> str | None:
    return state.character.name if state.character is not None else None


def test_background_world_updates_cache_without_overwriting_foreground() -> None:
    async def scenario() -> None:
        source = _FakeSource(
            [
                ["TS2 S session1 1 1 Alpha 1"],
                ['TS2 G session1 1 Alpha 2 Char.Status {"character_name":"Alice","health":"9"}'],
                ['TS2 G session1 2 Beta 3 Char.Status {"character_name":"Bob","health":"7"}'],
                ["TS2 S session1 2 2 Beta 4"],
            ]
        )
        publisher = _CollectingPublisher()
        markers: list[StateContext | None] = []

        await _run(source, publisher, context_marker=markers.append)

        assert [
            (kind, context.foreground if context else None, _character_name(state))
            for kind, context, state in publisher.operations
        ] == [
            ("select", 1, None),
            ("publish", 1, "Alice"),
            ("select", 2, "Bob"),
        ]
        assert markers == [
            None,
            StateContext("session1", 1, 1),
            StateContext("session1", 2, 2),
        ]

    asyncio.run(scenario())


def test_selection_and_gmcp_from_one_spool_drain_keep_the_context_order() -> None:
    class _StrictPublisher(_CollectingPublisher):
        selected: StateContext | None = None

        async def select(self, context: StateContext | None, state: GameState) -> None:
            self.selected = context
            await super().select(context, state)

        async def publish(self, context: StateContext, state: GameState) -> None:
            assert context == self.selected
            await super().publish(context, state)

    async def scenario() -> None:
        source = _FakeSource(
            [
                [
                    "TS2 S session1 1 1 Alpha 1",
                    'TS2 G session1 1 Alpha 2 Char.Status {"character_name":"Alice"}',
                ]
            ]
        )
        publisher = _StrictPublisher()

        await _run(source, publisher)

        assert [operation[0] for operation in publisher.operations] == ["select", "publish"]
        assert _character_name(publisher.operations[-1][2]) == "Alice"

    asyncio.run(scenario())


def test_text_events_forward_only_for_the_selected_exact_connection() -> None:
    async def scenario() -> None:
        source = _FakeSource(
            [
                [
                    "TS2 S s1 1 1 Alpha 1",
                    "TS2 T s1 1 Alpha 2 Alpha_32_one",
                    "TS2 T s1 2 Beta 3 Background",
                    "TS2 S s1 2 2 Beta 4",
                    "TS2 T s1 1 Alpha 5 Old_32_foreground",
                    "TS2 T s1 2 Beta 6 Beta_32_now",
                ]
            ]
        )
        publisher = _CollectingPublisher()

        await _run(source, publisher)

        assert publisher.texts == [
            (StateContext("s1", 1, 1), 2000, "Alpha one"),
            (StateContext("s1", 2, 2), 6000, "Beta now"),
        ]

    asyncio.run(scenario())


def test_text_events_are_not_checkpointed_or_written_to_diagnostics(tmp_path: Path) -> None:
    async def scenario() -> None:
        source = _FakeSource(
            [
                [
                    "TS2 S s1 1 1 Alpha 1",
                    "TS2 T s1 1 Alpha 2 Secret_32_received_32_line",
                ]
            ]
        )
        publisher = _CollectingPublisher()
        checkpoints: list[FeedCheckpoint] = []
        directory = tmp_path / "diagnostics"
        diagnostics = DiagnosticCapture(directory)
        diagnostics.open()
        try:
            await _run(
                source,
                publisher,
                checkpoint=checkpoints.append,
                diagnostics=diagnostics,
            )
        finally:
            diagnostics.close()

        captured = (directory / "gmcp.raw").read_text(encoding="utf-8")
        assert "TS2 S s1 1 1 Alpha 1" in captured
        assert "TS2 T " not in captured
        assert "Secret" not in captured
        assert len(checkpoints) == 1
        assert publisher.texts == [(StateContext("s1", 1, 1), 2000, "Secret received line")]

    asyncio.run(scenario())


def test_active_reset_clears_only_that_world_and_rejects_old_generation() -> None:
    async def scenario() -> None:
        source = _FakeSource(
            [
                ["TS2 S s1 1 1 Alpha 1"],
                ['TS2 G s1 1 Alpha 2 Char.Status {"character_name":"Alice"}'],
                ["TS2 R s1 3 Alpha 3"],
                ['TS2 G s1 1 Alpha 4 Char.Status {"character_name":"Old"}'],
                ['TS2 G s1 3 Alpha 5 Char.Status {"character_name":"New"}'],
            ]
        )
        publisher = _CollectingPublisher()

        await _run(source, publisher)

        assert [
            (kind, context.connection if context else None, _character_name(state))
            for kind, context, state in publisher.operations
        ] == [
            ("select", 1, None),
            ("publish", 1, "Alice"),
            ("select", 3, None),
            ("publish", 3, "New"),
        ]

    asyncio.run(scenario())


def test_new_session_invalidates_cached_world_state() -> None:
    async def scenario() -> None:
        source = _FakeSource(
            [
                ['TS2 G old 1 Alpha 1 Char.Status {"character_name":"Old"}'],
                ["TS2 S old 1 1 Alpha 2"],
                ["TS2 S fresh 1 1 Alpha 3"],
            ]
        )
        publisher = _CollectingPublisher()

        await _run(source, publisher)

        assert _character_name(publisher.operations[0][2]) == "Old"
        assert _character_name(publisher.operations[1][2]) is None

    asyncio.run(scenario())


def test_no_world_selects_empty_state_and_clears_marker() -> None:
    async def scenario() -> None:
        source = _FakeSource([["TS2 S s1 1 1 Alpha 1", "TS2 S s1 2 0 - 2"]])
        publisher = _CollectingPublisher()
        markers: list[StateContext | None] = []

        await _run(source, publisher, context_marker=markers.append)

        assert publisher.operations[-1] == ("select", None, EMPTY)
        assert markers[-1] is None

    asyncio.run(scenario())


def test_checkpoint_is_session_and_world_aware_and_private(tmp_path: Path) -> None:
    path = tmp_path / "run" / "state.json"
    state = GameState(character=None, target=None)
    checkpoint = FeedCheckpoint(
        "session1",
        {"Alpha": WorldCheckpoint(2, state), "Beta World": WorldCheckpoint(4, state)},
    )

    store_checkpoint(path, checkpoint)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_checkpoint(path) == checkpoint
    path.write_text('{"version":2,"session":"bad/session","worlds":{}}', encoding="utf-8")
    assert load_checkpoint(path) is None


def test_matching_checkpoint_seeds_world_but_different_session_discards_it() -> None:
    async def scenario() -> None:
        from tinyscry_relay.protocol import Character

        seeded = GameState(character=Character("Seed", None, None, None), target=None)
        checkpoint = FeedCheckpoint("same", {"Alpha": WorldCheckpoint(1, seeded)})

        matching = _CollectingPublisher()
        await _run(_FakeSource([["TS2 S same 1 1 Alpha 1"]]), matching, initial_checkpoint=checkpoint)
        assert _character_name(matching.operations[0][2]) == "Seed"

        fresh = _CollectingPublisher()
        await _run(_FakeSource([["TS2 S new 1 1 Alpha 1"]]), fresh, initial_checkpoint=checkpoint)
        assert _character_name(fresh.operations[0][2]) is None

    asyncio.run(scenario())


def test_relay_outage_does_not_stop_spool_draining_and_new_selection_cancels_old() -> None:
    class _BlockedPublisher(_CollectingPublisher):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def select(self, context: StateContext | None, state: GameState) -> None:
            self.operations.append(("select", context, state))
            if context is not None and context.foreground == 1:
                self.started.set()
                try:
                    await asyncio.Future[None]()
                except asyncio.CancelledError:
                    self.cancelled.set()
                    raise

    async def scenario() -> None:
        source = _FakeSource([["TS2 S s1 1 1 Alpha 1"], ["TS2 S s1 2 2 Beta 2"], [], []])
        publisher = _BlockedPublisher()

        await _run(source, publisher)

        assert source.calls >= 4
        assert publisher.cancelled.is_set()
        assert publisher.operations[-1][1] == StateContext("s1", 2, 2)

    asyncio.run(scenario())
