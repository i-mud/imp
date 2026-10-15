from __future__ import annotations

import asyncio
import builtins
import getpass
import http.client
import io
import json
import os
import signal
import socket
import ssl
import subprocess
import termios
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import pytest
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from imp_relay import direct_wss, public_verifier
from imp_relay.websocket_logging import websocket_logger

TOKEN = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
PREVIOUS = "B" * 42 + "A"
PUBLIC = "imp.example.test"


@pytest.mark.parametrize("failed_prompt", [1, 2])
def test_secure_prompt_failure_never_reads_fallback_or_uses_network(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], failed_prompt: int
) -> None:
    original_open = os.open
    real_getpass = getpass.getpass
    calls = 0

    def prompt(*args: Any, **kwargs: Any) -> str:
        nonlocal calls
        calls += 1
        return real_getpass(*args, **kwargs) if calls == failed_prompt else TOKEN

    def echo_failure(fd: int) -> None:
        raise termios.error("injected echo-control failure")

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("fallback credential read or network activity")

    monkeypatch.setattr(builtins, "open", lambda *args, **kwargs: io.StringIO())
    monkeypatch.setattr(os, "open", lambda *args, **kwargs: original_open(os.devnull, os.O_RDWR))
    monkeypatch.setattr(termios, "tcgetattr", echo_failure)
    monkeypatch.setattr(getpass, "getpass", prompt)
    monkeypatch.setattr(getpass, "_raw_input", forbidden)
    monkeypatch.setattr(public_verifier, "_public_connect", forbidden)
    monkeypatch.setattr(public_verifier, "_public_http_get", forbidden)

    with pytest.raises(SystemExit) as exc:
        public_verifier.main(["wss://localhost/state"])
    assert exc.value.code == 1
    assert calls == failed_prompt
    output = capsys.readouterr()
    assert output.out == "Verification failed.\n"
    assert output.err == ""
    assert TOKEN not in output.out + output.err


@pytest.mark.parametrize("interval", [0.0, 60.0])
def test_http_refuses_armed_real_timer_without_changing_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], interval: float
) -> None:
    probe = public_verifier._Probe(urlsplit(f"wss://{PUBLIC}/state"), ssl.create_default_context(), False)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("network activity")

    def handler(signum: int, frame: Any) -> None:
        pass

    monkeypatch.setattr(http.client.HTTPSConnection, "connect", forbidden)
    previous_handler = signal.signal(signal.SIGALRM, handler)
    try:
        signal.setitimer(signal.ITIMER_REAL, 30.0, interval)
        with pytest.raises(SystemExit) as exc:
            probe.http_get("/healthz")
        delay, remaining_interval = signal.getitimer(signal.ITIMER_REAL)
        assert exc.value.code == 1
        assert 29.0 < delay <= 30.0
        assert remaining_interval == pytest.approx(interval)
        assert signal.getsignal(signal.SIGALRM) is handler
        assert capsys.readouterr().out == "Verification failed.\n"
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


