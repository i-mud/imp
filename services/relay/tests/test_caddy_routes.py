from __future__ import annotations

import http.client
import http.server
import os
import shutil
import socket
import ssl
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from types import SimpleNamespace

import pytest

from imp_relay import direct_edge as edge

HOST = "edge.example.test"
ROUTES = ("/state", "/action", "/healthz")
ALLOWED = tuple(
    route + query
    for route in ROUTES
    for query in ("", "?", "?probe=1", "?next=/../state&encoded=%2F%3F%252e")
)
REJECTED = (
    "/state/",
    "/state/foo",
    "/action/",
    "/action/foo",
    "/healthz/",
    "/healthz/foo",
    "/ingest",
    "/action-consumer",
    "/unknown",
    "/st%61te",
    "/%73tate",
    "/sta%74e",
    "/action%2f",
    "/state%2f",
    "/state%2F",
    "/health%7a",
    "/state/../state",
    "/%2e/state",
    "/./state",
    "/state/./",
    "//state",
    "///state",
    "//action",
    "/STATE",
    "/State",
    "/ACTION",
    "/HEALTHZ",
    "/state%3f",
    "/state%3Fx=1",
    "/state%253f",
    "/state%3fanything",
    "/st%61te?probe=1",
    "/state/../state?probe=1",
    "/%2e/state?probe=1",
)


def test_generated_direct_and_nat_share_gate_and_fixed_upstream() -> None:
    direct = edge._caddyfile(HOST).decode("ascii")
    nat = edge._caddyfile(HOST, True).decode("ascii")
    assert direct.partition("\t@imp ")[1:] == nat.partition("\t@imp ")[1:]
    assert "\t@imp " in direct
    for config in (direct, nat):
        assert config.count("reverse_proxy 127.0.0.1:8788") == 1
        assert config.count("8788") == 1
        assert "8787" not in config


@pytest.fixture(scope="module")
def caddy_binary() -> str:
    configured = os.environ.get("IMP_TEST_CADDY")
    binary = shutil.which(configured or "caddy")
    if binary is None:
        if configured:
            pytest.fail(f"IMP_TEST_CADDY is not executable: {configured}")
        pytest.skip("real Caddy route matrix needs IMP_TEST_CADDY or caddy on PATH")
    return binary


