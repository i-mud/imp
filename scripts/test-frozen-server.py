#!/usr/bin/env python3.12
"""Frozen server acceptance: real roles from an installed bundle, no host Python.

Usage: test-frozen-server.py BUNDLE_DIR [--no-native-systemd]
Import:  verify_installed(home, work, version, token=None) checks an already-installed HOME.

The native phase is skipped (and reported as a limit) only when the user manager is unreachable.

Actual frozen/native evidence (no source injection): installs the shipping
bundle into a fixture HOME with a guard directory first on PATH whose
python/python3/python3.12 fail loudly, then runs the installed relay, feed,
gateway and action-consumer binaries (a) as direct child processes and (b) as
uniquely named imp-frozen-accept-<uuid>-ROLE units in the user's real systemd
manager, derived from the installed unit files with %h replaced by the fixture
HOME (the manager does not honour an Environment=HOME for %h).  Real
imp-*.service units, root, and system packages are never touched.  The harness
itself (this file, the websockets client) runs on the developer interpreter.

Every installed process (installer, roles, consumer, native unit ExecStart) is launched
through exec-guard.py, a Landlock EXECUTE-only guard (Linux x86_64, ABI>=1, required, no
fallback) that denies execution of host Python/pip to the whole installed process tree while
this parent harness stays unrestricted.
"""

import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "services/relay/src"))

from imp_relay.protocol import encode_action  # noqa: E402,I001 harness-only message encoder
from imp_relay.protocol import StateContext  # noqa: E402
from websockets.asyncio.client import connect  # noqa: E402

GUARD = REPO / "scripts/exec-guard.py"
SYSTEMCTL = shutil.which("systemctl")
ROLES = ("relay", "feed", "gateway")
SPOOL = (
    "IMP2 S s1 1 1 Alpha 1",
    'IMP2 G s1 1 Alpha 2 Char.Status {"character_name":"Alice"}',
)


def die(message):
    print(f"FAIL: {message}", file=sys.stderr)
    sys.exit(1)


def check(condition, message):
    if not condition:
        die(message)


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def guarded_env(work, **extra):
    guard = work / "guard"
    if not guard.exists():
        guard.mkdir()
        for name in ("python", "python3", "python3.12", "pip", "pip3"):
            (guard / name).write_text(
                '#!/bin/sh\necho "target PATH python/pip must not be used by the installed Imp runtime" >&2\nexit 97\n'
            )
            (guard / name).chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")}
    env["PATH"] = f"{guard}:{os.environ['PATH']}"
    env.update(extra)
    return env


def guarded(argv, *allow):
    """Launcher argv that runs argv under the Landlock exec guard, allowing execution beneath each allow dir."""
    return [sys.executable, str(GUARD), *(a for d in allow for a in ("--allow", str(d))), "--", *map(str, argv)]


def require_guard():
    done = subprocess.run([sys.executable, str(GUARD), "--abi"], capture_output=True, text=True)
    check(done.returncode == 0, f"Landlock exec guard unavailable (required): {done.stderr.strip()}")
    print(f"exec guard: Landlock ABI {done.stdout.strip()} (EXECUTE right only)")


