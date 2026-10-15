from __future__ import annotations

import os
import select
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from imp_tf.feed_watch import run_feed_watch


def test_readiness_notifications_and_idle_reader_loss(tmp_path: Path) -> None:
    ready, spool = tmp_path / "feed-ready", tmp_path / "spool"
    read_fd, write_fd = os.pipe()
    output = os.fdopen(write_fd, "w")
    errors: list[BaseException] = []

    def watch() -> None:
        try:
            run_feed_watch(ready, spool, output)
        except BaseException as error:
            errors.append(error)

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()

    def receive(expected: bytes | None) -> None:
        readable, _, _ = select.select([read_fd], [], [], 2.5 if expected else 1.2)
        if expected is None:
            assert not readable
        else:
            assert readable
            assert os.read(read_fd, 4096) == expected

    try:
        receive(None)  # Feed absent.
        first, second = b"a" * 32, b"b" * 32
        ready.write_bytes(first + b"\n")
        receive(None)  # A marker without the hook spool is not ready.
        ready.write_bytes(b"invalid\n")
        spool.touch()
        for malformed in (b"A" * 32, first + b"\nextra", b"private MUD value\n", first + b"\r\n"):
            ready.write_bytes(malformed)
            receive(None)
        ready.write_bytes(first + b"\n")
        receive(b"/imp_feed_ready " + first + b"\n")
        receive(None)
        ready.unlink()
        receive(None)
        ready.write_bytes(first)
        receive(None)  # Reappearance is not a new incarnation.
        ready.write_bytes(second)
        receive(b"/imp_feed_ready " + second + b"\n")
        receive(None)
        os.close(read_fd)
        read_fd = -1
        watcher.join(0.5)  # poll wakes on reader loss, not the next token change.
        assert not watcher.is_alive()
        assert not errors
    finally:
        if read_fd != -1:
            os.close(read_fd)
        watcher.join(2)
        output.close()


@pytest.mark.parametrize("ready", [False, True])
def test_reader_loss_stops_without_ready_feed(tmp_path: Path, ready: bool) -> None:
    marker, spool = tmp_path / "feed-ready", tmp_path / "spool"
    if ready:
        marker.write_bytes(b"c" * 32 + b"\n")
        spool.touch()
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    with os.fdopen(write_fd, "w") as output:
        watcher = threading.Thread(target=run_feed_watch, args=(marker, spool, output), daemon=True)
        watcher.start()
        watcher.join(0.5)
        assert not watcher.is_alive()


def test_cli_notifies_without_xdg_runtime_dir(tmp_path: Path) -> None:
    state = tmp_path / ".local" / "state" / "imp"
    state.mkdir(parents=True)
    token = b"d" * 32
    (state / "feed-ready").write_bytes(token + b"\n")
    (state / "spool").touch()
    environment = {**os.environ, "HOME": str(tmp_path)}
    environment.pop("XDG_RUNTIME_DIR", None)
    process = subprocess.Popen(
        [sys.executable, "-m", "imp_tf.feed_watch"],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    try:
        readable, _, _ = select.select([process.stdout], [], [], 3)
        assert readable
        assert process.stdout.readline() == b"/imp_feed_ready " + token + b"\n"
        process.stdout.close()
        assert process.wait(timeout=1) == 0
        assert process.stderr.read() == b""
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        process.stdout.close()
        process.stderr.close()
