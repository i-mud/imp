from __future__ import annotations

import asyncio
import os
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import imp_adapter.publisher as publisher_module
import pytest
from imp_adapter.publisher import RelayPublisher
from imp_relay.protocol import Character, GameState, StateContext, Target, Vital
from imp_relay.server import RelayServer
from imp_relay.state import Snapshot
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as websocket_connect

import imp_tf.feed as feed_module
from imp_tf.diagnostics import DiagnosticCapture
from imp_tf.feed import FeedCheckpoint, WorldCheckpoint, load_checkpoint, run_feed, store_checkpoint
from imp_tf.spool import RuntimeLayout, SpoolReader, acquire_producer_lock

EMPTY = GameState(character=None, target=None)


class _FakeSource:
    dropped = 0

    def __init__(self, batches: list[list[str]]) -> None:
        self.batches = list(batches)
        self.calls = 0

    def read_lines(self) -> list[str]:
        self.calls += 1
        return self.batches.pop(0) if self.batches else []


class _RecordingSpoolReader(SpoolReader):
    def __init__(self, layout: RuntimeLayout, *, reset_at: int = 65_536) -> None:
        super().__init__(layout, reset_at=reset_at)
        self.batches: asyncio.Queue[list[str]] = asyncio.Queue()

    def read_lines(self) -> list[str]:
        lines = super().read_lines()
        self.batches.put_nowait(lines)
        return lines


class _StepStop(asyncio.Event):
    """Release exactly one feed poll per explicit test step."""

    def __init__(self) -> None:
        super().__init__()
        self._advance = asyncio.Event()

    def advance(self) -> None:
        self._advance.set()

    def stop(self) -> None:
        self.set()
        self.advance()

    async def wait(self) -> Literal[True]:
        await self._advance.wait()
        self._advance.clear()
        return True


def _append_spool(path: Path, *lines: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write("".join(f"{line}\n" for line in lines))


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


async def _wait_for_snapshot(
    relay: RelayServer, context: StateContext, character_name: str | None
) -> Snapshot:
    async def wait() -> Snapshot:
        while True:
            snapshot = relay.state.snapshot()
            if (
                snapshot is not None
                and snapshot.context == context
                and _character_name(snapshot.state) == character_name
            ):
                return snapshot
            await asyncio.sleep(0.001)

    return await asyncio.wait_for(wait(), timeout=2)


def test_background_world_updates_cache_without_overwriting_foreground() -> None:
    async def scenario() -> None:
        source = _FakeSource(
            [
                ["IMP2 S session1 1 1 Alpha 1"],
                ['IMP2 G session1 1 Alpha 2 Char.Status {"character_name":"Alice","health":"9"}'],
                ['IMP2 G session1 2 Beta 3 Char.Status {"character_name":"Bob","health":"7"}'],
                ["IMP2 S session1 2 2 Beta 4"],
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
                    "IMP2 S session1 1 1 Alpha 1",
                    'IMP2 G session1 1 Alpha 2 Char.Status {"character_name":"Alice"}',
                ]
            ]
        )
        publisher = _StrictPublisher()

        await _run(source, publisher)

        assert [operation[0] for operation in publisher.operations] == ["select", "publish"]
        assert _character_name(publisher.operations[-1][2]) == "Alice"

    asyncio.run(scenario())


