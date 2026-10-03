from __future__ import annotations

import asyncio
from collections.abc import Callable

from imp_adapter.publisher import RelayPublisher
from imp_adapter.records import JsonValue, Record
from imp_relay.protocol import Character, StateContext, Vital
from imp_relay.server import RelayServer

from imp_mudlet.lifecycle import MudletLifecycle
from imp_mudlet.protocol import FocusMessage, GmcpMessage, InitMessage
from imp_mudlet.runtime import MudletRuntime


def _gmcp(package: str, payload: dict[str, JsonValue]) -> GmcpMessage:
    return GmcpMessage(Record(at=1234, package=package, payload=payload))


async def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 1.0,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("relay condition did not become true")
        await asyncio.sleep(0.01)


def _context_is(relay: RelayServer, expected: StateContext) -> bool:
    snapshot = relay.state.snapshot()
    return snapshot is not None and snapshot.context == expected


def test_focused_profile_selects_then_publishes_normalized_state() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()

        runtime = MudletRuntime(
            MudletLifecycle("mudlet_a"),
            lambda: RelayPublisher(url=f"ws://127.0.0.1:{relay.port}/ingest"),
        )

        try:
            context = StateContext("mudlet_a", 1, 1)

            await runtime.handle(InitMessage("A", True, True))
            await asyncio.wait_for(runtime.drain(), timeout=1)
            await _wait_until(lambda: _context_is(relay, context))

            snapshot = relay.state.snapshot()
            assert snapshot is not None
            assert snapshot.context == context
            assert snapshot.state.character is None
            assert relay.state.health()["feed"] == "stale"

            await runtime.handle(_gmcp("Char.Status", {"character_name": "Ariadne"}))
            await runtime.handle(_gmcp("Char.Vitals", {"hp": "12", "maxhp": "20"}))
            await asyncio.wait_for(runtime.drain(), timeout=1)
            await _wait_until(lambda: relay.state.health()["feed"] == "live")

            snapshot = relay.state.snapshot()
            assert snapshot is not None
            assert snapshot.context == context
            assert snapshot.state.character == Character(
                name="Ariadne",
                hp=Vital(current=12, max=20),
                mana=None,
                moves=None,
            )
        finally:
            await runtime.close()
            await relay.close()

    asyncio.run(scenario())


def test_repeated_observations_keep_relay_live_without_replacing_snapshot() -> None:
    import json

    from websockets.asyncio.client import connect

    async def scenario() -> None:
        now = [0.0]
        relay = RelayServer(port=0, stale_after=10.0, clock=lambda: now[0])
        await relay.start()
        publisher = RelayPublisher(url=f"ws://127.0.0.1:{relay.port}/ingest")
        runtime = MudletRuntime(MudletLifecycle("mudlet_observed"), lambda: publisher)
        subscriber = None

        try:
            await runtime.handle(InitMessage("Observed", True, True))
            await runtime.drain()
            context = StateContext("mudlet_observed", 1, 1)
            await _wait_until(lambda: _context_is(relay, context))
            assert relay.state.feed_status(now[0]) == "stale"
            subscriber = await connect(f"ws://127.0.0.1:{relay.port}/state")

            async def sync_delivery() -> None:
                await runtime.drain()
                assert await publisher.text(context, 1, "processed")
                while True:
                    message = json.loads(await asyncio.wait_for(subscriber.recv(), timeout=1))
                    if message["type"] == "text":
                        assert message["text"] == "processed"
                        return

            await runtime.handle(_gmcp("Char.Status", {"opponent_name": ""}))
            await sync_delivery()
            identical = relay.state.snapshot()
            assert identical is not None
            assert identical.context == context
            assert identical.seq == 1
            assert relay.state.feed_status(now[0]) == "live"

            await runtime.handle(
                _gmcp("Char.Status", {"character_name": "Ariadne", "health": "12", "health_max": "20"})
            )
            await sync_delivery()
            selected = relay.state.snapshot()
            assert selected is not None
            assert selected.context == context
            assert selected.seq == 2
            assert relay.state.feed_status(now[0]) == "live"

            for observation_at in (9.0, 18.0, 27.0, 36.0):
                now[0] = observation_at
                await runtime.handle(_gmcp("Char.Status", {"character_name": "Ariadne"}))
                await runtime.handle(_gmcp("Char.Vitals", {"hp": "12", "maxhp": "20"}))
                await sync_delivery()
                assert relay.state.snapshot() is selected
                now[0] = observation_at + 8.99
                assert relay.state.feed_status(now[0]) == "live"

            now[0] = 46.0
            await runtime.handle(_gmcp("Room.Info", {"name": "ignored"}))
            await runtime.handle(_gmcp("Char.Status", {"unmapped": "ignored"}))
            await runtime.handle(_gmcp("Char.Status", {"character_name": "Rejected", "opponent_health": 101}))
            await sync_delivery()
            assert relay.state.snapshot() == selected
            assert relay.state.feed_status(now[0]) == "stale"

            await runtime.handle(_gmcp("Char.Status", {"character_name": "Ariadne"}))
            await sync_delivery()
            assert relay.state.snapshot() == selected
            assert relay.state.feed_status(now[0]) == "live"
        finally:
            if subscriber is not None:
                await subscriber.close()
            await runtime.close()
            await relay.close()

    asyncio.run(scenario())