@pytest.fixture(scope="module", params=[False, True], ids=["direct", "nat"])
def caddy_edge(
    caddy_binary: str, tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest
) -> Iterator[SimpleNamespace]:
    root = tmp_path_factory.mktemp("caddy-routes")
    ca, ca_key = root / "ca.pem", root / "ca.key"
    cert, key, csr = root / "cert.pem", root / "key.pem", root / "cert.csr"
    extensions = root / "extensions.cnf"
    extensions.write_text(
        f"subjectAltName=DNS:{HOST}\nbasicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n",
        encoding="ascii",
    )
    commands = (
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(ca_key),
            "-out",
            str(ca),
            "-days",
            "1",
            "-subj",
            "/CN=Imp disposable route test CA",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
        ],
        [
            "openssl",
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(csr),
            "-subj",
            f"/CN={HOST}",
        ],
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(csr),
            "-CA",
            str(ca),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-out",
            str(cert),
            "-days",
            "1",
            "-extfile",
            str(extensions),
        ],
    )
    server = None
    thread = None
    child = None
    marker = "imp-recording-upstream-" + uuid.uuid4().hex
    ledger: list[tuple[str, str | None]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(3)

        def do_GET(self) -> None:
            ledger.append((self.path, self.headers.get("Upgrade")))
            body = marker.encode("ascii")
            self.send_response(404)
            self.send_header("X-Imp-Test-Upstream", marker)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    try:
        for command in commands:
            subprocess.run(command, check=True, capture_output=True, timeout=15)
        context = ssl.create_default_context(cafile=str(ca))
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        production = edge._caddyfile(HOST, request.param).decode("ascii")
        config = production.replace("{\n\tadmin off\n}", "{\n\tadmin off\n\tauto_https off\n}").replace(
            f"{HOST} {{\n", f"https://{HOST}:{port} {{\n\tbind 127.0.0.1\n"
        )
        tls = f'\ttls "{cert}" "{key}"\n'
        if request.param:
            nat_tls = "\ttls {\n\t\tissuer acme {\n\t\t\tdisable_http_challenge\n\t\t}\n\t}\n"
            assert config.count(nat_tls) == 1
            config = config.replace(nat_tls, tls)
        else:
            config = config.replace("\t@imp ", tls + "\t@imp ", 1)
        config = config.replace("127.0.0.1:8788", f"127.0.0.1:{server.server_port}")
        # Only listener/TLS/upstream endpoints change; the generated routing gate is exercised intact.
        gate = production.partition("\t@imp ")[2].split("\thandle @imp", 1)[0]
        assert config.partition("\t@imp ")[2].split("\thandle @imp", 1)[0] == gate
        assert "auto_https off" in config and "admin off" in config and "issuer acme" not in config
        config_path = root / "Caddyfile"
        config_path.write_text(config, encoding="ascii")
        with (root / "caddy.log").open("w+", encoding="utf-8") as log:
            child = subprocess.Popen(
                [caddy_binary, "run", "--config", str(config_path), "--adapter", "caddyfile"],
                stdout=log,
                stderr=log,
                env={**os.environ, "XDG_DATA_HOME": str(root), "XDG_CONFIG_HOME": str(root)},
            )
            deadline = time.monotonic() + 10
            while True:
                if child.poll() is not None:
                    log.seek(0)
                    pytest.fail("Caddy exited during startup:\n" + log.read())
                try:
                    with (
                        socket.create_connection(("127.0.0.1", port), timeout=0.25) as tcp,
                        context.wrap_socket(tcp, server_hostname=HOST),
                    ):
                        break
                except (OSError, ssl.SSLError):
                    if time.monotonic() >= deadline:
                        log.seek(0)
                        pytest.fail("Caddy TLS startup timed out:\n" + log.read())
                    time.sleep(0.05)
            yield SimpleNamespace(port=port, context=context, ledger=ledger, marker=marker)
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        if server is not None:
            if thread is not None and thread.is_alive():
                server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=3)
            assert not thread.is_alive()
        shutil.rmtree(root)


@pytest.mark.parametrize("upgrade", [False, True], ids=["https", "ws-upgrade"])
@pytest.mark.parametrize(
    ("target", "allowed"),
    [(target, True) for target in ALLOWED] + [(target, False) for target in REJECTED],
)
def test_raw_request_gate(caddy_edge: SimpleNamespace, target: str, allowed: bool, upgrade: bool) -> None:
    before = list(caddy_edge.ledger)
    headers = f"Host: {HOST}\r\nConnection: close\r\n"
    if upgrade:
        headers = (
            f"Host: {HOST}\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n"
            "Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        )
    # Raw sockets preserve percent escapes, slash runs, dot segments and the empty query delimiter.
    with (
        socket.create_connection(("127.0.0.1", caddy_edge.port), timeout=3) as tcp,
        caddy_edge.context.wrap_socket(tcp, server_hostname=HOST) as tls,
    ):
        tls.sendall(f"GET {target} HTTP/1.1\r\n{headers}\r\n".encode("ascii"))
        response = http.client.HTTPResponse(tls)
        response.begin()
        body = response.read(4096)
        origin = response.getheader("X-Imp-Test-Upstream")
        assert response.status == 404, target
        response.close()
    if allowed:
        assert caddy_edge.ledger == before + [(target, "websocket" if upgrade else None)], target
        assert origin == caddy_edge.marker, target
        assert body == caddy_edge.marker.encode("ascii"), target
    else:
        assert caddy_edge.ledger == before, target
        assert origin is None, target
        assert caddy_edge.marker.encode("ascii") not in body, target