def test_gmcp_publishes_repeated_observations_only_for_current_foreground_context() -> None:
    async def scenario() -> None:
        source = _FakeSource(
            [
                [
                    "IMP2 S session1 1 2 Alpha 1",
                    'IMP2 G session1 2 Alpha 2 Char.Status {"character_name":"Alice"}',
                    'IMP2 G session1 2 Alpha 3 Char.Status {"character_name":"Alice"}',
                    'IMP2 G session1 2 Alpha 4 Char.Vitals {"hp":"12","maxhp":"20"}',
                    'IMP2 G session1 2 Alpha 5 Char.Vitals {"hp":"12","maxhp":"20"}',
                    'IMP2 G session1 2 Alpha 6 Room.Info {"name":"ignored"}',
                    'IMP2 G session1 2 Alpha 7 Char.Status {"unmapped":"ignored"}',
                    "IMP2 G session1 2 Alpha 8 Char.Status "
                    '{"character_name":"Rejected","opponent_health":101}',
                    'IMP2 G session1 2 Beta 9 Char.Status {"character_name":"Background"}',
                    'IMP2 G session1 1 Alpha 10 Char.Status {"character_name":"Old generation"}',
                ]
            ]
        )
        publisher = _CollectingPublisher()

        await _run(source, publisher)

        assert [operation[0] for operation in publisher.operations] == [
            "select",
            "publish",
            "publish",
            "publish",
            "publish",
        ]
        assert all(operation[1] == StateContext("session1", 1, 2) for operation in publisher.operations)
        assert publisher.operations[-1][2].character == Character("Alice", Vital(12, 20), None, None)
        assert [_character_name(operation[2]) for operation in publisher.operations[1:]] == [
            "Alice",
            "Alice",
            "Alice",
            "Alice",
        ]

    asyncio.run(scenario())


