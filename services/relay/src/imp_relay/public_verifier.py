"""Bounded manual verification of the public Direct WSS edge.

Probes authenticated state, auth rejection, and 404s for privileged/unknown
routes. It sends no action frame and reads credentials only from /dev/tty.
Failures are intentionally generic so no exception text can expose secrets.
"""

from __future__ import annotations

import asyncio
import base64
import getpass
import json
import secrets
import ssl
import sys
import warnings
from collections.abc import Coroutine, Sequence
from typing import Any, NoReturn
from urllib.parse import SplitResult, urlsplit, urlunsplit

from websockets.exceptions import ConnectionClosed, InvalidStatus

from imp_relay.direct_wss import _public_connect, _public_http_get


def _fail() -> NoReturn:
    print("Verification failed.")
    raise SystemExit(1)


def _canonical_token(value: str) -> bool:
    try:
        raw = base64.urlsafe_b64decode(value + "=")
        return (
            len(value) == 43
            and len(raw) == 32
            and base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") == value
        )
    except Exception:
        return False


def _run(coroutine: Coroutine[Any, Any, bool]) -> bool:
    try:
        return asyncio.run(coroutine)
    except Exception:
        return False


class _Probe:
    def __init__(self, parts: SplitResult, context: ssl.SSLContext, loopback: bool) -> None:
        self.parts = parts
        self.context = context
        self.loopback = loopback

    def http_get(self, path: str) -> tuple[int, bytes]:
        try:
            return _public_http_get(self.parts, path, self.context, self.loopback)
        except Exception:
            _fail()

    def _connect(self, path: str) -> Any:
        url = urlunsplit(("wss", self.parts.netloc, path, "", ""))
        return _public_connect(url, self.context, self.loopback)

    async def hello(self, path: str, token: str) -> bool:
        async with self._connect(path) as ws:
            await ws.send(json.dumps({"type": "auth", "token": token}, separators=(",", ":")))
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        return bool(frame.get("type") == "hello" and frame.get("protocol") == 2)

    async def rejection(self, path: str, token: str | None) -> bool:
        try:
            async with self._connect(path) as ws:
                if token is not None:
                    await ws.send(json.dumps({"type": "auth", "token": token}, separators=(",", ":")))
                await asyncio.wait_for(ws.recv(), timeout=10)
        except ConnectionClosed as exc:
            return exc.rcvd is not None and exc.rcvd.code == 1008
        return False

    async def hidden(self, path: str) -> bool:
        try:
            async with self._connect(path):
                pass
        except InvalidStatus as exc:
            return exc.response.status_code == 404
        return False


def _new_token() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) not in (1, 2) or args[1:] not in ([], ["--verify-via-loopback"]):
        _fail()
    loopback = len(args) == 2
    url = args[0]
    try:
        parts = urlsplit(url)
        port = parts.port
        valid_url = bool(
            parts.scheme == "wss"
            and parts.hostname
            and parts.username is None
            and parts.password is None
            and not parts.query
            and not parts.fragment
            and parts.path.endswith("/state")
            and (not loopback or port in (None, 443))
        )
    except ValueError:
        _fail()
    if not valid_url or "?" in url or "#" in url:
        _fail()
    prefix = parts.path[: -len("/state")]
    state_path = prefix + "/state"
    action_path = prefix + "/action"
    health_path = prefix + "/healthz"
    try:
        with open("/dev/tty", "w") as tty, warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            token = getpass.getpass("Pairing token: ", stream=tty)
            previous_token = getpass.getpass("Previous token to verify revocation (optional): ", stream=tty)
        if not _canonical_token(token) or (previous_token and not _canonical_token(previous_token)):
            _fail()
    except Exception:
        _fail()

    probe = _Probe(parts, ssl.create_default_context(), loopback)
    if not _run(probe.hello(state_path, token)):
        _fail()

    wrong_token = _new_token()
    while wrong_token in (token, previous_token):
        wrong_token = _new_token()
    for route in (state_path, action_path):
        for rejected in (wrong_token, None, previous_token):
            if rejected != "" and not _run(probe.rejection(route, rejected)):
                _fail()

    for route in ("/ingest", "/action-consumer", "/unknown"):
        status, _ = probe.http_get(prefix + route)
        if status != 404:
            _fail()
        if not _run(probe.hidden(prefix + route)):
            _fail()

    status, body = probe.http_get(health_path)
    if status not in (200, 404):
        _fail()
    if status == 200:
        try:
            if json.loads(body, object_pairs_hook=list) != [("status", "ok")]:
                _fail()
        except Exception:
            _fail()

    print("Public gateway verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
