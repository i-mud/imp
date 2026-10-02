"""Frozen desktop entry point for the Imp node."""

from __future__ import annotations

import sys
from collections.abc import Callable

from imp_relay.__main__ import main as relay_main
from imp_relay.gateway_main import main as gateway_main


def _dispatch(argv: list[str]) -> tuple[Callable[[], None], list[str]]:
    """Select the bundled service while preserving the legacy relay CLI."""

    if not argv or argv[0].startswith("-"):
        return relay_main, argv

    mode, *rest = argv
    if mode == "relay":
        return relay_main, rest
    if mode == "gateway":
        return gateway_main, rest

    raise SystemExit(f"unknown imp-node mode: {mode}")


def main() -> None:
    handler, argv = _dispatch(sys.argv[1:])
    sys.argv = [sys.argv[0], *argv]
    handler()


if __name__ == "__main__":
    main()
