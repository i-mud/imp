"""Forward one relay action into TinyFugue as fixed-macro data."""

from __future__ import annotations

import argparse
import asyncio
import select
import sys
import time
from pathlib import Path
from typing import Final, TextIO
from urllib.parse import urlsplit

from tinyscry_relay.protocol import (
    ConsumerReadyMessage,
    DispatchMessage,
    StateContext,
    decode_server_message,
    encode_consumer,
    encode_consumer_result,
)
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from tinyscry_tf.context import read_context_marker
from tinyscry_tf.events import decode_tf_token

DEFAULT_CONSUMER_URL: Final = "ws://127.0.0.1:8787/action-consumer"
_INITIAL_READY_SECONDS: Final = 5.0
_MARKER_POLL_SECONDS: Final = 0.05
_PIPE_POLL_SECONDS: Final = 0.05


def encode_tf_token(value: str) -> str:
    """Match TinyFugue textencode.tf: only ASCII letters and digits pass literally."""

    return "".join(char if char.isascii() and char.isalnum() else f"_{ord(char)}_" for char in value)


def encode_tf_dispatch(context: StateContext, world: str, command: str) -> str:
    """Build fixed macro input from validated metadata and encoded command data."""

    if decode_tf_token(world) is None:
        raise ValueError("world must be a valid textencode.tf token")
    return (
        f"/tinyscry_send {context.session} {context.foreground} {context.connection} "
        f"{world} {encode_tf_token(command)}\n"
    )


def _validate_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "ws"
        or parsed.path != "/action-consumer"
        or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
    ):
        raise ValueError("consumer URL must be a loopback ws:// URL with the /action-consumer path")


async def _close_connection(connection: ClientConnection, *, abort: bool) -> None:
    if abort:
        # TinyFugue owns the pipe; its shutdown cannot wait for relay cooperation.
        connection.transport.abort()
        await connection.wait_closed()
    else:
        await connection.close()


async def _register(
    url: str,
    context: StateContext,
    marker: Path,
    output_lost: asyncio.Event,
) -> ClientConnection | None:
    while read_context_marker(marker) == context:
        connection: ClientConnection | None = None
        try:
            connection = await connect(url, proxy=None)
            await connection.send(encode_consumer(context))
            raw = await connection.recv()
            if not isinstance(raw, str):
                raise RuntimeError("relay sent a binary registration response")
            decoded = decode_server_message(raw)
            if decoded.ok and isinstance(decoded.value, ConsumerReadyMessage):
                if decoded.value.context == context:
                    registered, connection = connection, None
                    return registered
        except (ConnectionClosed, OSError, WebSocketException):
            pass
        finally:
            if connection is not None:
                await _close_connection(connection, abort=output_lost.is_set())
        await asyncio.sleep(_MARKER_POLL_SECONDS)
    return None


async def _wait_for_marker_change(marker: Path, context: StateContext) -> None:
    while read_context_marker(marker) == context:
        await asyncio.sleep(_MARKER_POLL_SECONDS)


async def _wait_for_stdout_reader_loss(fd: int, output_lost: asyncio.Event) -> None:
    terminal = select.POLLERR | select.POLLHUP | select.POLLNVAL
    poller = select.poll()
    poller.register(fd, terminal)
    while True:
        if any(event & terminal for _, event in poller.poll(0)):
            output_lost.set()
            return
        await asyncio.sleep(_PIPE_POLL_SECONDS)


async def run_action_consumer(
    url: str,
    context: StateContext,
    world: str,
    marker: Path,
    output: TextIO,
) -> None:
    _validate_url(url)
    encode_tf_dispatch(context, world, "")
    try:
        output_fd = output.fileno()
    except (AttributeError, OSError, ValueError):
        output_fd = None
    output_lost = asyncio.Event()
    pipe_reader_lost = (
        asyncio.create_task(_wait_for_stdout_reader_loss(output_fd, output_lost))
        if output_fd is not None
        else None
    )
    marker_changed: asyncio.Task[None] | None = None
    registration: asyncio.Task[ClientConnection | None] | None = None
    receive: asyncio.Task[str | bytes] | None = None
    connection: ClientConnection | None = None
    try:
        initial_deadline = time.monotonic() + _INITIAL_READY_SECONDS
        while read_context_marker(marker) != context:
            if pipe_reader_lost is not None and pipe_reader_lost.done():
                return
            if time.monotonic() >= initial_deadline:
                raise RuntimeError("context marker did not become current")
            await asyncio.sleep(_MARKER_POLL_SECONDS)

        marker_changed = asyncio.create_task(_wait_for_marker_change(marker, context))
        while read_context_marker(marker) == context:
            registration = asyncio.create_task(_register(url, context, marker, output_lost))
            watched: list[asyncio.Task[object]] = [registration, marker_changed]
            if pipe_reader_lost is not None:
                watched.append(pipe_reader_lost)
            done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
            if registration in done:
                connection = registration.result()
                registration = None
            if pipe_reader_lost is not None and pipe_reader_lost in done:
                return
            if marker_changed in done:
                return
            if connection is None:
                return

            try:
                while True:
                    receive = asyncio.create_task(connection.recv())
                    watched = [receive, marker_changed]
                    if pipe_reader_lost is not None:
                        watched.append(pipe_reader_lost)
                    done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
                    if pipe_reader_lost is not None and pipe_reader_lost in done:
                        return
                    if marker_changed in done:
                        return
                    completed_receive, receive = receive, None
                    try:
                        raw = completed_receive.result()
                    except ConnectionClosed:
                        break
                    if not isinstance(raw, str):
                        raise RuntimeError("relay sent a binary action frame")
                    decoded = decode_server_message(raw)
                    if not decoded.ok or not isinstance(decoded.value, DispatchMessage):
                        raise RuntimeError("relay sent an invalid action frame")
                    dispatch = decoded.value
                    if (
                        dispatch.context != context
                        or read_context_marker(marker) != context
                        or (pipe_reader_lost is not None and pipe_reader_lost.done())
                    ):
                        return
                    try:
                        output.write(encode_tf_dispatch(context, world, dispatch.command))
                        output.flush()
                    except OSError:
                        output_lost.set()
                        return
                    try:
                        await connection.send(encode_consumer_result(dispatch.id, "forwarded"))
                    except ConnectionClosed:
                        pass
                    return
            finally:
                if receive is not None:
                    receive.cancel()
                    await asyncio.gather(receive, return_exceptions=True)
                    receive = None
                await _close_connection(connection, abort=output_lost.is_set())
                connection = None
    finally:
        tasks = tuple(
            task for task in (registration, receive, marker_changed, pipe_reader_lost) if task is not None
        )
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if connection is not None:
            await _close_connection(connection, abort=output_lost.is_set())


def _arguments() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Consume actions for one TinyFugue context")
    parser.add_argument("--relay-url", default=DEFAULT_CONSUMER_URL)
    parser.add_argument("--session", required=True)
    parser.add_argument("--foreground", required=True, type=int)
    parser.add_argument("--connection", required=True, type=int)
    parser.add_argument("--world", required=True)
    parser.add_argument(
        "--marker", type=Path, default=Path.home() / ".local" / "state" / "tinyscry" / "context"
    )
    return parser


def main() -> None:
    args = _arguments().parse_args()
    context = StateContext(args.session, args.foreground, args.connection)
    try:
        asyncio.run(run_action_consumer(args.relay_url, context, args.world, args.marker, sys.stdout))
    except (OSError, RuntimeError, ValueError) as error:
        print(f"tinyscry action consumer stopped: {error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