@pytest.fixture
def edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[SimpleNamespace]:
    names = getattr(request, "param", f"DNS:localhost,DNS:{PUBLIC}")
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert)]
        + ["-days", "1", "-subj", "/CN=localhost", "-addext", f"subjectAltName={names}"],
        check=True,
        capture_output=True,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    # Trust only this disposable certificate through the normal store; TLS validation stays on.
    monkeypatch.setenv("SSL_CERT_FILE", str(cert))
    real_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        # Public test names are intentionally unresolvable; any lookup is a public dial attempt.
        if isinstance(host, str) and host.endswith(".example.test"):
            state.lookups.append(host)
            raise socket.gaierror(socket.EAI_NONAME, "intentionally unreachable")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    # Any client that honored proxy variables would connect here instead of the edge.
    proxy = socket.socket()
    proxy.bind(("127.0.0.1", 0))
    proxy.listen()
    proxy.setblocking(False)
    for name in ("https_proxy", "http_proxy", "wss_proxy", "ws_proxy", "all_proxy"):
        monkeypatch.setenv(name, f"http://127.0.0.1:{proxy.getsockname()[1]}")
        monkeypatch.setenv(name.upper(), f"http://127.0.0.1:{proxy.getsockname()[1]}")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    state = SimpleNamespace(
        prefix="",
        health=200,
        body='{"status":"ok"}',
        hello={"type": "hello", "protocol": 2, "at": 0, "relay": {"name": "imp-relay", "version": "0.0.0"}},
        forbidden=None,
        websocket_only=False,
        redirect=None,
        lax=set(),
        stall=False,
        requests=[],
        auths=[],
        extra=[],
        origin=False,
        hosts=[],
        sni=[],
        lookups=[],
        proxy=proxy,
    )

    def handshake(connection: ServerConnection, request: Request) -> Response | None:
        upgrade = request.headers.get("Upgrade") == "websocket"
        state.requests.append((request.path, upgrade))
        state.hosts.append(request.headers.get("Host"))
        route = request.path.removeprefix(state.prefix)
        if route == state.redirect:
            response = connection.respond(302, "redirect")
            response.headers["Location"] = state.url
            return response
        if route in ("/state", "/action"):
            return None
        if route == "/healthz":
            return connection.respond(state.health, state.body)
        if route == state.forbidden and (not state.websocket_only or upgrade):
            return connection.respond(200, "forbidden")
        return connection.respond(404, "Not found")

    async def handler(connection: ServerConnection) -> None:
        assert connection.request is not None
        route = connection.request.path.removeprefix(state.prefix)
        try:
            token = json.loads(await asyncio.wait_for(connection.recv(), timeout=0.75))["token"]
            kind = {TOKEN: "current", PREVIOUS: "previous"}.get(token, "wrong")
        except TimeoutError:
            kind = "missing"
        except ConnectionClosed:
            return
        state.auths.append((route, kind))
        if kind != "current" and (route, kind) not in state.lax:
            await connection.close(1008, "auth")
            return
        if state.stall:
            await connection.wait_closed()
            return
        await connection.send(json.dumps(state.hello))
        try:
            async for message in connection:
                state.extra.append(message)
        except ConnectionClosed:
            pass

    context.sni_callback = lambda sock, name, ctx: state.sni.append(name)
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    async def start() -> Server:
        return await serve(
            handler, "127.0.0.1", 0, ssl=context, process_request=handshake, logger=websocket_logger()
        )

    server = asyncio.run_coroutine_threadsafe(start(), loop).result(timeout=5)
    state.port = server.sockets[0].getsockname()[1]
    state.url = f"wss://localhost:{state.port}/state"
    # Test-only: production is fixed 127.0.0.1:443; the ephemeral port stands in for local Caddy.
    monkeypatch.setattr(direct_wss, "_LOOPBACK_ENDPOINT", ("127.0.0.1", state.port))
    try:
        yield state
    finally:

        async def stop() -> None:
            server.close()
            await server.wait_closed()

        asyncio.run_coroutine_threadsafe(stop(), loop).result(timeout=5)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=3)
        loop.close()
        proxy.close()


def _target(edge: SimpleNamespace, loopback: bool) -> tuple[str, tuple[str, ...]]:
    return (f"wss://{PUBLIC}/state", ("--verify-via-loopback",)) if loopback else (edge.url, ())


def _verify(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    url: str,
    *secrets: str,
    flags: tuple[str, ...] = (),
) -> tuple[int | str | None, str]:
    answers = iter(secrets)
    real_open = builtins.open
    monkeypatch.setattr(getpass, "getpass", lambda *args, **kwargs: next(answers))
    monkeypatch.setattr(
        builtins,
        "open",
        lambda file, *a, **k: io.StringIO() if file == "/dev/tty" else real_open(file, *a, **k),
    )
    code: int | str | None
    try:
        code = public_verifier.main([url, *flags])
    except SystemExit as exc:
        code = exc.code
    output = capsys.readouterr()
    assert output.err == ""
    return code, output.out


def _expected(prefix: str, previous: bool) -> tuple[list[tuple[str, bool]], list[tuple[str, str]]]:
    kinds = ["wrong", "missing"] + (["previous"] if previous else [])
    routes = ("/state", "/action")
    requests = [(prefix + "/state", True)] + [(prefix + route, True) for route in routes for _ in kinds]
    for route in ("/ingest", "/action-consumer", "/unknown"):
        requests += [(prefix + route, False), (prefix + route, True)]
    requests.append((prefix + "/healthz", False))
    auths = [("/state", "current")] + [(route, kind) for route in routes for kind in kinds]
    return requests, auths


@pytest.mark.parametrize("loopback", [False, True])
@pytest.mark.parametrize("prefix", ["", "/imp"])
@pytest.mark.parametrize("health", [200, 404])
@pytest.mark.parametrize("previous", [True, False])
def test_trusted_tls_matrix_passes_repeatably_without_action_or_leak(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    edge: SimpleNamespace,
    prefix: str,
    health: int,
    previous: bool,
    loopback: bool,
) -> None:
    edge.prefix = prefix
    edge.health = health
    caplog.set_level("DEBUG")
    expected = _expected(prefix, previous)
    url, flags = _target(edge, loopback)
    url = url.replace("/state", f"{prefix}/state")
    for _ in range(2):
        edge.requests.clear()
        edge.auths.clear()
        result = _verify(monkeypatch, capsys, url, TOKEN, PREVIOUS if previous else "", flags=flags)
        assert result == (0, "Public gateway verification passed.\n")
        assert (edge.requests, edge.auths) == expected
        assert signal.getitimer(signal.ITIMER_REAL) == (0, 0)
    assert edge.extra == []
    assert not edge.origin
    if loopback:
        # Public name is the TLS identity and Host, but is never resolved or dialed.
        assert set(edge.sni) == set(edge.hosts) == {PUBLIC}
        assert edge.lookups == []
    with pytest.raises(BlockingIOError):
        edge.proxy.accept()
    assert TOKEN not in caplog.text and PREVIOUS not in caplog.text


