"""Private runtime state for the live feed: spool transport and producer lock.

TinyFugue's ``fwrite()`` is a blocking open/write/close with no non-blocking
mode. A FIFO can therefore freeze the interactive MUD client. The live hop is
a private regular file instead.

The reader bounds that file without truncating an inode TinyFugue may have
already opened. Once the active spool crosses the reset threshold it is renamed
to ``spool.retired`` and a fresh active file is created at the stable path. The
reader keeps the retired inode until the *next* active generation itself reaches
the threshold, draining any late append that raced the rename before unlinking
the retired file. With TinyFugue's synchronous hook, one invocation cannot
remain in an old generation while also filling the next one, so this avoids the
old stat-then-truncate loss window while keeping at most two bounded
generations.

The hook-facing symlink is removed when the feed stops. The systemd unit also
removes it in ExecStopPost so SIGKILL/crash paths fail open by dropping updates
instead of leaving an indefinitely growing raw spool.
"""

from __future__ import annotations

import codecs
import fcntl
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from imp_adapter.records import MAX_RECORD_CHARS

LOGGER = logging.getLogger(__name__)

RUNTIME_DIR_MODE: Final = 0o700
SPOOL_MODE: Final = 0o600
SPOOL_RESET_BYTES: Final = 65_536
_READ_CHUNK: Final = 65_536
_MAX_PARTIAL_CHARS: Final = MAX_RECORD_CHARS * 2
_RETIRED_NAME: Final = "spool.retired"


class ProducerAlreadyRunning(RuntimeError):
    """Another Imp feed already owns the runtime lock."""


@dataclass(frozen=True)
class RuntimeLayout:
    """Private runtime/state paths owned by the feed."""

    runtime_dir: Path
    spool: Path
    lock: Path
    hook_spool: Path
    diagnostics: Path
    checkpoint: Path
    context: Path

    @classmethod
    def resolve(cls, runtime_dir: Path | None = None, state_dir: Path | None = None) -> RuntimeLayout:
        state = state_dir if state_dir is not None else _default_state_dir()
        runtime = runtime_dir if runtime_dir is not None else _default_runtime_dir()
        return cls(
            runtime_dir=runtime,
            spool=runtime / "spool",
            lock=runtime / "feed.lock",
            # TinyFugue's verified path expansion handles "~" but does not give
            # the hook an XDG_STATE_HOME lookup. Keep this path exactly aligned
            # with imp.tf instead of silently diverging when XDG_STATE_HOME
            # is customized.
            hook_spool=state / "spool",
            diagnostics=state / "diagnostics",
            # Normalized checkpoint only; never raw GMCP. Kept in the ephemeral
            # runtime directory so a feed restart can recover identity/vitals
            # without turning it into durable session history.
            checkpoint=runtime / "state.json",
            context=state / "context",
        )

    @property
    def retired_spool(self) -> Path:
        return self.runtime_dir / _RETIRED_NAME


def _default_state_dir() -> Path:
    # Must match the literal path used by integrations/tinyfugue/imp.tf.
    return Path.home() / ".local" / "state" / "imp"


def _default_runtime_dir() -> Path:
    runtime_home = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime_home:
        raise RuntimeError("XDG_RUNTIME_DIR is required for the live Imp spool")
    return Path(runtime_home) / "imp"


def acquire_producer_lock(path: Path) -> int:
    """Take exclusive ownership of the live feed, or refuse to start."""

    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, RUNTIME_DIR_MODE)
    fd = os.open(path, os.O_CREAT | os.O_WRONLY, SPOOL_MODE)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        holder = _lock_holder(path)
        os.close(fd)
        raise ProducerAlreadyRunning(
            f"another Imp feed holds {path}" + (f" (pid {holder})" if holder else "")
        ) from error
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    return fd


def _lock_holder(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="ascii").strip()
    except OSError:
        return None
    return text if text.isdigit() else None


