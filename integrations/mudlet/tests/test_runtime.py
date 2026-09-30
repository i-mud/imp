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