def denial_probes(home, work, version):
    """Kernel-denial and positive proofs for the guard; the parent harness stays unrestricted."""
    release = home / ".local/share/imp/releases" / version
    probe_bin = work / "probe-bin"
    probe_bin.mkdir(exist_ok=True)
    names = ("python", "python3", "python3.12", "pip", "pip3")
    for name in names:
        fixture = probe_bin / name
        fixture.write_text("#!/bin/sh\nexit 0\n")
        fixture.chmod(0o755)
        check(subprocess.run([str(fixture)]).returncode == 0, f"unguarded parent cannot run {fixture}")
    check(subprocess.run([sys.executable, "--version"], capture_output=True).returncode == 0, "parent interpreter broken")
    host = {Path(sys.executable), Path(os.path.realpath(sys.executable)), Path(sys._base_executable)}
    for name in names:
        found = shutil.which(name)
        host.update({Path(found), Path(os.path.realpath(found))} if found else ())
        host.update(p for p in (Path(sys.executable).parent / name, Path(sys.base_prefix) / "bin" / name) if p.exists())
        for directory in ("/usr/bin", "/bin", "/usr/local/bin"):
            path = Path(directory) / name
            if path.is_file() and os.access(path, os.X_OK):
                host.update((path, path.resolve()))
    env = guarded_env(work, HOME=str(home))
    env["PATH"] = f"{probe_bin}:{env['PATH']}"
    denied = [*names, *(probe_bin / name for name in names), *sorted(host)]
    for target in denied:
        done = subprocess.run(
            guarded([target, "--version"], home), env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL
        )
        check(
            done.returncode == 126 and "Permission denied" in done.stderr,
            f"guard did not deny {target}: rc={done.returncode} {done.stderr.strip()}",
        )
    ok = subprocess.run(guarded(["uname", "-s"], home), env=env, capture_output=True, text=True)
    check(ok.returncode == 0 and ok.stdout.strip() == "Linux", f"guarded system ELF utility failed: {ok.stderr}")
    server = release / "bin/imp-server"
    ok = subprocess.run(guarded([server, "runtime-check", version], home), env=env, capture_output=True, text=True)
    check(ok.returncode == 0, f"guarded native imp-server runtime-check failed: {ok.stdout}{ok.stderr}")
    roots = Path("/etc/ssl/certs/ca-certificates.crt")
    if roots.exists():
        ok = subprocess.run(guarded(["cat", roots], home), env=env, capture_output=True)
        check(ok.returncode == 0 and ok.stdout == roots.read_bytes(), "guard disturbed system CA reads")
    stub = work / "guard/python"
    if stub.exists():
        check(subprocess.run([str(stub)], capture_output=True).returncode == 97, "PATH stub no longer fails loudly")
    print(
        f"exec guard: {len(denied)} Python/pip targets denied by the KERNEL (rc 126 EACCES, distinct from PATH-stub rc 97); "
        "system ELF + native imp-server allowed; parent unrestricted. Source Python boundary doubles run outside this guard: OK"
    )


def selected_release(home, version):
    release = (home / ".local/share/imp/releases" / version).resolve()
    check(Path(os.path.realpath(home / ".local/share/imp/current")) == release, f"current does not select release {version}")
    check((release / "VERSION").read_text().strip() == version, f"release {release} VERSION is not {version}")
    return release


def check_identity(pid, release, label):
    """The process runs this release's native imp-server and maps libpython only from its onedir.

    Polls: the guard launcher execs the frozen binary shortly after the PID exists."""
    server = os.stat(release / "bin/imp-server")
    deadline = time.monotonic() + 20
    while True:
        try:
            is_server = os.path.samestat(os.stat(f"/proc/{pid}/exe"), server)
            libs = {
                os.path.realpath(fields[5].removesuffix(" (deleted)"))
                for fields in (line.split(maxsplit=5) for line in Path(f"/proc/{pid}/maps").read_text().splitlines())
                if len(fields) == 6 and "libpython" in os.path.basename(fields[5])
            }
        except OSError:
            is_server, libs = False, set()
        if is_server and libs:
            break
        check(time.monotonic() < deadline, f"{label}: pid {pid} never became the selected release's imp-server with libpython")
        time.sleep(0.1)
    shipped = {os.path.realpath(p) for p in (release / "bin/imp-server-runtime").glob("libpython*.so*")}
    check(shipped and libs <= shipped, f"{label}: mapped libpython {sorted(libs)} is not the release onedir's {sorted(shipped)}")


