"""Authenticated remote gateway for Imp desktop state and actions."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import math
import re
from contextlib import suppress
from http import HTTPStatus
from typing import Final
from urllib.parse import urlsplit

from websockets.asyncio.client import ClientConnection, connect
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed, InvalidHandshake
from websockets.http11 import Request, Response

MAX_FRAME_BYTES: Final = 65_536
MAX_AUTH_FRAME_CHARS: Final = 256
AUTH_POLICY_CLOSE_CODE: Final = 1008
UPSTREAM_UNAVAILABLE_CLOSE_CODE: Final = 1011

_LOOPBACK_HOSTS: Final = frozenset({"127.0.0.1", "::1"})
_BROWSER_ORIGINS: Final = frozenset({"http://localhost:1420", "http://tauri.localhost"})
_REMOTE_ENDPOINTS: Final = frozenset({"/state", "/action"})
_TOKEN_PATTERN: Final = re.compile(r"^[A-Za-z0-9_-]{43}$")


def valid_pairing_token(token: str) -> bool:
    """Return whether token is canonical unpadded base64url for exactly 32 bytes."""

    if _TOKEN_PATTERN.fullmatch(token) is None:
        return False

    try:
        decoded = base64.b64decode(
            token.encode("ascii") + b"=",
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, UnicodeEncodeError):
        return False

    if len(decoded) != 32:
        return False

    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    return hmac.compare_digest(canonical, token)


def token_digest(token: str) -> bytes:
    """Hash one already validated textual pairing token."""

    if not valid_pairing_token(token):
        raise ValueError("pairing token must be canonical 32-byte unpadded base64url")
    return hashlib.sha256(token.encode("ascii")).digest()


def validate_relay_url(value: str) -> str:
    """Validate and canonicalize the gateway's loopback relay base URL."""

    parsed = urlsplit(value)

    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("relay URL has an invalid port") from error

    if parsed.scheme != "ws":
        raise ValueError("gateway relay URL must use ws:")
    if parsed.hostname not in _LOOPBACK_HOSTS:
        raise ValueError("gateway relay URL must target loopback")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("gateway relay URL must not contain credentials")
    if port is None or not 0 < port <= 65_535:
        raise ValueError("gateway relay URL must contain an explicit valid port")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("gateway relay URL must not contain a path, query, or fragment")

    return value.rstrip("/")


