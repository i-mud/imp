"""Notify TinyFugue when a fresh local feed is ready, without granting authority."""

from __future__ import annotations

import argparse
import logging
import os
import re
import select
import sys
from pathlib import Path
from typing import TextIO

LOGGER = logging.getLogger(__name__)
_READY = re.compile(rb"([0-9a-f]{32})\n?")


def run_feed_watch(ready: Path, spool: Path, output: TextIO) -> None:
    terminal = select.POLLERR | select.POLLHUP | select.POLLNVAL
    poller = select.poll()
    poller.register(output.fileno(), terminal)
    previous: bytes | None = None
    while True:
        if any(event & terminal for _, event in poller.poll(1000)):
            return
        try:
            with ready.open("rb", buffering=0) as marker:
                raw = marker.read(34)
            if not spool.is_file():
                continue
        except OSError:
            continue
        match = _READY.fullmatch(raw)
        if match is None:
            continue
        token = match[1]
        if token != previous:
            output.write(f"/imp_feed_ready {token.decode('ascii')}\n")
            output.flush()
            previous = token


def main() -> None:
    argparse.ArgumentParser(description="Watch local TinyFugue feed readiness").parse_args()
    try:
        state = Path.home() / ".local" / "state" / "imp"
        run_feed_watch(state / "feed-ready", state / "spool", sys.stdout)
    except BrokenPipeError:
        # Suppress the interpreter's second flush after TinyFugue closes its quote.
        with open(os.devnull, "wb") as sink:
            os.dup2(sink.fileno(), sys.stdout.fileno())
    except (OSError, RuntimeError, ValueError):
        LOGGER.error("imp feed watcher stopped")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
