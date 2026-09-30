"""Single-flight action forwarding to the active local client consumer."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import uuid4

from websockets.asyncio.server import ServerConnection
from websockets.exceptions import ConnectionClosed

from .protocol import ActionStatus, ConsumerStatus, StateContext, encode_dispatch


@dataclass(frozen=True)
class ConsumerRegistration:
    connection: ServerConnection
    context: StateContext


class ActionBroker:
    """Owns the one eligible consumer and the one permitted in-flight action."""

    def __init__(self, result_timeout: float = 5.0) -> None:
        self._result_timeout = result_timeout
        self._consumer: ConsumerRegistration | None = None
        self._inflight_id: str | None = None
        self._inflight_consumer: ConsumerRegistration | None = None
        self._inflight_result: asyncio.Future[ConsumerStatus | None] | None = None

    def register(
        self, connection: ServerConnection, context: StateContext, active_context: StateContext | None
    ) -> ConsumerRegistration | None:
        if context != active_context:
            return None
        if self._consumer is not None and self._consumer.context == context:
            return None
        registration = ConsumerRegistration(connection, context)
        self._consumer = registration
        return registration

    def resolve(self, registration: ConsumerRegistration, correlation: str, status: ConsumerStatus) -> bool:
        result = self._inflight_result
        if (
            result is None
            or result.done()
            or registration != self._inflight_consumer
            or correlation != self._inflight_id
        ):
            return False
        result.set_result(status)
        return True

    def unregister(self, registration: ConsumerRegistration) -> None:
        if registration == self._consumer:
            self._consumer = None
        result = self._inflight_result
        if registration == self._inflight_consumer and result is not None and not result.done():
            result.set_result(None)

    async def forward(
        self, context: StateContext, command: str, active_context: StateContext | None
    ) -> tuple[ActionStatus, str | None]:
        consumer = self._consumer
        if context != active_context:
            return "rejected", "context is not active"
        if consumer is None or consumer.context != context:
            return "rejected", "no matching local action consumer"
        if self._inflight_result is not None:
            return "rejected", "another action is in flight"

        correlation = uuid4().hex
        loop = asyncio.get_running_loop()
        result: asyncio.Future[ConsumerStatus | None] = loop.create_future()
        self._inflight_id = correlation
        self._inflight_consumer = consumer
        self._inflight_result = result
        try:
            try:
                await consumer.connection.send(encode_dispatch(correlation, context, command))
            except ConnectionClosed:
                return "unknown", "consumer disconnected before dispatch completed"
            try:
                status = await asyncio.wait_for(result, timeout=self._result_timeout)
            except TimeoutError:
                return "unknown", "consumer result timed out"
            if status is None:
                return "unknown", "consumer disconnected after dispatch"
            return status, None
        finally:
            self._inflight_id = None
            self._inflight_consumer = None
            self._inflight_result = None
