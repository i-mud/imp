from __future__ import annotations

import asyncio
import json
import os
import socket
import stat
import sys
from pathlib import Path
from typing import cast

import pytest
from tinyscry_relay.protocol import Character, GameState, Vital
from websockets.asyncio.server import ServerConnection, serve

from tinyscry_tf.diagnostics import DiagnosticCapture
from tinyscry_tf.feed import load_checkpoint, run_feed, store_checkpoint
from tinyscry_tf.publisher import RelayPublisher


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return cast(int, probe.getsockname()[1])


class _FakeSource:
    """Hands run_feed() fixed batches of raw hook lines, like a drained spool."""

    def __init__(self, batches: list[list[str]]) -> None:
        self._batches = list(batches)
        self.dropped = 0

    def read_lines(self) -> list[str]:
        return self._batches.pop(0) if self._batches else []


async def _collect_publishes(source: _FakeSource, port: int) -> list[dict[str, object]]:
    received: list[dict[str, object]] = []
    got_all = asyncio.Event()

    async def handle(connection: ServerConnection) -> None:
        async for frame in connection:
            assert isinstance(frame, str)
            received.append(cast(dict[str, object], json.loads(frame)))
            if not source._batches:
                got_all.set()

    async with serve(handle, "127.0.0.1", port):
        publisher = RelayPublisher(url=f"ws://127.0.0.1:{port}/ingest")
        stop = asyncio.Event()

        async def stop_when_drained() -> None:
            await asyncio.wait_for(got_all.wait(), timeout=2)
            await asyncio.sleep(0.05)
            stop.set()

        await asyncio.gather(
            run_feed(source, publisher, poll_interval=0.01, stop=stop),
            stop_when_drained(),
        )
        await publisher.close()
    return received


def test_run_feed_normalizes_and_publishes_only_changed_states() -> None:
    async def scenario() -> None:
        port = _free_port()
        source = _FakeSource(
            [
                ['1700000000 Char.Status {"character_name":"Rin","health":"9","health_max":"10"}'],
                ["not-a-valid-line"],  # rejected, must not stop the loop
                ['1700000001 Char.Status {"health":"9","health_max":"10"}'],  # no material change
                ['1700000002 Char.Status {"health":"5","health_max":"10"}'],
            ]
        )
        received = await _collect_publishes(source, port)

        assert len(received) == 2
        first_state = cast(dict[str, object], received[0]["state"])
        second_state = cast(dict[str, object], received[1]["state"])
        first_character = cast(dict[str, object], first_state["character"])
        second_character = cast(dict[str, object], second_state["character"])
        assert cast(dict[str, object], first_character["hp"])["current"] == 9
        assert cast(dict[str, object], second_character["hp"])["current"] == 5

    asyncio.run(scenario())


def test_run_feed_writes_raw_lines_to_diagnostics_only_when_enabled(tmp_path: Path) -> None:
    async def scenario() -> None:
        diagnostics = DiagnosticCapture(tmp_path / "diagnostics")
        diagnostics.open()

        class _NullPublisher:
            async def publish(self, state: GameState) -> None:
                return None

        source = _FakeSource([['1700000000 Char.Status {"character_name":"Rin"}'], []])
        stop = asyncio.Event()

        async def stop_soon() -> None:
            await asyncio.sleep(0.05)
            stop.set()

        await asyncio.gather(
            run_feed(source, _NullPublisher(), poll_interval=0.01, diagnostics=diagnostics, stop=stop),
            stop_soon(),
        )
        diagnostics.close()

        assert (tmp_path / "diagnostics" / "gmcp.raw").read_text(encoding="utf-8") == (
            '1700000000 Char.Status {"character_name":"Rin"}\n'
        )

    asyncio.run(scenario())