def test_text_events_forward_only_for_the_selected_exact_connection() -> None:
    async def scenario() -> None:
        source = _FakeSource(
            [
                [
                    "IMP2 S s1 1 1 Alpha 1",
                    "IMP2 T s1 1 Alpha 2 Alpha_32_one",
                    "IMP2 T s1 2 Beta 3 Background",
                    "IMP2 S s1 2 2 Beta 4",
                    "IMP2 T s1 1 Alpha 5 Old_32_foreground",
                    "IMP2 T s1 2 Beta 6 Beta_32_now",
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
                    "IMP2 S s1 1 1 Alpha 1",
                    "IMP2 T s1 1 Alpha 2 Secret_32_received_32_line",
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
        assert "IMP2 S s1 1 1 Alpha 1" in captured
        assert "IMP2 T " not in captured
        assert "Secret" not in captured
        assert len(checkpoints) == 1
        assert publisher.texts == [(StateContext("s1", 1, 1), 2000, "Secret received line")]

    asyncio.run(scenario())


def test_active_reset_clears_only_that_world_and_rejects_old_generation() -> None:
    async def scenario() -> None:
        source = _FakeSource(
            [
                ["IMP2 S s1 1 1 Alpha 1"],
                ['IMP2 G s1 1 Alpha 2 Char.Status {"character_name":"Alice"}'],
                ["IMP2 R s1 3 Alpha 3"],
                ['IMP2 G s1 1 Alpha 4 Char.Status {"character_name":"Old"}'],
                ['IMP2 G s1 3 Alpha 5 Char.Status {"character_name":"New"}'],
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
                ['IMP2 G old 1 Alpha 1 Char.Status {"character_name":"Old"}'],
                ["IMP2 S old 1 1 Alpha 2"],
                ["IMP2 S fresh 1 1 Alpha 3"],
            ]
        )
        publisher = _CollectingPublisher()

        await _run(source, publisher)

        assert _character_name(publisher.operations[0][2]) == "Old"
        assert _character_name(publisher.operations[1][2]) is None

    asyncio.run(scenario())


def test_no_world_selects_empty_state_and_clears_marker() -> None:
    async def scenario() -> None:
        source = _FakeSource([["IMP2 S s1 1 1 Alpha 1", "IMP2 S s1 2 0 - 2"]])
        publisher = _CollectingPublisher()
        markers: list[StateContext | None] = []

        await _run(source, publisher, context_marker=markers.append)

        assert publisher.operations[-1] == ("select", None, EMPTY)
        assert markers[-1] is None

    asyncio.run(scenario())


def test_selection_ordering_preserves_foreground_and_world_authority() -> None:
    async def scenario() -> None:
        source = _FakeSource(
            [
                ["IMP2 S s1 1 0 - 1"],
                ["IMP2 S s1 1 1 Alpha 2"],
                ["IMP2 S s1 1 2 Alpha 3"],
                ["IMP2 R s1 3 Alpha 4"],
                ["IMP2 S s1 1 0 - 5"],
                ["IMP2 S s1 1 1 Beta 6"],
                ["IMP2 S s1 1 2 Alpha 7"],
                ["IMP2 S s1 1 3 Alpha 8"],
                ['IMP2 G s1 5 Beta 9 Char.Status {"character_name":"Bob"}'],
                ["IMP2 S s1 2 4 Beta 10"],
                ["IMP2 S s1 1 3 Alpha 11"],
                ["IMP2 S s1 2 5 Beta 12"],
                ["IMP2 S s1 1 3 Alpha 13"],
                ["IMP2 S s1 3 0 - 14"],
                ["IMP2 S s1 2 5 Beta 15"],
                ["IMP2 S s1 3 3 Alpha 16"],
                ["IMP2 S s2 1 1 Alpha 17"],
            ]
        )
        publisher = _CollectingPublisher()
        markers: list[StateContext | None] = []

        await _run(source, publisher, context_marker=markers.append)

        assert [(kind, context, _character_name(state)) for kind, context, state in publisher.operations] == [
            ("select", None, None),
            ("select", StateContext("s1", 1, 1), None),
            ("select", StateContext("s1", 1, 2), None),
            ("select", StateContext("s1", 1, 3), None),
            ("select", None, None),
            ("select", StateContext("s1", 1, 3), None),
            ("select", StateContext("s1", 1, 3), None),
            ("select", StateContext("s1", 2, 5), "Bob"),
            ("select", None, None),
            ("select", StateContext("s1", 3, 3), None),
            ("select", StateContext("s2", 1, 1), None),
        ]
        assert markers == [
            None,
            None,
            StateContext("s1", 1, 1),
            StateContext("s1", 1, 2),
            StateContext("s1", 1, 3),
            None,
            StateContext("s1", 1, 3),
            StateContext("s1", 1, 3),
            StateContext("s1", 2, 5),
            None,
            StateContext("s1", 3, 3),
            None,
            StateContext("s2", 1, 1),
        ]

    asyncio.run(scenario())


def test_spool_retired_late_selection_cannot_roll_back_live_foreground(tmp_path: Path) -> None:
    async def scenario() -> None:
        class _InterleavedReader(_RecordingSpoolReader):
            late_writer_fd: int | None = None
            late_suffix = b""
            inject_late = False

            def _drain_active(self, fd: int, lines: list[str]) -> None:
                if self.inject_late:
                    self.inject_late = False
                    assert self.late_writer_fd is not None
                    # Inject after the retired drain and before the active drain.
                    os.write(self.late_writer_fd, self.late_suffix)
                super()._drain_active(fd, lines)

        now = [0.0]
        relay = RelayServer(port=0, stale_after=10.0, clock=lambda: now[0])
        await relay.start()
        layout = RuntimeLayout.resolve(runtime_dir=tmp_path / "run", state_dir=tmp_path / "state")
        reader = _InterleavedReader(layout, reset_at=1)
        reader.open()
        writer_fd = os.open(layout.spool, os.O_WRONLY | os.O_APPEND)
        publisher = RelayPublisher(url=f"ws://127.0.0.1:{relay.port}/ingest")
        stop = _StepStop()
        feed = asyncio.create_task(run_feed(reader, publisher, poll_interval=3600, stop=stop))
        alpha = StateContext("s1", 1, 1)
        beta = StateContext("s1", 2, 2)
        late_alpha = [
            "IMP2 S s1 1 1 Alpha 5",
            'IMP2 G s1 1 Alpha 6 Char.Status {"character_name":"Alice"}',
        ]

        try:
            _append_spool(layout.spool, "IMP2 S s1 1 1 Alpha 1")
            assert await asyncio.wait_for(reader.batches.get(), timeout=1) == ["IMP2 S s1 1 1 Alpha 1"]
            await _wait_for_snapshot(relay, alpha, None)
            assert layout.retired_spool.exists()

            os.write(writer_fd, b"IMP2 S s1 1 ")
            stop.advance()
            assert await asyncio.wait_for(reader.batches.get(), timeout=1) == []

            _append_spool(layout.spool, 'IMP2 G s1 1 Alpha 2 Char.Status {"character_name":"Alice"}')
            stop.advance()
            assert await asyncio.wait_for(reader.batches.get(), timeout=1) == [
                'IMP2 G s1 1 Alpha 2 Char.Status {"character_name":"Alice"}'
            ]
            await _wait_for_snapshot(relay, alpha, "Alice")

            reader.late_writer_fd = writer_fd
            reader.late_suffix = b'1 Alpha 5\nIMP2 G s1 1 Alpha 6 Char.Status {"character_name":"Alice"'
            reader.inject_late = True
            beta_selection = "IMP2 S s1 2 2 Beta 3"
            _append_spool(layout.spool, beta_selection)
            stop.advance()
            assert await asyncio.wait_for(reader.batches.get(), timeout=1) == [beta_selection]
            await _wait_for_snapshot(relay, beta, None)

            reader.late_suffix = b"}\n"
            reader.inject_late = True
            beta_observation = 'IMP2 G s1 2 Beta 4 Char.Status {"character_name":"Bob"}'
            _append_spool(layout.spool, beta_observation)
            stop.advance()
            assert await asyncio.wait_for(reader.batches.get(), timeout=1) == [
                late_alpha[0],
                beta_observation,
            ]
            beta_snapshot = await _wait_for_snapshot(relay, beta, "Bob")
            assert relay.state.feed_status(now[0]) == "live"

            now[0] = 11.0
            stop.advance()
            assert await asyncio.wait_for(reader.batches.get(), timeout=1) == [late_alpha[1]]
            assert relay.state.snapshot() is beta_snapshot
            assert relay.state.feed_status(now[0]) == "stale"

            newer = StateContext("s1", 3, 2)
            _append_spool(layout.spool, "IMP2 S s1 3 2 Beta 7")
            stop.advance()
            assert await asyncio.wait_for(reader.batches.get(), timeout=1) == ["IMP2 S s1 3 2 Beta 7"]
            selected = await _wait_for_snapshot(relay, newer, "Bob")
            assert selected.context == newer
            assert relay.state.feed_status(now[0]) == "stale"
        finally:
            stop.stop()
            try:
                await asyncio.wait_for(feed, timeout=1)
            finally:
                os.close(writer_fd)
                reader.close()
                await publisher.close()
                await relay.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("offline_name", ["Alice", "Bob"], ids=["unchanged", "changed"])
def test_spool_reconnect_discards_offline_observation_until_fresh_unchanged_gmcp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, offline_name: str
) -> None:
    async def scenario() -> None:
        class _TrackingPublisher(RelayPublisher):
            def __init__(self, url: str) -> None:
                super().__init__(url)
                self.publishes: asyncio.Queue[None] = asyncio.Queue()

            async def publish(self, context: StateContext, state: GameState) -> None:
                await super().publish(context, state)
                self.publishes.put_nowait(None)

        now = [0.0]
        relay = RelayServer(port=0, stale_after=10.0, clock=lambda: now[0])
        await relay.start()
        port = relay.port
        layout = RuntimeLayout.resolve(runtime_dir=tmp_path / "run", state_dir=tmp_path / "state")
        reader = _RecordingSpoolReader(layout)
        reader.open()
        publisher = _TrackingPublisher(f"ws://127.0.0.1:{port}/ingest")
        stop = _StepStop()
        feed = asyncio.create_task(run_feed(reader, publisher, poll_interval=3600, stop=stop))
        context = StateContext("s1", 1, 1)
        state = GameState(Character("Alice", None, None, None), None)
        offline_state = GameState(Character(offline_name, None, None, None), None)
        reconnect_started = asyncio.Event()
        reconnect_release = asyncio.Event()
        gate_reconnect = False
        relay_closed = False
        restarted: RelayServer | None = None

        async def gated_connect(uri: str, *, proxy: None) -> ClientConnection:
            if gate_reconnect:
                reconnect_started.set()
                await reconnect_release.wait()
            return await websocket_connect(uri, proxy=proxy)

        monkeypatch.setattr(publisher_module, "connect", gated_connect)
        try:
            _append_spool(layout.spool, "IMP2 S s1 1 1 Alpha 1")
            assert await asyncio.wait_for(reader.batches.get(), timeout=1) == ["IMP2 S s1 1 1 Alpha 1"]
            await _wait_for_snapshot(relay, context, None)

            _append_spool(layout.spool, 'IMP2 G s1 1 Alpha 2 Char.Status {"character_name":"Alice"}')
            stop.advance()
            assert await asyncio.wait_for(reader.batches.get(), timeout=1) == [
                'IMP2 G s1 1 Alpha 2 Char.Status {"character_name":"Alice"}'
            ]
            await asyncio.wait_for(publisher.publishes.get(), timeout=1)
            initial = await _wait_for_snapshot(relay, context, "Alice")
            assert relay.state.feed_status(now[0]) == "live"

            gate_reconnect = True
            await relay.close()
            relay_closed = True
            await asyncio.wait_for(reconnect_started.wait(), timeout=2)
            now[0] = 20.0

            _append_spool(
                layout.spool,
                f'IMP2 G s1 1 Alpha 3 Char.Status {{"character_name":"{offline_name}"}}',
            )
            stop.advance()
            assert await asyncio.wait_for(reader.batches.get(), timeout=1) == [
                f'IMP2 G s1 1 Alpha 3 Char.Status {{"character_name":"{offline_name}"}}'
            ]
            await asyncio.wait_for(publisher.publishes.get(), timeout=1)

            restarted = RelayServer(port=port, stale_after=10.0, clock=lambda: now[0])
            await restarted.start()
            reconnect_release.set()
            reselected = await _wait_for_snapshot(restarted, context, offline_name)
            reselected_stamp = (reselected.seq, reselected.at)
            assert restarted.state.feed_status(now[0]) == "stale"

            _append_spool(
                layout.spool,
                f'IMP2 G s1 1 Alpha 4 Char.Status {{"character_name":"{offline_name}"}}',
            )
            stop.advance()
            assert await asyncio.wait_for(reader.batches.get(), timeout=1) == [
                f'IMP2 G s1 1 Alpha 4 Char.Status {{"character_name":"{offline_name}"}}'
            ]
            await asyncio.wait_for(publisher.publishes.get(), timeout=1)

            active_relay = restarted

            async def wait_live() -> None:
                while active_relay.state.feed_status(now[0]) != "live":
                    await asyncio.sleep(0.001)

            await asyncio.wait_for(wait_live(), timeout=2)
            assert active_relay.state.snapshot() is reselected
            assert (reselected.seq, reselected.at) == reselected_stamp
            assert initial.state == state
            assert reselected.state == offline_state
        finally:
            stop.stop()
            try:
                await asyncio.wait_for(feed, timeout=1)
            finally:
                reconnect_release.set()
                await publisher.close()
                reader.close()
                if restarted is not None:
                    await restarted.close()
                if not relay_closed:
                    await relay.close()

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
        from imp_relay.protocol import Character

        seeded = GameState(character=Character("Seed", None, None, None), target=None)
        checkpoint = FeedCheckpoint("same", {"Alpha": WorldCheckpoint(1, seeded)})

        matching = _CollectingPublisher()
        await _run(_FakeSource([["IMP2 S same 1 1 Alpha 1"]]), matching, initial_checkpoint=checkpoint)
        assert _character_name(matching.operations[0][2]) == "Seed"

        fresh = _CollectingPublisher()
        await _run(_FakeSource([["IMP2 S new 1 1 Alpha 1"]]), fresh, initial_checkpoint=checkpoint)
        assert _character_name(fresh.operations[0][2]) is None

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [None, "open", "checkpoint", "serve"])
def test_feed_readiness_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setattr(sys, "argv", ["imp-feed"])
    layout = RuntimeLayout.resolve()
    layout.ready.parent.mkdir(parents=True)
    layout.ready.write_text("obsolete\n", encoding="ascii")
    tokens: list[str] = []
    cached = FeedCheckpoint(
        "same", {"Alpha": WorldCheckpoint(1, GameState(Character("Seed", None, None, None), None))}
    )
    store_checkpoint(layout.checkpoint, cached)
    original_open = SpoolReader.open
    original_load = load_checkpoint

    def open_reader(reader: SpoolReader) -> None:
        assert not layout.ready.exists()
        assert not layout.spool.exists()
        assert not layout.retired_spool.exists()
        if failure == "open":
            raise RuntimeError("open failed")
        original_open(reader)

    def load(path: Path) -> FeedCheckpoint | None:
        assert not layout.ready.exists()
        assert layout.hook_spool.is_file()
        if failure == "checkpoint":
            raise RuntimeError("checkpoint failed")
        return original_load(path)

    async def serve(
        reader: SpoolReader,
        publisher: RelayPublisher,
        poll_interval: float,
        diagnostics: DiagnosticCapture | None,
        initial_checkpoint: FeedCheckpoint | None,
        actual_layout: RuntimeLayout,
    ) -> None:
        assert actual_layout == layout
        token = layout.ready.read_text(encoding="ascii")
        assert len(token) == 33 and token[-1] == "\n"
        assert all(char in "0123456789abcdef" for char in token[:-1])
        assert stat.S_IMODE(layout.ready.stat().st_mode) == 0o600
        tokens.append(token)
        collector = _CollectingPublisher()
        stop = asyncio.Event()
        stop.set()
        assert reader.read_lines() == []
        assert initial_checkpoint == cached
        await run_feed(reader, collector, stop=stop, initial_checkpoint=initial_checkpoint)
        assert collector.operations == []
        await publisher.close()
        if failure == "serve":
            raise RuntimeError("serve failed")

    monkeypatch.setattr(SpoolReader, "open", open_reader)
    monkeypatch.setattr(feed_module, "load_checkpoint", load)
    monkeypatch.setattr(feed_module, "_serve", serve)
    for _ in range(2):
        _append_spool(
            layout.spool,
            "IMP2 S same 1 1 Alpha 1",
            'IMP2 G same 1 Alpha 2 Char.Status {"character_name":"Old"}',
            "IMP2 T same 1 Alpha 3 old%20text",
        )
        _append_spool(layout.retired_spool, "IMP2 S same 1 1 Alpha 1")
        if failure is None:
            feed_module.main()
        else:
            with pytest.raises(RuntimeError, match=f"{failure} failed"):
                feed_module.main()
        assert not layout.ready.exists()
        assert not layout.hook_spool.exists()
        lock_fd = acquire_producer_lock(layout.lock)
        os.close(lock_fd)
    if failure in (None, "serve"):
        assert len(set(tokens)) == 2


def test_feed_lock_refusal_preserves_running_feed_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setattr(sys, "argv", ["imp-feed"])
    layout = RuntimeLayout.resolve()
    reader = SpoolReader(layout)
    lock_fd = acquire_producer_lock(layout.lock)
    reader.open()
    token = "a" * 32 + "\n"
    layout.ready.write_text(token, encoding="ascii")
    try:
        with pytest.raises(SystemExit, match="another Imp feed"):
            feed_module.main()
        assert layout.ready.read_text(encoding="ascii") == token
        assert layout.hook_spool.is_file()
    finally:
        reader.close()
        os.close(lock_fd)


@pytest.mark.parametrize("connection", [1, 2], ids=["same-connection", "new-connection"])
def test_checkpoint_requires_current_selection_before_stale_identity_then_fresh_gmcp(
    tmp_path: Path, connection: int
) -> None:
    async def scenario() -> None:
        seeded = GameState(Character("Seed", None, None, None), None)
        checkpoint = FeedCheckpoint("same", {"Alpha": WorldCheckpoint(1, seeded)})
        layout = RuntimeLayout.resolve(runtime_dir=tmp_path / "run", state_dir=tmp_path / "state")
        reader = _RecordingSpoolReader(layout)
        reader.open()
        relay = RelayServer(port=0)
        await relay.start()
        publisher = RelayPublisher(f"ws://127.0.0.1:{relay.port}/ingest")
        stop = _StepStop()
        task = asyncio.create_task(
            run_feed(reader, publisher, stop=stop, poll_interval=3600, initial_checkpoint=checkpoint)
        )
        context = StateContext("same", 2, connection)
        try:
            assert await asyncio.wait_for(reader.batches.get(), timeout=1) == []
            assert relay.state.snapshot() is None
            _append_spool(layout.spool, f"IMP2 S same 2 {connection} Alpha 1")
            stop.advance()
            await asyncio.wait_for(reader.batches.get(), timeout=1)
            snapshot = await _wait_for_snapshot(relay, context, "Seed" if connection == 1 else None)
            assert relay.state.health()["feed"] == "stale"
            payload = (
                '{"health":"9","health_max":"10"}'
                if connection == 1
                else '{"character_name":"New","health":"9","health_max":"10"}'
            )
            _append_spool(
                layout.spool,
                f"IMP2 G same {connection} Alpha 2 Char.Status {payload}",
            )
            stop.advance()
            await asyncio.wait_for(reader.batches.get(), timeout=1)

            async def wait_live() -> None:
                while relay.state.health()["feed"] != "live":
                    await asyncio.sleep(0.001)

            await asyncio.wait_for(wait_live(), timeout=2)
            fresh = relay.state.snapshot()
            assert fresh is not None
            assert _character_name(fresh.state) == ("Seed" if connection == 1 else "New")
            assert fresh.state.character is not None
            assert fresh.state.character.hp == Vital(9, 10)
            assert snapshot.context == fresh.context
        finally:
            stop.stop()
            await asyncio.wait_for(task, timeout=1)
            await publisher.close()
            await relay.close()
            reader.close()

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
        source = _FakeSource([["IMP2 S s1 1 1 Alpha 1"], ["IMP2 S s1 2 2 Beta 2"], [], []])
        publisher = _BlockedPublisher()

        await _run(source, publisher)

        assert source.calls >= 4
        assert publisher.cancelled.is_set()
        assert publisher.operations[-1][1] == StateContext("s1", 2, 2)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        "[" * 33 + "0" + "]" * 33,
        "[" * 1100 + "0" + "]" * 1100,
        '{"health":"5","mana":"' + "9" * 5000 + '"}',
        '{"health":"5","opponent_health":' + "9" * 400 + "}",
        '{"health":"5","opponent_health":1e400}',
    ],
)
def test_hostile_gmcp_is_skipped_and_feed_continues_without_partial_state(payload: str) -> None:
    async def scenario() -> None:
        source = _FakeSource(
            [
                [
                    "IMP2 S session1 1 1 Alpha 1",
                    'IMP2 G session1 1 Alpha 2 Char.Status {"character_name":"Ariadne",'
                    '"health":"90","health_max":"100","mana":"70","mana_max":"80",'
                    '"opponent_name":"Troll","opponent_health":"62"}',
                    f"IMP2 G session1 1 Alpha 3 Char.Status {payload}",
                    'IMP2 G session1 1 Alpha 4 Char.Status {"character_name":"Continued"}',
                ]
            ]
        )
        publisher = _CollectingPublisher()
        stop = asyncio.Event()

        def checkpoint(value: FeedCheckpoint) -> None:
            if _character_name(value.worlds["Alpha"].state) == "Continued":
                stop.set()

        await asyncio.wait_for(
            run_feed(source, publisher, stop=stop, checkpoint=checkpoint),
            timeout=1,
        )

        context = StateContext("session1", 1, 1)
        assert publisher.operations == [
            ("select", context, EMPTY),
            (
                "publish",
                context,
                GameState(Character("Ariadne", Vital(90, 100), Vital(70, 80), None), Target("Troll", 62.0)),
            ),
            (
                "publish",
                context,
                GameState(Character("Continued", Vital(90, 100), Vital(70, 80), None), Target("Troll", 62.0)),
            ),
        ]

    asyncio.run(scenario())