class SpoolReader:
    """Drain the TinyFugue spool while bounding raw runtime storage."""

    def __init__(self, layout: RuntimeLayout, reset_at: int = SPOOL_RESET_BYTES) -> None:
        if reset_at <= 0:
            raise ValueError("reset_at must be positive")
        self._layout = layout
        self._reset_at = reset_at

        self._fd: int | None = None
        self._inode: int | None = None
        self._offset = 0
        self._partial = ""
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        self._retired_fd: int | None = None
        self._retired_offset = 0
        self._retired_partial = ""
        self._retired_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        self.dropped = 0

    def open(self) -> None:
        """Create/claim the runtime files and publish the fixed hook symlink."""

        self._layout.runtime_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self._layout.runtime_dir, RUNTIME_DIR_MODE)
        self._layout.context.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self._layout.context.parent, RUNTIME_DIR_MODE)

        # Recover a generation left by a crash before opening the active file.
        self._open_retired_if_present()
        self._open_active()
        self._link_hook_path()
        LOGGER.info("spool ready at %s", self._layout.spool)

    def _open_retired_if_present(self) -> None:
        retired = self._layout.retired_spool
        if retired.is_symlink() or (retired.exists() and not retired.is_file()):
            LOGGER.warning("removing unusable retired spool path %s", retired)
            retired.unlink()
        if not retired.exists():
            return

        fd = os.open(retired, os.O_RDONLY)
        self._retired_fd = fd
        self._retired_offset = 0
        self._retired_partial = ""
        self._retired_decoder.reset()
        os.lseek(fd, 0, os.SEEK_SET)

    def _open_active(self) -> None:
        spool = self._layout.spool
        if spool.is_symlink() or (spool.exists() and not spool.is_file()):
            LOGGER.warning("replacing unusable spool path %s", spool)
            spool.unlink()
        fd = os.open(spool, os.O_RDWR | os.O_CREAT, SPOOL_MODE)
        os.fchmod(fd, SPOOL_MODE)
        self._close_active_fd()
        self._fd = fd
        self._inode = os.fstat(fd).st_ino
        self._offset = 0
        self._partial = ""
        self._decoder.reset()
        os.lseek(fd, 0, os.SEEK_SET)

    def _link_hook_path(self) -> None:
        hook = self._layout.hook_spool
        if hook == self._layout.spool:
            return
        hook.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(hook.parent, RUNTIME_DIR_MODE)
        if hook.is_symlink() and os.readlink(hook) == str(self._layout.spool):
            return
        staged = hook.with_name(f".{hook.name}.{os.getpid()}")
        staged.unlink(missing_ok=True)
        os.symlink(self._layout.spool, staged)
        os.replace(staged, hook)
        LOGGER.info("hook spool path %s now points at %s", hook, self._layout.spool)

    def read_lines(self) -> list[str]:
        """Return whole lines written since the last call and rotate safely."""

        fd = self._fd
        if fd is None:
            raise RuntimeError("spool reader is not open")

        if self._reopen_if_replaced():
            fd = self._fd
            assert fd is not None

        lines: list[str] = []
        self._drain_retired(lines)
        self._drain_active(fd, lines)
        self._bound_spool()
        return lines

    def _drain_active(self, fd: int, lines: list[str]) -> None:
        while True:
            chunk = os.read(fd, _READ_CHUNK)
            if not chunk:
                return
            self._offset += len(chunk)
            text = self._decoder.decode(chunk)
            self._partial = self._append_text(self._partial, text, lines, "active")

    def _drain_retired(self, lines: list[str]) -> None:
        fd = self._retired_fd
        if fd is None:
            return
        while True:
            chunk = os.read(fd, _READ_CHUNK)
            if not chunk:
                return
            self._retired_offset += len(chunk)
            text = self._retired_decoder.decode(chunk)
            self._retired_partial = self._append_text(self._retired_partial, text, lines, "retired")

    def _append_text(self, partial: str, text: str, lines: list[str], generation: str) -> str:
        buffered = partial + text
        *complete, partial = buffered.split("\n")
        lines.extend(complete)
        if len(partial) > _MAX_PARTIAL_CHARS:
            self.dropped += 1
            LOGGER.warning(
                "dropped an oversize partial %s spool line (total dropped=%d)",
                generation,
                self.dropped,
            )
            return ""
        return partial

    def _reopen_if_replaced(self) -> bool:
        """Follow an active spool deleted/replaced by an operator or crash cleanup."""

        fd = self._fd
        assert fd is not None
        try:
            on_disk = os.stat(self._layout.spool)
        except OSError:
            self._open_active()
            return True
        if on_disk.st_ino != self._inode:
            self._open_active()
            return True
        if os.fstat(fd).st_size < self._offset:
            self._offset = 0
            self._partial = ""
            self._decoder.reset()
            os.lseek(fd, 0, os.SEEK_SET)
        return False

    def _bound_spool(self) -> None:
        fd = self._fd
        assert fd is not None

        if self._offset < self._reset_at or self._partial or self._decoder.getstate()[0]:
            return

        # Do not retire another active generation while the previous generation
        # still exists. By waiting until the *new* active generation itself
        # reaches the threshold before deleting the old one, any TinyFugue
        # fwrite that opened the old inode before the rename has ample time to
        # finish; its late bytes are drained above rather than truncated.
        if self._retired_fd is not None:
            if self._retired_partial or self._retired_decoder.getstate()[0]:
                return
            retired_fd = self._retired_fd
            if os.fstat(retired_fd).st_size != self._retired_offset:
                return
            self._close_retired_fd()
            self._layout.retired_spool.unlink(missing_ok=True)
            return

        if os.fstat(fd).st_size != self._offset:
            return

        retired = self._layout.retired_spool
        retired.unlink(missing_ok=True)

        # Rename the inode instead of truncating it. A TinyFugue fwrite that
        # already opened this inode can safely finish against the retired name;
        # the reader keeps the same fd and drains that late append later.
        os.replace(self._layout.spool, retired)
        self._retired_fd = fd
        self._retired_offset = self._offset
        self._retired_partial = self._partial
        self._retired_decoder = self._decoder

        # A missing path for the few syscalls between rename and creation makes
        # TinyFugue drop an update immediately; it never blocks, which is the
        # required failure direction.
        self._fd = None
        self._inode = None
        self._offset = 0
        self._partial = ""
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._open_active()

    def close(self) -> None:
        # Stop future TinyFugue opens before releasing/removing runtime files.
        hook = self._layout.hook_spool
        try:
            if hook.is_symlink() and os.readlink(hook) == str(self._layout.spool):
                hook.unlink()
        except OSError:
            pass

        self._close_active_fd()
        self._close_retired_fd()
        self._layout.spool.unlink(missing_ok=True)
        self._layout.retired_spool.unlink(missing_ok=True)

    def _close_active_fd(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)

    def _close_retired_fd(self) -> None:
        fd, self._retired_fd = self._retired_fd, None
        if fd is not None:
            os.close(fd)
        self._retired_offset = 0
        self._retired_partial = ""
        self._retired_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
