#!/usr/bin/env bash
set -euo pipefail

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[[ $# -eq 1 ]] || die "usage: $0 ARCHIVE"

ARCHIVE="$(realpath "$1")"

[[ -f "$ARCHIVE" ]] || die "archive not found: $ARCHIVE"

for command in python3.12 tar sha256sum readlink stat; do
  command -v "$command" >/dev/null 2>&1 ||
    die "required command not found: $command"
done

if [[ -f "$ARCHIVE.sha256" ]]; then
  printf '=== outer checksum ===\n'
  (
    cd "$(dirname "$ARCHIVE")"
    sha256sum -c "$(basename "$ARCHIVE").sha256"
  )
fi

WORK_DIR="$(mktemp -d)"
trap 'rm -rf -- "$WORK_DIR"' EXIT

EXTRACT_DIR="$WORK_DIR/extract"
mkdir -p "$EXTRACT_DIR"

tar -xzf "$ARCHIVE" -C "$EXTRACT_DIR"

mapfile -t TOP_LEVEL < <(
  find "$EXTRACT_DIR" \
    -mindepth 1 \
    -maxdepth 1 \
    -type d \
    -print
)

[[ ${#TOP_LEVEL[@]} -eq 1 ]] ||
  die "expected exactly one top-level bundle directory"

BUNDLE_DIR="${TOP_LEVEL[0]}"
VERSION="$(tr -d '\r\n' < "$BUNDLE_DIR/VERSION")"

printf '\n=== bundle ===\n'
printf 'version=%s\n' "$VERSION"
printf 'directory=%s\n' "$BUNDLE_DIR"

(
  cd "$BUNDLE_DIR"
  sha256sum -c SHA256SUMS
)

assert_no_debris() {
  local home="$1"
  local -a debris=()

  mapfile -t debris < <(
    find "$home" \
      -name '.*.tmp.*' \
      -o -name '.*.previous.*' \
      -o -name '.install-backup.*'
  )

  if ((${#debris[@]} != 0)); then
    printf 'unexpected installer debris:\n' >&2
    printf '  %s\n' "${debris[@]}" >&2
    exit 1
  fi
}

printf '\n=== clean install + idempotency ===\n'

HOME_DIR="$WORK_DIR/home"
mkdir -p "$HOME_DIR"

printf '/echo existing TinyFugue startup\n' > "$HOME_DIR/.tfrc"

HOME="$HOME_DIR" \
  "$BUNDLE_DIR/install.sh" \
  --no-start

HOME="$HOME_DIR" \
  "$BUNDLE_DIR/install.sh" \
  --no-start

EXPECTED_RELEASE="$HOME_DIR/.local/share/imp/releases/$VERSION"

[[ "$(readlink "$HOME_DIR/.local/share/imp/current")" == "$EXPECTED_RELEASE" ]] ||
  die "current symlink does not select $VERSION"

[[ "$(
  readlink "$HOME_DIR/.local/bin/imp-action-consumer"
)" == "$HOME_DIR/.local/share/imp/current/.venv/bin/imp-action-consumer" ]] ||
  die "action-consumer symlink is incorrect"

[[ "$(
  readlink "$HOME_DIR/.local/bin/imp-direct-wss"
)" == "$HOME_DIR/.local/share/imp/current/.venv/bin/imp-direct-wss" ]] ||
  die "direct-wss symlink is incorrect"

mapfile -t WEBSOCKETS_WHEELS < <(
  find "$BUNDLE_DIR/wheels" \
    -maxdepth 1 \
    -type f \
    -name 'websockets-*.whl' \
    -print
)

[[ ${#WEBSOCKETS_WHEELS[@]} -eq 1 ]] ||
  die "expected exactly one websockets wheel"

WEBSOCKETS_WHEEL="$(basename "${WEBSOCKETS_WHEELS[0]}")"
EXPECTED_WEBSOCKETS="${WEBSOCKETS_WHEEL#websockets-}"
EXPECTED_WEBSOCKETS="${EXPECTED_WEBSOCKETS%%-*}"

"$HOME_DIR/.local/share/imp/current/.venv/bin/python" \
  - "$VERSION" "$EXPECTED_WEBSOCKETS" <<'PY_VERIFY'
import asyncio
import json
import sys
from importlib.metadata import version

from imp_relay.server import RelayServer
from websockets.asyncio.client import connect

expected_imp = sys.argv[1]
expected_websockets = sys.argv[2]

assert version("imp-relay") == expected_imp
assert version("imp-adapter") == expected_imp
assert version("imp-tinyfugue") == expected_imp
assert version("websockets") == expected_websockets

async def verify_hello_version():
    relay = RelayServer(port=0)
    await relay.start()
    try:
        async with connect(f"ws://127.0.0.1:{relay.port}/state") as subscriber:
            hello = json.loads(await subscriber.recv())
            assert hello["relay"]["version"] == version("imp-relay") == expected_imp
    finally:
        await relay.close()

asyncio.run(verify_hello_version())

print("package and hello versions: OK")
PY_VERIFY

for entry in imp-relay imp-gateway imp-feed imp-action-consumer imp-direct-wss; do
  script="$EXPECTED_RELEASE/.venv/bin/$entry"

  [[ -x "$script" ]] ||
    die "console script is not executable: $entry"

  IFS= read -r shebang < "$script" ||
    die "could not read console script: $entry"

  [[ "$shebang" == '#!'* ]] ||
    die "console script has no shebang: $entry"

  interpreter="${shebang#\#!}"

  [[ "$interpreter" == "$EXPECTED_RELEASE/.venv/bin/"* ]] ||
    die "console script points outside final release: $entry -> $interpreter"

  [[ -x "$interpreter" ]] ||
    die "console script interpreter does not exist: $entry -> $interpreter"
done

printf 'console script interpreters: OK\n'

[[ "$(
  grep -Fxc '/load ~/.config/imp/capture.tf' "$HOME_DIR/.tfrc"
)" -eq 1 ]] ||
  die "TinyFugue load line is not idempotent"

mapfile -t TF_BACKUPS < <(
  find "$HOME_DIR" \
    -maxdepth 1 \
    -type f \
    -name '.tfrc.imp-backup.*' \
    -print
)

[[ ${#TF_BACKUPS[@]} -eq 1 ]] ||
  die "expected exactly one TinyFugue startup backup"

[[ "$(stat -c '%a' "$HOME_DIR/.config/imp")" == "700" ]] ||
  die "Imp config directory mode is not 700"

[[ "$(stat -c '%a' "$HOME_DIR/.local/state/imp")" == "700" ]] ||
  die "Imp state directory mode is not 700"

[[ "$(stat -c '%a' "$HOME_DIR/.config/imp/capture.tf")" == "600" ]] ||
  die "capture.tf mode is not 600"

grep -Fxq \
  'ExecStart=%h/.local/share/imp/current/.venv/bin/imp-relay --host 127.0.0.1 --port 8787' \
  "$HOME_DIR/.config/systemd/user/imp-relay.service"

grep -Fxq \
  'ExecStart=%h/.local/share/imp/current/.venv/bin/imp-feed' \
  "$HOME_DIR/.config/systemd/user/imp-feed.service"

grep -Fxq \
  'ExecStart=%h/.local/share/imp/current/.venv/bin/imp-gateway --host 127.0.0.1 --port 8788 --relay-url ws://127.0.0.1:8787' \
  "$HOME_DIR/.config/systemd/user/imp-gateway.service"

assert_no_debris "$HOME_DIR"
[[ ! -e "$HOME_DIR/.config/imp/gateway.env" ]] ||
  die "ordinary install unexpectedly created gateway credentials"

printf 'clean install: OK\n'
printf 'idempotent reinstall: OK\n'

printf '\n=== credential retention across same-version and distinct-release installs ===\n'

UPGRADE_HOME="$WORK_DIR/upgrade-home"
SYNTHETIC_BUNDLE="$WORK_DIR/synthetic-release"
mkdir -p "$UPGRADE_HOME"
printf '/load ~/.config/imp/capture.tf\n' > "$UPGRADE_HOME/.tfrc"
HOME="$UPGRADE_HOME" "$BUNDLE_DIR/install.sh" --no-start

mkdir -p "$UPGRADE_HOME/.config/imp"
printf 'IMP_GATEWAY_TOKEN_SHA256=%064d\n' 0 \
  > "$UPGRADE_HOME/.config/imp/gateway.env"
CONFIG_SHA="$(sha256sum "$UPGRADE_HOME/.config/imp/gateway.env")"

HOME="$UPGRADE_HOME" "$BUNDLE_DIR/install.sh" --no-start
[[ "$(sha256sum "$UPGRADE_HOME/.config/imp/gateway.env")" == "$CONFIG_SHA" ]] ||
  die "same-version reinstall changed gateway credentials"

cp -a "$BUNDLE_DIR" "$SYNTHETIC_BUNDLE"
SYNTHETIC_VERSION="$(
  python3.12 - "$VERSION" "$SYNTHETIC_BUNDLE" <<'PY_SYNTHETIC_RELEASE'
import base64
import csv
from hashlib import sha256
from io import StringIO
from pathlib import Path
import re
import sys
from zipfile import ZIP_DEFLATED, ZipFile

old_version, root_value = sys.argv[1:]
root = Path(root_value)
parts = old_version.rsplit(".", 1)
new_version = f"{parts[0]}.{int(parts[1]) + 1}"
wheel_dir = root / "wheels"

for wheel in wheel_dir.glob("*.whl"):
    if wheel.name.startswith("websockets-"):
        continue
    rewritten = {}
    with ZipFile(wheel) as source:
        for name in source.namelist():
            renamed = name.replace(f"-{old_version}.dist-info/", f"-{new_version}.dist-info/")
            data = source.read(name)
            if name.endswith(".dist-info/METADATA"):
                data = re.sub(
                    rb"(?m)^Version: " + re.escape(old_version.encode()) + rb"$",
                    f"Version: {new_version}".encode(),
                    data,
                )
            rewritten[renamed] = data

    record_path = next(name for name in rewritten if name.endswith(".dist-info/RECORD"))
    rows = []
    for name, data in rewritten.items():
        if name == record_path:
            rows.append((name, "", ""))
        else:
            digest = base64.urlsafe_b64encode(sha256(data).digest()).rstrip(b"=").decode()
            rows.append((name, f"sha256={digest}", str(len(data))))
    record = StringIO()
    csv.writer(record, lineterminator="\n").writerows(rows)
    rewritten[record_path] = record.getvalue().encode()

    replacement = wheel.with_name(wheel.name.replace(f"-{old_version}-", f"-{new_version}-", 1))
    with ZipFile(replacement, "w", ZIP_DEFLATED) as target:
        for name, data in rewritten.items():
            target.writestr(name, data)
    wheel.unlink()

(root / "VERSION").write_text(new_version + "\n")
files = [
    root / "VERSION",
    root / "install.sh",
    root / "capture.tf",
    *sorted((root / "systemd").glob("*.service")),
    *sorted(wheel_dir.glob("*.whl")),
]
(root / "SHA256SUMS").write_text(
    "".join(
        f"{sha256(path.read_bytes()).hexdigest()}  {path.relative_to(root)}\n"
        for path in files
    )
)
print(new_version)
PY_SYNTHETIC_RELEASE
)"

HOME="$UPGRADE_HOME" "$SYNTHETIC_BUNDLE/install.sh" --no-start
[[ "$(readlink "$UPGRADE_HOME/.local/share/imp/current")" == \
  "$UPGRADE_HOME/.local/share/imp/releases/$SYNTHETIC_VERSION" ]] ||
  die "distinct-release install did not select the new runtime"
[[ "$(readlink "$UPGRADE_HOME/.local/bin/imp-direct-wss")" == \
  "$UPGRADE_HOME/.local/share/imp/current/.venv/bin/imp-direct-wss" ]] ||
  die "distinct-release install did not preserve the stable CLI link"
[[ "$(sha256sum "$UPGRADE_HOME/.config/imp/gateway.env")" == "$CONFIG_SHA" ]] ||
  die "distinct-release install changed gateway credentials"
printf 'credential retention and synthetic release selection: OK\n'

printf '\n=== installed Direct WSS setup, rotation, and status ===\n'

python3.12 - "$HOME_DIR" "$BUNDLE_DIR" "$SYNTHETIC_BUNDLE" <<'PY_DIRECT_WSS_BUNDLE'
import hashlib
import json
import os
from pathlib import Path
import pty
import re
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time

home, bundle, synthetic = map(Path, sys.argv[1:])
runtime = home / ".local/share/imp/current/.venv/bin"
cli = home / ".local/bin/imp-direct-wss"
unit_path = home / ".config/systemd/user/imp-gateway.service"
config_path = home / ".config/imp/gateway.env"
state = {
    "enabled": False,
    "gateway": None,
    "gateway_release": None,
    "fail_next_gateway_start": False,
    "starts": 0,
    "relay": False,
    "feed": False,
}
state_lock = threading.Lock()
temporary = tempfile.TemporaryDirectory()
socket_path = str(Path(temporary.name) / "systemd.sock")
server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(socket_path)
server.listen()
server.settimeout(0.2)
events = []
relay = None
stop_server = threading.Event()


def stop_gateway():
    process = state["gateway"]
    state["gateway"] = None
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def start_gateway():
    stop_gateway()
    assignment = config_path.read_text().strip().split("=", 1)[1]
    if state["fail_next_gateway_start"]:
        assignment = "0" * 64
        state["fail_next_gateway_start"] = False
    env = os.environ.copy()
    env["IMP_GATEWAY_TOKEN_SHA256"] = assignment
    binary = os.path.realpath(runtime / "imp-gateway")
    state["gateway_release"] = Path(binary).parent.parent.parent.name
    process = subprocess.Popen(
        [
            binary,
            "--host", "127.0.0.1", "--port", "8788",
            "--relay-url", "ws://127.0.0.1:8787",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    state["gateway"] = process
    state["starts"] += 1
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("disposable gateway exited during startup")
        try:
            with socket.create_connection(("127.0.0.1", 8788), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("disposable gateway did not listen")


def active(unit):
    if unit == "imp-gateway.service":
        process = state["gateway"]
        return process is not None and process.poll() is None
    return state["relay"] if unit == "imp-relay.service" else state["feed"]


def handle(arguments):
    args = arguments[1:] if arguments and arguments[0] == "--user" else arguments
    if len(args) >= 2 and args[0] == "show":
        binary = str(home / ".local/share/imp/current/.venv/bin/imp-gateway")
        command = [binary, "--host", "127.0.0.1", "--port", "8788",
                   "--relay-url", "ws://127.0.0.1:8787"]
        exec_start = f"{{ path={binary} ; argv[]={' '.join(command)} ; ignore_errors=no }}"
        values = {
            "LoadState": "loaded",
            "FragmentPath": str(unit_path),
            "DropInPaths": "",
            "NeedDaemonReload": "no",
            "Type": "simple",
            "ExecStart": exec_start,
            "EnvironmentFiles": (
                f"{home}/.config/imp/gateway.env (ignore_errors=no)"
            ),
            "Restart": "on-failure",
            "RestartUSec": "2s",
            "NoNewPrivileges": "yes",
            "UnitFileState": "enabled" if state["enabled"] else "disabled",
            "ActiveState": "active" if active("imp-gateway.service") else "inactive",
        }
        return 0, "".join(f"{key}={value}\n" for key, value in values.items())

    operation = args[0] if args else ""
    unit = args[-1] if args else ""
    events.append(tuple(args))
    if operation == "is-active":
        return (0 if active(unit) else 3), ""
    if operation == "is-enabled":
        if unit == "imp-gateway.service":
            enabled = state["enabled"]
        else:
            enabled = state["relay"] if unit == "imp-relay.service" else state["feed"]
        return (0 if enabled else 1), ""
    if operation == "enable":
        for name in args[1:]:
            if name == "--now":
                continue
            if name == "imp-gateway.service":
                state["enabled"] = True
            elif name == "imp-relay.service":
                state["relay"] = True
            elif name == "imp-feed.service":
                state["feed"] = True
        if "--now" in args:
            start_gateway()
    elif operation == "disable":
        if unit == "imp-gateway.service":
            state["enabled"] = False
        elif unit == "imp-relay.service":
            state["relay"] = False
        elif unit == "imp-feed.service":
            state["feed"] = False
    elif operation == "restart":
        if unit == "imp-gateway.service":
            start_gateway()
        elif unit == "imp-relay.service":
            state["relay"] = True
        elif unit == "imp-feed.service":
            state["feed"] = True
    elif operation == "stop":
        if unit == "imp-gateway.service":
            stop_gateway()
        elif unit == "imp-relay.service":
            state["relay"] = False
        elif unit == "imp-feed.service":
            state["feed"] = False
    return 0, ""


def serve():
    while not stop_server.is_set():
        try:
            connection, _ = server.accept()
        except socket.timeout:
            continue
        with connection:
            request = json.loads(connection.makefile("rb").readline())
            try:
                with state_lock:
                    code, output = handle(request)
                response = {"code": code, "output": output}
            except Exception:
                response = {"code": 1, "output": ""}
            connection.sendall(json.dumps(response).encode() + b"\n")


client = Path(temporary.name) / "systemctl"
client.write_text(
    "#!/usr/bin/env python3\n"
    "import json, os, socket, sys\n"
    "s=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
    "s.connect(os.environ['IMP_TEST_SYSTEMD_SOCKET'])\n"
    "s.sendall(json.dumps(sys.argv[1:]).encode()+b'\\n')\n"
    "r=json.loads(s.makefile('rb').readline())\n"
    "sys.stdout.write(r['output'])\n"
    "raise SystemExit(r['code'])\n"
)
client.chmod(0o755)
server_thread = threading.Thread(target=serve, daemon=True)
server_thread.start()
environment = os.environ.copy()
environment.update(
    HOME=str(home),
    PATH=f"{temporary.name}:{os.environ['PATH']}",
    IMP_TEST_SYSTEMD_SOCKET=socket_path,
)
environment.pop("PYTHONPATH", None)
environment.pop("PYTHONHOME", None)
environment.pop("IMP_GATEWAY_TOKEN_SHA256", None)


def run_tty(command, expected=0):
    master, slave = pty.openpty()
    process = subprocess.Popen(
        [str(arg) for arg in command],
        env=environment,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        close_fds=True,
    )
    os.close(slave)
    output = bytearray()
    deadline = time.monotonic() + 40
    timed_out = False
    try:
        while True:
            if not timed_out and time.monotonic() >= deadline and process.poll() is None:
                timed_out = True
                process.kill()
                process.wait()
            ready, _, _ = select.select([master], [], [], 0.2)
            if not ready:
                continue
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            output.extend(chunk)
        status = process.wait()
        if timed_out:
            raise AssertionError("CLI timed out")
        if status != expected:
            raise AssertionError(f"CLI returned {status}, expected {expected}")
        return bytes(output)
    finally:
        os.close(master)
        if process.poll() is None:
            process.kill()
        process.wait()


def token_from(output):
    values = re.findall(
        rb"(?:Pairing token:|Direct WSS pairing token \(shown once; not stored\):\r?\n)"
        rb"([A-Za-z0-9_-]{43})(?:\r?\n|$)",
        output,
    )
    if len(values) != 1:
        raise AssertionError("successful PTY handoff did not contain exactly one token")
    token = values[0]
    digest = config_path.read_text().strip().split("=", 1)[1]
    if hashlib.sha256(token).hexdigest() != digest:
        raise AssertionError("terminal token did not match the stored digest")
    return token


try:
    for port in (8787, 8788):
        try:
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("127.0.0.1", port))
        except OSError:
            print(f"Direct WSS PTY integration requires free loopback port {port}", file=sys.stderr)
            raise SystemExit(1)
    relay = subprocess.Popen(
        [str(runtime / "imp-relay"), "--host", "127.0.0.1", "--port", "8787"],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 5
    relay_ready = False
    while time.monotonic() < deadline and relay.poll() is None:
        try:
            with socket.create_connection(("127.0.0.1", 8787), timeout=0.1):
                relay_ready = True
                break
        except OSError:
            time.sleep(0.05)
    if relay.poll() is not None or not relay_ready:
        raise AssertionError("disposable relay did not become ready")
    setup_event_start = len(events)
    state["fail_next_gateway_start"] = True
    failed_setup = run_tty([cli, "setup"], expected=1)
    assert not re.search(
        rb"(?:Pairing token:|Direct WSS pairing token \(shown once; not stored\):\r?\n)"
        rb"[A-Za-z0-9_-]{43}",
        failed_setup,
    )
    assert not config_path.exists()
    assert not state["enabled"] and not active("imp-gateway.service")
    setup_actions = [
        event for event in events[setup_event_start:]
        if event and event[0] in {"enable", "disable", "restart", "stop"}
    ]
    assert setup_actions == [
        ("enable", "--now", "imp-gateway.service"),
        ("stop", "imp-gateway.service"),
        ("disable", "imp-gateway.service"),
    ]

    token = token_from(run_tty([cli, "setup"]))
    initial_config = config_path.read_bytes()
    assert b"=" in initial_config and token not in initial_config
    assert state["enabled"] and active("imp-gateway.service")
    assert state["starts"] == 2

    log_count = len(events)

    repeated = run_tty([cli, "setup"])
    assert token not in repeated
    assert config_path.read_bytes() == initial_config
    assert len(events) == log_count

    config_path.write_bytes(b"IMP_GATEWAY_TOKEN_SHA256=broken\n")
    malformed = config_path.read_bytes()
    log_count = len(events)
    run_tty([cli, "setup"], expected=1)
    assert config_path.read_bytes() == malformed
    assert len(events) == log_count
    config_path.write_bytes(initial_config)

    before_reinstall = config_path.read_bytes()
    completed = subprocess.run(
        [str(bundle / "install.sh"), "--no-start"],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    assert completed.returncode == 0
    assert config_path.read_bytes() == before_reinstall

    rotated = token_from(run_tty([cli, "rotate"]))
    assert rotated != token
    assert state["starts"] == 3 and active("imp-gateway.service")

    before_failed_rotation = config_path.read_bytes()
    failure_event_start = len(events)
    starts_before_failure = state["starts"]
    state["fail_next_gateway_start"] = True
    failed_output = run_tty([cli, "rotate"], expected=1)
    assert not re.search(
        rb"(?:Pairing token:|Direct WSS pairing token \(shown once; not stored\):\r?\n)"
        rb"[A-Za-z0-9_-]{43}",
        failed_output,
    )
    assert config_path.read_bytes() == before_failed_rotation
    assert state["starts"] == starts_before_failure + 2
    assert active("imp-gateway.service") and state["enabled"]
    failure_actions = [
        event for event in events[failure_event_start:]
        if event and event[0] in {"enable", "disable", "restart", "stop"}
    ]
    assert failure_actions == [
        ("restart", "imp-gateway.service"),
        ("restart", "imp-gateway.service"),
        ("enable", "imp-gateway.service"),
    ]

    import asyncio
    import logging

    sys.path.insert(0, str(runtime.parent / "lib/python3.12/site-packages"))
    from websockets.asyncio.client import connect

    async def verify_restored_credentials():
        async with connect(
            "ws://127.0.0.1:8788/state",
            proxy=None,
            logger=logging.getLogger("bundle-acceptance"),
        ) as connection:
            await connection.send(json.dumps({"type": "auth", "token": rotated.decode()}))
            hello = json.loads(await connection.recv())
            assert hello.get("type") == "hello" and hello.get("protocol") == 2

    asyncio.run(verify_restored_credentials())


    before_upgrade = config_path.read_bytes()
    upgraded = subprocess.run(
        [str(synthetic / "install.sh")],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    assert upgraded.returncode == 0
    assert config_path.read_bytes() == before_upgrade
    assert state["gateway_release"] == (synthetic / "VERSION").read_text().strip()
    assert state["starts"] == 6 and active("imp-gateway.service") and state["enabled"]

    manager = subprocess.run(
        [str(client), "--user", "stop", "imp-gateway.service"],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    assert manager.returncode == 0
    assert not active("imp-gateway.service") and state["enabled"]
    starts = state["starts"]
    mutations_before_inactive_rotate = [
        event for event in events
        if event and event[0] in {"enable", "disable", "restart", "stop"}
    ]

    inactive_token = token_from(run_tty([cli, "rotate"]))
    assert inactive_token != rotated
    mutations_after_inactive_rotate = [
        event for event in events
        if event and event[0] in {"enable", "disable", "restart", "stop"}
    ]
    assert mutations_after_inactive_rotate == mutations_before_inactive_rotate
    assert state["starts"] == starts
    assert not active("imp-gateway.service") and state["enabled"]

    status_before = config_path.stat()
    lock_before = (config_path.parent / "gateway.env.lock").stat()
    status = subprocess.run(
        [str(cli), "status"],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert status.returncode == 0
    assert inactive_token not in status.stdout and inactive_token not in status.stderr
    assert config_path.stat().st_mtime_ns == status_before.st_mtime_ns
    assert (config_path.parent / "gateway.env.lock").stat().st_mtime_ns == lock_before.st_mtime_ns

    # Ubuntu user-private-group layout: 0775 ancestors under a 0700 ~/.config/imp.
    # The account database is substituted inside the installed interpreter so the
    # verdict cannot depend on the runner's real passwd/group contents.
    ancestors = {path: path.stat().st_mode & 0o7777 for path in (home / ".config", home / ".config/systemd", home / ".config/systemd/user")}
    account_cli = (
        "import grp, os, pwd, sys\n"
        "from imp_relay import direct_wss\n"
        "uid, gid = os.geteuid(), os.stat(os.path.expanduser('~/.config')).st_gid\n"
        "user = pwd.struct_passwd(('imp-user', 'x', uid, gid, '', '', ''))\n"
        "group = grp.struct_group(('imp-user', 'x', gid, sys.argv[2:]))\n"
        "pwd.getpwuid = lambda value: {uid: user}[value]\n"
        "pwd.getpwall = lambda: [user]\n"
        "grp.getgrgid = lambda value: {gid: group}[value]\n"
        "grp.getgrall = lambda: [group]\n"
        "raise SystemExit(direct_wss.main([sys.argv[1]]))\n"
    )
    shared = "~/.config is group-writable by a group not shown private to the target user"
    try:
        for directory in ancestors:
            directory.chmod(0o775)
        for members, path_line, syntax_line, policy_line in (
            (
                ["imp-user"],
                "gateway.env path: supported",
                "gateway.env syntax: valid",
                "gateway unit policy: supported",
            ),
            (
                ["imp-user", "other"],
                f"gateway.env path: unsafe ({shared})",
                "gateway.env syntax: not inspected",
                f"gateway unit policy: unsupported ({shared})",
            ),
        ):
            private_status = subprocess.run(
                [str(runtime / "python"), "-c", account_cli, "status", *members],
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            assert private_status.returncode == 0, private_status.stderr
            lines = private_status.stdout.splitlines()
            assert path_line in lines and syntax_line in lines and policy_line in lines, lines
            assert inactive_token.decode() not in private_status.stdout + private_status.stderr
        assert config_path.stat().st_mtime_ns == status_before.st_mtime_ns

        # Installed-code rotate under the same layout (gateway enabled + stopped here).
        before_rotate = config_path.read_bytes()
        private_token = token_from(run_tty([runtime / "python", "-c", account_cli, "rotate", "imp-user"]))
        assert private_token != inactive_token
        assert config_path.read_bytes() != before_rotate
        assert private_token not in config_path.read_bytes()
        assert config_path.stat().st_mode & 0o7777 == 0o600
        assert config_path.parent.stat().st_mode & 0o7777 == 0o700
        assert not active("imp-gateway.service") and state["enabled"]
    finally:
        for directory, mode in ancestors.items():
            directory.chmod(mode)
    print("private-group ancestor status and rotate: OK")
finally:
    with state_lock:
        stop_gateway()
    stop_server.set()
    server_thread.join(timeout=2)
    server.close()
    if relay is not None and relay.poll() is None:
        relay.terminate()
        try:
            relay.wait(timeout=5)
        except subprocess.TimeoutExpired:
            relay.kill()
            relay.wait()
    temporary.cleanup()
PY_DIRECT_WSS_BUNDLE

printf '\n=== custom TinyFugue startup override ===\n'

CUSTOM_HOME="$WORK_DIR/custom-home"
mkdir -p "$CUSTOM_HOME"

printf '/echo default startup untouched\n' > "$CUSTOM_HOME/.tfrc"
printf '/echo custom startup\n' > "$CUSTOM_HOME/custom.tf"

HOME="$CUSTOM_HOME" \
  "$BUNDLE_DIR/install.sh" \
  --no-start \
  --tf-startup "$CUSTOM_HOME/custom.tf"

grep -Fxq \
  '/load ~/.config/imp/capture.tf' \
  "$CUSTOM_HOME/custom.tf" ||
  die "custom TinyFugue startup did not receive load line"

[[ "$(grep -Fxc '/load ~/.config/imp/capture.tf' "$CUSTOM_HOME/.tfrc" || true)" -eq 0 ]] ||
  die "custom startup override unexpectedly modified ~/.tfrc"

printf 'custom TinyFugue startup override: OK\n'

printf '\n=== missing default TinyFugue startup ===\n'

MISSING_HOME="$WORK_DIR/missing-home"
mkdir -p "$MISSING_HOME"

if HOME="$MISSING_HOME" \
   "$BUNDLE_DIR/install.sh" \
   --no-start \
   >"$WORK_DIR/missing-startup.out" 2>&1; then
  cat "$WORK_DIR/missing-startup.out" >&2
  die "missing default TinyFugue startup unexpectedly succeeded"
fi

grep -Fq \
  'default TinyFugue startup file does not exist:' \
  "$WORK_DIR/missing-startup.out" ||
  {
    cat "$WORK_DIR/missing-startup.out" >&2
    die "missing default startup produced the wrong failure"
  }

[[ ! -e "$MISSING_HOME/.tfrc" ]] ||
  die "installer unexpectedly created ~/.tfrc"

[[ ! -e "$MISSING_HOME/.local/share/imp" ]] ||
  die "missing default startup changed the installation home"

printf 'missing default TinyFugue startup rejection: OK\n'

printf '\n=== tamper rejection ===\n'

TAMPER_BUNDLE="$WORK_DIR/tampered"
TAMPER_HOME="$WORK_DIR/tamper-home"

cp -a "$BUNDLE_DIR" "$TAMPER_BUNDLE"
mkdir -p "$TAMPER_HOME"

printf '\n; tampered\n' >> "$TAMPER_BUNDLE/capture.tf"

if HOME="$TAMPER_HOME" \
   "$TAMPER_BUNDLE/install.sh" \
   --no-start \
   >"$WORK_DIR/tamper.out" 2>&1; then
  cat "$WORK_DIR/tamper.out" >&2
  die "tampered bundle unexpectedly installed"
fi

grep -Fq 'capture.tf: FAILED' "$WORK_DIR/tamper.out" ||
  {
    cat "$WORK_DIR/tamper.out" >&2
    die "tamper failure did not come from checksum verification"
  }

[[ ! -e "$TAMPER_HOME/.local/share/imp" ]] ||
  die "tampered bundle changed the installation home"

printf 'tamper rejection: OK\n'

printf '\n=== unlisted bundle content rejection ===\n'

EXTRA_BUNDLE="$WORK_DIR/extra-content"
EXTRA_HOME="$WORK_DIR/extra-home"

cp -a "$BUNDLE_DIR" "$EXTRA_BUNDLE"
mkdir -p "$EXTRA_HOME"

printf 'unlisted\n' > "$EXTRA_BUNDLE/wheels/unlisted.whl"

if HOME="$EXTRA_HOME" \
   "$EXTRA_BUNDLE/install.sh" \
   --no-start \
   >"$WORK_DIR/extra-content.out" 2>&1; then
  cat "$WORK_DIR/extra-content.out" >&2
  die "bundle with unlisted content unexpectedly installed"
fi

grep -Fq \
  'bundle contents do not match SHA256SUMS:' \
  "$WORK_DIR/extra-content.out" ||
  {
    cat "$WORK_DIR/extra-content.out" >&2
    die "unlisted content produced the wrong failure"
  }

[[ ! -e "$EXTRA_HOME/.local/share/imp" ]] ||
  die "unlisted bundle content changed the installation home"

printf 'unlisted bundle content rejection: OK\n'

printf '\n=== unsafe Direct WSS link rejection ===\n'

LINK_HOME="$WORK_DIR/unsafe-link-home"
mkdir -p "$LINK_HOME/.local/bin"
printf '/echo startup\n' > "$LINK_HOME/.tfrc"
ln -s /tmp/unrelated-command "$LINK_HOME/.local/bin/imp-direct-wss"

if HOME="$LINK_HOME" "$BUNDLE_DIR/install.sh" --no-start \
  >/dev/null 2>&1; then
  die "installer accepted an unrelated imp-direct-wss symlink"
fi

[[ "$(readlink "$LINK_HOME/.local/bin/imp-direct-wss")" == /tmp/unrelated-command ]] ||
  die "unsafe imp-direct-wss symlink was modified"
[[ ! -e "$LINK_HOME/.local/share/imp" ]] ||
  die "unsafe imp-direct-wss link rejection changed the install home"

printf '\n=== failed activation rollback ===\n'

ROLLBACK_HOME="$WORK_DIR/rollback-home"
FAKE_BIN="$WORK_DIR/fake-bin"

mkdir -p "$ROLLBACK_HOME" "$FAKE_BIN"

printf '/load ~/.config/imp/capture.tf\n' > "$ROLLBACK_HOME/.tfrc"

HOME="$ROLLBACK_HOME" \
  "$BUNDLE_DIR/install.sh" \
  --no-start

printf 'known-good\n' \
  > "$ROLLBACK_HOME/.local/share/imp/releases/$VERSION/rollback-marker"

printf 'old-capture\n' \
  > "$ROLLBACK_HOME/.config/imp/capture.tf"

printf 'old-relay-unit\n' \
  > "$ROLLBACK_HOME/.config/systemd/user/imp-relay.service"

printf 'old-feed-unit\n' \
  > "$ROLLBACK_HOME/.config/systemd/user/imp-feed.service"

printf 'old-gateway-unit\n' \
  > "$ROLLBACK_HOME/.config/systemd/user/imp-gateway.service"

ln -sfn /tmp/old-action-consumer \
  "$ROLLBACK_HOME/.local/bin/imp-action-consumer"


printf '/echo original startup\n' > "$ROLLBACK_HOME/.tfrc"

ROLLBACK_SYSTEMCTL_LOG="$WORK_DIR/rollback-systemctl.log"
ROLLBACK_RELAY_FAIL_MARKER="$WORK_DIR/rollback-relay-failed-once"

: > "$ROLLBACK_SYSTEMCTL_LOG"
ROLLBACK_SYSTEMCTL_STATE="$WORK_DIR/rollback-systemctl-state"
mkdir -p "$ROLLBACK_SYSTEMCTL_STATE"
for unit in imp-relay.service imp-feed.service imp-gateway.service; do
  : > "$ROLLBACK_SYSTEMCTL_STATE/active-$unit"
done
for unit in imp-relay.service imp-feed.service; do
  : > "$ROLLBACK_SYSTEMCTL_STATE/enabled-$unit"
done


cat > "$FAKE_BIN/systemctl" <<'SH_SYSTEMCTL'
#!/usr/bin/env bash
set -eu

printf '%s\n' "$*" >> "${IMP_TEST_SYSTEMCTL_LOG:?}"
state="${IMP_TEST_SYSTEMCTL_STATE:?}"

case "$2" in
  is-active)
    [[ -e "$state/active-${4:?}" ]]
    ;;
  is-enabled)
    [[ -e "$state/enabled-${4:?}" ]]
    ;;
  enable)
    for unit in "${@:3}"; do : > "$state/enabled-$unit"; done
    ;;
  disable)
    for unit in "${@:3}"; do rm -f "$state/enabled-$unit"; done
    ;;
  restart)
    unit="${3:?}"
    if [[ "$unit" == imp-relay.service &&
          ! -e "${IMP_TEST_RELAY_FAIL_MARKER:?}" ]]; then
      : > "$IMP_TEST_RELAY_FAIL_MARKER"
      exit 1
    fi
    : > "$state/active-$unit"
    ;;
  stop)
    rm -f "$state/active-${3:?}"
    ;;
esac
SH_SYSTEMCTL

chmod +x "$FAKE_BIN/systemctl"

printf '\n=== ordinary install service state ===\n'

START_HOME="$WORK_DIR/start-home"
START_STATE="$WORK_DIR/start-state"
mkdir -p "$START_HOME" "$START_STATE"
printf '/load ~/.config/imp/capture.tf\n' > "$START_HOME/.tfrc"
: > "$START_STATE/enabled-imp-gateway.service"
: > "$WORK_DIR/no-relay-failure"
: > "$WORK_DIR/start-systemctl.log"

HOME="$START_HOME" \
  PATH="$FAKE_BIN:$PATH" \
  IMP_TEST_SYSTEMCTL_LOG="$WORK_DIR/start-systemctl.log" \
  IMP_TEST_SYSTEMCTL_STATE="$START_STATE" \
  IMP_TEST_RELAY_FAIL_MARKER="$WORK_DIR/no-relay-failure" \
  "$BUNDLE_DIR/install.sh" \
  >"$WORK_DIR/start.out" 2>&1

[[ -e "$START_STATE/active-imp-relay.service" &&
   -e "$START_STATE/active-imp-feed.service" ]] ||
  die "ordinary install did not activate relay and feed"
[[ -e "$START_STATE/enabled-imp-relay.service" &&
   -e "$START_STATE/enabled-imp-feed.service" ]] ||
  die "ordinary install did not enable relay and feed"
[[ -e "$START_STATE/enabled-imp-gateway.service" &&
   ! -e "$START_STATE/active-imp-gateway.service" ]] ||
  die "ordinary install changed gateway enabled/active state"
[[ ! -e "$START_HOME/.config/imp/gateway.env" ]] ||
  die "ordinary install unexpectedly created gateway credentials"

for call in enable start restart stop disable; do
  if grep -E -- "--user $call( .*|$)imp-gateway.service" \
    "$WORK_DIR/start-systemctl.log"; then
    die "ordinary install mutated gateway service state"
  fi
done

INACTIVE_DISABLED_HOME="$WORK_DIR/inactive-disabled-home"
INACTIVE_DISABLED_STATE="$WORK_DIR/inactive-disabled-state"
mkdir -p "$INACTIVE_DISABLED_HOME" "$INACTIVE_DISABLED_STATE"
printf '/load ~/.config/imp/capture.tf\n' > "$INACTIVE_DISABLED_HOME/.tfrc"
HOME="$INACTIVE_DISABLED_HOME" \
  PATH="$FAKE_BIN:$PATH" \
  IMP_TEST_SYSTEMCTL_LOG="$WORK_DIR/inactive-disabled.log" \
  IMP_TEST_SYSTEMCTL_STATE="$INACTIVE_DISABLED_STATE" \
  IMP_TEST_RELAY_FAIL_MARKER="$WORK_DIR/no-relay-failure" \
  "$BUNDLE_DIR/install.sh" \
  >"$WORK_DIR/inactive-disabled.out" 2>&1
[[ ! -e "$INACTIVE_DISABLED_STATE/active-imp-gateway.service" &&
   ! -e "$INACTIVE_DISABLED_STATE/enabled-imp-gateway.service" ]] ||
  die "ordinary install changed inactive/disabled gateway state"

ACTIVE_DISABLED_HOME="$WORK_DIR/active-disabled-home"
ACTIVE_DISABLED_STATE="$WORK_DIR/active-disabled-state"
mkdir -p "$ACTIVE_DISABLED_HOME" "$ACTIVE_DISABLED_STATE"
printf '/load ~/.config/imp/capture.tf\n' > "$ACTIVE_DISABLED_HOME/.tfrc"
: > "$ACTIVE_DISABLED_STATE/active-imp-gateway.service"
HOME="$ACTIVE_DISABLED_HOME" \
  PATH="$FAKE_BIN:$PATH" \
  IMP_TEST_SYSTEMCTL_LOG="$WORK_DIR/active-disabled.log" \
  IMP_TEST_SYSTEMCTL_STATE="$ACTIVE_DISABLED_STATE" \
  IMP_TEST_RELAY_FAIL_MARKER="$WORK_DIR/no-relay-failure" \
  "$BUNDLE_DIR/install.sh" \
  >"$WORK_DIR/active-disabled.out" 2>&1
[[ -e "$ACTIVE_DISABLED_STATE/active-imp-gateway.service" &&
   ! -e "$ACTIVE_DISABLED_STATE/enabled-imp-gateway.service" ]] ||
  die "ordinary install changed active/disabled gateway state"
grep -Fxq -- '--user restart imp-gateway.service' "$WORK_DIR/active-disabled.log" ||
  die "ordinary install did not restart an active gateway"
for call in enable disable; do
  if grep -Fxq -- "--user $call imp-gateway.service" "$WORK_DIR/active-disabled.log"; then
    die "ordinary install changed gateway enablement"
  fi
done


if HOME="$ROLLBACK_HOME" \
   PATH="$FAKE_BIN:$PATH" \
   IMP_TEST_SYSTEMCTL_LOG="$ROLLBACK_SYSTEMCTL_LOG" \
   IMP_TEST_SYSTEMCTL_STATE="$ROLLBACK_SYSTEMCTL_STATE" \
   IMP_TEST_RELAY_FAIL_MARKER="$ROLLBACK_RELAY_FAIL_MARKER" \
   "$BUNDLE_DIR/install.sh" \
   >"$WORK_DIR/rollback.out" 2>&1; then
  cat "$WORK_DIR/rollback.out" >&2
  die "failed-activation test unexpectedly succeeded"
fi

grep -Fq \
  'Restoring previous Imp installation after failed activation...' \
  "$WORK_DIR/rollback.out" ||
  {
    cat "$WORK_DIR/rollback.out" >&2
    die "rollback path did not execute"
  }

for expected_call in \
  '--user enable imp-relay.service' \
  '--user enable imp-feed.service' \
  '--user restart imp-relay.service' \
  '--user restart imp-feed.service' \
  '--user restart imp-gateway.service'; do
  grep -Fxq -- "$expected_call" "$ROLLBACK_SYSTEMCTL_LOG" ||
    die "rollback did not restore service state: $expected_call"
done

for unexpected_call in \
  '--user disable imp-relay.service' \
  '--user disable imp-feed.service' \
  '--user stop imp-relay.service' \
  '--user stop imp-feed.service'; do
  if grep -Fxq -- "$unexpected_call" "$ROLLBACK_SYSTEMCTL_LOG"; then
    die "rollback restored the wrong service state: $unexpected_call"
  fi
done

printf 'service-state rollback: OK\n'

grep -Fxq \
  known-good \
  "$ROLLBACK_HOME/.local/share/imp/releases/$VERSION/rollback-marker"

[[ "$(
  readlink "$ROLLBACK_HOME/.local/share/imp/current"
)" == "$ROLLBACK_HOME/.local/share/imp/releases/$VERSION" ]] ||
  die "runtime rollback failed"

grep -Fxq old-capture \
  "$ROLLBACK_HOME/.config/imp/capture.tf"

grep -Fxq old-relay-unit \
  "$ROLLBACK_HOME/.config/systemd/user/imp-relay.service"

grep -Fxq old-feed-unit \
  "$ROLLBACK_HOME/.config/systemd/user/imp-feed.service"

grep -Fxq old-gateway-unit \
  "$ROLLBACK_HOME/.config/systemd/user/imp-gateway.service"

[[ "$(
  readlink "$ROLLBACK_HOME/.local/bin/imp-action-consumer"
)" == "/tmp/old-action-consumer" ]] ||
  die "action-consumer link rollback failed"

grep -Fxq \
  '/echo original startup' \
  "$ROLLBACK_HOME/.tfrc"

[[ "$(
  grep -Fxc '/load ~/.config/imp/capture.tf' "$ROLLBACK_HOME/.tfrc" ||
    true
)" -eq 0 ]] ||
  die "TinyFugue startup rollback failed"

mapfile -t ROLLBACK_BACKUPS < <(
  find "$ROLLBACK_HOME" \
    -maxdepth 1 \
    -name '.tfrc.imp-backup.*' \
    -print
)

[[ ${#ROLLBACK_BACKUPS[@]} -eq 0 ]] ||
  die "failed install left a TinyFugue startup backup"

assert_no_debris "$ROLLBACK_HOME"

[[ -e "$ROLLBACK_SYSTEMCTL_STATE/active-imp-relay.service" &&
   -e "$ROLLBACK_SYSTEMCTL_STATE/active-imp-feed.service" &&
   -e "$ROLLBACK_SYSTEMCTL_STATE/active-imp-gateway.service" ]] ||
  die "fake systemd did not restore the previous active state"
[[ -e "$ROLLBACK_SYSTEMCTL_STATE/enabled-imp-relay.service" &&
   -e "$ROLLBACK_SYSTEMCTL_STATE/enabled-imp-feed.service" &&
   ! -e "$ROLLBACK_SYSTEMCTL_STATE/enabled-imp-gateway.service" ]] ||
  die "fake systemd did not retain the previous enabled state"

[[ "$(
  readlink "$ROLLBACK_HOME/.local/bin/imp-direct-wss"
)" == "$ROLLBACK_HOME/.local/share/imp/current/.venv/bin/imp-direct-wss" ]] ||
  die "direct-wss link rollback failed"

printf 'runtime rollback: OK\n'
printf 'integration-file rollback: OK\n'
printf 'TinyFugue startup rollback: OK\n'

printf 'Fake systemd models state and launches real CLI-fixture gateway/relay processes; it does not validate real user-systemd behavior.\n'
printf '\nserver bundle acceptance: PASS\n'
