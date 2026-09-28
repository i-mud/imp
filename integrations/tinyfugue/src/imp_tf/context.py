"""Strict TinyFugue context marker shared by the feed and action helper."""

from __future__ import annotations

import os
import re
from pathlib import Path

from imp_relay.protocol import StateContext

_MARKER_MODE = 0o600
_DIRECTORY_MODE = 0o700
_MARKER = re.compile(rb"IMPCTX 2 ([A-Za-z0-9_]{1,128}) ([1-9][0-9]*) ([1-9][0-9]*)\n")


def read_context_marker(path: Path) -> StateContext | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    match = _MARKER.fullmatch(raw)
    if match is None:
        return None
    session, foreground, connection = match.groups()
    return StateContext(session.decode("ascii"), int(foreground), int(connection))


def write_context_marker(path: Path, context: StateContext | None) -> None:
    if context is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, _DIRECTORY_MODE)
    staged = path.with_name(f".{path.name}.{os.getpid()}")
    payload = f"IMPCTX 2 {context.session} {context.foreground} {context.connection}\n".encode("ascii")
    fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _MARKER_MODE)
    try:
        os.fchmod(fd, _MARKER_MODE)
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(staged, path)