def _write_env(base: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["HOME"] = str(base / "home")
    env["XDG_RUNTIME_DIR"] = str(base / "run")
    env["XDG_STATE_HOME"] = str(base / "state")
    (base / "home").mkdir(parents=True, exist_ok=True)
    (base / "run").mkdir(parents=True, exist_ok=True)
    os.chmod(base / "run", 0o700)
    return env


async def _spawn_feed(env: dict[str, str], port: int) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tinyscry_tf.feed",
        "--relay-url",
        f"ws://127.0.0.1:{port}/ingest",
        "--poll-interval",
        "0.02",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


def test_feed_subprocess_delivers_state_and_rejects_a_duplicate_producer(tmp_path: Path) -> None:
    async def scenario() -> None:
        env = _write_env(tmp_path)
        received: list[dict[str, object]] = []
        got_one = asyncio.Event()

        async def handle(connection: ServerConnection) -> None:
            async for frame in connection:
                assert isinstance(frame, str)
                received.append(cast(dict[str, object], json.loads(frame)))
                got_one.set()
            await connection.wait_closed()

        async with serve(handle, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            feed = await _spawn_feed(env, port)
            try:
                # Wait for the fixed hook path to exist before writing through it,
                # exactly like TinyFugue's fwrite() would once loaded after the feed.
                hook_spool = Path(env["HOME"]) / ".local" / "state" / "tinyscry" / "spool"
                for _ in range(200):
                    if hook_spool.exists():
                        break
                    await asyncio.sleep(0.02)
                assert hook_spool.exists(), "feed did not create the fixed hook spool path in time"

                with hook_spool.open("a", encoding="utf-8") as handle_file:
                    handle_file.write('1700000000 Char.Status {"character_name":"Rin","health":"9"}\n')

                await asyncio.wait_for(got_one.wait(), timeout=5)
                state = cast(dict[str, object], received[0]["state"])
                character = cast(dict[str, object], state["character"])
                assert character["name"] == "Rin"

                duplicate = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "tinyscry_tf.feed",
                    "--relay-url",
                    f"ws://127.0.0.1:{port}/ingest",
                    env=env,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                dup_stderr = (await duplicate.stderr.read()).decode()  # type: ignore[union-attr]
                await duplicate.wait()
                assert duplicate.returncode != 0
                assert "another TinyScry feed" in dup_stderr
            finally:
                feed.terminate()
                try:
                    await asyncio.wait_for(feed.wait(), timeout=5)
                except TimeoutError:
                    feed.kill()
                    await feed.wait()

    asyncio.run(scenario())


def test_feed_restart_recovers_after_supervised_crash_cleanup(tmp_path: Path) -> None:
    """A supervised crash/restart recreates the hook and preserves normalized identity."""

    async def scenario() -> None:
        env = _write_env(tmp_path)
        received: list[dict[str, object]] = []
        connected = asyncio.Event()

        async def handle(connection: ServerConnection) -> None:
            connected.set()
            async for frame in connection:
                assert isinstance(frame, str)
                received.append(cast(dict[str, object], json.loads(frame)))
            await connection.wait_closed()

        async with serve(handle, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            feed = await _spawn_feed(env, port)
            hook_spool = Path(env["HOME"]) / ".local" / "state" / "tinyscry" / "spool"

            for _ in range(200):
                if hook_spool.exists():
                    break
                await asyncio.sleep(0.02)
            assert hook_spool.exists()

            with hook_spool.open("a", encoding="utf-8") as handle_file:
                handle_file.write(
                    '1700000000 Char.Status {"character_name":"Rin","health":"9","health_max":"10"}\n'
                )

            await asyncio.wait_for(connected.wait(), timeout=5)
            for _ in range(200):
                if received:
                    break
                await asyncio.sleep(0.02)
            assert received

            feed.kill()
            await feed.wait()

            # Model systemd ExecStopPost after an abnormal service exit.
            hook_spool.unlink(missing_ok=True)
            assert not hook_spool.exists()

            connected.clear()
            received.clear()
            restarted = await _spawn_feed(env, port)
            try:
                for _ in range(200):
                    if hook_spool.exists():
                        break
                    await asyncio.sleep(0.02)
                assert hook_spool.exists()

                # No character_name or maximum is supplied after restart.
                # The private normalized checkpoint must restore both.
                with hook_spool.open("a", encoding="utf-8") as handle_file:
                    handle_file.write('1700000001 Char.Status {"health":"5"}\n')

                await asyncio.wait_for(connected.wait(), timeout=5)
                for _ in range(200):
                    if received:
                        state = cast(dict[str, object], received[-1]["state"])
                        character = cast(dict[str, object], state["character"])
                        hp = cast(dict[str, object], character["hp"])
                        if hp["current"] == 5:
                            break
                    await asyncio.sleep(0.02)

                state = cast(dict[str, object], received[-1]["state"])
                character = cast(dict[str, object], state["character"])
                hp = cast(dict[str, object], character["hp"])
                assert character["name"] == "Rin"
                assert hp == {"current": 5, "max": 10}
            finally:
                restarted.terminate()
                try:
                    await asyncio.wait_for(restarted.wait(), timeout=5)
                except TimeoutError:
                    restarted.kill()
                    await restarted.wait()

    asyncio.run(scenario())


@pytest.mark.parametrize("bad_interval", [0, -1])
def test_negative_or_zero_poll_interval_is_rejected(bad_interval: float, tmp_path: Path) -> None:
    async def scenario() -> None:
        env = _write_env(tmp_path)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "tinyscry_tf.feed",
            "--poll-interval",
            str(bad_interval),
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        stderr = (await process.stderr.read()).decode()  # type: ignore[union-attr]
        await process.wait()
        assert process.returncode != 0
        assert "--poll-interval" in stderr

    asyncio.run(scenario())


def test_run_feed_keeps_draining_and_stops_promptly_while_publish_is_blocked() -> None:
    class _CountingSource:
        dropped = 0

        def __init__(self) -> None:
            self.calls = 0

        def read_lines(self) -> list[str]:
            self.calls += 1
            return (
                [
                    f"170000000{self.calls} Char.Status "
                    f'{{"character_name":"Rin","health":"{self.calls}","health_max":"100"}}'
                ]
                if self.calls <= 5
                else []
            )

    class _BlockedPublisher:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def publish(self, state: GameState) -> None:
            self.started.set()
            await self.release.wait()

    async def scenario() -> None:
        source = _CountingSource()
        publisher = _BlockedPublisher()
        stop = asyncio.Event()

        task = asyncio.create_task(run_feed(source, publisher, poll_interval=0.01, stop=stop))
        await asyncio.wait_for(publisher.started.wait(), timeout=1)
        await asyncio.sleep(0.08)

        assert source.calls > 5, "relay outage must not stop spool draining"

        stop.set()
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(scenario())


def test_run_feed_does_not_publish_checkpoint_without_fresh_spool_input() -> None:
    async def scenario() -> None:
        checkpoint = GameState(
            character=Character(
                name="Rin",
                hp=Vital(current=9, max=10),
                mana=Vital(current=4, max=8),
                moves=Vital(current=7, max=12),
            ),
            target=None,
        )
        source = _FakeSource([[]])
        published: list[GameState] = []

        class _CollectingPublisher:
            async def publish(self, state: GameState) -> None:
                published.append(state)

        stop = asyncio.Event()

        async def stop_after_idle() -> None:
            await asyncio.sleep(0.08)
            stop.set()

        await asyncio.gather(
            run_feed(
                source,
                _CollectingPublisher(),
                poll_interval=0.01,
                stop=stop,
                initial_state=checkpoint,
            ),
            stop_after_idle(),
        )

        assert published == []

    asyncio.run(scenario())


def test_run_feed_seeds_checkpoint_before_publishing_a_fresh_change(tmp_path: Path) -> None:
    async def scenario() -> None:
        checkpoint_path = tmp_path / "run" / "state.json"
        checkpoint_path.parent.mkdir(parents=True)
        original = GameState(
            character=Character(
                name="Rin",
                hp=Vital(current=9, max=10),
                mana=Vital(current=4, max=8),
                moves=Vital(current=7, max=12),
            ),
            target=None,
        )
        store_checkpoint(checkpoint_path, original)
        restored = load_checkpoint(checkpoint_path)
        assert restored == original

        source = _FakeSource([['1700000001 Char.Status {"health":"5"}'], []])
        published: list[GameState] = []

        class _CollectingPublisher:
            async def publish(self, state: GameState) -> None:
                published.append(state)

        stop = asyncio.Event()

        async def stop_after_drain() -> None:
            await asyncio.sleep(0.08)
            stop.set()

        await asyncio.gather(
            run_feed(
                source,
                _CollectingPublisher(),
                poll_interval=0.01,
                stop=stop,
                initial_state=restored,
                checkpoint=lambda state: store_checkpoint(checkpoint_path, state),
            ),
            stop_after_drain(),
        )

        expected = GameState(
            character=Character(
                name="Rin",
                hp=Vital(current=5, max=10),
                mana=Vital(current=4, max=8),
                moves=Vital(current=7, max=12),
            ),
            target=None,
        )
        assert published == [expected]
        assert load_checkpoint(checkpoint_path) == expected

    asyncio.run(scenario())


def test_checkpoint_is_private_and_rejects_malformed_state(tmp_path: Path) -> None:
    checkpoint = tmp_path / "state.json"
    state = GameState(
        character=Character(name="Rin", hp=None, mana=None, moves=None),
        target=None,
    )

    store_checkpoint(checkpoint, state)

    assert stat.S_IMODE(checkpoint.stat().st_mode) == 0o600
    assert load_checkpoint(checkpoint) == state

    checkpoint.write_text('{"character":{"name":""},"target":null}', encoding="utf-8")
    assert load_checkpoint(checkpoint) is None
