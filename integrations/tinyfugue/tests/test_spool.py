from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from tinyscry_tf.spool import ProducerAlreadyRunning, RuntimeLayout, SpoolReader, acquire_producer_lock


def _layout(tmp_path: Path) -> RuntimeLayout:
    return RuntimeLayout.resolve(runtime_dir=tmp_path / "run", state_dir=tmp_path / "state")


def _append(path: Path, text: str) -> None:
    """Mimic TinyFugue's fwrite(): a fresh open("a"), one write, close."""
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


def test_resolve_uses_xdg_runtime_but_keeps_the_hook_at_its_fixed_tf_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "custom-state"))

    layout = RuntimeLayout.resolve()

    assert layout.runtime_dir == tmp_path / "run" / "tinyscry"
    assert layout.spool == layout.runtime_dir / "spool"
    assert layout.hook_spool == tmp_path / "home" / ".local" / "state" / "tinyscry" / "spool"
    assert layout.checkpoint == layout.runtime_dir / "state.json"


def test_resolve_requires_xdg_runtime_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

    with pytest.raises(RuntimeError, match="XDG_RUNTIME_DIR"):
        RuntimeLayout.resolve()


def test_open_creates_private_runtime_dir_and_links_the_fixed_hook_path(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    reader = SpoolReader(layout)

    reader.open()

    assert stat.S_IMODE(layout.runtime_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(layout.spool.stat().st_mode) == 0o600
    assert layout.hook_spool.is_symlink()
    assert Path(os.readlink(layout.hook_spool)) == layout.spool
    reader.close()


def test_reader_drains_lines_written_through_the_fixed_hook_path(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    reader = SpoolReader(layout)
    reader.open()
    try:
        _append(layout.hook_spool, "1700000000 Char.Vitals {}\n")
        _append(layout.hook_spool, "1700000001 Char.Status {}\n")

        assert reader.read_lines() == [
            "1700000000 Char.Vitals {}",
            "1700000001 Char.Status {}",
        ]
        assert reader.read_lines() == []
    finally:
        reader.close()


def test_reader_buffers_a_line_split_across_two_writes(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    reader = SpoolReader(layout)
    reader.open()
    try:
        _append(layout.spool, "1700000000 Char.Vitals ")
        assert reader.read_lines() == []
        _append(layout.spool, '{"hp":"1"}\n')
        assert reader.read_lines() == ['1700000000 Char.Vitals {"hp":"1"}']
    finally:
        reader.close()


def test_reader_recovers_when_spool_is_deleted_and_recreated(tmp_path: Path) -> None:
    """A feed restart replaces the spool file; TinyFugue's next write must still land."""
    layout = _layout(tmp_path)
    reader = SpoolReader(layout)
    reader.open()
    try:
        _append(layout.spool, "1700000000 Char.Vitals {}\n")
        assert reader.read_lines() == ["1700000000 Char.Vitals {}"]

        layout.spool.unlink()
        _append(layout.spool, "1700000001 Char.Vitals {}\n")

        assert reader.read_lines() == ["1700000001 Char.Vitals {}"]
    finally:
        reader.close()


def test_reader_resets_the_spool_once_fully_drained_past_the_threshold(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    reader = SpoolReader(layout, reset_at=10)
    reader.open()
    try:
        _append(layout.spool, "1700000000 Char.Vitals {}\n")  # 26 bytes, past reset_at
        reader.read_lines()

        assert layout.spool.stat().st_size == 0

        _append(layout.spool, "1700000001 Char.Vitals {}\n")
        assert reader.read_lines() == ["1700000001 Char.Vitals {}"]
    finally:
        reader.close()


def test_reader_drops_an_unbounded_partial_line_instead_of_growing_without_bound(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    reader = SpoolReader(layout)
    reader.open()
    try:
        _append(layout.spool, "z" * 40_000)  # no trailing newline: one giant partial line
        reader.read_lines()

        assert reader.dropped == 1

        _append(layout.spool, "1700000001 Char.Vitals {}\n")
        assert reader.read_lines() == ["1700000001 Char.Vitals {}"]
    finally:
        reader.close()


def test_open_replaces_a_stale_fifo_left_at_the_spool_path(tmp_path: Path) -> None:
    """A FIFO here would freeze TinyFugue's writer; the reader must never leave one behind."""
    layout = _layout(tmp_path)
    layout.runtime_dir.mkdir(parents=True)
    os.mkfifo(layout.spool)

    reader = SpoolReader(layout)
    reader.open()
    try:
        assert stat.S_ISREG(layout.spool.stat().st_mode)
    finally:
        reader.close()


def test_lock_is_exclusive_and_released_on_close(tmp_path: Path) -> None:
    lock_path = tmp_path / "run" / "feed.lock"

    held = acquire_producer_lock(lock_path)
    with pytest.raises(ProducerAlreadyRunning):
        acquire_producer_lock(lock_path)

    os.close(held)

    released = acquire_producer_lock(lock_path)
    os.close(released)


def test_rotation_preserves_a_write_from_an_fd_opened_before_rename(tmp_path: Path) -> None:
    """A TinyFugue fwrite already holding the old inode must survive rotation."""
    layout = _layout(tmp_path)
    reader = SpoolReader(layout, reset_at=10)
    reader.open()
    writer_fd: int | None = None
    try:
        first = b"1700000000 Char.Vitals {}\n"
        late = b"1700000001 Char.Vitals {}\n"

        # Model TinyFugue having opened the active spool immediately before
        # the reader rotates it. This fd must continue to reference the old
        # inode after the path is renamed to spool.retired.
        writer_fd = os.open(layout.spool, os.O_WRONLY | os.O_APPEND)

        _append(layout.hook_spool, first.decode())
        assert reader.read_lines() == [first.decode().rstrip("\n")]
        assert layout.retired_spool.exists()
        assert layout.spool.exists()

        os.write(writer_fd, late)
        os.close(writer_fd)
        writer_fd = None

        assert reader.read_lines() == [late.decode().rstrip("\n")]
    finally:
        if writer_fd is not None:
            os.close(writer_fd)
        reader.close()


def test_close_removes_the_hook_path_so_feed_absence_drops_instead_of_accumulates(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    reader = SpoolReader(layout)
    reader.open()
    assert layout.hook_spool.is_symlink()

    reader.close()

    assert not layout.hook_spool.exists()
    assert not layout.hook_spool.is_symlink()
    assert not layout.spool.exists()
    assert not layout.retired_spool.exists()
