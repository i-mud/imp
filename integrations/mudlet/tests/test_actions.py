from __future__ import annotations

import asyncio
from collections.abc import Callable

from imp_adapter.publisher import RelayPublisher
from imp_relay.protocol import (
    ActionResultMessage,
    DispatchMessage,
    GameState,
    StateContext,
    decode_server_message,
    encode_action,
)
from imp_relay.server import RelayServer
from websockets.asyncio.client import connect

from imp_mudlet.actions import RelayActionConsumer

EMPTY_STATE = GameState(character=None, target=None)


async def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 1.0,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("condition did not become true")
        await asyncio.sleep(0.01)


async def _request_action(
    url: str,
    context: StateContext,
    command: str,
) -> ActionResultMessage:
    connection = await connect(url, proxy=None)
    try:
        await connection.send(encode_action(context, command))
        raw = await connection.recv()
        assert isinstance(raw, str)

        decoded = decode_server_message(raw)
        assert decoded.ok
        assert isinstance(
            decoded.value,
            ActionResultMessage,
        )
        return decoded.value
    finally:
        await connection.close()


def test_action_is_forwarded_only_after_lua_result() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()

        context = StateContext("mudlet_test", 1, 1)
        publisher = RelayPublisher(url=f"ws://127.0.0.1:{relay.port}/ingest")
        emitted: list[DispatchMessage] = []
        consumer = RelayActionConsumer(
            context,
            emitted.append,
            url=(f"ws://127.0.0.1:{relay.port}/action-consumer"),
        )

        try:
            await publisher.select(
                context,
                EMPTY_STATE,
            )
            await _wait_until(lambda: relay.state.active_context == context)

            consumer.start()
            await asyncio.wait_for(
                consumer.wait_ready(),
                timeout=1,
            )

            action = asyncio.create_task(
                _request_action(
                    f"ws://127.0.0.1:{relay.port}/action",
                    context,
                    "look",
                )
            )

            await _wait_until(lambda: len(emitted) == 1)

            dispatch = emitted[0]
            assert dispatch.context == context
            assert dispatch.command == "look"

            assert not consumer.resolve(
                "wrong",
                "forwarded",
            )
            assert consumer.resolve(
                dispatch.id,
                "forwarded",
            )

            result = await asyncio.wait_for(
                action,
                timeout=1,
            )
            assert result.status == "forwarded"
            assert result.detail is None
        finally:
            await consumer.close()
            await publisher.close()
            await relay.close()

    asyncio.run(scenario())


def test_consumer_loss_after_dispatch_is_unknown_and_not_retried() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()

        context = StateContext("mudlet_test", 1, 1)
        publisher = RelayPublisher(url=f"ws://127.0.0.1:{relay.port}/ingest")
        emitted: list[DispatchMessage] = []
        consumer = RelayActionConsumer(
            context,
            emitted.append,
            url=(f"ws://127.0.0.1:{relay.port}/action-consumer"),
        )

        try:
            await publisher.select(
                context,
                EMPTY_STATE,
            )
            await _wait_until(lambda: relay.state.active_context == context)

            consumer.start()
            await asyncio.wait_for(
                consumer.wait_ready(),
                timeout=1,
            )

            action = asyncio.create_task(
                _request_action(
                    f"ws://127.0.0.1:{relay.port}/action",
                    context,
                    "north",
                )
            )

            await _wait_until(lambda: len(emitted) == 1)

            await consumer.close()

            result = await asyncio.wait_for(
                action,
                timeout=1,
            )
            assert result.status == "unknown"
            assert emitted[0].command == "north"

            # Consumer loss must never re-emit or retry the dispatch.
            await asyncio.sleep(0.05)
            assert len(emitted) == 1
        finally:
            await publisher.close()
            await relay.close()

    asyncio.run(scenario())
