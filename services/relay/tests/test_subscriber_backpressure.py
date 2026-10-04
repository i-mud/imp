from __future__ import annotations

import asyncio
import json
import socket
from typing import cast

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from imp_relay.protocol import (
    Character,
    GameState,
    StateContext,
    Target,
    Vital,
    encode_publish,
    encode_select,
)
from imp_relay.server import RelayServer

CONTEXT = StateContext("session1", 1, 1)
SNAPSHOTS = 4_000


def _state(sequence: int) -> GameState:
    name = f"{sequence:08d}" + "\u754c" * 56
    return GameState(
        Character(name, Vital(9, 10), Vital(8, 10), Vital(7, 10)),
        Target(name, float(sequence % 101)),
    )


async def _receive_type(connection: ClientConnection, expected_type: str) -> dict[str, object]:
    while True:
        frame = await connection.recv()
        assert isinstance(frame, str)
        message = cast(dict[str, object], json.loads(frame))
        if message["type"] == expected_type:
            return message


async def _receive_snapshots(connection: ClientConnection) -> list[int]:
    sequences: list[int] = []
    while len(sequences) < SNAPSHOTS:
        snapshot = await _receive_type(connection, "snapshot")
        sequence = snapshot["seq"]
        assert isinstance(sequence, int)
        sequences.append(sequence)
    return sequences


def test_slow_subscriber_cannot_stall_producer_or_healthy_readers() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()
        relay_listener = relay._server
        assert relay_listener is not None
        connections: list[ClientConnection] = []
        tasks: list[asyncio.Task[object]] = []
        try:
            slow = await connect(
                f"ws://127.0.0.1:{relay.port}/state",
                compression=None,
                max_queue=1,
                ping_interval=None,
            )
            connections.append(slow)
            healthy: list[ClientConnection] = []
            for _ in range(2):
                connection = await connect(
                    f"ws://127.0.0.1:{relay.port}/state",
                    compression=None,
                    max_queue=1,
                    ping_interval=None,
                )
                healthy.append(connection)
                connections.append(connection)
            for connection in [slow, *healthy]:
                await _receive_type(connection, "hello")
                await _receive_type(connection, "status")

            slow_socket = cast(socket.socket, slow.transport.get_extra_info("socket"))
            slow_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
            slow_port = cast(tuple[str, int], slow_socket.getsockname())[1]
            slow_server = next(
                connection
                for connection in relay_listener.connections
                if connection.remote_address[1] == slow_port
            )
            slow_queue = relay._subscribers[slow_server]
            server_socket = cast(socket.socket, slow_server.transport.get_extra_info("socket"))
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            slow.transport.pause_reading()

            max_backlog = 0
            max_write_buffer = 0

            async def observe_slow() -> None:
                nonlocal max_backlog, max_write_buffer
                while slow_server in relay_listener.connections:
                    max_backlog = max(max_backlog, slow_queue.qsize())
                    max_write_buffer = max(
                        max_write_buffer,
                        slow_server.transport.get_write_buffer_size(),
                    )
                    await asyncio.sleep(0)

            monitor = asyncio.create_task(observe_slow())
            tasks.append(monitor)
            readers = [asyncio.create_task(_receive_snapshots(connection)) for connection in healthy]
            tasks.extend(readers)

            producer = await connect(
                f"ws://127.0.0.1:{relay.port}/ingest",
                compression=None,
                max_queue=1,
                ping_interval=None,
            )
            connections.append(producer)

            async def send_snapshots() -> None:
                await producer.send(encode_select(CONTEXT, _state(1)))
                for sequence in range(2, SNAPSHOTS + 1):
                    await producer.send(encode_publish(CONTEXT, _state(sequence)))
                    if sequence % 32 == 0:
                        await asyncio.sleep(0)

            sender = asyncio.create_task(send_snapshots())
            tasks.append(sender)
            healthy_sequences = await asyncio.wait_for(
                asyncio.gather(sender, *readers),
                timeout=12,
            )
            assert healthy_sequences[1:] == [list(range(1, SNAPSHOTS + 1))] * 2

            async with asyncio.timeout(5):
                while slow_server in relay_listener.connections:
                    await asyncio.sleep(0.001)
            await asyncio.wait_for(monitor, timeout=2)

            snapshot = relay.state.snapshot()
            assert snapshot is not None and snapshot.seq == SNAPSHOTS
            assert slow_server not in relay._subscribers
            assert len(relay._subscribers) == 2
            assert slow_queue.maxsize == 16 and slow_queue.qsize() <= 16
            assert 0 < max_backlog <= 16
            assert max_write_buffer > slow_server.transport.get_write_buffer_limits()[1]

            slow.transport.resume_reading()
            with pytest.raises(ConnectionClosed):
                async with asyncio.timeout(5):
                    while True:
                        await slow.recv()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for connection in connections:
                connection.transport.abort()
            await relay.close()

    asyncio.run(scenario())
