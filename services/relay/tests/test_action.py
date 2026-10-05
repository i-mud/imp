"""Action broker timeout ownership and recovery."""

import asyncio
import json

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve

from imp_relay.action import DEFAULT_ACTION_RESULT_TIMEOUT, ActionBroker, ConsumerRegistration
from imp_relay.protocol import StateContext


@pytest.mark.parametrize("timeout", [0, -1.0, float("inf"), float("-inf"), float("nan")])
def test_constructor_rejects_invalid_result_timeout(timeout: float) -> None:
    with pytest.raises(ValueError, match="^result_timeout must be finite and positive$"):
        ActionBroker(result_timeout=timeout)


@pytest.mark.parametrize("timeout", [1e-12, DEFAULT_ACTION_RESULT_TIMEOUT, 3600.0])
def test_constructor_accepts_finite_positive_result_timeout(timeout: float) -> None:
    ActionBroker(result_timeout=timeout)


def test_timeout_returns_unknown_and_releases_inflight_action() -> None:
    async def scenario() -> None:
        broker = ActionBroker(result_timeout=0.05)
        context = StateContext("session1", 1, 1)
        registrations: asyncio.Queue[ConsumerRegistration] = asyncio.Queue()

        async def consumer(connection: ServerConnection) -> None:
            registration = broker.register(connection, context, context)
            assert registration is not None
            await registrations.put(registration)
            try:
                await connection.wait_closed()
            finally:
                broker.unregister(registration)

        async with asyncio.timeout(5):
            async with serve(consumer, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                async with connect(f"ws://127.0.0.1:{port}") as connection:
                    registration = await registrations.get()
                    first = asyncio.create_task(broker.forward(context, "look", context))
                    dispatch = json.loads(await connection.recv())
                    assert dispatch["command"] == "look"
                    assert await first == ("unknown", "consumer result timed out")
                    assert not broker.resolve(registration, dispatch["id"], "forwarded")

                    second = asyncio.create_task(broker.forward(context, "north", context))
                    dispatch = json.loads(await connection.recv())
                    assert dispatch["command"] == "north"
                    assert broker.resolve(registration, dispatch["id"], "forwarded")
                    assert await second == ("forwarded", None)

    asyncio.run(scenario())