def test_observation_during_relay_outage_retains_state_without_refreshing_feed() -> None:
    import json

    from websockets.asyncio.client import ClientConnection, connect
    from websockets.asyncio.server import ServerConnection
    from websockets.http11 import Request, Response

    from imp_mudlet.protocol import decode_lua_message

    class GatedRelayServer(RelayServer):
        def __init__(self, now: list[float]) -> None:
            super().__init__(port=0, stale_after=10.0, clock=lambda: now[0])
            self.reconnect_blocked = False
            self.reconnect_started = asyncio.Event()
            self.release_reconnect = asyncio.Event()

        async def _process_request(self, connection: ServerConnection, request: Request) -> Response | None:
            if self.reconnect_blocked and request.path == "/ingest":
                self.reconnect_started.set()
                await self.release_reconnect.wait()
            return await super()._process_request(connection, request)

    async def scenario(name: str) -> None:
        now = [0.0]
        relay = GatedRelayServer(now)
        await relay.start()
        publisher = RelayPublisher(url=f"ws://127.0.0.1:{relay.port}/ingest")
        runtime = MudletRuntime(MudletLifecycle("mudlet_outage"), lambda: publisher)
        context = StateContext("mudlet_outage", 1, 1)

        async def observe(character_name: str) -> None:
            decoded = decode_lua_message(
                json.dumps(
                    {
                        "type": "gmcp",
                        "at": 1234,
                        "package": "Char.Status",
                        "payload": {"character_name": character_name},
                    }
                )
            )
            assert decoded.message is not None
            await runtime.handle(decoded.message)

        async def marker(subscriber: ClientConnection, text: str) -> None:
            assert await publisher.text(context, 1, text)
            while True:
                raw = await asyncio.wait_for(subscriber.recv(), timeout=1)
                assert isinstance(raw, str)
                message = json.loads(raw)
                if message["type"] == "text":
                    assert message["text"] == text
                    return

        try:
            await runtime.handle(InitMessage("Outage", True, True))
            await asyncio.wait_for(runtime.drain(), timeout=1)
            await _wait_until(lambda: _context_is(relay, context))
            async with connect(f"ws://127.0.0.1:{relay.port}/state") as subscriber:
                await observe("Ariadne")
                await asyncio.wait_for(runtime.drain(), timeout=1)
                await marker(subscriber, "initial")
                assert relay.state.health()["feed"] == "live"
                before = relay.state.snapshot()
                assert before is not None

                relay.reconnect_blocked = True
                connection = publisher._connection
                assert connection is not None
                await connection.close()
                await asyncio.wait_for(relay.reconnect_started.wait(), timeout=1)
                await _wait_until(lambda: relay.state.health()["producer_count"] == 0)
                now[0] = 1.0
                await observe(name)
                retained_state = runtime.state
                now[0] = 12.0
                assert relay.state.snapshot() is before
                assert relay.state.health()["feed"] == "down"

                relay.release_reconnect.set()
                await asyncio.wait_for(runtime.drain(), timeout=1)
                await _wait_until(
                    lambda: (snapshot := relay.state.snapshot()) is not None and snapshot.seq > before.seq
                )
                await marker(subscriber, "recovered selection")
                retained = relay.state.snapshot()
                assert retained is not None
                assert retained.context == context
                assert retained.state == retained_state
                assert retained.seq == before.seq + 1
                assert retained.at == 12000
                assert relay.state.health()["feed"] == "stale"

                await observe(name)
                await asyncio.wait_for(runtime.drain(), timeout=1)
                await marker(subscriber, "new observation")
                assert relay.state.snapshot() is retained
                assert relay.state.health()["feed"] == "live"
        finally:
            relay.release_reconnect.set()
            await runtime.close()
            await relay.close()

    asyncio.run(scenario("Ariadne"))
    asyncio.run(scenario("Bellerophon"))


