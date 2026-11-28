#!/usr/bin/env python3
"""Audited production relay and malformed/stalled peers for Managed acceptance."""

import argparse
import asyncio
import os
import socket
import time
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from secrets import token_hex

from imp_relay.protocol import (
    Character,
    ConsumerReadyMessage,
    GameState,
    RelayInfo,
    StateContext,
    Vital,
    decode_server_message,
    encode_consumer,
    encode_hello,
    encode_publish,
    encode_snapshot,
    encode_select,
    encode_status,
)
from imp_relay.server import RelayServer
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

HOST = "127.0.0.1"
REAL_MODES = {"live", "stale", "down", "hold"}


def state(name, hp):
    return GameState(Character(name, Vital(hp, 100), None, None), None)


async def audit_messages(connection, record):
    try:
        async for _ in connection:
            record()
    except ConnectionClosed:
        pass


class ObservedRelay(RelayServer):
    """Record route use and optionally hold the real subscriber writer at startup."""

    def __init__(self, port, label, clock, record):
        super().__init__(port=port, clock=clock, relay_name="Managed check fixture")
        self.label = label
        self.record = record
        self.marker = token_hex(16)
        self.release = asyncio.Event()
        self.published = asyncio.Event()
        self.disconnected = asyncio.Event()

    def is_fixture(self, request):
        return request.path in ("/ingest", "/action-consumer") and (
            request.headers.get_all("X-Imp-Fixture") == [self.marker]
        )

    async def _process_request(self, connection, request):
        response = await super()._process_request(connection, request)
        if response is not None and not (self.label == "A" and request.path == "/healthz"):
            self.record(f"{self.label} REQUEST {request.path}", connection)
        return response

    async def _handle_connection(self, connection):
        request = connection.request
        assert request is not None
        path = request.path
        if self.is_fixture(request):
            self.record(f"{self.label} FIXTURE OPEN {path}")
            try:
                await super()._handle_connection(connection)
            finally:
                if path == "/ingest":
                    self.disconnected.set()
            return
        self.record(f"{self.label} OPEN {path}")
        incoming = None
        if path == "/state":
            # RelayServer owns the write side and never consumes subscriber messages.
            incoming = asyncio.create_task(audit_messages(
                connection, lambda: self.record(f"{self.label} MESSAGE {path}"),
            ))
        try:
            await super()._handle_connection(connection)
        finally:
            if incoming is not None:
                await incoming
            self.record(f"{self.label} CLOSED {path}")

    async def _write_subscriber(self, connection, queue):
        await self.release.wait()
        await super()._write_subscriber(connection, queue)

    async def _broadcast_snapshot(self, snapshot):
        await super()._broadcast_snapshot(snapshot)
        self.published.set()