class GatewayServer:
    """Authenticate remote desktop WebSockets before bridging to the local relay."""

    def __init__(
        self,
        token_sha256: bytes,
        *,
        host: str = "127.0.0.1",
        port: int = 8788,
        relay_url: str = "ws://127.0.0.1:8787",
        auth_timeout: float = 3.0,
    ) -> None:
        if host not in _LOOPBACK_HOSTS:
            raise ValueError("gateway binding is restricted to loopback")
        if not 0 <= port <= 65_535:
            raise ValueError("gateway port must be in 0..65535")
        if len(token_sha256) != 32:
            raise ValueError("gateway token digest must contain exactly 32 bytes")
        if not math.isfinite(auth_timeout) or auth_timeout <= 0:
            raise ValueError("gateway authentication timeout must be finite and positive")

        self._host = host
        self._port = port
        self._relay_url = validate_relay_url(relay_url)
        self._token_sha256 = token_sha256
        self._auth_timeout = auth_timeout
        self._server: Server | None = None

    @property
    def port(self) -> int:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("gateway server is not running")
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("gateway server is already running")

        self._server = await serve(
            self._handle_connection,
            self._host,
            self._port,
            process_request=self._process_request,
            max_size=MAX_FRAME_BYTES,
        )

    async def close(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()

    async def _process_request(
        self,
        connection: ServerConnection,
        request: Request,
    ) -> Response | None:
        path = request.path

        if path not in _REMOTE_ENDPOINTS and path != "/healthz":
            return connection.respond(HTTPStatus.NOT_FOUND, "Not found\n")

        origins = request.headers.get_all("Origin")
        if len(origins) > 1:
            return connection.respond(HTTPStatus.FORBIDDEN, "Forbidden\n")

        origin = origins[0] if origins else None
        if origin is not None and origin not in _BROWSER_ORIGINS:
            return connection.respond(HTTPStatus.FORBIDDEN, "Forbidden\n")

        if path == "/healthz":
            return connection.respond(HTTPStatus.OK, '{"status":"ok"}\n')

        return None

    async def _handle_connection(self, connection: ServerConnection) -> None:
        request = connection.request
        if request is None or request.path not in _REMOTE_ENDPOINTS:
            return

        if not await self._authenticate(connection):
            return

        if request.path == "/state":
            await self._proxy_state(connection)
        elif request.path == "/action":
            await self._proxy_action(connection)

    async def _authenticate(self, connection: ServerConnection) -> bool:
        try:
            frame = await asyncio.wait_for(connection.recv(), timeout=self._auth_timeout)
        except TimeoutError:
            await self._reject_auth(connection)
            return False
        except ConnectionClosed:
            return False

        if not isinstance(frame, str) or len(frame) > MAX_AUTH_FRAME_CHARS:
            await self._reject_auth(connection)
            return False

        try:
            value = json.loads(frame)
        except json.JSONDecodeError:
            await self._reject_auth(connection)
            return False

        if not isinstance(value, dict) or set(value) != {"type", "token"}:
            await self._reject_auth(connection)
            return False

        token = value.get("token")
        if value.get("type") != "auth" or not isinstance(token, str) or not valid_pairing_token(token):
            await self._reject_auth(connection)
            return False

        presented = hashlib.sha256(token.encode("ascii")).digest()
        if not hmac.compare_digest(presented, self._token_sha256):
            await self._reject_auth(connection)
            return False

        return True

    async def _reject_auth(self, connection: ServerConnection) -> None:
        with suppress(ConnectionClosed):
            await connection.close(
                code=AUTH_POLICY_CLOSE_CODE,
                reason="authentication failed",
            )

    async def _proxy_state(self, connection: ServerConnection) -> None:
        try:
            async with connect(
                self._upstream_url("/state"),
                proxy=None,
                max_size=MAX_FRAME_BYTES,
            ) as upstream:
                relay_task = asyncio.create_task(self._copy_state(upstream, connection))
                client_task = asyncio.create_task(self._watch_state_client(connection))

                _, pending = await asyncio.wait(
                    {relay_task, client_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in pending:
                    task.cancel()

                await asyncio.gather(*pending, return_exceptions=True)
        except (OSError, InvalidHandshake, TimeoutError):
            await self._close_upstream_unavailable(connection)

    async def _copy_state(
        self,
        upstream: ClientConnection,
        connection: ServerConnection,
    ) -> None:
        try:
            async for frame in upstream:
                await connection.send(frame)
        except ConnectionClosed:
            pass

    async def _watch_state_client(self, connection: ServerConnection) -> None:
        try:
            await connection.recv()
        except ConnectionClosed:
            return

        with suppress(ConnectionClosed):
            await connection.close(
                code=AUTH_POLICY_CLOSE_CODE,
                reason="unexpected state frame",
            )

    async def _proxy_action(self, connection: ServerConnection) -> None:
        try:
            request = await connection.recv()
        except ConnectionClosed:
            return

        try:
            async with connect(
                self._upstream_url("/action"),
                proxy=None,
                max_size=MAX_FRAME_BYTES,
            ) as upstream:
                await upstream.send(request)

                try:
                    result = await upstream.recv()
                except ConnectionClosed:
                    return

                await connection.send(result)
        except (OSError, InvalidHandshake, TimeoutError):
            await self._close_upstream_unavailable(connection)
        except ConnectionClosed:
            pass

    async def _close_upstream_unavailable(self, connection: ServerConnection) -> None:
        with suppress(ConnectionClosed):
            await connection.close(
                code=UPSTREAM_UNAVAILABLE_CLOSE_CODE,
                reason="local relay unavailable",
            )

    def _upstream_url(self, path: str) -> str:
        return f"{self._relay_url}{path}"