def install_fixture(bundle, work):
    home = work / "home"
    home.mkdir()
    (home / ".tfrc").write_text("/echo startup\n")
    done = subprocess.run(
        guarded([bundle / "install.sh", "--no-start", "--connection", "none"], bundle, home),
        env=guarded_env(work, HOME=str(home)),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    check(done.returncode == 0, f"fixture install failed:\n{done.stdout}{done.stderr}")
    return home


def entrypoints(bundle, version):
    """Shipping inventory contract: regular ELF files, aliases hardlinked to imp-server, no Python payload."""
    bin_dir = bundle / "bin"
    server = bin_dir / "imp-server"
    for name in (
        "relay", "gateway", "feed", "feed-watch", "action-consumer", "direct-wss", "direct-edge", "public-verify",
    ):
        alias = bin_dir / f"imp-{name}"
        check(
            alias.is_file() and not alias.is_symlink() and os.access(alias, os.X_OK),
            f"missing executable alias {alias.name}",
        )
        check(os.path.samestat(alias.stat(), server.stat()), f"{alias.name} is not a hardlink of imp-server")
    check(server.read_bytes()[:4] == b"\x7fELF", "imp-server is not a native executable")
    for banned in ("wheels", ".venv"):
        check(not (bundle / banned).exists() and not (bin_dir / banned).exists(), f"bundle carries {banned}")
    check(
        not any(p.name.endswith(".whl") or p.name == "python" for p in bundle.rglob("*") if p.is_file()),
        "bundle carries a wheel or python interpreter payload",
    )
    print(f"entrypoints {version}: OK")


async def exercise(home, bin_dir, env, run_dir, relay_port, gateway_port, token, version):
    """Real feed -> relay -> gateway data flow plus relay -> action-consumer dispatch."""
    spool = run_dir / "imp/spool"
    for _ in range(100):
        if spool.exists():
            break
        await asyncio.sleep(0.1)
    check(spool.exists(), "frozen feed never created its spool")
    async with connect(f"ws://127.0.0.1:{relay_port}/state", proxy=None) as subscriber:
        hello = json.loads(await subscriber.recv())
        check(hello["relay"]["version"] == version, f"relay hello version {hello['relay']['version']} != {version}")
        with spool.open("a") as handle:
            handle.write("".join(f"{line}\n" for line in SPOOL))
        deadline = time.monotonic() + 15
        seen = False
        while not seen:
            frame = await asyncio.wait_for(subscriber.recv(), timeout=max(deadline - time.monotonic(), 0.1))
            seen = "Alice" in frame
    async with connect(f"ws://127.0.0.1:{gateway_port}/state", proxy=None) as remote:
        await remote.send(json.dumps({"type": "auth", "token": token}))
        frames = [await asyncio.wait_for(remote.recv(), timeout=10)]
        check(json.loads(frames[0]).get("type") == "hello", "gateway did not authenticate the pairing token")
        deadline = time.monotonic() + 10
        while not any("Alice" in frame for frame in frames):
            frames.append(await asyncio.wait_for(remote.recv(), timeout=max(deadline - time.monotonic(), 0.1)))
    consumer = subprocess.Popen(
        guarded(
            [
                home / ".local/bin/imp-action-consumer",
                "--relay-url", f"ws://127.0.0.1:{relay_port}/action-consumer",
                "--session", "s1", "--foreground", "1", "--connection", "1", "--world", "Alpha",
            ],
            home,
        ),
        env=env,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        status = None
        for _ in range(100):
            async with connect(f"ws://127.0.0.1:{relay_port}/action", proxy=None) as action:
                await action.send(encode_action(StateContext("s1", 1, 1), "west"))
                status = json.loads(await action.recv())["status"]
            if status == "forwarded":
                break
            await asyncio.sleep(0.1)
        check(status == "forwarded", f"relay never forwarded the action to the consumer: {status}")
        check(
            consumer.stdout.readline() == "/imp_send s1 1 1 Alpha west\n",
            "consumer did not emit the dispatch",
        )
        check(consumer.wait(timeout=10) == 0, "consumer failed")
    finally:
        if consumer.poll() is None:
            consumer.kill()
            consumer.wait()


def runtime_env(home, work, run_dir):
    return guarded_env(work, HOME=str(home), XDG_RUNTIME_DIR=str(run_dir))


def direct_smoke(home, work, version, token):
    print("=== frozen roles as child processes (guarded PATH, fixture HOME) ===")
    release = selected_release(home, version)
    bin_dir = home / ".local/share/imp/current/bin"
    run_dir = Path(tempfile.mkdtemp(prefix="run-direct-", dir=work))
    run_dir.chmod(0o700)
    relay_port, gateway_port = free_port(), free_port()
    token = token or secrets.token_urlsafe(32)
    env = runtime_env(home, work, run_dir)
    log = (run_dir / "direct.log").open("wb")
    commands = {
        "relay": ([bin_dir / "imp-relay", "--host", "127.0.0.1", "--port", relay_port], {}),
        "feed": ([bin_dir / "imp-feed", "--relay-url", f"ws://127.0.0.1:{relay_port}/ingest"], {}),
        "gateway": (
            [bin_dir / "imp-gateway", "--host", "127.0.0.1", "--port", gateway_port,
             "--relay-url", f"ws://127.0.0.1:{relay_port}"],
            {"IMP_GATEWAY_TOKEN_SHA256": hashlib.sha256(token.encode()).hexdigest()},
        ),
    }
    processes = {}
    try:
        for role, (argv, extra) in commands.items():
            processes[role] = subprocess.Popen(
                guarded([str(part) for part in argv], home), env={**env, **extra}, stdout=log, stderr=log
            )
            if role != "feed":
                port = relay_port if role == "relay" else gateway_port
                for _ in range(100):
                    check(processes[role].poll() is None, f"frozen {role} exited early; see {run_dir / 'direct.log'}")
                    with socket.socket() as probe:
                        if probe.connect_ex(("127.0.0.1", port)) == 0:
                            break
                    time.sleep(0.1)
        for role, process in processes.items():
            check_identity(process.pid, release, f"direct {role}")
        asyncio.run(exercise(home, bin_dir, env, run_dir, relay_port, gateway_port, token, version))
        for role, process in processes.items():
            check(process.poll() is None, f"frozen {role} died during the flow")
    finally:
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
        for role, process in processes.items():
            try:
                code = process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                die(f"frozen {role} ignored SIGTERM")
            check(code in (0, -signal.SIGTERM), f"frozen {role} exit status {code}")
        log.close()
    print("frozen relay -> feed -> gateway -> action-consumer flow: OK")


def user(*args, check_rc=True):
    done = subprocess.run([SYSTEMCTL, "--user", *args], capture_output=True, text=True)
    if check_rc:
        check(done.returncode == 0, f"systemctl --user {' '.join(args)}: {done.stderr.strip()}")
    return done


def unit_files():
    return sorted(
        line for line in user("list-unit-files", "--no-legend", "--plain").stdout.splitlines()
        if line.lstrip().startswith("imp-")
    )


def native_smoke(home, work, version, token):
    print("=== frozen roles under isolated native user systemd units ===")
    probe = subprocess.run([SYSTEMCTL, "--user", "show-environment"], capture_output=True, text=True)
    if probe.returncode != 0 or not os.environ.get("XDG_RUNTIME_DIR"):
        print(f"native user systemd: SKIPPED, manager unavailable ({probe.stderr.strip() or 'no XDG_RUNTIME_DIR'}); limit: no native evidence")
        return
    release = selected_release(home, version)
    runtime = Path(os.environ["XDG_RUNTIME_DIR"])
    unit_dir = runtime / "systemd/user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"imp-frozen-accept-{uuid.uuid4().hex}"
    names = {role: f"{prefix}-{role}.service" for role in ROLES}
    run_dir = Path(tempfile.mkdtemp(prefix="run-native-", dir=work))
    run_dir.chmod(0o700)
    relay_port, gateway_port = free_port(), free_port()
    env = runtime_env(home, work, run_dir)
    gateway_env = home / ".config/imp/gateway.env"
    if token is None:
        token = secrets.token_urlsafe(32)
        gateway_env.write_text(f"IMP_GATEWAY_TOKEN_SHA256={hashlib.sha256(token.encode()).hexdigest()}\n")
        gateway_env.chmod(0o600)
    credential = gateway_env.read_bytes()
    replacements = {"8787": str(relay_port), "8788": str(gateway_port)}
    written = []
    before = unit_files()
    try:
        for role, name in names.items():
            text = (home / ".config/systemd/user" / f"imp-{role}.service").read_text().replace("%h", str(home))
            for old, new in replacements.items():
                text = text.replace(old, new)
            for other in ROLES:
                text = text.replace(f"imp-{other}.service", names[other])
            text = text.replace(
                "[Service]\n",
                f"[Service]\nEnvironment=HOME={home}\nEnvironment=XDG_RUNTIME_DIR={run_dir}\n"
                f"Environment=PATH={work / 'guard'}:/usr/bin:/bin\n",
            ).split("[Install]")[0]
            if role == "feed":
                # The installed feed unit relies on the default relay port; point the fixture at the ephemeral relay.
                text = text.replace(
                    "imp-server feed\n", f"imp-server feed --relay-url ws://127.0.0.1:{relay_port}/ingest\n"
                )
            check(
                re.search(r"(?<![\w-])imp-(relay|feed|gateway)\.service", text) is None,
                f"{role} unit still names a real imp-* service",
            )
            exec_start = next(line for line in text.splitlines() if line.startswith("ExecStart="))
            check(
                exec_start.startswith(f"ExecStart={home}/.local/share/imp/current/bin/imp-server {role} ")
                or exec_start == f"ExecStart={home}/.local/share/imp/current/bin/imp-server {role}",
                f"{role} unit does not run the absolute frozen role: {exec_start}",
            )
            check(".venv" not in text and "python" not in text and "/bin/sh" not in text, f"{role} unit uses Python/shell")
            path = unit_dir / name
            # Test-only launcher: developer interpreter applies the exec guard, then execs the frozen command.
            text = text.replace(exec_start, f"ExecStart={' '.join(guarded(exec_start.removeprefix('ExecStart=').split(), home))}")
            path.write_text(text)
            written.append(path)
        user("daemon-reload")
        for role in ROLES:
            user("start", names[role])
        for role, name in names.items():
            shown = dict(
                line.split("=", 1) for line in user("show", name, "-p", "Id,FragmentPath,ActiveState,MainPID").stdout.splitlines()
            )
            check(
                shown["Id"] == name and shown["FragmentPath"] == str(unit_dir / name) and shown["ActiveState"] == "active",
                f"{name}: unexpected identity/state {shown}",
            )
            check_identity(int(shown["MainPID"]), release, f"{name} MainPID")
            if role != "feed":
                port = relay_port if role == "relay" else gateway_port
                # Type=simple confirms process start, not a bound listening socket.
                for _ in range(100):
                    check(
                        user("is-active", name, check_rc=False).returncode == 0,
                        f"{name} stopped before listening",
                    )
                    with socket.socket() as probe:
                        probe.settimeout(0.1)
                        if probe.connect_ex(("127.0.0.1", port)) == 0:
                            break
                    time.sleep(0.1)
                else:
                    die(f"{name} did not listen on port {port}")
        try:
            asyncio.run(
                exercise(home, home / ".local/share/imp/current/bin", env, run_dir, relay_port, gateway_port, token, version)
            )
        except BaseException:
            # Unit logs only: roles never log the pairing token (only its digest lives in the env file).
            for name in names.values():
                logs = subprocess.run(
                    ["journalctl", "--user", "-u", name, "-n", "15", "--no-pager", "-o", "cat"],
                    capture_output=True,
                    text=True,
                )
                print(f"--- {name}\n{logs.stdout.replace(token, '<token>')}", file=sys.stderr)
            raise
        for name in names.values():
            check(user("is-active", name, check_rc=False).returncode == 0, f"{name} stopped during the flow")
        check(gateway_env.read_bytes() == credential, "gateway.env credential bytes changed during native acceptance")
    finally:
        for name in reversed(list(names.values())):
            user("stop", name, check_rc=False)
        for path in written:
            path.unlink(missing_ok=True)
        user("daemon-reload", check_rc=False)
        for name in names.values():
            user("reset-failed", name, check_rc=False)
    for name in names.values():
        state = user("show", name, "-p", "LoadState", "--value").stdout.strip()
        check(state == "not-found", f"{name} still loaded after cleanup: {state}")
    check(not list(unit_dir.glob(f"{prefix}-*")), "test unit files left behind")
    check(unit_files() == before, "real imp-* unit files changed during native acceptance")
    print(f"native user systemd flow ({prefix}-*): OK")


def verify_installed(home, work, version, token=None, native=True):
    """Guarded direct + native flows for an already-installed HOME; a supplied token never rewrites gateway.env."""
    home, work = Path(home), Path(work)
    require_guard()
    selected_release(home, version)
    denial_probes(home, work, version)
    direct_smoke(home, work, version, token)
    if native:
        native_smoke(home, work, version, token)
    else:
        print("native user systemd: SKIPPED (--no-native-systemd)")



def main(bundle, native):
    work = Path(tempfile.mkdtemp(prefix="imp-frozen-"))
    try:
        version = (bundle / "VERSION").read_text().strip()
        entrypoints(bundle, version)
        require_guard()
        verify_installed(install_fixture(bundle, work), work, version, native=native)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("frozen server acceptance: PASS")


if __name__ == "__main__":
    args = sys.argv[1:]
    flags = [a for a in args if a.startswith("--")]
    paths = [a for a in args if not a.startswith("--")]
    if len(paths) != 1 or set(flags) - {"--no-native-systemd"}:
        sys.exit(f"usage: {sys.argv[0]} BUNDLE_DIR [--no-native-systemd]")
    main(Path(paths[0]).resolve(), "--no-native-systemd" not in flags)