def test_unfocused_profile_caches_gmcp_without_becoming_a_producer() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()

        runtime = MudletRuntime(
            MudletLifecycle("mudlet_a"),
            lambda: RelayPublisher(url=f"ws://127.0.0.1:{relay.port}/ingest"),
        )

        try:
            await runtime.handle(InitMessage("A", True, False))
            await runtime.handle(_gmcp("Char.Status", {"character_name": "Cached"}))

            assert relay.state.snapshot() is None
            assert relay.state.health()["producer_count"] == 0

            context = StateContext("mudlet_a", 1, 1)

            await runtime.handle(FocusMessage(True))
            await asyncio.wait_for(runtime.drain(), timeout=1)
            await _wait_until(lambda: _context_is(relay, context))

            snapshot = relay.state.snapshot()
            assert snapshot is not None
            assert snapshot.context == context
            assert snapshot.state.character is not None
            assert snapshot.state.character.name == "Cached"
            assert relay.state.health()["producer_count"] == 1
        finally:
            await runtime.close()
            await relay.close()

    asyncio.run(scenario())


def test_late_unfocus_cannot_clobber_new_foreground_profile() -> None:
    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()
        url = f"ws://127.0.0.1:{relay.port}/ingest"

        first = MudletRuntime(
            MudletLifecycle("mudlet_first"),
            lambda: RelayPublisher(url=url),
        )
        second = MudletRuntime(
            MudletLifecycle("mudlet_second"),
            lambda: RelayPublisher(url=url),
        )

        try:
            first_context = StateContext("mudlet_first", 1, 1)
            second_context = StateContext("mudlet_second", 1, 1)

            await first.handle(InitMessage("First", True, True))
            await first.handle(_gmcp("Char.Status", {"character_name": "First"}))
            await asyncio.wait_for(first.drain(), timeout=1)
            await _wait_until(lambda: _context_is(relay, first_context))

            await second.handle(InitMessage("Second", True, False))
            await second.handle(_gmcp("Char.Status", {"character_name": "Second"}))

            # Exercise the hazardous ordering explicitly: the new profile gains
            # focus and selects first, then the old profile hears that it lost
            # focus. The old profile must only close its producer; it must not
            # send a later deselection.
            await second.handle(FocusMessage(True))
            await asyncio.wait_for(second.drain(), timeout=1)
            await _wait_until(lambda: _context_is(relay, second_context))

            await first.handle(FocusMessage(False))
            await _wait_until(lambda: relay.state.health()["producer_count"] == 1)

            snapshot = relay.state.snapshot()
            assert snapshot is not None
            assert snapshot.context == second_context
            assert snapshot.state.character is not None
            assert snapshot.state.character.name == "Second"
        finally:
            await first.close()
            await second.close()
            await relay.close()

    asyncio.run(scenario())


def test_runtime_routes_action_result_back_to_exact_consumer() -> None:
    from imp_relay.protocol import (
        ActionResultMessage as RelayActionResultMessage,
    )
    from imp_relay.protocol import (
        DispatchMessage,
        decode_server_message,
        encode_action,
    )
    from websockets.asyncio.client import connect

    from imp_mudlet.protocol import ActionResultMessage

    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()

        context = StateContext(
            "mudlet_action",
            1,
            1,
        )
        emitted: list[DispatchMessage] = []

        runtime = MudletRuntime(
            MudletLifecycle("mudlet_action"),
            lambda: RelayPublisher(url=(f"ws://127.0.0.1:{relay.port}/ingest")),
            emit_action=emitted.append,
            action_url=(f"ws://127.0.0.1:{relay.port}/action-consumer"),
        )

        try:
            await runtime.handle(
                InitMessage(
                    "Action",
                    True,
                    True,
                )
            )
            await asyncio.wait_for(
                runtime.drain(),
                timeout=1,
            )

            await _wait_until(
                lambda: _context_is(
                    relay,
                    context,
                )
            )
            await _wait_until(lambda: runtime.action_ready)

            connection = await connect(
                f"ws://127.0.0.1:{relay.port}/action",
                proxy=None,
            )
            try:
                await connection.send(
                    encode_action(
                        context,
                        "look",
                    )
                )

                await _wait_until(lambda: len(emitted) == 1)
                dispatch = emitted[0]

                await runtime.handle(
                    ActionResultMessage(
                        dispatch.id,
                        "forwarded",
                    )
                )

                raw = await asyncio.wait_for(
                    connection.recv(),
                    timeout=1,
                )
                assert isinstance(raw, str)

                decoded = decode_server_message(raw)
                assert decoded.ok
                assert isinstance(
                    decoded.value,
                    RelayActionResultMessage,
                )
                assert decoded.value.status == "forwarded"
            finally:
                await connection.close()
        finally:
            await runtime.close()
            await relay.close()

    asyncio.run(scenario())


