#!/usr/bin/env bash
set -euo pipefail

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[[ $# -ge 1 && $# -le 2 ]] || die "usage: $0 ARCHIVE [NEXT_ARCHIVE]"

ARCHIVE="$(realpath "$1")"
NEXT_ARCHIVE=""
[[ $# -eq 2 ]] && NEXT_ARCHIVE="$(realpath "$2")"

[[ -f "$ARCHIVE" ]] || die "archive not found: $ARCHIVE"
[[ -z "$NEXT_ARCHIVE" || -f "$NEXT_ARCHIVE" ]] || die "archive not found: $NEXT_ARCHIVE"

SCRIPT_DIR="$(cd -- "$(dirname -- "$(realpath "$0")")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"

for command in tar sha256sum readlink stat head; do
  command -v "$command" >/dev/null 2>&1 ||
    die "required command not found: $command"
done

# Developer harness interpreter (absolute, never a target dependency): drives PTYs,
# fake systemd, and websocket clients. The installed system is exercised with a
# guard first on PATH that makes python/python3/python3.12/pip/pip3 fail loudly.
# Deterministic: only the prepared repo relay interpreter, never PATH or an override.
DEV_PYTHON="$ROOT/services/relay/.venv/bin/python"
[[ -x "$DEV_PYTHON" ]] ||
  die "prepared developer Python missing: $DEV_PYTHON (run: uv sync --locked --project services/relay --managed-python)"
IMP_SRC_PATH="$ROOT/services/relay/src:$ROOT/integrations/common/src:$ROOT/integrations/tinyfugue/src"
PYTHONPATH="$IMP_SRC_PATH" "$DEV_PYTHON" -c 'import websockets, imp_relay' ||
  die "developer Python lacks websockets/imp_relay: $DEV_PYTHON"

if [[ -f "$ARCHIVE.sha256" ]]; then
  printf '=== outer checksum ===\n'
  (
    cd "$(dirname "$ARCHIVE")"
    sha256sum -c "$(basename "$ARCHIVE").sha256"
  )
fi

WORK_DIR="$(mktemp -d)"
trap 'rm -rf -- "$WORK_DIR"' EXIT

# Inside WORK_DIR but never allowlisted (guarded excludes it), so bare python/pip names are kernel-denied.
GUARD_BIN="$WORK_DIR/guard-bin"
mkdir -p "$GUARD_BIN"
for name in python python3 python3.12 pip pip3; do
  printf '#!/bin/sh\necho "target PATH python must not be used by the installed Imp runtime" >&2\nexit 97\n' \
    > "$GUARD_BIN/$name"
  chmod +x "$GUARD_BIN/$name"
done
export PATH="$GUARD_BIN:$PATH"
export IMP_SRC_PATH
unset PYTHONPATH PYTHONHOME VIRTUAL_ENV
# Every shipping installed process tree runs developer Python -> Landlock EXECUTE-only guard ->
# installed command, with no fallback. Only the bundle/fixture work tree may be executed besides
# non-Python system ELF tools; the host/managed interpreters are denied by the kernel.
GUARD="$SCRIPT_DIR/exec-guard.py"
"$DEV_PYTHON" "$GUARD" --abi >/dev/null ||
  die "Landlock exec guard unavailable (required, no fallback)"
# Allow every immediate WORK_DIR entry (computed per call, so later homes/clients are covered) except guard-bin.
guarded() {
  local -a allow=()
  local entry
  for entry in "$WORK_DIR"/* "$WORK_DIR"/.[!.]*; do
    if [[ -e "$entry" && "$entry" != "$GUARD_BIN" ]]; then allow+=(--allow "$entry"); fi
  done
  "$DEV_PYTHON" "$GUARD" "${allow[@]}" -- "$@"
}

expect_denied() {
  local rc=0
  guarded "$@" --version >/dev/null 2>"$WORK_DIR/denied.err" || rc=$?
  [[ "$rc" -eq 126 ]] && grep -Fq 'Permission denied' "$WORK_DIR/denied.err" ||
    die "guard did not deny $* (rc=$rc)"
}
BASE_PYTHON="$("$DEV_PYTHON" -c 'import sys; print(sys._base_executable)')"
VENV_BIN="$(dirname "$DEV_PYTHON")"
for target in "$DEV_PYTHON" "$(realpath "$DEV_PYTHON")" "$BASE_PYTHON" "$(realpath "$BASE_PYTHON")" \
  "$(dirname "$(realpath "$BASE_PYTHON")")"/{python,python3,python3.12,pip,pip3} \
  "$VENV_BIN"/{python,python3,python3.12,pip,pip3} \
  /usr/bin/{python,python3,python3.12,pip,pip3} /usr/local/bin/{python3,pip3}; do
  if [[ -e "$target" ]]; then expect_denied "$target"; fi
done
for name in python python3 python3.12 pip pip3; do
  expect_denied "$name"
  expect_denied /usr/bin/env "$name"
  if [[ -e "$VENV_BIN/$name" ]]; then PATH="$VENV_BIN:/usr/bin:/bin" expect_denied "$name"; fi
done
printf 'exec guard: absolute, bare and env-resolved Python/pip denied by the kernel (rc 126)\n'

EXTRACT_DIR="$WORK_DIR/extract"
mkdir -p "$EXTRACT_DIR"

tar -xzf "$ARCHIVE" -C "$EXTRACT_DIR"

NEXT_BUNDLE=""
if [[ -n "$NEXT_ARCHIVE" ]]; then
  mkdir -p "$WORK_DIR/next-extract"
  tar -xzf "$NEXT_ARCHIVE" -C "$WORK_DIR/next-extract"
  mapfile -t NEXT_TOP < <(find "$WORK_DIR/next-extract" -mindepth 1 -maxdepth 1 -type d -print)
  [[ ${#NEXT_TOP[@]} -eq 1 ]] || die "expected exactly one top-level directory in NEXT_ARCHIVE"
  NEXT_BUNDLE="${NEXT_TOP[0]}"
fi

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
  guarded "$BUNDLE_DIR/install.sh" \
  --no-start --connection none

HOME="$HOME_DIR" \
  guarded "$BUNDLE_DIR/install.sh" \
  --no-start --connection none

EXPECTED_RELEASE="$HOME_DIR/.local/share/imp/releases/$VERSION"

[[ "$(readlink "$HOME_DIR/.local/share/imp/current")" == "$EXPECTED_RELEASE" ]] ||
  die "current symlink does not select $VERSION"

[[ "$(
  readlink "$HOME_DIR/.local/bin/imp-action-consumer"
)" == "$HOME_DIR/.local/share/imp/current/bin/imp-action-consumer" ]] ||
  die "action-consumer symlink is incorrect"

[[ "$(
  readlink "$HOME_DIR/.local/bin/imp-direct-wss"
)" == "$HOME_DIR/.local/share/imp/current/bin/imp-direct-wss" ]] ||
  die "direct-wss symlink is incorrect"

[[ "$(
  readlink "$HOME_DIR/.local/bin/imp-feed-watch"
)" == "$HOME_DIR/.local/share/imp/current/bin/imp-feed-watch" ]] ||
  die "feed-watch symlink is incorrect"
guarded "$HOME_DIR/.local/bin/imp-feed-watch" --help ||
  die "feed-watch stable link does not run"

# The release is native: no venv, no wheelhouse, no interpreter on the target PATH.
for path in .venv wheels; do
  [[ ! -e "$EXPECTED_RELEASE/$path" ]] || die "release carries $path"
done

guarded "$EXPECTED_RELEASE/bin/imp-server" runtime-check "$VERSION" ||
  die "runtime-check rejected the installed release"

SERVER_INODE="$(stat -c '%d:%i' "$EXPECTED_RELEASE/bin/imp-server")"
for entry in relay gateway feed feed-watch action-consumer direct-wss direct-edge public-verify; do
  binary="$EXPECTED_RELEASE/bin/imp-$entry"

  [[ -x "$binary" && ! -L "$binary" ]] ||
    die "role alias is not an executable regular file: imp-$entry"
  [[ "$(stat -c '%d:%i' "$binary")" == "$SERVER_INODE" ]] ||
    die "role alias is not a hardlink of imp-server: imp-$entry"
  [[ "$(head -c4 "$binary")" == $'\x7f'ELF ]] ||
    die "role alias is not a native executable: imp-$entry"
done

printf 'native multicall entrypoints: OK\n'

printf '\n=== frozen relay/feed/gateway/action-consumer flow + native user systemd ===\n'
"$DEV_PYTHON" "$SCRIPT_DIR/test-frozen-server.py" "$BUNDLE_DIR"

[[ "$(
  grep -Fxc '/load ~/.config/imp/capture.tf' "$HOME_DIR/.tfrc"
)" -eq 1 ]] ||
  die "TinyFugue load line is not idempotent"
[[ "$(< "$HOME_DIR/.tfrc")" == $'/load ~/.config/imp/capture.tf\n/echo existing TinyFugue startup' ]] ||
  die "TinyFugue hook was not prepended before original startup commands"

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
  'ExecStart=%h/.local/share/imp/current/bin/imp-server relay --host 127.0.0.1 --port 8787' \
  "$HOME_DIR/.config/systemd/user/imp-relay.service"

grep -Fxq \
  'ExecStart=%h/.local/share/imp/current/bin/imp-server feed' \
  "$HOME_DIR/.config/systemd/user/imp-feed.service"

grep -Fxq \
  'ExecStart=%h/.local/share/imp/current/bin/imp-server gateway --host 127.0.0.1 --port 8788 --relay-url ws://127.0.0.1:8787' \
  "$HOME_DIR/.config/systemd/user/imp-gateway.service"

if grep -Eq 'python|\.venv|/bin/sh|^Environment=PATH' "$HOME_DIR"/.config/systemd/user/imp-*.service; then
  die "installed units depend on Python, a venv, a shell, or PATH"
fi

assert_no_debris "$HOME_DIR"
[[ ! -e "$HOME_DIR/.config/imp/gateway.env" ]] ||
  die "ordinary install unexpectedly created gateway credentials"

printf 'clean install: OK\n'
printf 'idempotent reinstall: OK\n'

printf '\n=== credential retention across same-version and distinct-release installs ===\n'

UPGRADE_HOME="$WORK_DIR/upgrade-home"
mkdir -p "$UPGRADE_HOME"
printf '/load ~/.config/imp/capture.tf\n' > "$UPGRADE_HOME/.tfrc"
HOME="$UPGRADE_HOME" guarded "$BUNDLE_DIR/install.sh" --no-start --connection none

mkdir -p "$UPGRADE_HOME/.config/imp"
printf 'IMP_GATEWAY_TOKEN_SHA256=%064d\n' 0 \
  > "$UPGRADE_HOME/.config/imp/gateway.env"
CONFIG_SHA="$(sha256sum "$UPGRADE_HOME/.config/imp/gateway.env")"

HOME="$UPGRADE_HOME" guarded "$BUNDLE_DIR/install.sh" --no-start
[[ "$(sha256sum "$UPGRADE_HOME/.config/imp/gateway.env")" == "$CONFIG_SHA" ]] ||
  die "same-version reinstall changed gateway credentials"

# A distinct release needs a real second artifact: runtime-check validates the
# embedded package metadata, so a rebranded copy of this bundle is invalid by design.
UPGRADE_BUNDLE="$BUNDLE_DIR"
if [[ -n "$NEXT_BUNDLE" ]]; then
  UPGRADE_BUNDLE="$NEXT_BUNDLE"
  NEXT_VERSION="$(tr -d '\r\n' < "$NEXT_BUNDLE/VERSION")"
  [[ "$NEXT_VERSION" != "$VERSION" ]] || die "NEXT_ARCHIVE must be a distinct release"
  HOME="$UPGRADE_HOME" guarded "$NEXT_BUNDLE/install.sh" --no-start
  [[ "$(readlink "$UPGRADE_HOME/.local/share/imp/current")" == \
    "$UPGRADE_HOME/.local/share/imp/releases/$NEXT_VERSION" ]] ||
    die "distinct-release install did not select the new runtime"
  [[ "$(readlink "$UPGRADE_HOME/.local/bin/imp-direct-wss")" == \
    "$UPGRADE_HOME/.local/share/imp/current/bin/imp-direct-wss" ]] ||
    die "distinct-release install did not preserve the stable CLI link"
  [[ "$(sha256sum "$UPGRADE_HOME/.config/imp/gateway.env")" == "$CONFIG_SHA" ]] ||
    die "distinct-release install changed gateway credentials"
  printf 'credential retention and real distinct-release selection: OK\n'
else
  printf 'credential retention (same version): OK; distinct new-layout release not run (no NEXT_ARCHIVE; legacy-to-new upgrade is covered below)\n'
fi

printf '\n=== installed Direct WSS setup, rotation, and status ===\n'

PYTHONPATH="$IMP_SRC_PATH" "$DEV_PYTHON" - "$HOME_DIR" "$BUNDLE_DIR" "$UPGRADE_BUNDLE" "$WORK_DIR" "$GUARD" <<'PY_DIRECT_WSS_BUNDLE'
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

home, bundle, upgrade, work, guard = map(Path, sys.argv[1:])


def guarded(argv):
    # Shipping installed processes only: developer Python -> exec-guard -> command.
    allow = [a for p in sorted(work.iterdir()) if p.name != "guard-bin" for a in ("--allow", str(p))]
    return [sys.executable, str(guard), *allow, "--", *map(str, argv)]


runtime = home / ".local/share/imp/current/bin"
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
temporary = tempfile.TemporaryDirectory(dir=work)
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind(("127.0.0.1", 0))
server.listen()
server.settimeout(0.2)
control_port = server.getsockname()[1]
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
    binary = os.path.realpath(runtime / "imp-server")
    state["gateway_release"] = Path(binary).parent.parent.name
    process = subprocess.Popen(
        guarded([
            binary, "gateway",
            "--host", "127.0.0.1", "--port", "8788",
            "--relay-url", "ws://127.0.0.1:8787",
        ]),
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
        binary = str(home / ".local/share/imp/current/bin/imp-server")
        command = [binary, "gateway", "--host", "127.0.0.1", "--port", "8788",
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
            connection.settimeout(5)
            line = connection.makefile("rb").readline().rstrip(b"\n")
            request = line.decode().split("\t") if line else []
            try:
                with state_lock:
                    code, output = handle(request)
            except Exception:
                code, output = 1, ""
            connection.sendall(f"{code}\n{output}".encode())


# Bounded Bash client for the installed shell/CLI: tab-separated argv line out, numeric exit
# code line then output until EOF back; arguments are fixed systemctl words, never credentials.
client = Path(temporary.name) / "systemctl"
client.write_text(
    "#!/bin/bash\n"
    "IFS=$'\\t'\n"
    "exec 3<>\"/dev/tcp/127.0.0.1/${IMP_TEST_SYSTEMD_PORT:?}\" || exit 1\n"
    "printf '%s\\n' \"$*\" >&3\n"
    "IFS= read -r -t 10 -u 3 code || exit 1\n"
    "[[ $code =~ ^[0-9]+$ ]] || exit 1\n"
    "while :; do\n"
    "  IFS= read -r -t 10 -u 3 line || { rc=$?; break; }\n"
    "  printf '%s\\n' \"$line\"\n"
    "done\n"
    "((rc == 1)) || exit 1\n"
    "exit \"$code\"\n"
)
client.chmod(0o755)
server_thread = threading.Thread(target=serve, daemon=True)
server_thread.start()
environment = os.environ.copy()
environment.update(
    HOME=str(home),
    PATH=f"{temporary.name}:{os.environ['PATH']}",
    IMP_TEST_SYSTEMD_PORT=str(control_port),
)
environment.pop("PYTHONPATH", None)
environment.pop("PYTHONHOME", None)
environment.pop("IMP_GATEWAY_TOKEN_SHA256", None)


def run_tty(command, expected=0, confined=True):
    master, slave = pty.openpty()
    process = subprocess.Popen(
        guarded(command) if confined else [str(arg) for arg in command],
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
        guarded([runtime / "imp-relay", "--host", "127.0.0.1", "--port", "8787"]),
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
        guarded([bundle / "install.sh", "--no-start", "--connection", "none"]),
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

    # websockets client for the harness only; the installed runtime is frozen.
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
        guarded([upgrade / "install.sh"]),
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    assert upgraded.returncode == 0
    assert config_path.read_bytes() == before_upgrade
    assert state["gateway_release"] == (upgrade / "VERSION").read_text().strip()
    assert state["starts"] == 6 and active("imp-gateway.service") and state["enabled"]

    manager = subprocess.run(
        guarded([client, "--user", "stop", "imp-gateway.service"]),
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
        guarded([cli, "status"]),
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
    # Source system-boundary injection (fixture-only, developer-harness evidence): the account
    # database is substituted around the developer-interpreter source of imp_relay.direct_wss,
    # because the frozen binary has no test hooks and the verdict must not depend on the
    # runner's real passwd/group contents.
    source_env = {**environment, "PYTHONPATH": os.environ["IMP_SRC_PATH"]}
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
                [sys.executable, "-c", account_cli, "status", *members],
                env=source_env,
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

        # Account-layout boundary injection only; frozen rotation is exercised above.
        before_rotate = config_path.read_bytes()
        environment["PYTHONPATH"] = source_env["PYTHONPATH"]  # source-boundary run only
        try:
            private_token = token_from(run_tty([sys.executable, "-c", account_cli, "rotate", "imp-user"], confined=False))
        finally:
            environment.pop("PYTHONPATH")
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

printf '\n=== connection modes, legacy upgrade/rollback, and Direct edge (shipping frozen runtime; edge system boundary injected from source) ===\n'

[[ -x "$BUNDLE_DIR/bin/imp-direct-edge" ]] ||
  die "bundle is missing bin/imp-direct-edge"
"$DEV_PYTHON" "$SCRIPT_DIR/test-installer-connections.py" \
  "$BUNDLE_DIR" ${NEXT_BUNDLE:+"$NEXT_BUNDLE"}

printf '\n=== custom TinyFugue startup override ===\n'

CUSTOM_HOME="$WORK_DIR/custom-home"
mkdir -p "$CUSTOM_HOME"

printf '/echo default startup untouched\n' > "$CUSTOM_HOME/.tfrc"
printf '/echo custom startup\n' > "$CUSTOM_HOME/custom.tf"

HOME="$CUSTOM_HOME" \
  guarded "$BUNDLE_DIR/install.sh" \
  --no-start --connection none \
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
   guarded "$BUNDLE_DIR/install.sh" \
   --no-start --connection none \
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
   guarded "$TAMPER_BUNDLE/install.sh" \
   --no-start --connection none \
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

printf 'unlisted\n' > "$EXTRA_BUNDLE/bin/unlisted"

if HOME="$EXTRA_HOME" \
   guarded "$EXTRA_BUNDLE/install.sh" \
   --no-start --connection none \
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

printf '\n=== symlink in bundle rejection ===\n'

LINK_BUNDLE="$WORK_DIR/symlink-content"
LINK_BUNDLE_HOME="$WORK_DIR/symlink-content-home"
cp -a "$BUNDLE_DIR" "$LINK_BUNDLE"
mkdir -p "$LINK_BUNDLE_HOME"
ln -s /etc/passwd "$LINK_BUNDLE/bin/imp-unsafe-link"

if HOME="$LINK_BUNDLE_HOME" guarded "$LINK_BUNDLE/install.sh" --no-start --connection none \
  >"$WORK_DIR/symlink-content.out" 2>&1; then
  die "bundle containing a symlink unexpectedly installed"
fi
grep -Fq 'unexpected symlink' "$WORK_DIR/symlink-content.out" ||
  {
    cat "$WORK_DIR/symlink-content.out" >&2
    die "symlinked bundle content produced the wrong failure"
  }
[[ ! -e "$LINK_BUNDLE_HOME/.local/share/imp" ]] ||
  die "symlinked bundle content changed the installation home"

printf 'symlink rejection: OK\n'

printf '\n=== unsafe Direct WSS link rejection ===\n'

LINK_HOME="$WORK_DIR/unsafe-link-home"
mkdir -p "$LINK_HOME/.local/bin"
printf '/echo startup\n' > "$LINK_HOME/.tfrc"
ln -s /tmp/unrelated-command "$LINK_HOME/.local/bin/imp-direct-wss"

if HOME="$LINK_HOME" guarded "$BUNDLE_DIR/install.sh" --no-start --connection none \
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
  guarded "$BUNDLE_DIR/install.sh" \
  --no-start --connection none

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

ROLLBACK_ACTION_TARGET="$ROLLBACK_HOME/.local/share/imp/current/.venv/bin/imp-action-consumer"
ln -sfn "$ROLLBACK_ACTION_TARGET" \
  "$ROLLBACK_HOME/.local/bin/imp-action-consumer"
ROLLBACK_WATCH_TARGET="$(readlink "$ROLLBACK_HOME/.local/bin/imp-feed-watch")"


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
      # Injected failure: the unit was active, is now failed/inactive; record outside the fixture HOME.
      if [[ -e "$state/active-$unit" ]]; then echo active-before > "$IMP_TEST_RELAY_FAIL_MARKER"
      else echo inactive-before > "$IMP_TEST_RELAY_FAIL_MARKER"; fi
      rm -f "$state/active-$unit"
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
  guarded "$BUNDLE_DIR/install.sh" --connection none \
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
  guarded "$BUNDLE_DIR/install.sh" --connection none \
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
  guarded "$BUNDLE_DIR/install.sh" --connection none \
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
   guarded "$BUNDLE_DIR/install.sh" --connection none \
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

[[ "$(<"$ROLLBACK_RELAY_FAIL_MARKER")" == active-before ]] ||
  die "injected relay restart failure did not start from an active relay"
[[ "$(grep -Fxc -- '--user restart imp-relay.service' "$ROLLBACK_SYSTEMCTL_LOG")" -eq 2 ]] ||
  die "rollback did not restart the relay that the injected failure left inactive"

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
)" == "$ROLLBACK_ACTION_TARGET" ]] ||
  die "action-consumer link rollback failed"

[[ "$(readlink "$ROLLBACK_HOME/.local/bin/imp-feed-watch")" == "$ROLLBACK_WATCH_TARGET" ]] ||
  die "pre-existing feed-watch link rollback failed"

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
)" == "$ROLLBACK_HOME/.local/share/imp/current/bin/imp-direct-wss" ]] ||
  die "direct-wss link rollback failed"

printf 'runtime rollback: OK\n'
printf 'integration-file rollback: OK\n'
printf 'TinyFugue startup rollback: OK\n'

printf 'Fake systemd models state for installer rollback and launches real frozen gateway/relay processes; real user-systemd evidence is the isolated native phase of test-frozen-server.py. Source-boundary injection (edge root boundary, account database) is developer-harness-only.\n'
printf '\nserver bundle acceptance: PASS\n'
