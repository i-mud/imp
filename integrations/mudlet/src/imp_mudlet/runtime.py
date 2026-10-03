"""Mudlet profile state and actions mapped onto focus-owned Imp connections."""

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

from imp_mudlet.actions import (
    DEFAULT_CONSUMER_URL,
    ActionEmitter,
    RelayActionConsumer,
)
from imp_mudlet.lifecycle import MudletLifecycle
from imp_mudlet.protocol import (
    ActionResultMessage,
    ConnectedMessage,
    DisconnectedMessage,
    FocusMessage,
    GmcpMessage,
    InitMessage,
    LuaMessage,
    ProtocolMessage,
)


class StatePublisher(Protocol):
    async def select(
        self,
        context: StateContext | None,
        state: GameState,
    ) -> None: ...

    async def publish(
        self,
        context: StateContext,
        state: GameState,
    ) -> None: ...

    async def close(self) -> None: ...


type PublisherFactory = Callable[[], StatePublisher]


def default_publisher_factory() -> StatePublisher:
    return RelayPublisher()


@dataclass
class MudletRuntime:
    lifecycle: MudletLifecycle
    publisher_factory: PublisherFactory = default_publisher_factory
    emit_action: ActionEmitter | None = None
    action_url: str = DEFAULT_CONSUMER_URL
    _normalizer: Normalizer = field(default_factory=Normalizer, init=False)
    _publisher: StatePublisher | None = field(default=None, init=False)
    _active_context: StateContext | None = field(default=None, init=False)
    _delivery: asyncio.Task[None] | None = field(default=None, init=False)
    _action_consumer: RelayActionConsumer | None = field(
        default=None,
        init=False,
    )

    @property
    def state(self) -> GameState:
        return self._normalizer.state

    @property
    def action_ready(self) -> bool:
        consumer = self._action_consumer
        return consumer is not None and consumer.ready

    async def handle(self, message: LuaMessage) -> None:
        if isinstance(message, ActionResultMessage):
            consumer = self._action_consumer
            if consumer is not None:
                consumer.resolve(message.id, message.status)
            return

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

            state = self._normalizer.apply(message.record)
            context = self.lifecycle.context

            if (
                self._normalizer.observed
                and self._publisher is not None
                and context is not None
                and context == self._active_context
            ):
                await self._replace_delivery(
                    partial(
                        self._publisher.publish,
                        context,
                        state,
                    )
                )
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
            await self._stop_action_consumer()
            self._active_context = context

            await self._replace_delivery(
                partial(
                    self._publisher.select,
                    context,
                    self._normalizer.state,
                )
            )

            # Registration can race the relay processing the selection.
            # RelayActionConsumer therefore retries registration until this
            # exact context becomes eligible.
            if self.emit_action is not None:
                consumer = RelayActionConsumer(
                    context,
                    self.emit_action,
                    url=self.action_url,
                )
                self._action_consumer = consumer
                consumer.start()

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

    async def _stop_action_consumer(self) -> None:
        consumer = self._action_consumer
        self._action_consumer = None
        if consumer is not None:
            await consumer.close()

    async def _deactivate(self) -> None:
        # Stop accepting actions before retiring the state producer.
        await self._stop_action_consumer()
        await self._cancel_delivery()

        publisher = self._publisher
        self._publisher = None
        self._active_context = None

        if publisher is not None:
            await publisher.close()

    async def drain(self) -> None:
        """Wait for the current state delivery; intended for deterministic tests."""

        task = self._delivery
        if task is not None:
            await task

    async def close(self) -> None:
        await self._deactivate()