def test_focus_change_cancels_pending_action_and_isolates_new_context() -> None:
    from imp_relay.protocol import (
        ActionResultMessage as RelayActionResultMessage,
    )
    from imp_relay.protocol import (
        DispatchMessage,
        decode_server_message,
        encode_action,
    )
    from websockets.asyncio.client import connect

    from imp_mudlet.protocol import ActionResultMessage

    async def scenario() -> None:
        relay = RelayServer(port=0)
        await relay.start()

        old_context = StateContext(
            "mudlet_switch",
            1,
            1,
        )
        new_context = StateContext(
            "mudlet_switch",
            2,
            1,
        )
        emitted: list[DispatchMessage] = []

        runtime = MudletRuntime(
            MudletLifecycle("mudlet_switch"),
            lambda: RelayPublisher(url=f"ws://127.0.0.1:{relay.port}/ingest"),
            emit_action=emitted.append,
            action_url=(f"ws://127.0.0.1:{relay.port}/action-consumer"),
        )

        try:
            await runtime.handle(InitMessage("Switch", True, True))
            await asyncio.wait_for(
                runtime.drain(),
                timeout=1,
            )
            await _wait_until(
                lambda: _context_is(
                    relay,
                    old_context,
                )
            )
            await _wait_until(lambda: runtime.action_ready)

            old_connection = await connect(
                f"ws://127.0.0.1:{relay.port}/action",
                proxy=None,
            )
            try:
                await old_connection.send(
                    encode_action(
                        old_context,
                        "north",
                    )
                )

                await _wait_until(lambda: len(emitted) == 1)
                old_dispatch = emitted[0]
                assert old_dispatch.context == old_context
                assert old_dispatch.command == "north"

                # Lose focus before Lua acknowledges the final send() hop.
                # Retiring the old consumer makes this action ambiguous rather
                # than forwarding, replaying, or carrying it into the next
                # foreground generation.
                await runtime.handle(FocusMessage(False))

                raw = await asyncio.wait_for(
                    old_connection.recv(),
                    timeout=1,
                )
                assert isinstance(raw, str)

                decoded = decode_server_message(raw)
                assert decoded.ok
                assert isinstance(
                    decoded.value,
                    RelayActionResultMessage,
                )
                assert decoded.value.status == "unknown"
            finally:
                await old_connection.close()

            # Re-entering the profile creates a new foreground generation and
            # therefore a distinct action consumer.
            await runtime.handle(FocusMessage(True))
            await asyncio.wait_for(
                runtime.drain(),
                timeout=1,
            )
            await _wait_until(
                lambda: _context_is(
                    relay,
                    new_context,
                )
            )
            await _wait_until(lambda: runtime.action_ready)

            new_connection = await connect(
                f"ws://127.0.0.1:{relay.port}/action",
                proxy=None,
            )
            try:
                await new_connection.send(
                    encode_action(
                        new_context,
                        "look",
                    )
                )

                new_receive = asyncio.create_task(new_connection.recv())

                await _wait_until(lambda: len(emitted) == 2)
                new_dispatch = emitted[1]
                assert new_dispatch.context == new_context
                assert new_dispatch.command == "look"

                # A delayed acknowledgement for the retired context must not
                # resolve the new context's in-flight action.
                await runtime.handle(
                    ActionResultMessage(
                        old_dispatch.id,
                        "forwarded",
                    )
                )

                await asyncio.sleep(0.05)
                assert not new_receive.done()

                await runtime.handle(
                    ActionResultMessage(
                        new_dispatch.id,
                        "forwarded",
                    )
                )

                raw = await asyncio.wait_for(
                    new_receive,
                    timeout=1,
                )
                assert isinstance(raw, str)

                decoded = decode_server_message(raw)
                assert decoded.ok
                assert isinstance(
                    decoded.value,
                    RelayActionResultMessage,
                )
                assert decoded.value.status == "forwarded"
            finally:
                await new_connection.close()

            assert len(emitted) == 2
        finally:
            await runtime.close()
            await relay.close()

    asyncio.run(scenario())
