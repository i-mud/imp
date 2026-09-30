"""JSONL bridge between Mudlet and the local Imp node."""

from __future__ import annotations

import asyncio
import json
import secrets
import sys
from typing import TextIO

from imp_relay.protocol import DispatchMessage

from imp_mudlet.actions import DEFAULT_CONSUMER_URL
from imp_mudlet.lifecycle import MudletLifecycle
from imp_mudlet.protocol import decode_lua_message
from imp_mudlet.runtime import MudletRuntime, PublisherFactory


def new_session() -> str:
    return f"mudlet_{secrets.token_hex(16)}"


def _context_wire(
    session: str,
    foreground: int,
    connection: int,
) -> dict[str, object]:
    return {
        "session": session,
        "foreground": foreground,
        "connection": connection,
    }


def _status(runtime: MudletRuntime) -> dict[str, object]:
    lifecycle = runtime.lifecycle
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
            else _context_wire(
                context.session,
                context.foreground,
                context.connection,
            )
        ),
    }


def _action(dispatch: DispatchMessage) -> dict[str, object]:
    return {
        "type": "action",
        "protocol": 1,
        "id": dispatch.id,
        "context": _context_wire(
            dispatch.context.session,
            dispatch.context.foreground,
            dispatch.context.connection,
        ),
        "command": dispatch.command,
    }


def _write(output: TextIO, value: dict[str, object]) -> None:
    output.write(
        json.dumps(
            value,
            separators=(",", ":"),
            ensure_ascii=True,
        )
    )
    output.write("\n")
    output.flush()


async def run_bridge_async(
    source: TextIO,
    output: TextIO,
    *,
    session: str | None = None,
    publisher_factory: PublisherFactory | None = None,
    action_url: str = DEFAULT_CONSUMER_URL,
) -> None:
    lifecycle = MudletLifecycle(session=session or new_session())

    def emit_action(dispatch: DispatchMessage) -> None:
        _write(output, _action(dispatch))

    if publisher_factory is None:
        runtime = MudletRuntime(
            lifecycle,
            emit_action=emit_action,
            action_url=action_url,
        )
    else:
        runtime = MudletRuntime(
            lifecycle,
            publisher_factory,
            emit_action=emit_action,
            action_url=action_url,
        )

    try:
        while True:
            # stdin is a Mudlet-owned pipe. Keep its blocking read out of the
            # asyncio loop so relay reconnect/backoff continues while Mudlet is
            # otherwise idle.
            line = await asyncio.to_thread(source.readline)
            if line == "":
                return

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
                await runtime.handle(decoded.message)
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

            _write(output, _status(runtime))
    finally:
        await runtime.close()


def run_bridge(
    source: TextIO,
    output: TextIO,
    *,
    session: str | None = None,
    publisher_factory: PublisherFactory | None = None,
    action_url: str = DEFAULT_CONSUMER_URL,
) -> None:
    asyncio.run(
        run_bridge_async(
            source,
            output,
            session=session,
            publisher_factory=publisher_factory,
            action_url=action_url,
        )
    )


def main() -> None:
    run_bridge(sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
