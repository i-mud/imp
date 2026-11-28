#!/usr/bin/env python3
"""Authenticated end-to-end fixture for Direct connection codes.

Credentials (code, token, runtime configs) live only in this process's memory and in pipes. The one disk
copy is the disposable ``tunnel.json`` that native settings persistence writes; that is the behaviour under
test. This script never prints subprocess output beyond fixed failure categories.

Chain, against production code with a disposable TLS authority as the only added trust:

1. ``direct_wss.main(["setup", "--public-url", ...])`` runs unmodified on the main thread. Replaced: the
   terminal (``Terminal`` only answers ``isatty``), the home directory, the ports, and the user systemd
   manager (``FakeSystemd`` owns the production gateway process, started from the written ``gateway.env``
   digest). Public verification uses real TLS; ``SSL_CERT_FILE`` names the disposable CA. A run without that
   trust is the negative control: it must refuse before printing a code and roll back.
2. The issued code goes to the native store through the runtime-acceptance harness (``direct-code``): refused
   with other fields or modes, saved without changing the running transport, loaded after a restart (a new
   process).
3. The restarted runtime configs go over stdin to Vitest (``apps/desktop/test/direct-code.e2e.ts``), where the real renderer
   clients authenticate through TLS to the production gateway and relay, receive synthetic state, send
   exact-context actions once without replay, and are refused with a wrong token. Manual setup from the same
   issuance must yield an identical runtime config.

Run: ``uv run --project services/relay python scripts/test-direct-code-e2e.py``
Needs: openssl, cargo (Tauri build dependencies), node >= 24 after ``npm ci``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import imp_relay.direct_wss as dw

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "apps/desktop/src-tauri/Cargo.toml"
HARNESS = "runtime-ownership-harness"
DESKTOP = ROOT / "apps/desktop"
CODE = re.compile(r"^imp1\.([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]{43})$", re.M)
MANUAL_TOKEN = re.compile(r"Direct WSS pairing token \(shown once; not stored\):\n([A-Za-z0-9_-]{43})\n")
VERIFIED = "Public WSS endpoint: verified."


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class Terminal(io.StringIO):
    """Stand-in for the interactive terminal; the production issuer only asks whether it is one."""

    def isatty(self) -> bool:
        return True


class Relay:
    """The production relay CLI; owned and reaped here."""

    def __init__(self, port: int) -> None:
        self.port = port

    def __enter__(self) -> Relay:
        self.process = subprocess.Popen(
            [sys.executable, "-m", "imp_relay", "--port", str(self.port)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert self.process.stderr is not None
        for line in self.process.stderr:  # blocks until the relay itself reports it is listening
            if "relay listening" in line:
                break
        else:
            self.process.wait()
            raise SystemExit("FAIL: relay exited before listening")
        self.drain = threading.Thread(target=self._discard)
        self.drain.start()
        return self

    def _discard(self) -> None:
        assert self.process.stderr is not None
        for _ in self.process.stderr:
            pass

    def __exit__(self, *_: object) -> None:
        self.process.terminate()
        self.process.wait()
        if hasattr(self, "drain"):
            self.drain.join()
        if self.process.stderr is not None:
            self.process.stderr.close()


class FakeSystemd:
    """User systemd manager replacement. Owns the production gateway process it starts."""

    def __init__(self, home: Path, gateway_port: int, relay_port: int) -> None:
        self.home = home
        self.gateway_port = gateway_port
        self.relay_port = relay_port
        self.enabled = False
        self.gateway: subprocess.Popen[bytes] | None = None
        unit_dir = home / ".config/systemd/user"
        unit_dir.mkdir(parents=True)
        for directory in (unit_dir.parent.parent, unit_dir.parent, unit_dir):
            directory.chmod(0o700)
        self.unit = unit_dir / dw._UNIT
        self.unit.write_bytes(dw._SHIPPED_UNIT)
        self.unit.chmod(0o644)

    def close(self) -> None:
        self._stop()

    def _start(self) -> None:
        env_file = (self.home / ".config/imp" / dw._CONFIG_NAME).read_text()
        self.gateway = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "imp_relay.gateway_main",
                "--port",
                str(self.gateway_port),
                "--relay-url",
                f"ws://127.0.0.1:{self.relay_port}",
            ],
            env={**os.environ, "IMP_GATEWAY_TOKEN_SHA256": env_file.split("=", 1)[1].strip()},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _stop(self) -> None:
        gateway, self.gateway = self.gateway, None
        if gateway is not None:
            gateway.terminate()
            gateway.wait()

    def _show(self) -> str:
        binary = str(self.home / ".local/share/imp/current/bin/imp-server")
        exec_start = (
            f"{{ path={binary} ; argv[]={binary} gateway --host 127.0.0.1 --port {self.gateway_port} "
            f"--relay-url ws://127.0.0.1:{self.relay_port} ; ignore_errors=no ; "
            "start_time=[n/a] ; stop_time=[n/a] ; pid=0 }"
        )
        values = {
            "LoadState": "loaded",
            "FragmentPath": str(self.unit),
            "DropInPaths": "",
            "NeedDaemonReload": "no",
            "Type": "simple",
            "ExecStart": exec_start,
            "EnvironmentFiles": f"{self.home}/.config/imp/{dw._CONFIG_NAME} (ignore_errors=no)",
            "Restart": "on-failure",
            "RestartUSec": "2s",
            "NoNewPrivileges": "yes",
            "UnitFileState": "enabled" if self.enabled else "disabled",
            "ActiveState": "active" if self.gateway is not None else "inactive",
        }
        return "".join(f"{key}={value}\n" for key, value in values.items())

    def __call__(self, *args: str) -> subprocess.CompletedProcess[str]:
        op, code, out = args[0], 0, ""
        if op == "show":
            out = self._show()
        elif op == "is-active":
            code = 0 if self.gateway is not None else 3
        elif op == "is-enabled":
            code = 0 if self.enabled else 1
        elif op == "enable":
            self.enabled = True
            if "--now" in args:
                self._start()
        elif op == "disable":
            self.enabled = False
        elif op == "restart":
            self._stop()
            self._start()
        elif op == "stop":
            self._stop()
        else:
            code = 1
        return subprocess.CompletedProcess(args, code, out, "")


class TlsProxy:
    """Disposable TLS terminator in front of the production gateway; forwards bytes only."""

    def __init__(self, cert: Path, key: Path, port: int, upstream: int) -> None:
        self.cert, self.key, self.port, self.upstream = cert, key, port, upstream
        self.ready = threading.Event()
        self.error: BaseException | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.stop: asyncio.Event | None = None
        self.thread = threading.Thread(target=lambda: asyncio.run(self._serve()))

    def __enter__(self) -> TlsProxy:
        self.thread.start()
        self.ready.wait()
        if self.error is not None:
            self.thread.join()
            raise SystemExit("FAIL: TLS proxy could not listen")
        return self

    def __exit__(self, *_: object) -> None:
        if self.loop is not None and self.stop is not None and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.stop.set)
        self.thread.join()

    async def _serve(self) -> None:
        self.loop, self.stop = asyncio.get_running_loop(), asyncio.Event()
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(self.cert, self.key)
            server = await asyncio.start_server(self._handle, "127.0.0.1", self.port, ssl=context)
        except BaseException as error:
            self.error = error
            self.ready.set()
            return
        self.ready.set()
        await self.stop.wait()
        server.close()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection("127.0.0.1", self.upstream)
        except OSError:
            writer.close()
            return
        await asyncio.gather(self._pipe(reader, upstream_writer), self._pipe(upstream_reader, writer))

    @staticmethod
    async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        except Exception:  # a reset or TLS teardown ends one direction of the tunnel
            pass
        finally:
            writer.close()


class Fixture:
    def __init__(self, work: Path) -> None:
        self.work = work
        self.secrets: set[str] = set()
        self.home = work / "home"
        self.home.mkdir(mode=0o700)
        self.relay_port, self.gateway_port, self.tls_port = free_port(), free_port(), free_port()
        self.url = f"wss://127.0.0.1:{self.tls_port}/state"

    def redact(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "<redacted>")
        return CODE.sub("<redacted-code>", text)

    def fail(self, category: str, detail: str = "") -> None:
        tail = "\n".join(self.redact(detail).splitlines()[-30:])
        raise SystemExit(f"FAIL: {category}" + (f"\n{tail}" if tail else ""))

    def check(self, condition: bool, category: str) -> None:
        if not condition:
            self.fail(category)

    def pki(self) -> Path:
        ca_key, ca = self.work / "ca.key", self.work / "ca.pem"
        key, csr, cert = self.work / "leaf.key", self.work / "leaf.csr", self.work / "leaf.pem"
        extensions = self.work / "leaf.ext"
        extensions.write_text(
            "subjectAltName=IP:127.0.0.1\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n"
            "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n"
        )
        curve = ["-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes"]
        steps = [
            ["req", "-x509", *curve, "-keyout", str(ca_key), "-out", str(ca), "-days", "1"]
            + ["-subj", "/CN=Imp disposable test CA"]
            + ["-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign"],
            ["req", *curve, "-keyout", str(key), "-out", str(csr), "-subj", "/CN=127.0.0.1"],
            ["x509", "-req", "-in", str(csr), "-CA", str(ca), "-CAkey", str(ca_key), "-CAcreateserial"]
            + ["-out", str(cert), "-days", "1", "-extfile", str(extensions)],
        ]
        for step in steps:
            done = subprocess.run(["openssl", *step], capture_output=True, text=True, cwd=self.work)
            if done.returncode:
                self.fail("disposable TLS authority creation", done.stderr)
        ca_key.chmod(0o600)
        key.chmod(0o600)
        return ca

    def issue(self, systemd: Callable[..., object], ca: Path | None) -> tuple[int, str, str]:
        """Production ``imp-direct-wss setup`` on this thread; returns (exit code, stdout, stderr)."""
        stdout, stderr = Terminal(), io.StringIO()
        replaced = {
            "_supported_platform": lambda: None,
            "_user_home": lambda: (os.geteuid(), str(self.home)),
            "_systemctl": systemd,
            "_GATEWAY_PORT": self.gateway_port,
            "_RELAY_PORT": self.relay_port,
        }
        with ExitStack() as patched:
            for name, value in replaced.items():
                patched.enter_context(mock.patch.object(dw, name, value))
            patched.enter_context(mock.patch.object(sys, "stdin", Terminal()))
            patched.enter_context(mock.patch.object(sys, "stdout", stdout))
            patched.enter_context(mock.patch.object(sys, "stderr", stderr))
            patched.enter_context(mock.patch.dict(os.environ))
            os.environ.pop("SSL_CERT_FILE", None)
            os.environ.pop("SSL_CERT_DIR", None)
            if ca is not None:
                os.environ["SSL_CERT_FILE"] = str(ca)  # the only added trust root
            code = dw.main(["setup", "--public-url", self.url])
        return code, stdout.getvalue(), stderr.getvalue()

    def build_harness(self) -> Path:
        done = subprocess.run(
            ["cargo", "build", "--manifest-path", str(MANIFEST), "--features", "runtime-acceptance"]
            + ["--bin", HARNESS, "--message-format=json-render-diagnostics"],
            capture_output=True,
            text=True,
            cwd=ROOT,
            timeout=3600,
        )
        if done.returncode:  # nothing secret exists yet
            self.fail("native harness build", done.stderr)
        for line in done.stdout.splitlines():
            artifact = json.loads(line) if line.startswith("{") else {}
            if artifact.get("reason") == "compiler-artifact" and artifact["target"]["name"] == HARNESS:
                if artifact.get("executable"):
                    return Path(artifact["executable"])
        self.fail("native harness executable not reported")
        raise AssertionError

    def native(self, harness: Path, phase: str, config: Path, stdin: str) -> dict[str, str]:
        done = subprocess.run(
            [str(harness), "direct-code", phase, str(config), self.url],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if done.returncode:
            reasons = [line for line in done.stderr.splitlines() if line.startswith("E2E-FAIL ")]
            self.fail(f"native phase {phase}", "\n".join(reasons))
        pairs = (line.split(" ", 1) for line in done.stdout.splitlines() if line.startswith("E2E-"))
        return {key: value for key, value in pairs}


def run(f: Fixture, harness: Path, ca: Path) -> None:
    with (
        Relay(f.relay_port),
        ExitStack() as services,
    ):
        systemd = FakeSystemd(f.home, f.gateway_port, f.relay_port)
        services.callback(systemd.close)
        services.enter_context(
            TlsProxy(f.work / "leaf.pem", f.work / "leaf.key", f.tls_port, f.gateway_port)
        )

        # Negative control: the same production issuance without the trusted authority must refuse and roll back.
        code, output, stderr = f.issue(systemd, None)
        f.check(code == 1 and "imp1." not in output, "untrusted TLS issuance printed a code or succeeded")
        f.check("public WSS verification failed" in stderr, "untrusted TLS refusal is not a verification error")
        f.check("service state restored" in stderr, "untrusted issuance did not roll back")
        f.check(not (f.home / ".config/imp/gateway.env").exists(), "untrusted issuance kept a credential")
        f.check(systemd.gateway is None, "untrusted issuance left the gateway running")

        # Real verified issuance with the disposable authority trusted.
        code, output, stderr = f.issue(systemd, ca)
        f.check(code == 0, "trusted issuance failed")
        issued, manual = CODE.search(output), MANUAL_TOKEN.search(output)
        if issued is None or manual is None:
            f.fail("issuance did not print a code and a manual token")
            return
        token, code_value = issued[2], issued[0]
        f.secrets |= {token, code_value}
        f.check(manual[1] == token, "manual token differs from the code credential")
        f.check(VERIFIED in output and output.index(VERIFIED) < issued.start(), "code before verification")
        f.check(b64decode(issued[1]).decode("ascii") == f.url, "code endpoint is not the verified URL")
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        env_file = (f.home / ".config/imp/gateway.env").read_text()
        f.check(env_file == f"IMP_GATEWAY_TOKEN_SHA256={digest}\n", "gateway.env is not the digest alone")

        # Native save by code: refusals, unchanged running transport, then a restart in a new process.
        first = f.work / "native-code/tunnel.json"
        saved = f.native(harness, "save-code", first, code_value + "\n")
        f.check(json.loads(saved["E2E-RUNNING"])["mode"] == "unconfigured", "save changed the running config")
        code_runtime = json.loads(f.native(harness, "restart", first, token + "\n")["E2E-RUNTIME"])
        expected = {"mode": "direct", "stateUrl": f.url, "authenticationToken": token}
        f.check(code_runtime == expected, "restarted runtime is not the issued endpoint and token")

        # Manual setup with the URL and token block of the same issuance.
        second = f.work / "native-manual/tunnel.json"
        f.native(harness, "save-manual", second, f"{f.url}\n{token}\n")
        manual_runtime = json.loads(f.native(harness, "restart", second, token + "\n")["E2E-RUNTIME"])
        f.check(manual_runtime == code_runtime, "manual and code setup differ")

        # A well-formed code with another token reaches the same endpoint and must be refused there.
        other = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode("ascii")
        f.check(other != token, "random wrong token collided")
        wrong_code = f"imp1.{issued[1]}.{other}"
        f.secrets |= {other, wrong_code}
        third = f.work / "native-wrong/tunnel.json"
        f.native(harness, "save-code", third, wrong_code + "\n")
        wrong_runtime = json.loads(f.native(harness, "restart", third, other + "\n")["E2E-RUNTIME"])

        # Renderer clients: TLS trust comes only from the disposable authority.
        payload = json.dumps({"code": code_runtime, "manual": manual_runtime, "wrongToken": wrong_runtime})
        # The Vitest entry point is run directly; the config's reporter prints only fixed labels and counts.
        done = subprocess.run(
            ["node", "node_modules/vitest/vitest.mjs", "run", "--config", "vitest.direct-code.config.ts"],
            input=payload,
            capture_output=True,
            text=True,
            env=os.environ | {"NODE_EXTRA_CA_CERTS": str(ca), "IMP_E2E_RELAY_PORT": str(f.relay_port)},
            cwd=DESKTOP,
            timeout=600,
        )
        lines = [line for line in done.stdout.splitlines() if line.startswith("DIRECT-E2E-")]
        passed = [line for line in lines if line.startswith("DIRECT-E2E-RESULT passed ")]
        if done.returncode or len(passed) != 3 or "DIRECT-E2E-SUMMARY passed=3 total=3 errors=0" not in lines:
            f.fail("renderer Direct runtime scenarios", "\n".join(lines) or "no scenario results reported")
        print("\n".join(lines))
        print("PASS: code-based state and action, manual state and action, wrong-token refusal")


def main() -> int:
    for tool in ("openssl", "cargo", "node"):
        if shutil.which(tool) is None:
            raise SystemExit(f"FAIL: {tool} is required")
    with tempfile.TemporaryDirectory(prefix="imp-direct-code-e2e-") as directory:
        fixture = Fixture(Path(directory))
        ca = fixture.pki()
        harness = fixture.build_harness()
        run(fixture, harness, ca)
    print("PASS: verified issuance -> native save -> restart -> Direct authentication, state and action")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