def _lax(route: str, kind: str) -> dict[str, Any]:
    return {"lax": {(route, kind)}}


FAILURES: dict[str, dict[str, Any]] = {
    "hello-protocol": {"hello": {"type": "hello", "protocol": 1}},
    "hello-type": {"hello": {"type": "snapshot", "protocol": 2}},
    "redirect-state": {"redirect": "/state"},
    "redirect-ingest": {"redirect": "/ingest"},
    "health-503": {"health": 503},
    "health-body": {"body": '{"status":"ok","extra":true}'},
    "health-wrong-status": {"body": '{"status":"bad"}'},
    "health-duplicate-status": {"body": '{"status":"bad","status":"ok"}'},
    **{
        f"{kind}-{route}": _lax(f"/{route}", kind)
        for route in ("state", "action")
        for kind in ("wrong", "missing", "previous")
    },
    **{
        f"{transport}-{route}": {"forbidden": f"/{route}", "websocket_only": transport == "ws"}
        for transport in ("http", "ws")
        for route in ("ingest", "action-consumer", "unknown")
    },
}


@pytest.mark.parametrize("loopback", [False, True])
@pytest.mark.parametrize("failure", FAILURES)
def test_trusted_tls_matrix_failures_are_generic(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    edge: SimpleNamespace,
    failure: str,
    loopback: bool,
) -> None:
    vars(edge).update(FAILURES[failure])
    caplog.set_level("DEBUG")
    url, flags = _target(edge, loopback)
    result = _verify(monkeypatch, capsys, url, TOKEN, PREVIOUS, flags=flags)
    assert result == (1, "Verification failed.\n")
    assert TOKEN not in caplog.text and PREVIOUS not in caplog.text
    assert signal.getitimer(signal.ITIMER_REAL) == (0, 0)
    assert not (loopback and edge.lookups)
    with pytest.raises(BlockingIOError):
        edge.proxy.accept()


@pytest.mark.parametrize("loopback", [False, True])
@pytest.mark.parametrize("failure", ["tls", "hostname", "wrong-current", "noncanonical"])
def test_trusted_tls_environment_and_credential_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    edge: SimpleNamespace,
    failure: str,
    loopback: bool,
) -> None:
    url, flags = _target(edge, loopback)
    token = TOKEN
    if failure == "tls":
        monkeypatch.delenv("SSL_CERT_FILE")
    elif failure == "hostname":
        url = "wss://other.example.test/state" if loopback else url.replace("localhost", "127.0.0.1")
    elif failure == "wrong-current":
        token = "C" * 42 + "A"
    else:
        token = "short"
    assert _verify(monkeypatch, capsys, url, token, PREVIOUS, flags=flags) == (1, "Verification failed.\n")
    assert [kind for _, kind in edge.auths] == (["wrong"] if failure == "wrong-current" else [])
    assert edge.extra == []
    assert not (loopback and edge.lookups)


@pytest.mark.parametrize("edge", ["DNS:localhost"], indirect=True)
def test_loopback_certificate_for_localhost_does_not_match_public_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], edge: SimpleNamespace
) -> None:
    url, flags = _target(edge, True)
    assert _verify(monkeypatch, capsys, url, TOKEN, PREVIOUS, flags=flags) == (1, "Verification failed.\n")
    assert edge.requests == [] and edge.auths == [] and edge.lookups == []


def test_default_mode_still_dials_public_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], edge: SimpleNamespace
) -> None:
    assert _verify(monkeypatch, capsys, f"wss://{PUBLIC}/state", TOKEN, PREVIOUS) == (
        1,
        "Verification failed.\n",
    )
    assert edge.lookups and set(edge.lookups) == {PUBLIC}
    assert edge.requests == [] and edge.auths == []


@pytest.mark.parametrize(
    "url,flags",
    [
        (f"wss://{PUBLIC}:8443/state", ("--verify-via-loopback",)),
        (f"wss://{PUBLIC}/state", ("--verify-via-loopback", "--verify-via-loopback")),
        (f"wss://{PUBLIC}/state", ("--verify-via-loopback", "127.0.0.1:1")),
        (f"wss://{PUBLIC}/state", ("--other",)),
    ],
)
def test_loopback_invalid_invocations_do_no_network(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    edge: SimpleNamespace,
    url: str,
    flags: tuple[str, ...],
) -> None:
    assert _verify(monkeypatch, capsys, url, TOKEN, PREVIOUS, flags=flags) == (1, "Verification failed.\n")
    assert edge.requests == [] and edge.lookups == []


def test_stalled_hello_fails_within_bounded_time(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], edge: SimpleNamespace
) -> None:
    edge.stall = True
    start = time.monotonic()
    assert _verify(monkeypatch, capsys, edge.url, TOKEN, PREVIOUS) == (1, "Verification failed.\n")
    assert 4 <= time.monotonic() - start < 9
