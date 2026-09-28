from __future__ import annotations

import asyncio
import json
import sys
from io import StringIO
from typing import cast

from imp_relay.protocol import GameState, StateContext
from websockets.asyncio.server import ServerConnection, serve

from imp_tf.bridge import process_lines


def _record(at: int, payload: dict[str, str]) -> bytes:
    return (
        json.dumps({"at": at, "package": "Char.Status", "payload": payload}, separators=(",", ":")) + "\n"
    ).encode()


def test_bridge_reconnects_after_relay_closes_producer() -> None:
    async def scenario() -> None:
        frames: list[dict[str, object]] = []
        connections: list[ServerConnection] = []
        first_closed = asyncio.Event()
        second_received = asyncio.Event()

        async def close_first_producer(connection: ServerConnection) -> None:
            connections.append(connection)
            frame = await connection.recv()
            assert isinstance(frame, str)
            frames.append(cast(dict[str, object], json.loads(frame)))
            if len(connections) == 1:
                await connection.close()
                first_closed.set()
            else:
                second_received.set()
                await connection.wait_closed()

        async with serve(close_first_producer, "127.0.0.1", 0, close_timeout=0.1) as server:
            port = server.sockets[0].getsockname()[1]
            bridge = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "imp_tf.bridge",
                "--relay-url",
                f"ws://127.0.0.1:{port}/ingest",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            assert bridge.stdin is not None
            assert bridge.stderr is not None
            try:
                bridge.stdin.write(
                    _record(
                        1_710_000_000_000,
                        {
                            "character_name": "Repro",
                            "health": "10",
                            "health_max": "20",
                        },
                    )
                )
                await bridge.stdin.drain()
                await asyncio.wait_for(first_closed.wait(), timeout=2)
                assert connections[0].close_code == 1000

                bridge.stdin.write(_record(1_710_000_000_100, {"health": "9"}))
                await bridge.stdin.drain()
                await asyncio.wait_for(second_received.wait(), timeout=2)
            finally:
                bridge.stdin.close()
                await bridge.stdin.wait_closed()
                try:
                    await asyncio.wait_for(bridge.wait(), timeout=2)
                except TimeoutError:
                    bridge.kill()
                    await bridge.wait()

            stderr = (await bridge.stderr.read()).decode()
            assert bridge.returncode == 0, stderr
            assert len(connections) == 2
            assert len(frames) == 2
            assert frames[0]["state"] != frames[1]["state"]
            assert not server.connections

    asyncio.run(scenario())


def test_offline_bridge_state_never_selects_an_actionable_context() -> None:
    class Publisher:
        def __init__(self) -> None:
            self.operations: list[tuple[str, StateContext | None, GameState]] = []

        async def select(self, context: StateContext | None, state: GameState) -> None:
            self.operations.append(("select", context, state))

        async def publish(self, context: StateContext, state: GameState) -> None:
            self.operations.append(("publish", context, state))

    async def scenario() -> None:
        publisher = Publisher()
        stream = StringIO(
            '{"at":1710000000000,"package":"Char.Status",'
            '"payload":{"character_name":"Offline","health":"10","health_max":"20"}}\n'
            '{"at":1710000000100,"package":"Char.Status","payload":{"health":"9"}}\n'
        )

        stats = await process_lines(stream, publisher)

        assert stats.published == 2
        assert [kind for kind, _, _ in publisher.operations] == ["select", "select"]
        assert all(context is None for _, context, _ in publisher.operations)

    asyncio.run(scenario())
