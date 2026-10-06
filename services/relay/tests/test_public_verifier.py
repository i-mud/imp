from __future__ import annotations

import ast
import builtins
import getpass
import http.server
import io
import os
import signal
import sys
import termios
import threading
import time
from pathlib import Path
from typing import Any

import pytest

TOKEN = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def _snippet() -> str:
    readme = Path(__file__).resolve().parents[3] / "deploy" / "README.md"
    section = readme.read_text().split("### Bounded public verification", 1)[1]
    return section.split("```python\n", 1)[1].split("```", 1)[0]


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
    monkeypatch.setattr("websockets.sync.client.connect", forbidden)
    monkeypatch.setattr("urllib.request.OpenerDirector.open", forbidden)
    monkeypatch.setattr(sys, "argv", ["public_wss_check.py", "wss://localhost/state"])

    with pytest.raises(SystemExit) as exc:
        exec(compile(_snippet(), "public_wss_check.py", "exec"), {})

    assert exc.value.code == 1
    assert calls == failed_prompt
    output = capsys.readouterr()
    assert output.out == "Verification failed.\n"
    assert output.err == ""
    assert TOKEN not in output.out + output.err


def _definitions() -> dict[str, Any]:
    # Execute the snippet's imports/definitions, stopping before interactive input.
    tree = ast.parse(_snippet())
    boundary = next(i for i, node in enumerate(tree.body) if isinstance(node, ast.If))
    tree.body = tree.body[:boundary]
    namespace: dict[str, Any] = {}
    exec(compile(tree, "public_wss_check.py", "exec"), namespace)
    namespace["opener"] = namespace["build_opener"](
        namespace["ProxyHandler"]({}), namespace["HTTPSHandler"](), namespace["NoRedirect"]()
    )
    return namespace


@pytest.mark.parametrize("interval", [0.0, 60.0])
def test_http_refuses_armed_real_timer_without_changing_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], interval: float
) -> None:
    namespace = _definitions()

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("network activity")

    def handler(signum: int, frame: Any) -> None:
        pass

    monkeypatch.setattr("urllib.request.OpenerDirector.open", forbidden)
    previous_handler = signal.signal(signal.SIGALRM, handler)
    try:
        signal.setitimer(signal.ITIMER_REAL, 30.0, interval)
        with pytest.raises(SystemExit) as exc:
            namespace["http_get"]("https://127.0.0.1/healthz")
        delay, remaining_interval = signal.getitimer(signal.ITIMER_REAL)
        assert exc.value.code == 1
        assert 29.0 < delay <= 30.0
        assert remaining_interval == pytest.approx(interval)
        assert signal.getsignal(signal.SIGALRM) is handler
        assert capsys.readouterr().out == "Verification failed.\n"
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


@pytest.mark.parametrize("status", [200, 302])
def test_http_fast_response_and_redirect_release_timer_on_repeat(status: int) -> None:
    namespace = _definitions()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(status)
            self.send_header("Location", "/elsewhere")
            self.send_header("Content-Length", "15")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    previous_handler = signal.getsignal(signal.SIGALRM)
    try:
        for _ in range(2):
            result = namespace["http_get"](f"http://127.0.0.1:{server.server_port}/healthz")
            assert result == ((200, b'{"status":"ok"}') if status == 200 else (302, b""))
            assert signal.getsignal(signal.SIGALRM) == previous_handler
            assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("drip", ["headers", "body"])
def test_http_deadline_interrupts_drip_fed_response(capsys: pytest.CaptureFixture[str], drip: str) -> None:
    namespace = _definitions()
    stop = threading.Event()
    sent = 0

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            nonlocal sent
            header = b"HTTP/1.1 200 OK\r\nContent-Length: 15\r\n\r\n"
            body = b'{"status":"ok"}'
            if drip == "body":
                self.wfile.write(header)
                self.wfile.flush()
            try:
                for byte in header if drip == "headers" else body:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    sent += 1
                    if stop.wait(0.6):
                        break
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    previous_handler = signal.getsignal(signal.SIGALRM)
    start = time.monotonic()
    try:
        with pytest.raises(SystemExit) as exc:
            namespace["http_get"](f"http://127.0.0.1:{server.server_port}/healthz")
        elapsed = time.monotonic() - start
        assert exc.value.code == 1
        assert namespace["HTTP_DEADLINE"] <= elapsed < namespace["HTTP_DEADLINE"] + 1
        assert sent >= 5
        assert signal.getsignal(signal.SIGALRM) == previous_handler
        assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
        assert capsys.readouterr().out == "Verification failed.\n"
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join()
