"""Development JSONL bridge for the Mudlet lifecycle protocol."""

from __future__ import annotations

import json
import secrets
import sys
from typing import TextIO

from imp_mudlet.lifecycle import MudletLifecycle
from imp_mudlet.protocol import decode_lua_message


def new_session() -> str:
    return f"mudlet_{secrets.token_hex(16)}"


def _status(lifecycle: MudletLifecycle) -> dict[str, object]:
    context = lifecycle.context
    return {
        "type": "status",
        "protocol": 1,
        "session": lifecycle.session,
        "profile": lifecycle.profile,
        "connected": lifecycle.connected,
        "focused": lifecycle.focused,
        "gmcpEnabled": lifecycle.gmcp_enabled,
        "connection": lifecycle.connection,
        "foreground": lifecycle.foreground,
        "context": (
            None
            if context is None
            else {
                "session": context.session,
                "foreground": context.foreground,
                "connection": context.connection,
            }
        ),
    }


def _write(output: TextIO, value: dict[str, object]) -> None:
    output.write(json.dumps(value, separators=(",", ":"), ensure_ascii=True))
    output.write("\n")
    output.flush()


def run_bridge(
    source: TextIO,
    output: TextIO,
    *,
    session: str | None = None,
) -> None:
    lifecycle = MudletLifecycle(session=session or new_session())

    for line in source:
        decoded = decode_lua_message(line)
        if not decoded.ok or decoded.message is None:
            _write(
                output,
                {
                    "type": "error",
                    "protocol": 1,
                    "code": decoded.error or "invalid_message",
                },
            )
            continue

        try:
            lifecycle.apply(decoded.message)
        except ValueError:
            _write(
                output,
                {
                    "type": "error",
                    "protocol": 1,
                    "code": "invalid_lifecycle",
                },
            )
            continue

        _write(output, _status(lifecycle))


def main() -> None:
    run_bridge(sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