class ProductionEndpoint:
    def __init__(self, label, record):
        self.label = label
        self.record = record
        self.name, self.hp = ("RunningFixture", 41) if label == "A" else ("DiagnosticFixture", 73)
        self.context = StateContext(f"fixture_{label}", 1, 1)
        self.now = time.time()
        self.dispatched = 0

    async def publish(self):
        self.relay.published.clear()
        await self.producer.send(encode_publish(self.context, state(self.name, self.hp)))
        await self.relay.published.wait()

    async def update(self):
        self.hp += 1
        await self.publish()
        self.record(f"{self.label} UPDATED")

    async def set_mode(self, mode):
        if mode == "hold":
            self.relay.release.clear()
        elif mode == "live":
            assert self.relay.state.health()["feed"] == "live"
            self.relay.release.set()
        elif mode == "down":
            await self.producer.close()
            await self.relay.disconnected.wait()
            assert self.relay.state.health()["feed"] == "down"
            self.relay.release.set()
        else:
            raise ValueError("unknown relay fixture mode")

    def dispatch(self):
        self.dispatched += 1
        self.record(f"{self.label} DISPATCH")

    @asynccontextmanager
    async def listen(self, port, mode, *, gated):
        relay = self.relay = ObservedRelay(port, self.label, lambda: self.now, self.record)
        await relay.start()
        try:
            async with AsyncExitStack() as stack:
                def socket_to(route):
                    return connect(
                        f"ws://{HOST}:{port}/{route}", proxy=None, close_timeout=1,
                        additional_headers={"X-Imp-Fixture": relay.marker},
                    )

                self.producer = await stack.enter_async_context(socket_to("ingest"))
                await self.producer.send(encode_select(self.context, state(self.name, self.hp - 1)))
                await relay.published.wait()
                await self.publish()
                consumer = await stack.enter_async_context(socket_to("action-consumer"))
                await consumer.send(encode_consumer(self.context))
                ready = decode_server_message(await consumer.recv())
                assert ready.ok and isinstance(ready.value, ConsumerReadyMessage)
                assert ready.value.context == self.context
                watcher = asyncio.create_task(audit_messages(consumer, self.dispatch))
                try:
                    # Stale advances the injected clock; down actually disconnects the producer.
                    if mode == "stale":
                        self.now += relay.state.stale_after + 1
                    elif mode == "down":
                        await self.set_mode("down")
                    if gated:
                        relay.release.clear()
                    elif mode != "hold":
                        relay.release.set()
                    health = relay.state.health()
                    assert health["has_snapshot"] and health["seq"] == 2
                    assert health["feed"] == ("live" if mode == "hold" else mode)
                    self.record(f"{self.label} FIXTURE READY {health['feed']} retained=2")
                    yield
                finally:
                    await consumer.close()
                    await watcher
                    assert self.dispatched == 0, "diagnostic dispatched to the real action consumer"
        finally:
            await relay.close()


@asynccontextmanager
async def invalid_peer(mode, port, record):
    async def websocket(connection):
        path = connection.request.path
        record(f"B OPEN {path}")
        try:
            at = time.time_ns() // 1_000_000
            await connection.send(encode_hello(at, RelayInfo("Managed check fixture", "1")))
            if mode == "invalid-frame":
                await connection.send('{"type":"status","protocol":999}')
            elif mode == "empty-live":
                # An inconsistent live feed: production can never be live before a publish.
                await connection.send(encode_status(at, "live", None))
            elif mode == "status-stall":
                await connection.send(encode_snapshot(
                    1, at, StateContext("fixture_B", 1, 1), state("DiagnosticFixture", 73),
                ))
            await audit_messages(connection, lambda: record(f"B MESSAGE {path}"))
        except ConnectionClosed:
            pass
        finally:
            record(f"B CLOSED {path}")

    async def raw(reader, writer):
        path = None
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            method, route, _ = head.split(b"\r\n", 1)[0].split(b" ")
            assert method == b"GET"
            path = route.decode("ascii")
            record(f"B OPEN {path}")
            if mode == "invalid-upgrade":
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
            if await reader.read(1):
                record(f"B MESSAGE {path}")
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()
            if path is not None:
                record(f"B CLOSED {path}")

    with socket.socket() as listener:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((HOST, port))
        if mode in ("invalid-upgrade", "upgrade-stall"):
            async with await asyncio.start_server(raw, sock=listener):
                yield
        else:
            async with serve(websocket, sock=listener, ping_interval=None, close_timeout=1):
                yield


async def main(mode):
    def record(event, connection=None):
        print(event, flush=True)

    if mode in REAL_MODES:
        relay = ProductionEndpoint("B", record)
        listener = relay.listen(8787, mode, gated=True)
    else:
        listener = invalid_peer(mode, 8787, record)
    async with listener:
        record("READY 8787")
        command = await asyncio.to_thread(input)
        if command == "release":
            assert mode in REAL_MODES and mode != "hold"
            relay.relay.release.set()
            command = await asyncio.to_thread(input)
        assert command == "stop"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode")
    asyncio.run(main(parser.parse_args().mode))
