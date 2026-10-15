#!/usr/bin/env python3
"""Exercise restart recovery with genuine TinyFugue and loopback-only fake MUDs.

Run with the TinyFugue integration's existing virtualenv and pass a TF binary.
No real startup files, MUD connections, or installed services are used.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    "integrations/tinyfugue/src",
    "integrations/common/src",
    "services/relay/src",
)


async def wait_for(predicate: Any) -> Any:
    async with asyncio.timeout(8):
        while not (value := predicate()):
            await asyncio.sleep(0.05)
        return value


class FakeMud:
    def __init__(self) -> None:
        self.writer: asyncio.StreamWriter | None = None
        self.accepts = 0
        self.received = bytearray()

    async def accept(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.writer = writer
        self.accepts += 1
        writer.write(bytes((255, 251, 201)))  # WILL GMCP
        await writer.drain()
        try:
            while data := await reader.read(4096):
                self.received.extend(data)
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()

    async def gmcp(self, payload: dict[str, str]) -> None:
        assert self.writer is not None
        self.writer.write(
            bytes((255, 250, 201))
            + b"Char.Status "
            + json.dumps(payload).encode()
            + bytes((255, 240))
        )
        await self.writer.drain()


async def snapshot(url: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    async with connect(url + "/state", proxy=None) as websocket:
        async with asyncio.timeout(3):
            async for raw in websocket:
                frame = json.loads(raw)
                if frame["type"] in ("snapshot", "status"):
                    result[frame["type"]] = frame
                if frame["type"] == "status":
                    return result
    raise AssertionError("relay closed before its initial status")


async def wait_snapshot(url: str, predicate: Any) -> dict[str, Any]:
    async with asyncio.timeout(8):
        while True:
            current = await snapshot(url)
            if "snapshot" in current and predicate(current["snapshot"]):
                return current
            await asyncio.sleep(0.05)


async def collect_text(url: str, frames: list[dict[str, Any]]) -> None:
    while True:
        try:
            async with connect(url + "/state", proxy=None, open_timeout=1) as websocket:
                async for raw in websocket:
                    frame = json.loads(raw)
                    if frame["type"] == "text":
                        frames.append(frame)
        except (OSError, TimeoutError, ConnectionClosed):
            await asyncio.sleep(0.05)


class Runtime:
    def __init__(self, directory: Path, binary: Path) -> None:
        self.directory = directory
        self.binary = binary
        self.home = directory / "home"
        self.state = self.home / ".local/state/imp"
        self.state.mkdir(parents=True)
        (self.home / ".local/bin").mkdir(parents=True)
        runtime = directory / "runtime"
        runtime.mkdir(mode=0o700)
        self.environment = dict(
            os.environ,
            HOME=str(self.home),
            XDG_RUNTIME_DIR=str(runtime),
            XDG_STATE_HOME=str(directory / "unused-xdg-state"),
            PYTHONPATH=os.pathsep.join(str(ROOT / source) for source in SOURCES),
        )
        self.processes: list[subprocess.Popen[bytes]] = []
        self.logs: list[Any] = []
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.url = f"ws://127.0.0.1:{self.port}"
        helper = self.home / ".local/bin/imp-action-consumer"
        helper.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> {shlex.quote(str(directory / 'helpers.log'))}\n"
            f"exec {shlex.quote(sys.executable)} -m imp_tf.action_consumer "
            f'--relay-url {self.url}/action-consumer "$@"\n'
        )
        helper.chmod(0o700)
        watcher = self.home / ".local/bin/imp-feed-watch"
        watcher.write_text(
            f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -m imp_tf.feed_watch "$@"\n'
        )
        watcher.chmod(0o700)

    def spawn(self, name: str, arguments: list[str]) -> subprocess.Popen[bytes]:
        log = (self.directory / f"{len(self.processes)}-{name}.log").open("wb")
        self.logs.append(log)
        process = subprocess.Popen(
            arguments,
            env=self.environment,
            cwd=self.directory,
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.processes.append(process)
        return process

    @staticmethod
    def stop(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def close(self) -> None:
        for process in reversed(self.processes):
            # TF's quote subprocesses share its private session/process group.
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            self.stop(process)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if not self.owned_helpers():
                break
            time.sleep(0.05)
        for pid in self.owned_helpers():
            with suppress(ProcessLookupError):
                os.killpg(pid, signal.SIGKILL)
        for log in self.logs:
            log.close()

    def owned_helpers(self) -> list[int]:
        # Only inspect/terminate process groups created by this harness.
        groups = {process.pid for process in self.processes}
        live: set[int] = set()
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                raw = (entry / "stat").read_text()
                fields = raw[raw.rfind(")") + 2 :].split()
                group = int(fields[2])
                if group in groups and fields[0] != "Z":
                    live.add(group)
            except (OSError, ValueError):
                continue
        return list(live)

    def quote_helpers(self, tf: subprocess.Popen[bytes], module: str) -> list[int]:
        live = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                raw = (entry / "stat").read_text()
                fields = raw[raw.rfind(")") + 2 :].split()
                argv = (entry / "cmdline").read_bytes().split(b"\0")
                if (
                    int(fields[2]) == tf.pid
                    and fields[0] != "Z"
                    and module.encode() in argv
                ):
                    live.append(int(entry.name))
            except (OSError, ValueError):
                continue
        return live

    def consumer_connections(self) -> int:
        path = self.directory / "consumer-connections.log"
        return len(path.read_text().splitlines()) if path.exists() else 0

    async def assert_single_consumer(self, tf: subprocess.Popen[bytes]) -> None:
        await wait_for(
            lambda: len(self.quote_helpers(tf, "imp_tf.action_consumer")) == 1
        )
        # Allow an in-flight registration to settle, then observe well beyond one retry.
        await asyncio.sleep(0.5)
        connections = self.consumer_connections()
        assert connections > 0
        for _ in range(30):
            assert len(self.quote_helpers(tf, "imp_tf.action_consumer")) == 1
            assert self.consumer_connections() == connections, (
                "consumer registration churn"
            )
            await asyncio.sleep(0.1)

    def relay(self) -> subprocess.Popen[bytes]:
        code = (
            "import asyncio\nfrom imp_relay.server import RelayServer\n"
            "class ObservedRelay(RelayServer):\n"
            " async def _handle_action_consumer(self, connection):\n"
            f"  with open({str(self.directory / 'consumer-connections.log')!r}, 'a') as log:\n"
            "   log.write('connection\\n')\n"
            "  await super()._handle_action_consumer(connection)\n"
            "async def main():\n"
            f" relay = ObservedRelay(port={self.port},stale_after=3)\n"
            " await relay.start()\n"
            " await asyncio.Event().wait()\nasyncio.run(main())\n"
        )
        return self.spawn("relay", [sys.executable, "-c", code])

    def feed(self) -> subprocess.Popen[bytes]:
        return self.spawn(
            "feed",
            [
                sys.executable,
                "-m",
                "imp_tf.feed",
                "--relay-url",
                self.url + "/ingest",
                "--poll-interval",
                "0.03",
            ],
        )

    def helper_calls(self) -> int:
        path = self.directory / "helpers.log"
        return len(path.read_text().splitlines()) if path.exists() else 0


async def scenario(runtime: Runtime) -> None:
    alpha, beta = FakeMud(), FakeMud()
    servers = [
        await asyncio.start_server(mud.accept, "127.0.0.1", 0) for mud in (alpha, beta)
    ]
    observer: asyncio.Task[None] | None = None
    try:
        relay = runtime.relay()
        await asyncio.sleep(0.4)
        feed = runtime.feed()
        ready = runtime.state / "feed-ready"
        await wait_for(ready.exists)
        text: list[dict[str, Any]] = []
        observer = asyncio.create_task(collect_text(runtime.url, text))
        script = runtime.directory / "fixture.tf"
        script.write_text(
            "\n".join(
                [
                    "/set visual=off",
                    "/set more=off",
                    "/set login=off",
                    "/set quiet=on",
                    "/set quitdone=off",
                    "/set connect=blocking",
                    "/set gethostbyname=blocking",
                    "/set gmcp=on",
                    f"/load {ROOT}/integrations/tinyfugue/imp.tf",
                    *[
                        f"/addworld -p -Tfixture {name} 127.0.0.1 {server.sockets[0].getsockname()[1]}"
                        for name, server in zip(("Alpha", "Beta"), servers, strict=True)
                    ],
                    "/repeat -120 1 /quit -y",
                ]
            )
            + "\n"
        )
        arguments = [str(runtime.binary)]
        library = runtime.binary.parent.parent / "lib/tf"
        if library.is_dir():
            arguments.append(f"-L{library}")
        arguments.extend([f"-f{script}", "-n", "-l", "-q", "-v"])
        tf = runtime.spawn("tf", arguments)

        def command(line: str) -> None:
            assert tf.stdin is not None
            tf.stdin.write((line + "\n").encode())
            tf.stdin.flush()

        command("/quote -0 -dexec !/bin/sleep 120")
        await wait_for(lambda: runtime.quote_helpers(tf, "/bin/sleep"))
        ready_token = ready.read_bytes()

        async def restart() -> None:
            nonlocal relay, feed, ready_token
            old = ready_token
            runtime.stop(feed)
            runtime.stop(relay)
            relay = runtime.relay()
            await asyncio.sleep(0.3)
            feed = runtime.feed()
            await wait_for(lambda: ready.exists() and ready.read_bytes() != old)
            ready_token = ready.read_bytes()
            assert tf.poll() is None, "TF exited during feed/relay restart"

        await asyncio.sleep(1.5)
        initial_null = await snapshot(runtime.url)
        assert initial_null["snapshot"]["context"] is None
        assert not (runtime.state / "context").exists()
        command("/connect -fl Alpha")
        command("/connect -bl Beta")
        command("/fg Alpha")
        await wait_for(lambda: alpha.writer and beta.writer)
        await asyncio.sleep(0.4)
        await alpha.gmcp(
            {"character_name": "RuntimeAlice", "health": "17", "health_max": "23"}
        )
        await beta.gmcp(
            {"character_name": "RuntimeBob", "health": "8", "health_max": "20"}
        )
        await asyncio.sleep(0.5)
        first = await snapshot(runtime.url)
        context = first["snapshot"]["context"]
        assert context is not None and first["status"]["feed"] == "live"
        assert alpha.writer is not None
        alpha.writer.write(b"RUNTIME_LOCAL_TEXT\n")
        await alpha.writer.drain()
        await wait_for(
            lambda: any(frame["text"] == "RUNTIME_LOCAL_TEXT" for frame in text)
        )
        async with connect(runtime.url + "/action", proxy=None) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "type": "action",
                        "protocol": 2,
                        "context": context,
                        "command": "RUNTIME_LOCAL_ECHO",
                    }
                )
            )
            result = json.loads(await asyncio.wait_for(websocket.recv(), 3))
            assert result["status"] == "forwarded"
        await wait_for(lambda: b"RUNTIME_LOCAL_ECHO" in alpha.received)
        await wait_for(lambda: runtime.helper_calls() >= 2)
        calls = runtime.helper_calls()
        await restart()
        restored = await wait_snapshot(
            runtime.url, lambda frame: frame["context"] == context
        )
        assert [alpha.accepts, beta.accepts] == [1, 1]
        assert restored["snapshot"]["context"] == context
        assert restored["snapshot"]["state"]["character"]["name"] == "RuntimeAlice"
        assert restored["snapshot"]["state"]["character"]["hp"] == {
            "current": 17,
            "max": 23,
        }
        assert restored["status"]["feed"] == "stale"
        assert alpha.received.count(b"RUNTIME_LOCAL_ECHO") == 1
        assert sum(frame["text"] == "RUNTIME_LOCAL_TEXT" for frame in text) == 1
        assert runtime.helper_calls() == calls + 1
        await alpha.gmcp({"health": "16"})
        await asyncio.sleep(0.4)
        assert (await snapshot(runtime.url))["status"]["feed"] == "live"
        await runtime.assert_single_consumer(tf)
        # Selection during an outage starts a helper; readiness must replace that
        # helper even though the recovered selection has exactly the same context.
        old_ready = ready_token
        old_helpers = runtime.quote_helpers(tf, "imp_tf.action_consumer")
        runtime.stop(feed)
        calls = runtime.helper_calls()
        command("/imp_select_world Alpha")
        await wait_for(lambda: runtime.helper_calls() == calls + 1)
        await wait_for(
            lambda: (
                not set(old_helpers)
                & set(runtime.quote_helpers(tf, "imp_tf.action_consumer"))
            )
        )
        feed = runtime.feed()
        await wait_for(lambda: ready.exists() and ready.read_bytes() != old_ready)
        ready_token = ready.read_bytes()
        await wait_for(lambda: runtime.helper_calls() >= calls + 2)
        await runtime.assert_single_consumer(tf)
        assert runtime.helper_calls() == calls + 2
        assert (await snapshot(runtime.url))["snapshot"]["context"] == context
        assert alpha.received.count(b"RUNTIME_LOCAL_ECHO") == 1
        assert sum(frame["text"] == "RUNTIME_LOCAL_TEXT" for frame in text) == 1
        assert len(runtime.quote_helpers(tf, "/bin/sleep")) == 1, (
            "unrelated quote was killed"
        )
        calls = runtime.helper_calls()
        command(f"/load {ROOT}/integrations/tinyfugue/imp.tf")
        command(f"/load {ROOT}/integrations/tinyfugue/imp.tf")
        await asyncio.sleep(2.5)
        assert runtime.helper_calls() == calls, "reload started another action helper"
        assert len(runtime.quote_helpers(tf, "imp_tf.feed_watch")) == 1
        await runtime.assert_single_consumer(tf)
        assert (await snapshot(runtime.url))["snapshot"]["context"] == context
        runtime.stop(feed)
        runtime.stop(relay)
        command("/fg Beta")
        await asyncio.sleep(0.3)
        await restart()
        selected = await wait_snapshot(
            runtime.url,
            lambda frame: frame["context"] is not None and frame["context"] != context,
        )
        assert selected["snapshot"]["context"] != context
        assert selected["snapshot"]["state"]["character"]["name"] == "RuntimeBob"
        assert selected["status"]["feed"] == "stale"
        await runtime.assert_single_consumer(tf)
        async with connect(runtime.url + "/action", proxy=None) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "type": "action",
                        "protocol": 2,
                        "context": context,
                        "command": "RUNTIME_OLD_CONTEXT",
                    }
                )
            )
            result = json.loads(await asyncio.wait_for(websocket.recv(), 3))
            assert result["status"] == "rejected"
        assert all(b"RUNTIME_OLD_CONTEXT" not in mud.received for mud in (alpha, beta))
        await beta.gmcp({"health": "7"})
        await asyncio.sleep(0.4)
        assert (await snapshot(runtime.url))["status"]["feed"] == "live"
        async with connect(runtime.url + "/action", proxy=None) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "type": "action",
                        "protocol": 2,
                        "context": selected["snapshot"]["context"],
                        "command": "RUNTIME_BETA_ECHO",
                    }
                )
            )
            result = json.loads(await asyncio.wait_for(websocket.recv(), 3))
            assert result["status"] == "forwarded"
        await wait_for(lambda: b"RUNTIME_BETA_ECHO" in beta.received)
        await runtime.assert_single_consumer(tf)
        assert beta.received.count(b"RUNTIME_BETA_ECHO") == 1
        assert b"RUNTIME_BETA_ECHO" not in alpha.received
        assert alpha.received.count(b"RUNTIME_LOCAL_ECHO") == 1
        assert sum(frame["text"] == "RUNTIME_LOCAL_TEXT" for frame in text) == 1
        runtime.stop(feed)
        runtime.stop(relay)
        command("/dc Beta")
        command("/dc Alpha")
        command("/fg -n")
        await asyncio.sleep(0.3)
        await restart()
        cleared = await wait_snapshot(
            runtime.url, lambda frame: frame["context"] is None
        )
        assert cleared["snapshot"]["context"] is None
        assert not (runtime.state / "context").exists()
        assert cleared["status"]["feed"] == "stale"
        for path in runtime.directory.glob("*-tf.log"):
            assert b"KILL: no process" not in path.read_bytes(), (
                "completed quote was killed again"
            )
        command("/quit -y")
    finally:
        if observer is not None:
            observer.cancel()
            with suppress(asyncio.CancelledError):
                await observer
        for server in servers:
            server.close()
        for mud in (alpha, beta):
            if mud.writer is not None:
                mud.writer.close()
        with suppress(TimeoutError):
            async with asyncio.timeout(3):
                for mud in (alpha, beta):
                    if mud.writer is not None:
                        with suppress(OSError):
                            await mud.writer.wait_closed()
                for server in servers:
                    await server.wait_closed()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "tf", type=Path, help="genuine TinyFugue binary with GMCP support"
    )
    args = parser.parse_args()
    binary = args.tf.resolve(strict=True)
    if sys.platform != "linux":
        parser.error("this native runtime proof requires Linux /proc process ownership")
    with tempfile.TemporaryDirectory(prefix="imp-tf-restart-") as temporary:
        runtime = Runtime(Path(temporary), binary)
        try:
            asyncio.run(asyncio.wait_for(scenario(runtime), timeout=110))
        except BaseException:
            for path in sorted(Path(temporary).glob("*.log")):
                print(
                    f"--- {path.name} ---\n{path.read_text(errors='replace')[-4000:]}",
                    file=sys.stderr,
                )
            raise
        finally:
            runtime.close()
    print(
        "PASS: genuine TF restart, same-context consumer ownership, no registration churn, and action/text fences"
    )


if __name__ == "__main__":
    main()
