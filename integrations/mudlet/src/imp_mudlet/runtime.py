"""Mudlet profile state mapped onto one focus-owned Imp producer."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from contextlib import suppress
from dataclasses import dataclass, field
from functools import partial
from typing import Protocol

from imp_adapter.normalize import Normalizer
from imp_adapter.publisher import RelayPublisher
from imp_relay.protocol import GameState, StateContext

from imp_mudlet.lifecycle import MudletLifecycle
from imp_mudlet.protocol import (
    ConnectedMessage,
    DisconnectedMessage,
    FocusMessage,
    GmcpMessage,
    InitMessage,
    LuaMessage,
    ProtocolMessage,
)

EMPTY_STATE = GameState(character=None, target=None)


class StatePublisher(Protocol):
    async def select(self, context: StateContext | None, state: GameState) -> None: ...

    async def publish(self, context: StateContext, state: GameState) -> None: ...

    async def close(self) -> None: ...


type PublisherFactory = Callable[[], StatePublisher]


def default_publisher_factory() -> StatePublisher:
    return RelayPublisher()


@dataclass
class MudletRuntime:
    lifecycle: MudletLifecycle
    publisher_factory: PublisherFactory = default_publisher_factory
    _normalizer: Normalizer = field(default_factory=Normalizer, init=False)
    _publisher: StatePublisher | None = field(default=None, init=False)
    _active_context: StateContext | None = field(default=None, init=False)
    _delivery: asyncio.Task[None] | None = field(default=None, init=False)

    @property
    def state(self) -> GameState:
        return self._normalizer.state

    async def handle(self, message: LuaMessage) -> None:
        if isinstance(message, InitMessage):
            self.lifecycle.apply(message)
            await self._reconcile_focus()
            return

        if isinstance(message, ConnectedMessage):
            was_connected = self.lifecycle.connected
            self.lifecycle.apply(message)
            if not was_connected and self.lifecycle.connected:
                self._normalizer = Normalizer()
            await self._reconcile_focus()
            return

        if isinstance(message, DisconnectedMessage):
            self.lifecycle.apply(message)
            self._normalizer = Normalizer()
            await self._deactivate()
            return

        if isinstance(message, FocusMessage):
            self.lifecycle.apply(message)
            await self._reconcile_focus()
            return

        if isinstance(message, ProtocolMessage):
            self.lifecycle.apply(message)
            return

        if isinstance(message, GmcpMessage):
            self.lifecycle.apply(message)
            if not self.lifecycle.connected:
                return

            previous = self._normalizer.state
            state = self._normalizer.apply(message.record)
            context = self.lifecycle.context

            if (
                state != previous
                and self._publisher is not None
                and context is not None
                and context == self._active_context
            ):
                await self._replace_delivery(partial(self._publisher.publish, context, state))
            return

        raise TypeError(f"unsupported Mudlet message: {type(message)!r}")

    async def _reconcile_focus(self) -> None:
        context = self.lifecycle.context

        if context is None:
            await self._deactivate()
            return

        if self._publisher is None:
            self._publisher = self.publisher_factory()

        if context != self._active_context:
            self._active_context = context
            await self._replace_delivery(partial(self._publisher.select, context, self._normalizer.state))

    async def _replace_delivery(
        self,
        operation: Callable[[], Coroutine[object, object, None]],
    ) -> None:
        await self._cancel_delivery()

        task = asyncio.create_task(operation())
        self._delivery = task

        # Surface operations that fail synchronously, while leaving relay
        # reconnect attempts asynchronous so Mudlet input never blocks on node
        # availability.
        await asyncio.sleep(0)
        if task.done():
            await task

    async def _cancel_delivery(self) -> None:
        task = self._delivery
        self._delivery = None
        if task is None:
            return
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _deactivate(self) -> None:
        await self._cancel_delivery()

        publisher = self._publisher
        self._publisher = None
        self._active_context = None

        if publisher is not None:
            await publisher.close()

    async def drain(self) -> None:
        """Wait for the current relay operation; intended for deterministic tests."""

        task = self._delivery
        if task is not None:
            await task

    async def close(self) -> None:
        await self._deactivate()
