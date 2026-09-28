from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from typing import TextIO, cast

import pytest
from imp_relay.action import ConsumerRegistration
from imp_relay.protocol import (
    GameState,
    StateContext,
    encode_action,
    encode_consumer,
    encode_consumer_ready,
    encode_dispatch,
    encode_select,
)
from imp_relay.server import RelayServer
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

import imp_tf.action_consumer as action_consumer
from imp_tf.action_consumer import encode_tf_dispatch, encode_tf_token, run_action_consumer
from imp_tf.context import read_context_marker, write_context_marker
from imp_tf.publisher import RelayPublisher

CONTEXT = StateContext("session1", 2, 3)
OTHER_CONTEXT = StateContext("session1", 3, 3)


class _TrackedPipeWriter:
    def __init__(self, fd: int) -> None:
        self._stream = os.fdopen(fd, "w", encoding="utf-8", buffering=1)
        self.writes: list[str] = []

    def fileno(self) -> int:
        return self._stream.fileno()

    def write(self, value: str) -> int:
        self.writes.append(value)
        return self._stream.write(value)

    def flush(self) -> None:
        self._stream.flush()

    def close(self) -> None:
        try:
            self._stream.close()
        except BrokenPipeError:
            pass


@contextmanager
def _stalled_consumer_peer(
    *, acknowledge_registration: bool
) -> Iterator[tuple[str, threading.Event, list[dict[str, object]]]]:
    started = threading.Event()
    stalled = threading.Event()
    release = threading.Event()
    shutdown = threading.Event()
    frames: list[dict[str, object]] = []
    url: list[str] = []
    failures: list[BaseException] = []

    async def run_server() -> None:
        async def handle(connection: ServerConnection) -> None:
            frames.append(cast(dict[str, object], json.loads(cast(str, await connection.recv()))))
            if acknowledge_registration:
                await connection.send(encode_consumer_ready(CONTEXT))
            stalled.set()
            release.wait()

        async with serve(handle, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            url.append(f"ws://127.0.0.1:{port}/action-consumer")
            started.set()
            await asyncio.to_thread(shutdown.wait)

    def run_thread() -> None:
        try:
            asyncio.run(run_server())
        except BaseException as error:
            failures.append(error)
            started.set()

    thread = threading.Thread(target=run_thread)
    thread.start()
    if not started.wait(1):
        raise AssertionError("stalled WebSocket peer did not start")
    if failures:
        raise failures[0]
    try:
        yield url[0], stalled, frames
    finally:
        release.set()
        shutdown.set()
        thread.join(timeout=1)
        if thread.is_alive():
            raise AssertionError("stalled WebSocket peer did not stop")
        if failures:
            raise failures[0]


def test_context_marker_is_private_and_rejects_partial_or_extra_content(tmp_path: Path) -> None:
    marker = tmp_path / "private" / "context"
    write_context_marker(marker, CONTEXT)

    assert read_context_marker(marker) == CONTEXT
    assert stat.S_IMODE(marker.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(marker.stat().st_mode) == 0o600

    marker.write_bytes(b"IMPCTX 2 session1 2 3")
    assert read_context_marker(marker) is None
    marker.write_bytes(b"IMPCTX 2 session1 2 3\nextra")
    assert read_context_marker(marker) is None
    write_context_marker(marker, None)
    assert not marker.exists()


def test_tf_encoding_never_emits_command_separators_or_whitespace() -> None:
    command = 'say 100% /foo; "bar"'
    token = encode_tf_token(command)

    assert token == "say_32_100_37__32__47_foo_59__32__34_bar_34_"


def test_idle_pipe_reader_loss_aborts_stalled_registered_peer(tmp_path: Path) -> None:
    async def scenario() -> None:
        marker = tmp_path / "context"
        write_context_marker(marker, CONTEXT)
        read_fd, write_fd = os.pipe()
        output = _TrackedPipeWriter(write_fd)

        try:
            with _stalled_consumer_peer(acknowledge_registration=True) as (url, stalled, frames):
                helper = asyncio.create_task(
                    run_action_consumer(url, CONTEXT, "Alpha", marker, cast(TextIO, output))
                )
                assert await asyncio.to_thread(stalled.wait, 1)

                os.close(read_fd)
                read_fd = -1
                await asyncio.wait_for(helper, timeout=0.75)
                assert len(frames) == 1
                assert frames[0]["type"] == "consumer"
                assert output.writes == []
        finally:
            if read_fd >= 0:
                os.close(read_fd)
            output.close()

    asyncio.run(scenario())


def test_pipe_reader_loss_aborts_stalled_pending_registration(tmp_path: Path) -> None:
    async def scenario() -> None:
        marker = tmp_path / "context"
        write_context_marker(marker, CONTEXT)
        read_fd, write_fd = os.pipe()
        output = _TrackedPipeWriter(write_fd)

        try:
            with _stalled_consumer_peer(acknowledge_registration=False) as (url, stalled, frames):
                helper = asyncio.create_task(
                    run_action_consumer(url, CONTEXT, "Alpha", marker, cast(TextIO, output))
                )
                assert await asyncio.to_thread(stalled.wait, 1)

                os.close(read_fd)
                read_fd = -1
                await asyncio.wait_for(helper, timeout=0.75)
                assert len(frames) == 1
                assert frames[0]["type"] == "consumer"
                assert output.writes == []
        finally:
            if read_fd >= 0:
                os.close(read_fd)
            output.close()

    asyncio.run(scenario())


def test_completed_real_pipe_delivery_remains_forwarded(tmp_path: Path) -> None:
    async def scenario() -> None:
        marker = tmp_path / "context"
        write_context_marker(marker, CONTEXT)
        read_fd, write_fd = os.pipe()
        output = _TrackedPipeWriter(write_fd)
        result: dict[str, object] | None = None

        async def handle(connection: ServerConnection) -> None:
            nonlocal result
            await connection.recv()
            await connection.send(encode_consumer_ready(CONTEXT))
            await connection.send(encode_dispatch("dispatch1", CONTEXT, "east"))
            result = cast(dict[str, object], json.loads(cast(str, await connection.recv())))

        try:
            async with serve(handle, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                await run_action_consumer(
                    f"ws://127.0.0.1:{port}/action-consumer",
                    CONTEXT,
                    "Alpha",
                    marker,
                    cast(TextIO, output),
                )

            assert os.read(read_fd, 4096) == b"/imp_send session1 2 3 Alpha east\n"
            assert result is not None
            assert result["status"] == "forwarded"
            assert len(output.writes) == 1
        finally:
            os.close(read_fd)
            output.close()

    asyncio.run(scenario())


def test_shell_and_idle_helper_exit_when_parent_closes_pipe(tmp_path: Path) -> None:
    async def scenario() -> None:
        marker = tmp_path / "context"
        write_context_marker(marker, CONTEXT)
        registered = asyncio.Event()
        disconnected = asyncio.Event()

        async def handle(connection: ServerConnection) -> None:
            await connection.recv()
            await connection.send(encode_consumer_ready(CONTEXT))
            registered.set()
            await connection.wait_closed()
            disconnected.set()

        read_fd, write_fd = os.pipe()
        process: subprocess.Popen[bytes] | None = None
        try:
            async with serve(handle, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                process = subprocess.Popen(
                    [
                        "/bin/sh",
                        "-c",
                        '{ "$@"; }',
                        "imp-action-shell",
                        sys.executable,
                        "-m",
                        "imp_tf.action_consumer",
                        "--relay-url",
                        f"ws://127.0.0.1:{port}/action-consumer",
                        "--session",
                        CONTEXT.session,
                        "--foreground",
                        str(CONTEXT.foreground),
                        "--connection",
                        str(CONTEXT.connection),
                        "--world",
                        "Alpha",
                        "--marker",
                        str(marker),
                    ],
                    stdout=write_fd,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                )
                os.close(write_fd)
                write_fd = -1
                await asyncio.wait_for(registered.wait(), timeout=1)
                os.close(read_fd)
                read_fd = -1
                returncode = await asyncio.to_thread(process.wait, 0.5)
                await asyncio.wait_for(disconnected.wait(), timeout=0.5)

            assert returncode == 0
        finally:
            if read_fd >= 0:
                os.close(read_fd)
            if write_fd >= 0:
                os.close(write_fd)
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()

    asyncio.run(scenario())


def test_helper_forwards_one_fixed_macro_line_then_exits(tmp_path: Path) -> None:
    async def scenario() -> None:
        marker = tmp_path / "context"
        write_context_marker(marker, CONTEXT)
        output = StringIO()
        received: list[dict[str, object]] = []
        disconnected = asyncio.Event()

        async def handle(connection: ServerConnection) -> None:
            registration = cast(dict[str, object], json.loads(cast(str, await connection.recv())))
            received.append(registration)
            await connection.send(encode_consumer_ready(CONTEXT))
            await connection.send(encode_dispatch("dispatch1", CONTEXT, "say hello; /quit"))
            result = cast(dict[str, object], json.loads(cast(str, await connection.recv())))
            received.append(result)
            await connection.wait_closed()
            disconnected.set()

        async with serve(handle, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            await run_action_consumer(
                f"ws://127.0.0.1:{port}/action-consumer", CONTEXT, "Alpha", marker, output
            )
            await asyncio.wait_for(disconnected.wait(), timeout=1)

        assert received[0]["type"] == "consumer"
        assert received[1] == {
            "type": "consumer-result",
            "protocol": 2,
            "id": "dispatch1",
            "status": "forwarded",
        }
        assert output.getvalue() == ("/imp_send session1 2 3 Alpha say_32_hello_59__32__47_quit\n")

    asyncio.run(scenario())


def test_replacement_consumer_recovers_from_registration_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        marker = tmp_path / "context"
        write_context_marker(marker, CONTEXT)
        output = StringIO()
        unregister_deferred = asyncio.Event()
        duplicate_rejected = asyncio.Event()
        replacement_registered = asyncio.Event()
        registrations: list[ConsumerRegistration] = []
        deferred: list[ConsumerRegistration] = []

        relay = RelayServer(port=0)
        await relay.start()
        original_register = relay._actions.register
        original_unregister = relay._actions.unregister

        def observe_register(
            connection: ServerConnection,
            context: StateContext,
            active_context: StateContext | None,
        ) -> ConsumerRegistration | None:
            registration = original_register(connection, context, active_context)
            if registration is None and context == CONTEXT:
                duplicate_rejected.set()
            elif registration is not None:
                registrations.append(registration)
                if len(registrations) == 2:
                    replacement_registered.set()
            return registration

        def observe_unregister(registration: ConsumerRegistration) -> None:
            if not deferred and registration == registrations[0]:
                deferred.append(registration)
                unregister_deferred.set()
                return
            original_unregister(registration)

        monkeypatch.setattr(relay._actions, "register", observe_register)
        monkeypatch.setattr(relay._actions, "unregister", observe_unregister)
        first: asyncio.Task[None] | None = None
        replacement: asyncio.Task[None] | None = None
        try:
            async with connect(f"ws://127.0.0.1:{relay.port}/ingest") as producer:
                await producer.send(encode_select(CONTEXT, GameState(character=None, target=None)))

            async def send_when_ready(command: str) -> dict[str, object]:
                for _ in range(100):
                    async with connect(f"ws://127.0.0.1:{relay.port}/action") as action:
                        await action.send(encode_action(CONTEXT, command))
                        result = cast(dict[str, object], json.loads(cast(str, await action.recv())))
                    if result["status"] == "forwarded":
                        return result
                    assert result["status"] == "rejected"
                    await asyncio.sleep(0.01)
                raise AssertionError("action consumer never became ready")

            first = asyncio.create_task(
                run_action_consumer(
                    f"ws://127.0.0.1:{relay.port}/action-consumer", CONTEXT, "Alpha", marker, output
                )
            )
            assert (await send_when_ready("west"))["status"] == "forwarded"
            await asyncio.wait_for(unregister_deferred.wait(), timeout=1)

            replacement = asyncio.create_task(
                run_action_consumer(
                    f"ws://127.0.0.1:{relay.port}/action-consumer", CONTEXT, "Alpha", marker, output
                )
            )
            await asyncio.wait_for(duplicate_rejected.wait(), timeout=1)
            original_unregister(deferred[0])
            await asyncio.wait_for(first, timeout=1)
            await asyncio.wait_for(replacement_registered.wait(), timeout=1)

            original_unregister(deferred[0])
            assert (await send_when_ready("east"))["status"] == "forwarded"
            await asyncio.wait_for(replacement, timeout=1)
        finally:
            if deferred:
                original_unregister(deferred[0])
            await relay.close()
            for helper in (first, replacement):
                if helper is not None and not helper.done():
                    helper.cancel()
                    await asyncio.gather(helper, return_exceptions=True)

        assert output.getvalue().splitlines() == [
            "/imp_send session1 2 3 Alpha west",
            "/imp_send session1 2 3 Alpha east",
        ]

    asyncio.run(scenario())


def test_helper_rechecks_context_immediately_before_delivery(tmp_path: Path) -> None:
    async def scenario() -> None:
        marker = tmp_path / "context"
        write_context_marker(marker, CONTEXT)
        output = StringIO()

        async def handle(connection: ServerConnection) -> None:
            await connection.recv()
            await connection.send(encode_consumer_ready(CONTEXT))
            write_context_marker(marker, None)
            await connection.send(encode_dispatch("dispatch1", CONTEXT, "north"))
            await connection.wait_closed()

        async with serve(handle, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            await run_action_consumer(
                f"ws://127.0.0.1:{port}/action-consumer", CONTEXT, "Alpha", marker, output
            )

        assert output.getvalue() == ""

    asyncio.run(scenario())


def test_tf_dispatch_contains_fixed_context_and_encoded_data() -> None:
    assert encode_tf_dispatch(CONTEXT, "Alpha_32_World", "say hi; /quit") == (
        "/imp_send session1 2 3 Alpha_32_World say_32_hi_59__32__47_quit\n"
    )


def test_session_initialization_evaluates_shell_safe_token_once() -> None:
    source = (Path(__file__).parents[1] / "imp.tf").read_text(encoding="utf-8")
    initialization = (
        '/if (!isvar("imp_session")) \\\n'
        '    /test imp_session := textencode(strcat(getpid(), ".", time()))%; \\\n'
        "/endif"
    )

    assert initialization in source


def test_imp_send_fence_keeps_dynamic_lookup_inside_eval_scope() -> None:
    """TinyFugue destroys /eval locals, so the dynamic lookup must remain in its guard."""
    source = (Path(__file__).parents[1] / "imp.tf").read_text(encoding="utf-8")
    macro = source.split("/def -i imp_send = \\\n", 1)[1].split("\n\n/def", 1)[0]
    guard = (
        "/eval \\\n"
        "        /if (_expected_session =~ imp_session & \\\n"
        "            _expected_foreground = imp_foreground & \\\n"
        "            _expected_connection =~ %%{imp_connection_%{_pinned_world}} & \\\n"
        "            _expected_world =~ imp_selected_world & \\\n"
        "            _expected_world =~ _pinned_world) \\"
    )

    assert guard in macro
    assert "_current_connection" not in macro
    send = macro.index("/test send(textdecode({5}))%%;")
    replacement = macro.index("/imp_start_consumer %{_expected_connection} %{_expected_world}%%;")
    endif = macro.index("/endif", macro.index(guard))
    assert macro.index(guard) < send < replacement < endif
    assert macro.count("/imp_start_consumer") == 1


def test_tf_eval_locals_never_escape_single_command_eval() -> None:
    source = (Path(__file__).parents[1] / "imp.tf").read_text(encoding="utf-8")

    assert "/eval /let " not in source
    assert "/eval /set imp_connection_%{_world}=%{imp_connection_serial}%;" in source
    assert "/set _connection" not in source
    assert source.count("imp_connection_serial :=") == 1


def test_select_world_deselects_until_known_generation_is_connected() -> None:
    source = (Path(__file__).parents[1] / "imp.tf").read_text(encoding="utf-8")
    macro = source.split("/def -i imp_select_world = \\\n", 1)[1].split("\n\n/def", 1)[0]
    lookup = "/let _connection=%%{imp_connection_%{_world}}%%;"

    changed = macro.index("/if (_world !~ imp_selected_world)")
    increment = macro.index("imp_foreground := imp_foreground + 1", changed)
    selected = macro.index("/set imp_selected_world=%{_world}", increment)
    changed_end = macro.index("/endif%;", selected)

    scope = macro.index("/eval \\", changed_end)
    first_lookup = macro.index(lookup, scope)
    unavailable = macro.index(
        "/if (!strlen(_connection) | !is_connected(textdecode(_world)))",
        first_lookup,
    )
    deselect = macro.index(
        'strcat("IMP2 S ", imp_session, " ", imp_foreground, " 0 - ", time()))%%;',
        unavailable,
    )
    alternative = macro.index("/else", deselect)
    event = macro.index('strcat("IMP2 S "', alternative)
    consumer = macro.index("/imp_start_consumer %%{_connection} %{_world}%%;", event)
    missing_end = macro.index("/endif", consumer)

    assert changed < increment < selected < changed_end < scope
    assert scope < first_lookup < unavailable < deselect < alternative
    assert alternative < event < consumer < missing_end
    assert macro.count(lookup) == 1
    assert "/imp_reset_world" not in macro
    assert macro.count("/imp_start_consumer") == 1
    assert "/let _world=$[textencode({1})]" in macro
    assert '_connection, " ", _world' in macro[event:consumer]


def test_connect_owns_connection_generation_not_gmcp_login() -> None:
    source = (Path(__file__).parents[1] / "imp.tf").read_text(encoding="utf-8")

    assert '/def -Fp2 -ag -h"CONNECT" imp_capture_connect = /imp_reset_world %{1}' in source
    assert '/def -Fp2 -ag -h"GMCP_LOGIN" imp_capture_gmcp_login' not in source


def test_gmcp_preserves_known_generation_and_initializes_only_missing() -> None:
    source = (Path(__file__).parents[1] / "imp.tf").read_text(encoding="utf-8")
    macro = source.split('/def -Fp2 -ag -h"GMCP" imp_capture_gmcp = \\\n', 1)[1]
    lookup = "/let _connection=%%{imp_connection_%{_world}}%%;"

    scope = macro.index("/eval \\")
    first_lookup = macro.index(lookup, scope)
    missing = macro.index("/if (!strlen(_connection))", first_lookup)
    reset = macro.index("/imp_reset_world %{_world_name}%%;", missing)
    second_lookup = macro.index(lookup, first_lookup + len(lookup))
    missing_end = macro.index("/endif%%;", second_lookup)
    event = macro.index('strcat("IMP2 G "', missing_end)

    assert scope < first_lookup < missing < reset < second_lookup < missing_end < event
    assert macro.count(lookup) == 2
    assert macro.count("/imp_reset_world") == 1
    assert "/let _world=$[textencode(_world_name)]" in macro
    assert '_connection, " ", _world' in macro[event:]


class _FailingWriter(StringIO):
    def __init__(self, fail_on: str) -> None:
        super().__init__()
        self.fail_on = fail_on
        self.write_attempts = 0

    def write(self, value: str) -> int:
        self.write_attempts += 1
        if self.fail_on == "write":
            raise OSError("closed pipe")
        return super().write(value)

    def flush(self) -> None:
        if self.fail_on == "flush":
            raise OSError("closed pipe")
        super().flush()


@pytest.mark.parametrize("fail_on", ["write", "flush"])
def test_pipe_failure_reports_unknown_without_retry(tmp_path: Path, fail_on: str) -> None:
    async def scenario() -> None:
        marker = tmp_path / "context"
        write_context_marker(marker, CONTEXT)
        output = _FailingWriter(fail_on)
        relay = RelayServer(port=0)
        await relay.start()
        try:
            async with connect(f"ws://127.0.0.1:{relay.port}/ingest") as producer:
                await producer.send(encode_select(CONTEXT, GameState(character=None, target=None)))

            consumer = asyncio.create_task(
                run_action_consumer(
                    f"ws://127.0.0.1:{relay.port}/action-consumer",
                    CONTEXT,
                    "Alpha",
                    marker,
                    output,
                )
            )
            for _ in range(100):
                async with connect(f"ws://127.0.0.1:{relay.port}/action") as action:
                    await action.send(encode_action(CONTEXT, "north"))
                    result = cast(dict[str, object], json.loads(cast(str, await action.recv())))
                if result["status"] == "unknown":
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("action consumer never registered")
            assert result["detail"] == "consumer disconnected after dispatch"
            await asyncio.wait_for(consumer, timeout=1)
            assert output.write_attempts == 1
        finally:
            await relay.close()

    asyncio.run(scenario())


def test_real_pipe_loss_during_dispatch_reports_unknown_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        marker = tmp_path / "context"
        write_context_marker(marker, CONTEXT)
        read_fd, write_fd = os.pipe()
        output = _TrackedPipeWriter(write_fd)
        relay = RelayServer(port=0)
        await relay.start()
        monkeypatch.setattr(action_consumer, "_PIPE_POLL_SECONDS", 10.0)
        async with connect(f"ws://127.0.0.1:{relay.port}/ingest") as producer:
            await producer.send(encode_select(CONTEXT, GameState(character=None, target=None)))

        async def wait_until_registered() -> None:
            for _ in range(100):
                probe = await connect(f"ws://127.0.0.1:{relay.port}/action-consumer")
                await probe.send(encode_consumer(CONTEXT))
                try:
                    await probe.recv()
                except ConnectionClosed:
                    return
                finally:
                    await probe.close()
                await asyncio.sleep(0.01)
            raise AssertionError("action consumer never registered")

        helper = asyncio.create_task(
            run_action_consumer(
                f"ws://127.0.0.1:{relay.port}/action-consumer",
                CONTEXT,
                "Alpha",
                marker,
                cast(TextIO, output),
            )
        )
        try:
            await wait_until_registered()
            os.close(read_fd)
            read_fd = -1

            async with connect(f"ws://127.0.0.1:{relay.port}/action") as action:
                await action.send(encode_action(CONTEXT, "north"))
                result = cast(dict[str, object], json.loads(cast(str, await action.recv())))

            assert result == {
                "type": "action-result",
                "protocol": 2,
                "status": "unknown",
                "detail": "consumer disconnected after dispatch",
            }
            await asyncio.wait_for(helper, timeout=0.5)
            assert output.writes == ["/imp_send session1 2 3 Alpha north\n"]
        finally:
            if read_fd >= 0:
                os.close(read_fd)
            output.close()
            await relay.close()
            if not helper.done():
                helper.cancel()
                await asyncio.wait_for(helper, timeout=1)

    asyncio.run(scenario())


def test_idle_context_change_terminates_old_helper_without_replay(tmp_path: Path) -> None:
    async def scenario() -> None:
        marker = tmp_path / "context"
        output_a = StringIO()
        output_b = StringIO()
        relay = RelayServer(port=0)
        await relay.start()
        port = relay.port
        publisher = RelayPublisher(url=f"ws://127.0.0.1:{port}/ingest")
        write_context_marker(marker, CONTEXT)
        await publisher.select(CONTEXT, GameState(character=None, target=None))
        helper_a = asyncio.create_task(
            run_action_consumer(
                f"ws://127.0.0.1:{port}/action-consumer",
                CONTEXT,
                "Alpha",
                marker,
                output_a,
            )
        )
        helper_b: asyncio.Task[None] | None = None

        async def send_when_ready(context: StateContext, command: str) -> dict[str, object]:
            for _ in range(100):
                async with connect(f"ws://127.0.0.1:{port}/action") as action:
                    await action.send(encode_action(context, command))
                    result = cast(dict[str, object], json.loads(cast(str, await action.recv())))
                if result["status"] == "forwarded":
                    return result
                assert result["status"] == "rejected"
                await asyncio.sleep(0.01)
            raise AssertionError("action consumer never became ready")

        async def wait_until_registered(context: StateContext) -> None:
            for _ in range(100):
                probe = await connect(f"ws://127.0.0.1:{port}/action-consumer")
                await probe.send(encode_consumer(context))
                try:
                    await probe.recv()
                except ConnectionClosed:
                    return
                finally:
                    await probe.close()
                await asyncio.sleep(0.01)
            raise AssertionError("action consumer never registered")

        try:
            await wait_until_registered(CONTEXT)
            write_context_marker(marker, OTHER_CONTEXT)
            await publisher.select(OTHER_CONTEXT, GameState(character=None, target=None))
            await asyncio.wait_for(helper_a, timeout=0.5)

            async with connect(f"ws://127.0.0.1:{port}/action") as action:
                await action.send(encode_action(CONTEXT, "old"))
                old_result = cast(dict[str, object], json.loads(cast(str, await action.recv())))
            assert old_result["status"] == "rejected"

            helper_b = asyncio.create_task(
                run_action_consumer(
                    f"ws://127.0.0.1:{port}/action-consumer",
                    OTHER_CONTEXT,
                    "Beta",
                    marker,
                    output_b,
                )
            )
            assert (await send_when_ready(OTHER_CONTEXT, "east"))["status"] == "forwarded"
            await asyncio.wait_for(helper_b, timeout=1)
            assert output_a.getvalue() == ""
            assert output_b.getvalue() == "/imp_send session1 3 3 Beta east\n"
        finally:
            write_context_marker(marker, None)
            await publisher.close()
            await relay.close()
            if helper_b is not None and not helper_b.done():
                await asyncio.wait_for(helper_b, timeout=1)
            if not helper_a.done():
                await asyncio.wait_for(helper_a, timeout=1)

    asyncio.run(scenario())


def test_marker_transition_closes_helper_without_reregistering(tmp_path: Path) -> None:
    async def scenario() -> None:
        marker = tmp_path / "context"
        write_context_marker(marker, CONTEXT)
        output = StringIO()
        registered = asyncio.Event()
        disconnected = asyncio.Event()
        registrations = 0

        async def handle(connection: ServerConnection) -> None:
            nonlocal registrations
            registrations += 1
            await connection.recv()
            await connection.send(encode_consumer_ready(CONTEXT))
            registered.set()
            await connection.wait_closed()
            disconnected.set()

        async with serve(handle, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            helper = asyncio.create_task(
                run_action_consumer(
                    f"ws://127.0.0.1:{port}/action-consumer",
                    CONTEXT,
                    "Alpha",
                    marker,
                    output,
                )
            )
            await asyncio.wait_for(registered.wait(), timeout=1)
            write_context_marker(marker, OTHER_CONTEXT)
            await asyncio.wait_for(helper, timeout=0.5)
            await asyncio.wait_for(disconnected.wait(), timeout=0.5)
            await asyncio.sleep(0.1)

        assert registrations == 1
        assert output.getvalue() == ""

    asyncio.run(scenario())


def test_real_pipe_consumer_recovers_after_relay_restart_then_stops_on_reader_loss(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        marker = tmp_path / "context"
        write_context_marker(marker, CONTEXT)
        read_fd, write_fd = os.pipe()
        output = _TrackedPipeWriter(write_fd)
        relay = RelayServer(port=0)
        await relay.start()
        port = relay.port
        publisher = RelayPublisher(url=f"ws://127.0.0.1:{port}/ingest")
        await publisher.select(CONTEXT, GameState(character=None, target=None))
        consumer = asyncio.create_task(
            run_action_consumer(
                f"ws://127.0.0.1:{port}/action-consumer",
                CONTEXT,
                "Alpha",
                marker,
                cast(TextIO, output),
            )
        )

        async def wait_until_registered() -> None:
            for _ in range(100):
                probe = await connect(f"ws://127.0.0.1:{port}/action-consumer")
                await probe.send(encode_consumer(CONTEXT))
                try:
                    await probe.recv()
                except ConnectionClosed:
                    return
                finally:
                    await probe.close()
                await asyncio.sleep(0.01)
            raise AssertionError("action consumer never registered")

        async def send_when_ready(command: str) -> dict[str, object]:
            for _ in range(300):
                async with connect(f"ws://127.0.0.1:{port}/action") as action:
                    await action.send(encode_action(CONTEXT, command))
                    result = cast(dict[str, object], json.loads(cast(str, await action.recv())))
                if result["status"] == "forwarded":
                    return result
                assert result["status"] == "rejected"
                await asyncio.sleep(0.01)
            raise AssertionError("action consumer never became ready")

        restarted: RelayServer | None = None
        replacement: asyncio.Task[None] | None = None
        try:
            await wait_until_registered()
            assert output.writes == []
            await relay.close()
            restarted = RelayServer(port=port)
            await restarted.start()

            for _ in range(300):
                snapshot = restarted.state.snapshot()
                if snapshot is not None and snapshot.context == CONTEXT:
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("publisher did not restore the retained selection")

            assert restarted.state.health()["feed"] == "stale"
            assert output.writes == []
            assert (await send_when_ready("east"))["status"] == "forwarded"
            await asyncio.wait_for(consumer, timeout=1)
            assert os.read(read_fd, 4096) == b"/imp_send session1 2 3 Alpha east\n"
            assert output.writes == ["/imp_send session1 2 3 Alpha east\n"]

            replacement = asyncio.create_task(
                run_action_consumer(
                    f"ws://127.0.0.1:{port}/action-consumer",
                    CONTEXT,
                    "Alpha",
                    marker,
                    cast(TextIO, output),
                )
            )
            await wait_until_registered()
            os.close(read_fd)
            read_fd = -1
            await asyncio.wait_for(replacement, timeout=0.75)
            assert output.writes == ["/imp_send session1 2 3 Alpha east\n"]
        finally:
            write_context_marker(marker, None)
            if read_fd >= 0:
                os.close(read_fd)
            output.close()
            await publisher.close()
            if restarted is not None:
                await restarted.close()
            else:
                await relay.close()
            for helper in (consumer, replacement):
                if helper is not None and not helper.done():
                    helper.cancel()
                    await asyncio.gather(helper, return_exceptions=True)

    asyncio.run(scenario())
