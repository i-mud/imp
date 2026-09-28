"""Executable end-to-end proof that a HUD client receives retained relay state."""

from __future__ import annotations

import asyncio
import json
import sys

from websockets.asyncio.client import ClientConnection, connect

from imp_relay.protocol import Character, GameState, StateContext, Vital, encode_publish, encode_select
from imp_relay.server import RelayServer


async def _receive_snapshot(connection: ClientConnection) -> dict[str, object]:
    while True:
        frame = await connection.recv()
        assert isinstance(frame, str)
        message = json.loads(frame)
        if message["type"] == "snapshot":
            return message


async def _run() -> None:
    relay = RelayServer(port=0)
    await relay.start()
    try:
        url = f"ws://127.0.0.1:{relay.port}"
        state = GameState(character=Character("Roundtrip", Vital(42, 50), None, None), target=None)
        context = StateContext("session1", 1, 1)
        async with connect(f"{url}/state") as subscriber, connect(f"{url}/ingest") as producer:
            await producer.send(encode_select(context, state))
            await producer.send(encode_publish(context, state))
            snapshot = await _receive_snapshot(subscriber)
            assert snapshot["state"]["character"]["name"] == "Roundtrip"
            print("PASS: subscriber received published relay state")
        async with connect(f"{url}/state") as reconnected_subscriber:
            retained = await _receive_snapshot(reconnected_subscriber)
            assert retained["seq"] == 2
            assert retained["state"]["character"]["hp"] == {"current": 42, "max": 50}
            print("PASS: reconnecting subscriber received retained snapshot")
    finally:
        await relay.close()


def main() -> int:
    try:
        asyncio.run(_run())
    except Exception as error:
        print(f"FAIL: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
