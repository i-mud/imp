"""Opt-in, bounded raw GMCP capture for diagnostics.

Off by default. Normal production runtime never persists raw GMCP; this exists
only for debugging a specific session, and writes to a private directory
outside the live spool path with rotation so it cannot grow without bound.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

DIAGNOSTICS_DIR_MODE: Final = 0o700
DIAGNOSTICS_FILE_MODE: Final = 0o600
DEFAULT_MAX_FILE_BYTES: Final = 1_048_576
DEFAULT_MAX_FILES: Final = 5
ACTIVE_NAME: Final = "gmcp.raw"


class DiagnosticCapture:
    """Appends raw spool lines to a size-rotated, count-bounded file set."""

    def __init__(
        self,
        directory: Path,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        max_files: int = DEFAULT_MAX_FILES,
    ) -> None:
        if max_file_bytes <= 0:
            raise ValueError("max_file_bytes must be positive")
        if max_files < 1:
            raise ValueError("max_files must be at least 1")
        self._directory = directory
        self._max_file_bytes = max_file_bytes
        self._max_files = max_files
        self._fd: int | None = None
        self._size = 0

    def open(self) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self._directory, DIAGNOSTICS_DIR_MODE)
        self._open_active()

    def _open_active(self) -> None:
        fd = os.open(self._active_path(), os.O_WRONLY | os.O_CREAT | os.O_APPEND, DIAGNOSTICS_FILE_MODE)
        os.fchmod(fd, DIAGNOSTICS_FILE_MODE)
        self._fd = fd
        self._size = os.fstat(fd).st_size

    def write(self, raw_line: str) -> None:
        """Persist one raw hook line exactly as received, unparsed."""

        if self._fd is None:
            raise RuntimeError("diagnostic capture is not open")
        payload = raw_line if raw_line.endswith("\n") else raw_line + "\n"
        encoded = payload.encode("utf-8", errors="replace")
        os.write(self._fd, encoded)
        self._size += len(encoded)
        if self._size >= self._max_file_bytes:
            self._rotate()

    def _rotate(self) -> None:
        assert self._fd is not None
        os.close(self._fd)
        self._fd = None

        if self._max_files == 1:
            self._active_path().unlink(missing_ok=True)
        else:
            oldest = self._rotated_path(self._max_files - 1)
            oldest.unlink(missing_ok=True)
            for index in range(self._max_files - 2, 0, -1):
                source = self._rotated_path(index)
                if source.exists():
                    source.replace(self._rotated_path(index + 1))
            self._active_path().replace(self._rotated_path(1))

        self._open_active()

    def _active_path(self) -> Path:
        return self._directory / ACTIVE_NAME

    def _rotated_path(self, index: int) -> Path:
        return self._directory / f"{ACTIVE_NAME}.{index}"

    def close(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)
