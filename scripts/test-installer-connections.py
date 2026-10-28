#!/usr/bin/env python3.12
"""Installed-bundle acceptance for install.sh --connection modes and legacy upgrades.

Usage: test-installer-connections.py BUNDLE_DIR [NEXT_BUNDLE_DIR]

The real install.sh and the real frozen bin/ runtime (bundle-check, runtime-check,
and every role except direct-edge) run unmodified, with a guard directory first
on PATH whose python/python3/python3.12 fail loudly: the installed system has no
Python dependency.  Source system-boundary injection evidence (fixture-only,
never shipping hooks): the root-side system boundary of direct-edge (paths, DNS,
systemctl/apt/ss/ip runner, HTTPS, sudo) is replaced inside imp_relay.direct_edge
run from source by the developer interpreter, reached through a copied bundle
whose bin/imp-server forwards only `direct-edge` to that driver.  The shipping
bundle's edge safe commands are exercised separately.  User systemctl, cp, and
the credential provisioning role (an external contract; real token semantics are
covered by the PTY harness in test-server-bundle.sh) are PATH/file fixtures.
Nothing here touches real root, DNS, packages, or the network.
"""

import ctypes
import hashlib
import importlib.util
import itertools
import json
import os
import pty
import re
import secrets
import select
import shutil
import signal
import ssl
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOST = "imp.example.com"
OTHER = "other.example.com"
GLOBAL = "93.184.216.34"
WRONG = "8.8.8.8"
SELF = Path(__file__).resolve()
DEV_PYTHON = sys.executable  # developer harness interpreter, never a target dependency
GUARD = SELF.with_name("exec-guard.py")
EDGE_SRC = str(SELF.parents[1] / "services/relay/src")
CADDY_LISTENER = 'tcp LISTEN 0 4096 *:{port} *:* users:(("caddy",pid=4242,fd=7))'


def die(message):
    print(f"FAIL: {message}", file=sys.stderr)
    sys.exit(1)


def check(condition, message, result=None):
    if not condition:
        if result is not None:
            print(result.out, file=sys.stderr)
        die(message)


def installer_transaction(site, commands, result):
    calls = site.edge().get("transaction_calls", [])
    check([call[0] for call in calls] == commands, "wrong guarded transaction command order", result)
    identities = [call[call.index("--transaction") + 1] for call in calls]
    check(
        len(set(identities)) == 1
        and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", identities[0]) is not None,
        "installer did not preserve one canonical kernel transaction identity",
        result,
    )
    return identities[0]


# ---------------------------------------------------------------- shims ----

SYSTEMCTL = """#!/bin/sh
units=$IMP_TEST_UNITS
if [ "${1-}" = --user ]; then shift; fi
printf '%s\\n' "$*" >> "$units/calls.log"
op=${1-}
if [ $# -gt 0 ]; then shift; fi
now=no
count=$#
while [ "$count" -gt 0 ]; do
  arg=$1; shift; count=$((count - 1))
  case $arg in
    --now) now=yes ;;
    --*) ;;
    *) set -- "$@" "$arg" ;;
  esac
done
unit=${1-}
case $op in
  is-active) [ -e "$units/active-$unit" ] && exit 0; exit 3 ;;
  is-enabled) [ -e "$units/enabled-$unit" ] && exit 0; exit 1 ;;
  show)
    if [ -e "$units/active-$unit" ] || [ -e "$units/enabled-$unit" ] || [ -e "$units/loaded-$unit" ]; then
      echo loaded
    else
      echo not-found
    fi ;;
  enable)
    for name; do
      : > "$units/enabled-$name"
      if [ "$now" = yes ]; then : > "$units/active-$name"; fi
    done ;;
  disable) for name; do rm -f "$units/enabled-$name"; done ;;
  start | restart)
    if [ "$op" = restart ] && [ "${IMP_TEST_FAIL_RESTART-}" = "$unit" ] && [ ! -e "$units/failed-once" ]; then
      : > "$units/failed-once"
      rm -f "$units/active-$unit"
      if [ -e "$units/active-$unit" ]; then seen=active; else seen=inactive; fi
      printf '%s %s\\n' "$unit" "$seen" >> "$units/failure-record"
      exit 1
    fi
    for name; do : > "$units/active-$name"; done ;;
  stop) for name; do rm -f "$units/active-$name"; done ;;
esac
if [ "$op" = restart ] && [ "$unit" = imp-feed.service ] && [ "${IMP_TEST_SIGNAL_PHASE-}" = feed ]; then
  kill -s "${IMP_TEST_SIGNAL#SIG}" "$PPID"
fi
"""

PYTHON_GUARD = """#!/bin/sh
echo "target PATH python must not be used by the installed Imp runtime" >&2
exit 97
"""

# Fixture-only: forwards direct-edge to the source driver; every other role execs
# the shipped binary under the multicall name.
LAUNCHER = f"""#!/bin/bash
if [ "${{1-}}" = direct-edge ]; then
  shift
  PYTHONPATH={EDGE_SRC} exec {DEV_PYTHON} {SELF} --edge-driver "$@"
fi
exec -a imp-server "$(dirname "$(readlink -f "$0")")/imp-relay" "$@"
"""

CP_SHIM = f"""#!/bin/sh
case "${{3-}}" in
  *.install-backup.*/capture.tf)
    if [ "${{IMP_TEST_RESTORE_COPY_FAILURE-}}" = yes ]; then exit 1; fi ;;
esac
exec {shutil.which("cp")} "$@"
"""

PROVISIONER = """#!{python}
import hashlib, json, os, secrets, signal, subprocess, sys
from pathlib import Path

home = Path(os.environ["HOME"])
marker = Path(os.environ["IMP_TEST_EDGE_ROOT"]) / "etc/imp/direct-edge.json"
pending = json.loads(marker.read_text())["pending"] if marker.exists() else None
with open(os.environ["IMP_TEST_WSS_LOG"], "a") as log:
    log.write(json.dumps({{
        "argv": sys.argv[1:],
        "stdin_tty": os.ttyname(0) if os.isatty(0) else None,
        "stdout_tty": os.ttyname(1) if os.isatty(1) else None,
        "edge_pending": pending,
        "edge_version": json.loads(marker.read_text())["version"] if marker.exists() else None,
        "edge_caddyfile": (marker.parent / "Caddyfile").read_text() if marker.exists() else None,
    }}) + "\\n")
with open(os.environ["IMP_TEST_EVENTS"], "a") as events:
    events.write("wss " + " ".join(sys.argv[1:]) + "\\n")
config = home / ".config/imp/gateway.env"
units = Path(os.environ["IMP_TEST_UNITS"])
expected = ["setup", "--public-url", "wss://imp.example.com/state"]
if marker.exists() and json.loads(marker.read_text()).get("behind_nat", False):
    expected.append("--verify-via-loopback")
if sys.argv[1:] != expected:
    print("public verification does not match the owned edge topology", file=sys.stderr)
    sys.exit(1)
if config.exists():
    print("Direct WSS is already configured; no changes made.")
    sys.exit(0)
if (units / "active-imp-gateway.service").exists():
    print("setup cannot replace credentials while the gateway is already active", file=sys.stderr)
    sys.exit(1)
if os.environ.get("IMP_TEST_PROVISION") == "fail":
    print("provisioning failed", file=sys.stderr)
    sys.exit(1)
token = secrets.token_urlsafe(32)
with open(os.environ["IMP_TEST_EVENTS"], "a") as events:
    events.write("token-generated\\n")
config.write_text("IMP_GATEWAY_TOKEN_SHA256=" + hashlib.sha256(token.encode()).hexdigest() + "\\n")
config.chmod(0o600)
subprocess.run(["systemctl", "--user", "enable", "--now", "imp-gateway.service"], check=True)
if os.environ.get("IMP_TEST_PROVISION") == "fail_after_replacement":
    config.unlink()
    subprocess.run(["systemctl", "--user", "stop", "imp-gateway.service"], check=True)
    subprocess.run(["systemctl", "--user", "disable", "imp-gateway.service"], check=True)
    print("handled provisioning failure; previous state restored", file=sys.stderr)
    sys.exit(1)
print("PAIRING-TOKEN:" + token, flush=True)
if os.environ.get("IMP_TEST_SIGNAL_PHASE") == "handoff":
    os.kill(os.getppid(), getattr(signal, os.environ["IMP_TEST_SIGNAL"]))
"""


RECONCILE_QUERY = r"""
import json, os, signal, sys, time
from pathlib import Path
ready = Path(sys.argv[1])
state = {"helper": os.getppid(), "query": os.getpid(),
         "ignored": [signal.getsignal(s) == signal.SIG_IGN
                     for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)],
         "helper_ignored": json.loads(sys.argv[4])}
ready.with_suffix(".tmp").write_text(json.dumps(state))
ready.with_suffix(".tmp").replace(ready)
deadline = time.monotonic() + 5
while not ready.with_suffix(".release").exists():
    if time.monotonic() >= deadline:
        sys.exit("query release timed out")
    time.sleep(0.01)
ready.with_suffix(".done").write_text(json.dumps(state))
if sys.argv[3] == "fail":
    sys.exit("injected service-query failure")
print(sys.argv[2], end="")
"""


def signal_at_reconciliation_fork(process, ready, signame):
    """Ptrace stops the next fork's child before it can run its first user instruction."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.ptrace.restype = ctypes.c_long

    def ptrace(request, pid, data=0):
        rc = libc.ptrace(request, pid, None, ctypes.c_void_p(data))
        if rc == -1:
            raise OSError(ctypes.get_errno(), "ptrace")

    def stopped(pid):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            found, status = os.waitpid(pid, os.WNOHANG)
            if found:
                check(os.WIFSTOPPED(status), "reconciliation tracee exited before fork stop")
                return status
            time.sleep(0.01)
        raise AssertionError("reconciliation fork stop timed out")

    # SEIZE + TRACEFORK/TRACEVFORK: no production/test-only startup hook in the child.
    ptrace(0x4206, process.pid, 2 | 4)
    child = None
    parent_stopped = False
    child_stopped = False
    try:
        ready.with_suffix(".release").touch()
        while True:
            status = stopped(process.pid)
            parent_stopped = True
            if status >> 16 in (1, 2):
                msg = ctypes.c_ulong()
                ptrace(0x4201, process.pid, ctypes.addressof(msg))
                child = msg.value
                stopped(child)
                child_stopped = True
                break
            ptrace(7, process.pid, os.WSTOPSIG(status))
            parent_stopped = False
        ignored = int(
            next(
                line.split()[1]
                for line in Path(f"/proc/{child}/status").read_text().splitlines()
                if line.startswith("SigIgn:")
            ),
            16,
        )
        os.killpg(process.pid, getattr(signal, signame))
        ready.with_suffix(".spawn").write_text(json.dumps({"child": child, "ignored": ignored}))
    finally:
        try:
            if child_stopped:
                ptrace(17, child)
        finally:
            if not parent_stopped:
                ptrace(0x4207, process.pid)  # INTERRUPT only for tracer teardown, never a terminal signal.
                stopped(process.pid)
            ptrace(17, process.pid)


def edge_driver(args):
    """Run imp_relay.direct_edge from source with its system boundary replaced."""
    state_path = Path(os.environ["IMP_TEST_EDGE_STATE"])
    root = Path(os.environ["IMP_TEST_EDGE_ROOT"])
    sys.dont_write_bytecode = True
    from imp_relay import direct_edge as mod
    real_run = mod._run
    original_resolve = mod._resolve

    def under(value):
        return str(root / str(value).lstrip("/"))

    for name in ("IMP_DIR", "UNIT_PATH", "CADDYFILE", "MARKER", "CADDY_BIN", "OS_RELEASE", "SYSTEMD_RUN_DIR"):
        setattr(mod, name, under(getattr(mod, name)))
    for name in ("STOCK_UNITS", "PREEXISTING", "APT_DIRS"):
        setattr(mod, name, tuple(under(item) for item in getattr(mod, name)))
    mod._ANCHOR = str(root)
    mod._ROOT_UID = os.getuid()
    mod._EDGE_DEADLINE = 0
    mod._EDGE_INTERVAL = 0

    def load():
        return json.loads(state_path.read_text())

    def save(state):
        state_path.write_text(json.dumps(state))

    def run(argv, timeout=120):
        state = load()
        state["calls"].append(" ".join(argv))
        unit = state["unit"]
        rc, out, err = 0, "", ""
        tool, rest = argv[0], argv[1:]
        if tool == "systemctl" and rest[:1] == ["show"]:
            loaded = os.path.exists(mod.UNIT_PATH)
            props = {
                "Id": mod.UNIT_NAME,
                "LoadState": "loaded" if loaded else "not-found",
                "FragmentPath": mod.UNIT_PATH if loaded else "",
                "DropInPaths": "/etc/systemd/system/imp-caddy.service.d/x.conf"
                if state.get("dropin")
                else "",
                "ActiveState": "active" if unit["active"] else "inactive",
                "UnitFileState": "enabled" if unit["enabled"] else "disabled",
                "MainPID": "4242" if unit["active"] else "0",
                "NeedDaemonReload": "no",
            }
            props.update(state.get("unit_properties", {}))
            out = "".join(f"{k}={v}\n" for k, v in props.items())
        elif (
            tool == "systemctl"
            and rest[:1] in (["enable"], ["start"])
            and state.get("start_fail")
            and (rest[:1] == ["start"] or "--now" in rest)
        ):
            rc, err = 1, "Job failed"
        elif tool == "systemctl" and rest[:1] == ["enable"]:
            unit["enabled"] = True
            unit["active"] = unit["active"] or "--now" in rest
        elif tool == "systemctl" and rest[:1] == ["disable"]:
            unit["enabled"] = False
            unit["active"] = unit["active"] and "--now" not in rest
        elif tool == "systemctl" and rest[:1] == ["start"]:
            unit["active"] = True
        elif tool == "systemctl" and rest[:1] == ["stop"]:
            unit["active"] = False
        elif tool == "systemctl" and rest[:1] == ["restart"]:
            if (
                args[:1] == ["migrate"]
                and state.get("migration_fail") == "restart"
                and not state.get("migration_failed")
            ) or state.get("migration_restore_fail"):
                unit["active"] = False
                state["migration_failed"] = True
                state["failed_restart_state"] = "inactive"
                rc, err = 1, "injected migration restart failure"
            else:
                unit["active"] = True
        elif tool == "systemctl" and rest[:1] == ["mask"]:
            for item in mod.STOCK_UNITS:
                if os.path.basename(item) in rest and not os.path.lexists(item):
                    os.symlink("/dev/null", item)
        elif tool == "ss":
            lines = list(state["listeners"])
            if unit["active"]:
                lines += [CADDY_LISTENER.format(port=p) for p in (80, 443)]
            out = "\n".join(lines) + "\n"
        elif tool == "ip":
            failure = state.get("ip_failure")
            if failure in ("missing", "unexecutable", "timeout"):
                save(state)
                original_run = subprocess.run

                def fail_ip(*unused, **kwargs):
                    if failure == "timeout":
                        raise subprocess.TimeoutExpired(argv, timeout)
                    if failure == "missing":
                        raise FileNotFoundError("ip fixture missing")
                    raise PermissionError("ip fixture unexecutable")

                subprocess.run = fail_ip
                try:
                    return real_run(argv, timeout)
                finally:
                    subprocess.run = original_run
            if failure == "nonzero":
                rc, err = 1, "injected ip inspection failure"
            out = state.get("ip_output", json.dumps([{"addr_info": [{"local": a} for a in state["addrs"]]}]))
        elif tool == "dpkg-query":
            rc = 0 if state.get("dpkg_caddy") else 1
        elif tool == "apt-get" and rest[:1] == ["install"]:
            if state.get("apt_fail"):
                rc, err = 100, "E: unable to locate package caddy"
            else:
                Path(mod.CADDY_BIN).parent.mkdir(parents=True, exist_ok=True)
                Path(mod.CADDY_BIN).touch(mode=0o755)
        elif tool == "runuser" and "validate" in rest and (
            args[:1] == ["migrate"] and state.get("migration_fail") == "validate"
            or args[:1] == ["enable"] and state.get("enable_validation_fail")
        ):
            rc, err = 1, "injected Caddy validation failure"
        save(state)
        query = os.environ.get("IMP_TEST_RECONCILE_QUERY")
        if query and args[:1] == ["ssh-committed"] and argv[:2] == ["systemctl", "show"]:
            with open(os.environ["IMP_TEST_EVENTS"], "a") as events:
                events.write(f"reconcile-query timeout={timeout}\n")
            return real_run(
                [
                    sys.executable,
                    "-c",
                    RECONCILE_QUERY,
                    query,
                    out,
                    os.environ.get("IMP_TEST_RECONCILE_QUERY_RESULT", "ok"),
                    json.dumps(
                        [
                            signal.getsignal(s) == signal.SIG_IGN
                            for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
                        ]
                    ),
                ],
                timeout,
            )
        return subprocess.CompletedProcess(argv, rc, out, err)

    def resolve(host):
        state = load()
        state["resolve_calls"] = state.get("resolve_calls", 0) + 1
        sequence = state.get("dns_sequence")
        dns = sequence[min(state["resolve_calls"] - 1, len(sequence) - 1)] if sequence else state["dns"]
        save(state)
        if state.get("resolver_failure"):
            original_getaddrinfo = mod.socket.getaddrinfo

            def fail_resolver(*unused, **kwargs):
                raise mod.socket.gaierror("injected resolver failure")

            mod.socket.getaddrinfo = fail_resolver
            try:
                return original_resolve(host)
            finally:
                mod.socket.getaddrinfo = original_getaddrinfo
        if host not in dns:
            raise mod.EdgeError(f"{host} does not resolve in DNS; create an A/AAAA record for this server")
        return dns[host]

    def https_status(host, path, behind_nat=False):
        state = load()
        state["https"].setdefault("calls", []).append([host, path, behind_nat])
        save(state)
        if not state["unit"]["active"]:
            raise ConnectionRefusedError
        if args[:1] == ["migrate"] and state.get("migration_fail") == "post-verify":
            raise ssl.SSLCertVerificationError("injected migration post-verification failure")
        answer = state["https"].get(path, 404)
        if answer == "cert":
            raise ssl.SSLCertVerificationError("fake untrusted certificate")
        return answer

    def sudo(argv, *, interactive):
        state = load()
        state["sudo_calls"] = state.get("sudo_calls", 0) + 1
        save(state)
        if state.get("sudo_unavailable"):
            raise mod.EdgeError("sudo is required to change the public edge")
        original = mod._euid
        mod._euid = lambda: 0
        os.environ["SUDO_UID"] = str(os.getuid())
        try:
            return mod.main(argv)
        finally:
            mod._euid = original

    mod._run = run
    mod._resolve = resolve
    mod._https_status = https_status
    if args[:1] == ["migrate"] and load().get("migration_fail") == "publication":
        original_atomic = mod._atomic

        def fail_publication(path, data, **kwargs):
            state = load()
            if path == mod.CADDYFILE and not state.get("migration_failed"):
                state["migration_failed"] = True
                save(state)
                raise mod.EdgeError("injected migration publication failure")
            original_atomic(path, data, **kwargs)

        mod._atomic = fail_publication
    if "--transaction" in args:
        state = load()
        state.setdefault("transaction_calls", []).append(args)
        save(state)
    if args[:1] == [os.environ.get("IMP_TEST_REPLACE_TRANSACTION")]:
        marker = Path(mod.MARKER)
        owned = json.loads(marker.read_text())
        owned["pending"]["transaction"] = Path("/proc/sys/kernel/random/uuid").read_text().strip()
        marker.write_text(json.dumps(owned, sort_keys=True, separators=(",", ":")) + "\n")
        state = load()
        state["replacement_marker"] = marker.read_text()
        state["replacement_caddyfile"] = Path(mod.CADDYFILE).read_text()
        state["replacement_sudo_calls"] = state.get("sudo_calls", 0)
        state["replacement_call_count"] = len(state["calls"])
        save(state)
    mod._sudo = sudo
    publication = os.environ.get("IMP_TEST_PUBLICATION_SIGNAL")
    if publication and args[:1] == ["enable"]:
        original_atomic = mod._atomic

        def publish(path, data, **kwargs):
            original_atomic(path, data, **kwargs)
            if os.path.basename(path) == publication:
                os.kill(os.getpid(), signal.SIGTERM)

        mod._atomic = publish
    commit_boundary = os.environ.get("IMP_TEST_COMMIT_SIGNAL_BOUNDARY")
    if commit_boundary and args[:1] == ["commit"]:
        original_atomic = mod._atomic
        signum = getattr(signal, os.environ["IMP_TEST_SIGNAL"])
        signal.signal(signum, signal.SIG_DFL)

        def interrupt_commit(path, data, **kwargs):
            if commit_boundary == "after":
                original_atomic(path, data, **kwargs)
            with open(os.environ["IMP_TEST_EVENTS"], "a") as events:
                pending = json.loads(Path(mod.MARKER).read_text())["pending"]
                events.write(f"commit-signal boundary={commit_boundary} pending={json.dumps(pending)}\n")
            spawn = os.environ.get("IMP_TEST_RECONCILE_SPAWN")
            if spawn:
                ready = Path(spawn)
                ready.with_suffix(".tmp").write_text(str(os.getpid()))
                ready.with_suffix(".tmp").replace(ready)
                deadline = time.monotonic() + 5
                while not ready.with_suffix(".release").exists():
                    if time.monotonic() >= deadline:
                        raise AssertionError("commit child release timed out")
                    time.sleep(0.01)
            if os.environ.get("IMP_TEST_COMMIT_SIGNAL_TARGET") == "child":
                os.kill(os.getpid(), signum)
            else:
                os.killpg(os.getpgrp(), signum)

        mod._atomic = interrupt_commit
    if args[:1] == ["commit"] and load().get("commit_fail"):
        rc = 1
        print("imp direct edge: injected commit failure", file=sys.stderr)
    else:
        rc = mod.main(args)
    with open(os.environ["IMP_TEST_EVENTS"], "a") as events:
        events.write(
            f"edge {' '.join(args if args[:1] in (['hostname'], ['dns'], ['preflight']) else args[:1])} rc={rc}\n"
        )
    if rc == 0 and args[:1] == [os.environ.get("IMP_TEST_SIGNAL_PHASE")]:
        os.kill(os.getppid(), getattr(signal, os.environ["IMP_TEST_SIGNAL"]))
    if rc == 0 and args[:1] == ["disable"] and os.environ.get("IMP_TEST_HANGUP_READY"):
        ready_path = Path(os.environ["IMP_TEST_HANGUP_READY"])
        ready_path.with_suffix(".tmp").write_text(str(os.getpid()))
        ready_path.with_suffix(".tmp").replace(ready_path)
        while not ready_path.with_suffix(".release").exists():
            time.sleep(0.02)
    sys.exit(rc)


# ---------------------------------------------------------------- sites ----


class Result:
    def __init__(self, rc, out, tty=None):
        self.rc, self.out, self.tty = rc, out, tty


class Site:
    """One HOME plus its fake user-systemd, fake edge root, and logs."""

    def __init__(self, base, name):
        self.root = Path(base) / name
        self.home = self.root / "home"
        self.units = self.root / "units"
        self.edge_root = self.root / "edge"
        self.state = self.root / "edge-state.json"
        self.events = self.root / "events.log"
        self.wss_log = self.root / "wss.log"
        self.home.mkdir(parents=True)
        (self.home / ".tfrc").write_text("/load ~/.config/imp/capture.tf\n")
        self.units.mkdir()
        self.clean_edge()
        self.events.write_text("")
        self.wss_log.write_text("")

    # --- environment

    def env(self, extra=None):
        shims = Path(self.root.parent) / "shims"
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")}
        env.update(
            HOME=str(self.home),
            USER=os.environ.get("USER", "imp-test"),
            PATH=f"{shims}:{os.environ['PATH']}",
            IMP_TEST_UNITS=str(self.units),
            IMP_TEST_EDGE_STATE=str(self.state),
            IMP_TEST_EDGE_ROOT=str(self.edge_root),
            IMP_TEST_EVENTS=str(self.events),
            IMP_TEST_WSS_LOG=str(self.wss_log),
        )
        env.update(extra or {})
        return env

    def clean_edge(self, **state):
        self.state.unlink(missing_ok=True)
        shutil.rmtree(self.edge_root, ignore_errors=True)
        for directory in ("etc/systemd/system", "usr/bin", "run/systemd/system"):
            (self.edge_root / directory).mkdir(parents=True)
        (self.edge_root / "etc/os-release").write_text("ID=ubuntu\n")
        self.set_edge(
            dns={HOST: [GLOBAL], OTHER: [GLOBAL]},
            addrs=[GLOBAL],
            unit={"enabled": False, "active": False},
            listeners=[],
            https={},
            calls=[],
            **state,
        )

    def set_edge(self, **updates):
        state = json.loads(self.state.read_text()) if self.state.exists() else {}
        state.update(updates)
        self.state.write_text(json.dumps(state))

    def edge(self):
        return json.loads(self.state.read_text())

    def unit_state(self):
        unit = self.edge()["unit"]
        return unit["enabled"], unit["active"]

    def marker(self):
        path = self.edge_root / "etc/imp/direct-edge.json"
        return json.loads(path.read_text()) if path.exists() else None

    def events_list(self):
        return self.events.read_text().splitlines()

    def wss_calls(self):
        return [json.loads(line) for line in self.wss_log.read_text().splitlines()]

    # --- fake user manager / installed state

    def set_gateway(self, enabled=False, active=False, loaded=False):
        for kind, on in (("enabled", enabled), ("active", active), ("loaded", loaded)):
            path = self.units / f"{kind}-imp-gateway.service"
            path.unlink(missing_ok=True)
            if on:
                path.touch()

    def gateway_state(self):
        return (
            (self.units / "enabled-imp-gateway.service").exists(),
            (self.units / "active-imp-gateway.service").exists(),
        )

    def config(self):
        return self.home / ".config/imp/gateway.env"

    def write_credential(self):
        self.config().parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(b"retained-direct-credential").hexdigest()
        self.config().write_text(f"IMP_GATEWAY_TOKEN_SHA256={digest}\n")
        self.config().chmod(0o600)

    def reset(self, gateway=(False, False)):
        self.set_gateway()
        self.config().unlink(missing_ok=True)
        self.set_gateway(*gateway)
        (self.units / "calls.log").unlink(missing_ok=True)
        self.clean_edge()
        self.events.write_text("")
        self.wss_log.write_text("")

    def snapshot(self):
        link = self.home / ".local/share/imp/current"
        files = [self.config(), self.home / ".tfrc", self.home / ".config/imp/capture.tf"]
        return {
            "files": {str(f): f.read_bytes() if f.is_file() else None for f in files},
            "link": os.readlink(link) if link.is_symlink() else None,
            "units": sorted(p.name for p in self.units.glob("*-imp-*.service")),
            "debris": sorted(
                p.name for p in (self.home / ".local/share/imp").glob(".*") if p.is_dir() or p.is_symlink()
            )
            if (self.home / ".local/share/imp").exists()
            else None,
        }

    def pristine(self):
        return not any((self.home / part).exists() for part in (".local", ".config"))

    def guarded(self, command, *bundles):
        """Shipping installed process tree under the Landlock EXECUTE guard (developer Python stays outside)."""
        shims = self.root.parent / "shims"
        allow = (*bundles, self.home, shims / "systemctl", shims / "cp")  # never the python/pip stubs beside them
        return [DEV_PYTHON, str(GUARD), *(a for d in allow for a in ("--allow", str(d))), "--", *map(str, command)]

    def drive(self, *args):
        """Source-edge control, deliberately UNGUARDED (developer Python): run a helper subcommand (test pre-state)."""
        done = subprocess.run(
            [str(self.bundle / "bin/imp-server"), "direct-edge", *args],
            env=self.env(),
            capture_output=True,
            text=True,
        )
        check(done.returncode == 0, f"helper {' '.join(args)} failed: {done.stderr}")

    def owned_edge(self, version=3, *, behind_nat=False):
        """Developer-only exact ownership fixture, never executed inside the installed guard."""
        self.drive("enable", HOST, str(os.getuid()), *(["--behind-nat"] if behind_nat else []))
        self.drive("commit", str(os.getuid()))
        sys.path.insert(0, EDGE_SRC)
        from imp_relay import direct_edge as mod
        (self.edge_root / "etc/imp/Caddyfile").write_bytes(
            mod._caddyfile(HOST, behind_nat, version=version)
        )
        (self.edge_root / "etc/imp/direct-edge.json").write_bytes(
            mod._marker_bytes(os.getuid(), HOST, None, behind_nat, version=version)
        )
        certificate = self.edge_root / "var/lib/caddy/certificate.fixture"
        certificate.parent.mkdir(parents=True, exist_ok=True)
        certificate.write_bytes(b"retained certificate and private key fixture\n")
        self.events.write_text("")
        self.set_edge(calls=[], https={})

    # --- run the installer

    def install(self, bundle, args, *, tty=False, keys="", extra=None, timeout=900, guarded=False):
        command = [str(Path(bundle) / "install.sh"), *args]
        if guarded:  # actual shipping install tree; default False only for the source-edge boundary matrix
            command = self.guarded(command, bundle)
        env = self.env(extra)
        if not tty:
            done = subprocess.run(
                command,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
            )
            return Result(done.returncode, done.stdout)
        master, slave = pty.openpty()
        tty_name = os.ttyname(slave)
        process = subprocess.Popen(
            command, env=env, stdin=slave, stdout=slave, stderr=slave, start_new_session=True
        )
        os.close(slave)
        if keys:
            os.write(master, keys.encode())
        chunks = []
        deadline = time.monotonic() + timeout
        query_path = Path(env["IMP_TEST_RECONCILE_QUERY"]) if env.get("IMP_TEST_RECONCILE_QUERY") else None
        second_signal_sent = False
        spawn_path = Path(env["IMP_TEST_RECONCILE_SPAWN"]) if env.get("IMP_TEST_RECONCILE_SPAWN") else None
        spawn_signal_sent = False
        while time.monotonic() < deadline:
            if spawn_path is not None and spawn_path.exists() and not spawn_signal_sent:
                signal_at_reconciliation_fork(process, spawn_path, env["IMP_TEST_SECOND_SIGNAL"])
                spawn_signal_sent = True
            if query_path is not None and query_path.exists() and not second_signal_sent:
                os.killpg(
                    process.pid, getattr(signal, env.get("IMP_TEST_SECOND_SIGNAL", env["IMP_TEST_SIGNAL"]))
                )
                query_path.with_suffix(".release").touch()
                second_signal_sent = True
            ready, _, _ = select.select([master], [], [], 0.5)
            if ready:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                chunks.append(data)
            elif process.poll() is not None:
                break
        else:
            process.kill()
        rc = process.wait()
        os.close(master)
        return Result(rc, b"".join(chunks).decode(errors="replace").replace("\r\n", "\n"), tty_name)

    def install_hangup(self, bundle, extra=None):
        """Acquire a real controlling PTY, then remove it after the owned SSH transition."""
        ready_path = self.root / "hangup-ready"
        ready_path.unlink(missing_ok=True)
        ready_path.with_suffix(".release").unlink(missing_ok=True)
        env = self.env({**(extra or {}), "IMP_TEST_HANGUP_READY": str(ready_path)})
        acquire_terminal = (
            "import fcntl, os, signal, sys, termios; "
            "signal.signal(signal.SIGHUP, signal.SIG_DFL); "
            "fcntl.ioctl(0, termios.TIOCSCTTY, 0); "
            "os.tcsetpgrp(0, os.getpgrp()); os.execv(sys.argv[1], sys.argv[1:])"
        )
        master, slave = pty.openpty()
        process = subprocess.Popen(
            [DEV_PYTHON, "-c", acquire_terminal, str(Path(bundle) / "install.sh"), "--connection", "ssh"],
            env=env,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            start_new_session=True,
        )
        os.close(slave)
        helper_fd = None
        try:
            deadline = time.monotonic() + 60
            while not ready_path.exists():
                check(
                    process.poll() is None and time.monotonic() < deadline,
                    "installer did not reach the mutated closed-PTY boundary",
                )
                readable, _, _ = select.select([master], [], [], 0.05)
                if readable:
                    os.read(master, 65536)
            helper_fd = os.pidfd_open(int(ready_path.read_text()))
            check(
                self.gateway_state() == (False, False) and self.unit_state() == (False, False),
                "closed-PTY boundary did not mutate the connection state",
            )
            os.close(master)
            master = None
            ready_path.with_suffix(".release").touch()
            return process.wait(timeout=30)
        finally:
            if master is not None:
                os.close(master)
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if helper_fd is not None:
                if not select.select([helper_fd], [], [], 0)[0]:
                    signal.pidfd_send_signal(helper_fd, signal.SIGKILL)
                os.close(helper_fd)


# ------------------------------------------------------------ the checks ----


LEGACY_ROLES = ("imp-relay", "imp-gateway", "imp-feed", "imp-action-consumer", "imp-direct-wss")
LEGACY_EXEC = {
    "imp-relay.service": "imp-relay --host 127.0.0.1 --port 8787",
    "imp-feed.service": "imp-feed",
    "imp-gateway.service": "imp-gateway --host 127.0.0.1 --port 8788 --relay-url ws://127.0.0.1:8787",
}


def install_legacy(site, version):
    """Emulate a v0.3.2 install: .venv/bin console scripts (shell stand-ins that
    identify themselves), absolute current link, stable links, units, capture.tf."""
    data = site.home / ".local/share/imp"
    release = data / "releases" / version
    scripts = release / ".venv/bin"
    scripts.mkdir(parents=True)
    for name in LEGACY_ROLES:
        script = scripts / name
        script.write_text(f'#!/bin/sh\nprintf "legacy-{version} {name} %s\\n" "$*"\n')
        script.chmod(0o755)
    (release / "VERSION").write_text(version + "\n")
    (release / "BUNDLE_SHA256").write_text("0" * 64 + "\n")
    (data / "current").symlink_to(release)
    bin_dir = site.home / ".local/bin"
    bin_dir.mkdir(parents=True)
    for name in ("imp-action-consumer", "imp-direct-wss"):
        (bin_dir / name).symlink_to(data / "current/.venv/bin" / name)
    unit_dir = site.home / ".config/systemd/user"
    unit_dir.mkdir(parents=True)
    for unit, command in LEGACY_EXEC.items():
        gateway = "EnvironmentFile=%h/.config/imp/gateway.env\n" if "gateway" in unit else ""
        (unit_dir / unit).write_text(
            f"[Unit]\nDescription=legacy {unit}\n\n[Service]\nType=simple\n{gateway}"
            f"ExecStart=%h/.local/share/imp/current/.venv/bin/{command}\nRestart=on-failure\n\n"
            "[Install]\nWantedBy=default.target\n"
        )
    config = site.home / ".config/imp"
    config.mkdir(parents=True, exist_ok=True)
    config.chmod(0o700)
    (config / "capture.tf").write_text("; legacy capture\n")
    (config / "capture.tf").chmod(0o600)
    (site.home / ".local/state/imp").mkdir(parents=True)
    (site.home / ".local/state/imp").chmod(0o700)
    for unit in ("imp-relay.service", "imp-feed.service"):
        (site.units / f"enabled-{unit}").touch()
        (site.units / f"active-{unit}").touch()


def home_tree(home):
    """Every path under home (symlinks unfollowed) with its content or link target."""
    out = {}
    for base, dirs, files in os.walk(home):
        for name in (*dirs, *files):
            path = Path(base, name)
            out[str(path.relative_to(home))] = (
                os.readlink(path) if path.is_symlink() else path.read_bytes() if path.is_file() else "dir"
            )
    return out


def legacy_version(*bundles):
    """Lowest 0.3.2+legacy.fixture.N absent from every reserved artifact VERSION identity."""
    taken = {(bundle / "VERSION").read_text().strip() for bundle in bundles}
    return next(v for n in itertools.count() if (v := f"0.3.2+legacy.fixture.{n}") not in taken)


def subtree(tree, prefix):
    return {k: v for k, v in tree.items() if k == prefix or k.startswith(prefix + "/")}


def failed_relay_restart(site, bundle):
    """Guarded shipping install with an injected relay restart failure: the double observes the
    relay inactive after the failed restart (record lives outside HOME), then rollback restores it active."""
    active, record = site.units / "active-imp-relay.service", site.units / "failure-record"
    check(active.exists() and not record.exists(), "relay is not active before the injected failure")
    result = site.install(bundle, [], guarded=True, extra={"IMP_TEST_FAIL_RESTART": "imp-relay.service"})
    check(
        result.rc != 0 and "Restoring previous Imp installation" in result.out,
        "failed upgrade did not roll back",
        result,
    )
    check(
        record.read_text() == "imp-relay.service inactive\n",
        "failed relay restart was not observed inactive",
        result,
    )
    check(active.exists(), "rollback did not restore the active relay", result)
    return result


def legacy_upgrade(work, bundle, next_bundle, edge_bundle, uid):
    new_version = (bundle / "VERSION").read_text().strip()
    # A synthetic v0.3.2 .venv layout under a label no reserved artifact can collide with.
    old = legacy_version(*(b for b in (bundle, next_bundle) if b is not None))

    def legacy_site(name):
        site = Site(work, name)
        site.bundle = edge_bundle  # unguarded source-edge control only; installs below are shipping bundles
        install_legacy(site, old)
        site.set_gateway(True, True)
        site.write_credential()
        site.drive("enable", HOST, str(uid))
        site.drive("commit", str(uid))
        site.events.write_text("")
        return site

    def states(site):
        return (
            site.config().read_bytes(),
            site.gateway_state(),
            site.unit_state(),
            site.marker(),
            sorted(p.name for p in site.units.glob("*-imp-*.service")),
        )

    def run_legacy(site, result):
        """Execute the old release through the stable link and its restored unit's ExecStart."""
        done = subprocess.run(
            site.guarded([site.home / ".local/bin/imp-direct-wss", "status"]),
            env=site.env(),
            capture_output=True,
            text=True,
        )
        check(
            done.returncode == 0 and done.stdout == f"legacy-{old} imp-direct-wss status\n",
            "restored stable link does not run the old release",
            result,
        )
        unit = (site.home / ".config/systemd/user/imp-relay.service").read_text()
        command = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
        done = subprocess.run(
            site.guarded(command.removeprefix("ExecStart=").replace("%h", str(site.home)).split()),
            env=site.env(),
            capture_output=True,
            text=True,
        )
        check(
            done.stdout == f"legacy-{old} imp-relay --host 127.0.0.1 --port 8787\n",
            "restored unit ExecStart does not run the old release",
            result,
        )

    print("--- failed upgrade restores and executes the old release")
    site = legacy_site("legacy-failed")
    tree, before = home_tree(site.home), states(site)
    snap = site.snapshot()
    result = failed_relay_restart(site, bundle)
    check(home_tree(site.home) == tree, "failed upgrade did not restore the legacy tree exactly", result)
    check(states(site) == before and site.snapshot() == snap, "failed upgrade changed env/connection/service state", result)
    run_legacy(site, result)
    check(not (site.home / f".local/share/imp/releases/{new_version}").exists(), "failed release left behind", result)
    check(
        not (site.home / ".local/bin/imp-feed-watch").is_symlink(),
        "failed legacy upgrade retained the new watcher link",
        result,
    )

    print("--- foreign watcher helper is rejected without mutation")
    site = legacy_site("legacy-foreign-watcher")
    (site.home / ".local/bin/imp-feed-watch").symlink_to("/unrelated/feed-watch")
    tree, before = home_tree(site.home), states(site)
    result = site.install(bundle, [], guarded=True)
    check(result.rc != 0, "foreign watcher helper was replaced", result)
    check(home_tree(site.home) == tree and states(site) == before, "foreign watcher rejection mutated state", result)

    print("--- no-mode upgrade migrates links/units and preserves env, connection, edge, TinyFugue")
    site = legacy_site("legacy-upgrade")
    tree, before = home_tree(site.home), states(site)
    result = site.install(bundle, [], guarded=True)
    check(result.rc == 0 and "specify --connection" not in result.out, "legacy no-mode upgrade failed", result)
    data = site.home / ".local/share/imp"
    check(os.readlink(data / "current") == str(data / "releases" / new_version), "upgrade did not select the new release", result)
    for name in ("imp-action-consumer", "imp-feed-watch", "imp-direct-wss"):
        check(
            os.readlink(site.home / ".local/bin" / name) == str(data / "current/bin" / name),
            f"{name} link not migrated to the frozen layout",
            result,
        )
    units = site.home / ".config/systemd/user"
    check(
        f"ExecStart=%h/.local/share/imp/current/bin/imp-server relay --host 127.0.0.1 --port 8787"
        in (units / "imp-relay.service").read_text()
        and all(".venv" not in (units / name).read_text() for name in LEGACY_EXEC),
        "units were not migrated to imp-server roles",
        result,
    )
    check(states(site) == before, "upgrade changed env/connection/edge/service state", result)
    check(not site.events_list(), "no-mode upgrade invoked the public edge", result)
    after = home_tree(site.home)
    old_release = f".local/share/imp/releases/{old}"
    check(
        subtree(after, old_release) == subtree(tree, old_release),
        "upgrade modified or deleted the old release",
        result,
    )
    check(after[".tfrc"] == tree[".tfrc"], "upgrade changed TinyFugue startup", result)
    check(
        (site.home / ".config/imp/capture.tf").read_bytes() == (bundle / "capture.tf").read_bytes(),
        "capture.tf not refreshed",
        result,
    )
    check(
        not (data / "releases" / new_version / ".venv").exists(),
        "new release has a Python venv",
        result,
    )
    done = subprocess.run(
        site.guarded([site.home / ".local/bin/imp-action-consumer", "--help"]),
        env=site.env(),
        capture_output=True,
        text=True,
    )
    check(done.returncode == 0 and "--session" in done.stdout, "migrated stable link does not run the frozen consumer", result)
    done = subprocess.run(
        site.guarded([site.home / ".local/bin/imp-feed-watch", "--help"]),
        env=site.env(),
        capture_output=True,
        text=True,
        timeout=10,
    )
    check(done.returncode == 0 and "usage:" in done.stdout, "installed watcher helper does not run", result)
    if next_bundle is not None:
        print("--- upgrade between two real new-layout releases")
        next_version = (next_bundle / "VERSION").read_text().strip()
        check(next_version != new_version, "NEXT_BUNDLE_DIR must be a distinct release")
        before = states(site)
        result = site.install(next_bundle, [], guarded=True)
        check(result.rc == 0, "new-to-new upgrade failed", result)
        check(os.readlink(data / "current") == str(data / "releases" / next_version), "new-to-new did not switch", result)
        check(states(site) == before and (data / "releases" / new_version).is_dir(), "new-to-new changed state", result)


def frozen_upgrade(work, a, b, edge_a, uid):
    """Genuine frozen A -> B upgrade and failed-B rollback on the shipping binaries (no launcher)."""
    spec = importlib.util.spec_from_file_location("frozen_server", SELF.with_name("test-frozen-server.py"))
    frozen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(frozen)
    va, vb = ((bundle / "VERSION").read_text().strip() for bundle in (a, b))
    sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()  # noqa: E731
    ha, hb = sha(a / "bin/imp-server"), sha(b / "bin/imp-server")
    check(va != vb and ha != hb, "A and B must differ in version and native executable")
    for runtime, mine, other in ((a, va, vb), (b, vb, va)):
        accepted, rejected = (
            subprocess.run(
                frozen.guarded([runtime / "bin/imp-server", "runtime-check", version], runtime),
                capture_output=True,
            ).returncode
            for version in (mine, other)
        )
        check(accepted == 0 and rejected != 0, f"{mine}: embedded version is not distinct from {other}")

    site = Site(work, "frozen-upgrade")
    site.bundle = edge_a  # edge ownership boundary only; every install below is the shipping bundle
    data = site.home / ".local/share/imp"
    token = secrets.token_urlsafe(32)
    legacy = legacy_version(a, b)

    def states():
        return (
            site.config().read_bytes(),
            site.gateway_state(),
            site.unit_state(),
            site.marker(),
            sorted(p.name for p in site.units.glob("*-imp-*.service")),
        )

    def selected(version, digest):
        """current and every alias/stable link are the one native executable of that release."""
        release = data / "releases" / version
        check(os.readlink(data / "current") == str(release), f"current does not select {version}")
        server = release / "bin/imp-server"
        check(sha(server) == digest, f"{version}: installed executable bytes differ from its bundle")
        for role in (
            "relay",
            "gateway",
            "feed",
            "feed-watch",
            "action-consumer",
            "direct-wss",
            "direct-edge",
            "public-verify",
        ):
            check(
                os.path.samestat((data / "current/bin" / f"imp-{role}").stat(), server.stat()),
                f"{version}: imp-{role}",
            )
        for name in ("imp-action-consumer", "imp-feed-watch", "imp-direct-wss"):
            link = site.home / ".local/bin" / name
            check(os.readlink(link) == str(data / "current/bin" / name), f"{name} stable link")
            check(os.path.samestat(link.stat(), server.stat()), f"{version}: stable {name} is not its alias")
        frozen.verify_installed(site.home, work, version, token)

    def retained(result=None):
        """The synthetic legacy release is neither A nor B and stays byte-identical beside them."""
        release = f".local/share/imp/releases/{legacy}"
        check(legacy not in (va, vb), "legacy fixture collides with A or B", result)
        check(
            (data / "releases" / legacy / "VERSION").read_text() == legacy + "\n"
            and subtree(home_tree(site.home), release) == legacy_tree,
            "retained legacy release changed",
            result,
        )

    print("--- begin: legacy fixture -> A on the same site, legacy retained")
    install_legacy(site, legacy)
    site.set_gateway(True, True)
    site.config().write_text(f"IMP_GATEWAY_TOKEN_SHA256={hashlib.sha256(token.encode()).hexdigest()}\n")
    site.config().chmod(0o600)
    site.drive("enable", HOST, str(uid))  # unguarded source-edge control
    site.drive("commit", str(uid))
    site.events.write_text("")
    legacy_tree = subtree(home_tree(site.home), f".local/share/imp/releases/{legacy}")
    before = states()
    result = site.install(a, [], guarded=True)
    check(result.rc == 0 and states() == before, "legacy -> A upgrade failed or changed state", result)
    check(not site.events_list(), "legacy -> A upgrade invoked the public edge", result)
    selected(va, ha)
    retained(result)
    tree, snap = home_tree(site.home), site.snapshot()

    print("--- failed B restores and re-runs A")
    result = failed_relay_restart(site, b)
    check(home_tree(site.home) == tree and states() == before, "failed B did not restore A exactly", result)
    check(
        site.snapshot() == snap and not (data / "releases" / vb).exists(),
        "failed B left state or release",
        result,
    )
    selected(va, ha)
    retained(result)

    print("--- successful A -> B keeps credentials, services, edge and the A runtime")
    result = site.install(b, [], guarded=True)
    check(result.rc == 0 and states() == before, "A -> B upgrade failed or changed state", result)
    check(not site.events_list(), "A -> B upgrade invoked the public edge", result)
    selected(vb, hb)
    check(
        (site.home / ".tfrc").read_bytes() == tree[".tfrc"]
        and (site.home / ".config/imp/capture.tf").read_bytes() == (b / "capture.tf").read_bytes(),
        "A -> B changed TinyFugue startup or did not refresh capture.tf",
        result,
    )
    retained(result)
    check(sha(data / "releases" / va / "bin/imp-server") == ha, "A runtime was not retained for rollback")

def startup_cases(work, bundle, *, guarded=True):
    """Real installer selection, byte preservation, ordering, and rollback."""
    hook = b"/load ~/.config/imp/capture.tf"
    serial = itertools.count()

    def site():
        return Site(work, f"startup-{next(serial)}")

    def install(s, *args, **kwargs):
        result = s.install(bundle, ["--connection", "none", *args], guarded=guarded, **kwargs)
        return result

    def metadata(path):
        st = path.stat()
        return st.st_uid, st.st_gid, st.st_mode

    def completed(result, path, status):
        check(result.rc == 0, "startup install failed", result)
        for text in (str(path), status, "tf -f FILE", "current TF session",
                     "matching normalized checkpoint", "without that identity"):
            check(text in result.out, f"missing onboarding output: {text}", result)
        check("integration: configured" not in result.out, "unqualified configured claim", result)

    for original in (b"/connect game.example 1234\n/echo retained\n",
                     b"/connect game.example 1234\r\n/echo retained\r\n",
                     b"/connect game.example 1234", b"; comment\n\n  /connect retained\n",
                     b"; column-zero comment\n \t \n \t/connect retained\n\t /echo retained\n",
                     b"# column-zero comment\n\t \t\n\t /connect retained\n \t/echo retained\n", b""):
        s = site()
        path = s.home / ".tfrc"
        path.write_bytes(original)
        path.chmod(0o640)
        before = metadata(path)
        result = install(s, "--no-start")
        completed(result, path, "inserted before original startup commands")
        newline = b"\r\n" if original.startswith(b"/connect game.example 1234\r\n") else b"\n"
        expected = hook + newline + original
        check(path.read_bytes() == expected and metadata(path) == before, "prepend lost bytes or metadata", result)
        backups = list(s.home.glob(".tfrc.imp-backup.*"))
        check(len(backups) == 1 and backups[0].read_bytes() == original, "original backup missing", result)
        result = install(s, "--no-start")
        completed(result, path, "already exact")
        check(path.read_bytes() == expected and len(list(s.home.glob(".tfrc.imp-backup.*"))) == 1,
              "reinstall duplicated or rewrote hook", result)
        path.write_bytes(original)
        result = install(s, "--no-start")
        check(result.rc == 0 and len(list(s.home.glob(".tfrc.imp-backup.*"))) == 2
              and backups[0].read_bytes() == original, "reinstall overwrote prior backup", result)

    s = site()
    default = s.home / ".tfrc"
    default.unlink()
    custom = s.home / "custom startup.tf"
    custom.write_bytes(b"/connect retained\n")
    result = install(s, "--no-start", tty=True, keys=str(custom) + "\n")
    completed(result, custom, "inserted before original startup commands")
    check("Enter an existing TinyFugue startup file" in result.out and not default.exists(),
          "missing default did not select an existing file", result)
    check(custom.read_bytes() == hook + b"\n/connect retained\n", "custom path with spaces mishandled", result)

    s = site()
    default = s.home / ".tfrc"
    untouched = default.read_bytes()
    custom = s.home / "explicit startup.tf"
    custom.write_bytes(b"/connect retained\n")
    result = install(s, "--no-start", "--tf-startup", str(custom))
    completed(result, custom, "inserted before original startup commands")
    check(default.read_bytes() == untouched and custom.read_bytes() == hook + b"\n/connect retained\n",
          "explicit startup override changed wrong file", result)

    for kind in ("missing-default", "missing-explicit", "directory", "dangling", "fifo", "readonly"):
        s = site()
        default = s.home / ".tfrc"
        default.unlink()
        path = s.home / "invalid startup"
        if kind == "directory":
            path.mkdir()
        elif kind == "dangling":
            path.symlink_to(s.home / "absent")
        elif kind == "fifo":
            os.mkfifo(path)
        elif kind == "readonly":
            path.write_bytes(b"/echo retained\n")
            path.chmod(0o400)
        before = home_tree(s.home)
        args = [] if kind == "missing-default" else ["--tf-startup", str(path)]
        result = install(s, "--no-start", *args, tty=kind == "missing-explicit", timeout=30)
        check(result.rc != 0 and "TinyFugue startup" in result.out, f"{kind} not rejected", result)
        if kind == "missing-explicit":
            check("Enter an existing TinyFugue startup file" not in result.out,
                  "explicit nonexistent startup prompted instead of failing", result)
        check(home_tree(s.home) == before and not (s.home / ".local").exists(),
              f"{kind} mutated installation before rejection", result)

    for original in (hook + b"\n/connect retained\n", b"/connect retained\n" + hook + b"\n",
                     b"  \t" + hook + b"\n/connect retained\n",
                     hook + b"\r\n" + hook + b"\r\n", b"\xef\xbb\xbf/connect retained\r\n"):
        s = site()
        path = s.home / ".tfrc"
        path.write_bytes(original)
        result = install(s, "--no-start", "--tf-startup", str(path))
        completed(result, path, "load order not established")
        check(path.read_bytes() == original and not list(s.home.glob(".tfrc.imp-backup.*")),
              "existing hook or BOM file rewritten", result)
        if original.count(hook) > 1:
            check("multiple Imp hook loads found" in result.out, "duplicate hook warning absent", result)
        if original.startswith(b"\xef\xbb\xbf"):
            check("No new hook inserted" in result.out and "Manually arrange" in result.out,
                  "BOM manual action missing", result)
        if original.startswith(b"  \t"):
            check("existing hook preserved; no duplicate added" in result.out and "already exact" not in result.out,
                  "indented hook falsely described as exact canonical line", result)

    for original in (b"# legacy worlds\nworld character password game.example 1234\n",
                     b"  world character password game.example 1234\n/connect retained\n",
                     b"\t/eval /load %{startup}\n/connect retained\n",
                     b"  /require %{startup}\n/connect retained\n",
                     *(prefix + marker + b" indented comment\n" + commands
                       for prefix in (b"  ", b"\t", b" \t ")
                       for marker in (b";", b"#")
                       for commands in (b"/connect retained\n",
                                        b"world character password game.example 1234\n/connect retained\n"))):
        s = site()
        path = s.home / ".tfrc"
        path.write_bytes(original)
        result = install(s, "--no-start")
        completed(result, path, "load order not established")
        check(path.read_bytes() == original and not list(s.home.glob(".tfrc.imp-backup.*")),
              "legacy, indented comment, or computed startup content rewritten", result)
        check("No new hook inserted" in result.out and "Manually arrange" in result.out,
              "legacy, indented comment, or computed startup lacks manual action", result)
        if b"world character" in original:
            check("after legacy world definitions" in result.out and "/addworld" in result.out
                  and "Do not blindly prepend" in result.out, "legacy format guidance missing", result)

    for alias in (False, True):
        s = site()
        path = s.home / ".tfrc"
        capture = s.home / ".config/imp/capture.tf"
        target = capture
        if alias:
            capture.parent.mkdir(parents=True)
            capture.write_bytes(b"; existing capture fixture\n")
            target = s.home / "capture alias.tf"
            target.symlink_to(capture)
        original = f"  /load {target}\n/connect retained\n".encode()
        path.write_bytes(original)
        for _ in range(2):
            result = install(s, "--no-start")
            completed(result, path, "existing hook preserved; no duplicate added")
            check("already exact" not in result.out, "absolute or alias hook falsely described as exact", result)
            check(path.read_bytes() == original and not list(s.home.glob(".tfrc.imp-backup.*")),
                  "absolute or alias hook duplicated on fresh install/reinstall", result)

    for prefix in ("absolute", "home", "relative", "deeper", "duplicates"):
        s = site()
        path = s.home / ".tfrc"
        nested = s.home / "nested startup.tf"
        nested.write_bytes(hook + b"\r\n")
        include = str(nested) if prefix == "absolute" else "~/nested startup.tf"
        if prefix == "relative":
            include = "nested startup.tf"
        elif prefix == "deeper":
            nested.write_bytes(b"/load ~/deeper.tf\n")
            (s.home / "deeper.tf").write_bytes(hook + b"\n")
        elif prefix == "duplicates":
            nested.write_bytes(hook + b"\n" + hook + b"\n")
        original = f"/load {include}\n/connect retained\n".encode()
        path.write_bytes(original)
        result = install(s, "--no-start")
        completed(result, path, "load order not established")
        check(path.read_bytes() == original, "nested integration rewritten or duplicated", result)
        if prefix in ("relative", "deeper"):
            check("No new hook inserted" in result.out and "Manually arrange" in result.out,
                  "unsupported include lacked manual action", result)
        else:
            check("nested hook preserved" in result.out, "nested hook was not detected", result)
        if prefix == "duplicates":
            check("multiple Imp hook loads found" in result.out, "nested duplicate warning absent", result)

    s = site()
    path = s.home / ".tfrc"
    original = b"/connect retained\r\n/echo exact rollback\r\n"
    path.write_bytes(original)
    path.chmod(0o640)
    before = metadata(path)
    result = install(s, extra={"IMP_TEST_FAIL_RESTART": "imp-feed.service"})
    check(result.rc != 0 and (s.units / "failed-once").exists(), "post-mutation failure not reached", result)
    check(path.read_bytes() == original and metadata(path) == before, "startup rollback lost bytes or metadata", result)
    check(not list(s.home.glob(".tfrc.imp-backup.*")), "rollback stranded startup backup", result)
    print("TinyFugue startup onboarding: PASS")


def installer_shims(work):
    shims = work / "shims"
    shims.mkdir()
    for name, text in (
        ("systemctl", SYSTEMCTL),
        ("cp", CP_SHIM),
        *((n, PYTHON_GUARD) for n in ("python", "python3", "python3.12", "pip", "pip3")),
    ):
        (shims / name).write_text(text)
        (shims / name).chmod(0o755)
    return shims


def startup_main(bundle):
    with tempfile.TemporaryDirectory(prefix="imp-tf-startup-") as directory:
        work = Path(directory)
        installer_shims(work)
        startup_cases(work, Path(bundle))



def main(bundle, next_bundle):
    work = Path(tempfile.mkdtemp(prefix="imp-connections-"))
    try:
        run_all(work, Path(bundle), Path(next_bundle) if next_bundle else None)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def edge_fixture_bundle(bundle, work, name):
    """Copy a bundle (preserving hardlinked aliases) with a fixture bin/imp-server."""
    target = work / name
    work.mkdir(exist_ok=True)
    subprocess.run([shutil.which("cp"), "-a", str(bundle), str(target)], check=True)
    launcher = target / "bin/imp-server"
    launcher.unlink()  # never write through a hardlinked alias
    launcher.write_text(LAUNCHER)
    launcher.chmod(0o755)
    digest = hashlib.sha256(launcher.read_bytes()).hexdigest()
    sums = target / "SHA256SUMS"
    sums.write_text(
        "".join(
            (f"{digest}  bin/imp-server" if line.endswith("  bin/imp-server") else line) + "\n"
            for line in sums.read_text().splitlines()
        )
    )
    return target


def run_all(work, shipping, next_shipping):
    shims = installer_shims(work)
    startup_cases(work, shipping)
    bundle = edge_fixture_bundle(shipping, work / "fixtures", "bundle")
    next_bundle = edge_fixture_bundle(next_shipping, work / "fixtures", "next") if next_shipping else None
    uid = os.getuid()
    check(uid != 0, "run as a normal user")

    def refuses(site, result, snap, why):
        check(result.rc != 0, f"{why}: installer unexpectedly succeeded", result)
        check("Imp Direct WSS is ready" not in result.out, f"{why}: printed success", result)
        check(not site.wss_calls(), f"{why}: credential provisioning ran", result)
        check(site.snapshot() == snap, f"{why}: installer state changed", result)

    print("=== shipping bundle: direct-edge safe commands run frozen, no host Python ===")
    guard_abi = subprocess.run([DEV_PYTHON, str(GUARD), "--abi"], capture_output=True, text=True)
    check(guard_abi.returncode == 0, f"Landlock exec guard unavailable (required): {guard_abi.stderr.strip()}")
    safe_home = work / "safe-home"
    safe_home.mkdir()
    safe_env = {**os.environ, "PATH": f"{shims}:{os.environ['PATH']}", "HOME": str(safe_home)}
    for command in ([shipping / "bin/imp-server", "direct-edge"], [shipping / "bin/imp-direct-edge"]):
        for edge_args, rc, text in (
            (["hostname", HOST], 0, ""),
            (["hostname", "https://imp.example.com"], 1, "hostname must be"),
            ([], 1, "usage:"),
            (["no-such-command"], 1, "usage:"),
        ):
            done = subprocess.run(
                [DEV_PYTHON, str(GUARD), "--allow", str(shipping), "--allow", str(safe_home), "--"]
                + [str(part) for part in (*command, *edge_args)],
                env=safe_env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )
            check(
                done.returncode == rc and text in done.stderr,
                f"shipping {command[-1]} {edge_args}: rc={done.returncode} {done.stderr!r}",
            )

    print("=== mode matrix: rejections and fresh-install prompts ===")
    cases = [
        ("no mode, non-interactive", [], False, "specify --connection"),
        (
            "hostname with ssh",
            ["--connection", "ssh", "--hostname", HOST],
            False,
            "only with --connection direct",
        ),
        (
            "hostname with none",
            ["--connection", "none", "--hostname", HOST],
            False,
            "only with --connection direct",
        ),
        (
            "direct with no-start",
            ["--connection", "direct", "--hostname", HOST, "--no-start"],
            True,
            "--no-start",
        ),
        ("direct without hostname, non-interactive", ["--connection", "direct"], False, "--hostname"),
        ("new direct without tty", ["--connection", "direct", "--hostname", HOST], False, "interactive"),
        ("NAT with ssh", ["--connection", "ssh", "--behind-nat"], False, "only with"),
        ("NAT with none", ["--connection", "none", "--behind-nat"], False, "only with"),
        (
            "NAT direct with no-start",
            ["--connection", "direct", "--hostname", HOST, "--behind-nat", "--no-start"],
            True,
            "--no-start",
        ),
        (
            "duplicate NAT",
            ["--connection", "direct", "--hostname", HOST, "--behind-nat", "--behind-nat"],
            True,
            "only once",
        ),
        ("duplicate mode", ["--connection", "ssh", "--connection", "none"], False, "only once"),
        ("bogus mode", ["--connection", "wss"], False, "ssh|direct|none"),
    ]
    for label, args, tty, message in cases:
        site = Site(work, "reject")
        result = site.install(bundle, args, tty=tty)
        check(result.rc != 0 and message in result.out, f"{label}: not rejected with '{message}'", result)
        check(site.pristine(), f"{label}: mutated HOME", result)
        check(
            not any(e.startswith(("edge enable", "wss")) for e in site.events_list()),
            f"{label}: ran edge/provisioning",
        )
        shutil.rmtree(site.root)
    for bad in (
        "https://imp.example.com",
        "203.0.113.5",
        f"{HOST}:443",
        "localhost",
        f"{HOST}/x",
        "a b.example.com",
    ):
        site = Site(work, "badhost")
        result = site.install(bundle, ["--connection", "direct", "--hostname", bad], tty=True)
        check(result.rc != 0, f"hostname {bad!r} accepted", result)
        check(site.pristine(), f"hostname {bad!r}: mutated HOME", result)
        check(
            not any(e.startswith(("edge preflight", "edge dns", "edge enable", "wss")) for e in site.events_list()),
            f"{bad!r}: went past hostname validation",
        )
        shutil.rmtree(site.root)
    site = Site(work, "choice")
    result = site.install(bundle, [], tty=True, keys="9\n")
    check(
        result.rc != 0 and "How will the Imp desktop connect?" in result.out,
        "interactive prompt missing/accepted bad choice",
        result,
    )
    check(site.pristine(), "bad interactive choice mutated HOME", result)
    shutil.rmtree(site.root)

    print("=== read-only Direct preflight: NAT offers, inspection/DNS failures, and revalidation ===")

    def preflight_refuses(name, updates, *, tty=True, keys="1\n", message, offer=False):
        candidate = Site(work, "preflight-" + name)
        candidate.set_edge(**updates)
        before = (home_tree(candidate.home), home_tree(candidate.units), home_tree(candidate.edge_root))
        result = candidate.install(
            bundle, ["--connection", "direct", "--hostname", HOST], tty=tty, keys=keys
        )
        check(result.rc != 0 and message in result.out, f"{name}: missing diagnostic {message!r}", result)
        check(("Choice [2]" in result.out) == offer, f"{name}: incorrect NAT offer", result)
        unit_tree = home_tree(candidate.units)
        manager_calls = unit_tree.pop("calls.log", b"").decode().splitlines()
        check(all(call.split()[0] in ("is-enabled", "is-active", "show") for call in manager_calls),
              f"{name}: mutated user manager: {manager_calls}", result)
        check(
            before == (home_tree(candidate.home), unit_tree, home_tree(candidate.edge_root))
            and not candidate.wss_calls() and not candidate.edge().get("sudo_calls")
            and all(call.startswith("ip ") for call in candidate.edge()["calls"])
            and all(event.startswith(("edge hostname ", "edge preflight ", "edge dns "))
                    for event in candidate.events_list()),
            f"{name}: preflight changed files, credentials, services, or public edge",
            result,
        )
        check("Imp Direct WSS is ready" not in result.out, f"{name}: claimed readiness", result)
        if not offer:
            check("--behind-nat" not in result.out and "1. Yes" not in result.out,
                  f"{name}: suggested NAT after rejection", result)
        if f"edge preflight {HOST} rc=1" in candidate.events_list():
            check("verify DNS and iproute2" in result.out
                  and "ip -j address show scope global" in result.out
                  and not any(event.startswith("edge dns ") for event in candidate.events_list()),
                  f"{name}: failed inspection lacked recovery guidance or retried DNS-only", result)
        shutil.rmtree(candidate.root)

    eligible = (
        ("mismatch", {}, "not assigned"),
        ("private-only", {"addrs": ["192.168.1.12"]}, "no public interface"),
        ("empty-interfaces", {"ip_output": "[]"}, "no public interface"),
        ("empty-entry", {"ip_output": '[{"addr_info": [{}]}]'}, "no public interface"),
    )
    for name, updates, fact in eligible:
        updates = {"dns": {HOST: [WRONG]}, **updates}
        for choice in ("\n", "2\n"):
            preflight_refuses(name + "-decline-" + str(len(choice)), updates, keys=choice,
                              message=fact, offer=True)
        preflight_refuses(name + "-noninteractive", updates, tty=False,
                          message="interactive topology decision")
        preflight_refuses(
            name + "-revalidation", {**updates, "dns_sequence": [{HOST: [WRONG]}, {HOST: [WRONG, GLOBAL]}]},
            message="exactly one global IPv4", offer=True,
        )
        preflight_refuses(
            name + "-revalidation-failure", {**updates, "dns_sequence": [{HOST: [WRONG]}, {}]},
            message="does not resolve", offer=True,
        )

    for failure in ("missing", "unexecutable", "timeout", "nonzero"):
        preflight_refuses(failure, {"ip_failure": failure, "dns": {HOST: [WRONG]}},
                          message="injected ip inspection failure" if failure == "nonzero" else "could not run ip")
    malformed_ip = (
        "", "{", "{}", "null", "[null]", "[false]", "[0]", '[""]', "[[]]", "[{}]",
        '[{"addr_info": null}]', '[{"addr_info": {}}]', '[{"addr_info": ""}]',
        '[{"addr_info": [null]}]', '[{"addr_info": [false]}]', '[{"addr_info": [0]}]',
        '[{"addr_info": [""]}]', '[{"addr_info": [[]]}]', '[{"addr_info": [{"family": "inet"}]}]',
        '[{"addr_info": [{"local": null}]}]', '[{"addr_info": [{"local": 1234}]}]',
        '[{"addr_info": [{"local": "invalid"}]}]',
    )
    for number, raw in enumerate(malformed_ip):
        preflight_refuses(f"malformed-{number}", {"ip_output": raw, "dns": {HOST: [WRONG]}},
                          message="could not read this server's addresses")
    for name, updates, diagnostic in (
        ("resolver", {"resolver_failure": True}, "does not resolve"),
        ("missing-dns", {"dns": {}}, "does not resolve"),
        ("empty-dns", {"dns": {HOST: []}}, "does not resolve"),
        ("malformed-dns", {"dns": {HOST: ["invalid"]}}, "unparseable"),
        ("private-dns", {"dns": {HOST: ["10.0.0.5"]}}, "non-public"),
        ("cgnat-dns", {"dns": {HOST: ["100.64.1.2"]}}, "non-public"),
        ("multi-a", {"dns": {HOST: [WRONG, "1.1.1.1"]}}, "update DNS"),
        ("mismatch-dual", {"dns": {HOST: [WRONG, "2606:2800:220:1::1"]}}, "update DNS"),
        ("no-public-multi", {"addrs": [], "dns": {HOST: [WRONG, GLOBAL]}}, "no globally routable address"),
        ("no-public-dual", {"addrs": [], "dns": {HOST: [WRONG, "2606:2800:220:1::1"]}},
         "no globally routable address"),
        ("ipv6-only", {"dns": {HOST: ["2606:2800:220:1::1"]}}, "update DNS"),
        ("private-dual", {"dns": {HOST: [GLOBAL, "fd00::1"]}}, "non-public"),
    ):
        preflight_refuses(name, updates, message=diagnostic)
    for name, updates in (
        ("matching", {}),
        ("matching-dual", {"dns": {HOST: [GLOBAL, "2606:2800:220:1::1"]},
                           "addrs": [GLOBAL, "2606:2800:220:1::1"]}),
    ):
        preflight_refuses(name, updates, tty=False, message="requires interactive stdin and stdout")

    site = Site(work, "dns")
    site.set_edge(dns={HOST: [WRONG]})
    result = site.install(bundle, [], tty=True, keys=f"2\n{HOST}\n\n")
    check(result.rc != 0 and "router/NAT" in result.out and site.pristine(),
          "interactive hostname selection skipped NAT authorization", result)
    for addresses in ([WRONG, GLOBAL], [WRONG, "2606:2800:220:1::1"], ["100.64.1.2"]):
        site.set_edge(dns={HOST: addresses})
        result = site.install(
            bundle, ["--connection", "direct", "--hostname", HOST, "--behind-nat"], tty=True
        )
        check(result.rc != 0 and site.pristine() and not site.wss_calls(),
              f"invalid explicit NAT DNS mutated installation: {addresses}", result)
    shutil.rmtree(site.root)

    print("=== fresh interactive SSH (builds the runtime) ===")
    site = Site(work, "fresh-ssh")
    result = site.install(bundle, [], tty=True, keys="1\n")
    check(
        result.rc == 0 and "Imp is ready for SSH connections" in result.out,
        "interactive SSH install failed",
        result,
    )
    check("Managed SSH" in result.out and "External SSH" in result.out, "SSH instructions missing", result)
    check(
        not site.config().exists() and site.gateway_state() == (False, False),
        "fresh SSH touched Direct state",
        result,
    )
    check(
        site.marker() is None and not any(e.startswith(("edge enable", "wss")) for e in site.events_list()),
        "fresh SSH created public edge",
    )
    shutil.rmtree(site.root)

    print("=== no mode on an install detected only by user-manager state (builds the runtime) ===")
    site = Site(work, "manager-signal")
    site.set_gateway(enabled=True)
    result = site.install(bundle, [])
    check(
        result.rc == 0 and "specify --connection" not in result.out,
        "manager-state-only install treated as fresh",
        result,
    )
    check(
        site.gateway_state() == (True, False) and not site.config().exists(),
        "manager-state install changed gateway/credentials",
        result,
    )
    check(site.events_list() == ["edge migration-needed rc=0"], "no-mode install mutated the edge")
    shutil.rmtree(site.root)

    print("=== baseline existing installation ===")
    site = Site(work, "base")
    result = site.install(bundle, ["--connection", "none"])
    check(result.rc == 0 and site.pristine() is False, "baseline none install failed", result)
    check(
        site.gateway_state() == (False, False) and not site.config().exists(),
        "fresh none configured Direct",
        result,
    )
    check(site.marker() is None and not site.events_list(), "fresh none touched the public edge", result)
    site.bundle = bundle
    release = Path(os.readlink(site.home / ".local/share/imp/current"))
    check(
        not (release / ".venv").exists() and not (release / "wheels").exists(),
        "release carries a Python venv or wheelhouse",
    )
    provisioner = release / "bin/imp-direct-wss"
    provisioner.unlink()  # fixture replacement: never write through the hardlinked alias
    provisioner.write_text(PROVISIONER.format(python=DEV_PYTHON))
    provisioner.chmod(0o755)
    base_bundle = bundle

    def direct(extra=None, tty=True, args=None, behind_nat=False, keys=""):
        options = ["--connection", "direct", "--hostname", HOST]
        if behind_nat:
            options.append("--behind-nat")
        return site.install(
            base_bundle,
            args or options,
            tty=tty,
            extra=extra,
            keys=keys,
        )

    print("=== NAT explicit/interactive authorization, loopback handoff, and preservation ===")
    for authorized, updates in (
        (True, {"addrs": ["192.168.1.12"]}),
        *((False, updates) for _, updates, _ in eligible),
        (False, {"dns_sequence": [{HOST: [WRONG]}, {HOST: ["1.1.1.1"]}]}),
    ):
        site.reset()
        site.set_edge(dns={HOST: [WRONG]}, **updates)
        result = direct(behind_nat=authorized, keys="" if authorized else "1\n")
        check(result.rc == 0 and "Imp Direct WSS is ready." in result.out, "NAT Direct failed", result)
        check(
            ("router/NAT" in result.out) == (not authorized),
            "explicit NAT prompted or interactive mismatch skipped authorization",
            result,
        )
        events = site.events_list()
        dns_event = f"edge dns {HOST} --behind-nat rc=0"
        check(dns_event in events and events.index(dns_event) < events.index("edge enable rc=0")
              and (f"edge preflight {HOST} rc=0" in events) == (not authorized),
              "NAT did not revalidate DNS before enabling the edge", result)
        if not authorized:
            check(events.index(f"edge preflight {HOST} rc=0") < events.index(dns_event)
                  and "TCP 443 is forwarded here" in result.out
                  and WRONG in result.out and HOST in result.out,
                  "NAT authorization omitted observed facts or explicit forwarding affirmation", result)
        check(
            site.wss_calls()[0]["argv"]
            == ["setup", "--public-url", f"wss://{HOST}/state", "--verify-via-loopback"]
            and site.wss_calls()[0]["stdin_tty"] == site.wss_calls()[0]["stdout_tty"] == result.tty,
            "NAT provisioner did not inherit terminal with loopback verification",
            result,
        )
        config = (site.edge_root / "etc/imp/Caddyfile").read_text()
        check(
            "disable_http_challenge" in config
            and "disable_tlsalpn_challenge" not in config
            and "127.0.0.1:8788" in config
            and "8787" not in config,
            "NAT ACME/upstream policy is incorrect",
            result,
        )
        check(
            all(probe[2] is True for probe in site.edge()["https"]["calls"])
            and site.marker()["behind_nat"] is True
            and site.marker()["version"] == 3 and site.marker()["pending"] is None,
            "NAT readiness/ownership was not recorded",
            result,
        )
        for args in (
            [],
            ["--connection", "none"],
            ["--connection", "direct", "--hostname", HOST, "--behind-nat"],
        ):
            snap = site.snapshot()
            edge_state = (site.marker(), site.unit_state(), config)
            site.events.write_text("")
            site.wss_log.write_text("")
            result = site.install(bundle, args)
            check(result.rc == 0 and site.snapshot() == snap, "NAT upgrade changed identity/gateway", result)
            check(
                edge_state
                == (site.marker(), site.unit_state(), (site.edge_root / "etc/imp/Caddyfile").read_text())
                and not site.wss_calls()
                and not any(e.startswith(("edge enable", "edge dns", "edge preflight")) for e in site.events_list()),
                "NAT upgrade reprovisioned edge or claimed a new handoff",
                result,
            )
    site.reset()
    operator = site.edge_root / "etc/caddy/Caddyfile"
    operator.parent.mkdir()
    operator.write_text("operator configuration\n")
    snap = site.snapshot()
    result = direct(behind_nat=True)
    refuses(site, result, snap, "NAT operator Caddy")
    check(
        operator.read_text() == "operator configuration\n" and site.unit_state() == (False, False),
        "NAT adopted operator edge",
        result,
    )
    site.reset()
    print("=== no-option upgrade and none preserve every connection state ===")
    owned_edge_state = None
    for label, args in (("no option", []), ("none", ["--connection", "none"])):
        site.reset(gateway=(True, True))
        site.write_credential()
        site.drive("enable", HOST, str(uid))
        site.drive("commit", str(uid))
        site.events.write_text("")
        owned_edge_state = (
            site.unit_state(),
            site.marker(),
            (site.edge_root / "etc/imp/Caddyfile").read_bytes(),
        )
        snap = site.snapshot()
        result = site.install(bundle, args)
        check(result.rc == 0, f"{label} upgrade failed", result)
        check(site.snapshot() == snap, f"{label} changed gateway/credential/runtime state", result)
        check(site.gateway_state() == (True, True), f"{label} changed gateway enabled/active state", result)
        check(
            owned_edge_state
            == (site.unit_state(), site.marker(), (site.edge_root / "etc/imp/Caddyfile").read_bytes()),
            f"{label} changed the public edge",
            result,
        )
        check(site.events_list() == ["edge migration-needed rc=0"], f"{label} mutated the edge", result)
    site.reset(gateway=(False, False))
    site.set_gateway(enabled=True)
    site.write_credential()
    snap = site.snapshot()
    result = site.install(bundle, [])
    check(
        result.rc == 0 and site.gateway_state() == (True, False),
        "orphan/enabled-inactive gateway not preserved",
        result,
    )
    check(
        site.snapshot() == snap and site.events_list() == ["edge migration-needed rc=0"],
        "orphan credentials changed by no-option install",
        result,
    )

    for args in ([], ["--connection", "none"]):
        site.reset()
        site.set_edge(sudo_unavailable=True)
        snap, edge_state = site.snapshot(), site.edge()
        result = site.install(bundle, args)
        check(
            result.rc == 0 and site.snapshot() == snap and site.edge() == edge_state
            and site.gateway_state() == (False, False) and site.marker() is None
            and not site.config().exists() and not site.events_list() and not site.wss_calls(),
            f"unconfigured {args} queried the edge or changed connection state",
            result,
        )

    print("=== exact legacy Direct/NAT policy migration retains identity and operating state ===")
    for version, mode, gateway in itertools.product(
        (1, 2), ("no option", "none", "existing Direct"),
        ((True, True), (True, False), (False, True)),
    ):
        site.reset(gateway=gateway)
        site.write_credential()
        site.owned_edge(version, behind_nat=version == 2)
        active = gateway[1]
        site.set_edge(unit={"enabled": active, "active": active})
        if version == 2:
            site.set_edge(addrs=["192.168.1.12"])
        snap, edge_tree = site.snapshot(), home_tree(site.edge_root)
        sudo_before = site.edge()["sudo_calls"]
        args = {
            "no option": [],
            "none": ["--connection", "none"],
            "existing Direct": ["--connection", "direct", "--hostname", OTHER],
        }[mode]
        result = site.install(bundle, args, tty=True)
        check(result.rc == 0, f"v{version} {mode} {gateway} migration failed", result)
        check(
            site.snapshot() == snap and not site.wss_calls()
            and "How will the Imp desktop connect?" not in result.out
            and "PAIRING-TOKEN:" not in result.out,
            "migration changed identity/runtime or provisioned/prompted",
            result,
        )
        marker = site.marker()
        check(
            marker["version"] == 3 and marker["hostname"] == HOST
            and marker["behind_nat"] == (version == 2) and marker["pending"] is None
            and site.gateway_state() == gateway
            and site.edge()["sudo_calls"] > sudo_before
            and site.unit_state() == (active, active),
            "migration changed retained topology/hostname/service state",
            result,
        )
        after = home_tree(site.edge_root)
        changed = {name for name in after.keys() | edge_tree.keys() if after.get(name) != edge_tree.get(name)}
        check(changed == {"etc/imp/Caddyfile", "etc/imp/direct-edge.json"}, f"migration changed other edge files: {changed}", result)
        check(
            [event for event in site.events_list() if event.startswith("edge ")]
            == (["edge hostname " + OTHER + " rc=0"] if mode == "existing Direct" else [])
            + ["edge migration-needed rc=0", "edge migrate rc=0", "edge commit rc=0"],
            "migration used provisioning/rotation or wrong helper order",
            result,
        )
        sudo_before = site.edge()["sudo_calls"]
        site.events.write_text("")
        tree = home_tree(site.edge_root)
        result = site.install(bundle, [])
        check(
            result.rc == 0 and home_tree(site.edge_root) == tree
            and site.events_list() == ["edge migration-needed rc=0"]
            and site.edge()["sudo_calls"] == sudo_before,
            "current v3 migration query was not an unprivileged no-op",
            result,
        )

    print("=== former Direct SSH upgrades retain inactive legacy edges without sudo or queries ===")
    for version in (1, 2):
        site.reset(gateway=(True, True))
        site.write_credential()
        site.owned_edge(version, behind_nat=version == 2)
        if version == 2:
            site.set_edge(addrs=["192.168.1.12"])
        edge_tree, credential = home_tree(site.edge_root), site.config().read_bytes()
        result = site.install(bundle, ["--connection", "ssh"])
        check(
            result.rc == 0 and "ready for SSH" in result.out
            and site.gateway_state() == site.unit_state() == (False, False),
            f"v{version} SSH did not disable the gateway and owned legacy edge",
            result,
        )
        check(
            home_tree(site.edge_root) == edge_tree and site.marker()["version"] == version
            and site.marker()["pending"] is None and site.config().read_bytes() == credential
            and not site.wss_calls() and "PAIRING-TOKEN:" not in result.out,
            f"v{version} SSH migrated/deleted legacy ownership or changed credentials",
            result,
        )
        site.set_edge(sudo_unavailable=True)
        for args in ([], ["--connection", "none"], ["--no-start"]):
            site.events.write_text("")
            (site.units / "calls.log").unlink(missing_ok=True)
            snap, tree, edge_state = site.snapshot(), home_tree(site.home), site.edge()
            result = site.install(bundle, args)
            check(
                result.rc == 0 and site.snapshot() == snap and home_tree(site.home) == tree
                and site.gateway_state() == site.unit_state() == (False, False)
                and site.config().read_bytes() == credential,
                f"v{version} former Direct {args} changed runtime/TinyFugue/credentials/services",
                result,
            )
            check(
                home_tree(site.edge_root) == edge_tree and site.edge() == edge_state
                and site.marker()["version"] == version and not site.events_list()
                and not site.wss_calls() and "PAIRING-TOKEN:" not in result.out
                and "How will the Imp desktop connect?" not in result.out,
                f"v{version} former Direct {args} queried/migrated the edge or needed sudo",
                result,
            )
            if "--no-start" in args:
                check(
                    all(
                        call.split()[0] in ("is-enabled", "is-active", "show")
                        for call in (site.units / "calls.log").read_text().splitlines()
                    ),
                    f"v{version} inactive --no-start mutated the user manager",
                    result,
                )
        site.set_edge(sudo_unavailable=False)
        site.events.write_text("")
        snap, sudo_before = site.snapshot(), site.edge()["sudo_calls"]
        result = site.install(bundle, ["--connection", "direct", "--hostname", HOST], tty=True)
        check(
            result.rc == 0 and site.snapshot() == snap and site.config().read_bytes() == credential
            and site.gateway_state() == site.unit_state() == (False, False)
            and site.edge()["sudo_calls"] > sudo_before and not site.wss_calls()
            and "PAIRING-TOKEN:" not in result.out
            and "How will the Imp desktop connect?" not in result.out,
            f"v{version} explicit Direct reentry provisioned/enabled or failed to migrate",
            result,
        )
        marker = site.marker()
        after = home_tree(site.edge_root)
        changed = {name for name in after.keys() | edge_tree.keys() if after.get(name) != edge_tree.get(name)}
        check(
            marker["version"] == 3 and marker["hostname"] == HOST
            and marker["behind_nat"] == (version == 2) and marker["pending"] is None
            and changed == {"etc/imp/Caddyfile", "etc/imp/direct-edge.json"}
            and site.events_list() == [
                "edge hostname " + HOST + " rc=0", "edge migration-needed rc=0",
                "edge migrate rc=0", "edge commit rc=0",
            ],
            f"v{version} explicit Direct reentry changed retained topology/root state",
            result,
        )

    print("=== retained legacy edge -> SSH -> remove only gateway.env -> matching Direct ===")

    def retained_legacy(version):
        # Reset only before the sequence: SSH must retain the actual owned root files.
        site.reset(gateway=(True, True))
        site.write_credential()
        site.owned_edge(version, behind_nat=version == 2)
        if version == 2:
            site.set_edge(addrs=["192.168.1.12"])
        edge_tree, credential = home_tree(site.edge_root), site.config().read_bytes()
        result = site.install(bundle, ["--connection", "ssh"])
        check(
            result.rc == 0 and site.gateway_state() == site.unit_state() == (False, False)
            and home_tree(site.edge_root) == edge_tree and site.config().read_bytes() == credential,
            f"v{version}: SSH failed to retain the canonical legacy baseline",
            result,
        )
        site.config().unlink()
        site.events.write_text("")
        site.wss_log.write_text("")
        site.set_edge(calls=[])
        return edge_tree

    for version in (1, 2):
        edge_tree = retained_legacy(version)
        for args in ([], ["--connection", "none"]):
            snap = site.snapshot()
            result = site.install(bundle, args)
            check(
                result.rc == 0 and site.snapshot() == snap and home_tree(site.edge_root) == edge_tree
                and site.gateway_state() == site.unit_state() == (False, False)
                and not site.events_list() and not site.wss_calls() and "PAIRING-TOKEN:" not in result.out,
                f"v{version}: missing credential {args} implicitly recovered Direct",
                result,
            )
        result = direct(behind_nat=version == 2)
        check(result.rc == 0, f"v{version}: explicit retained-edge recovery failed", result)
        calls = site.wss_calls()
        transaction = installer_transaction(site, ["enable", "commit"], result)
        sys.path.insert(0, EDGE_SRC)
        from imp_relay import direct_edge as edge_mod
        check(
            len(calls) == 1
            and calls[0]["argv"] == ["setup", "--public-url", f"wss://{HOST}/state"]
            + (["--verify-via-loopback"] if version == 2 else [])
            and calls[0]["edge_version"] == version
            and calls[0]["edge_pending"] == {
                "op": "enable", "prior_enabled": False, "prior_active": False, "prep": None,
                "transaction": transaction,
            }
            and calls[0]["edge_caddyfile"] == edge_mod._caddyfile(HOST, version == 2).decode()
            and calls[0]["stdin_tty"] == calls[0]["stdout_tty"] == result.tty,
            f"v{version}: setup did not observe the precise legacy-enable/current-policy journal",
            result,
        )
        tokens = re.findall(r"PAIRING-TOKEN:(\S+)", result.out)
        check(
            len(tokens) == 1 and site.events_list().count("token-generated") == 1
            and site.config().read_text() == f"IMP_GATEWAY_TOKEN_SHA256={hashlib.sha256(tokens[0].encode()).hexdigest()}\n"
            and site.marker()["version"] == 3 and site.marker()["pending"] is None
            and site.marker()["behind_nat"] == (version == 2)
            and site.gateway_state() == site.unit_state() == (True, True),
            f"v{version}: recovery did not hand off exactly one new identity and commit schema3",
            result,
        )
        events = site.events_list()
        check(
            events.index("edge enable rc=0") < events.index("token-generated") < events.index("edge commit rc=0")
            and sum(e.startswith("edge enable ") for e in events) == 1,
            f"v{version}: recovery bypassed the existing enable/setup/commit sequence",
            result,
        )
        after = home_tree(site.edge_root)
        check(
            {p for p in edge_tree.keys() | after.keys() if edge_tree.get(p) != after.get(p)}
            == {"etc/imp/Caddyfile", "etc/imp/direct-edge.json"},
            f"v{version}: recovery changed retained unit, masks, or certificate state",
            result,
        )

    print("=== retained legacy recovery failures restore public edge, preserve handed-off identity ===")
    for version, phase in itertools.product(
        (1, 2), ("validate", "publication", "start", "fail", "fail_after_replacement", "active-gateway", "commit", "handoff")
    ):
        edge_tree = retained_legacy(version)
        extra = {}
        if phase == "validate":
            site.set_edge(enable_validation_fail=True)
        elif phase == "publication":
            extra = {"IMP_TEST_PUBLICATION_SIGNAL": "Caddyfile"}
        elif phase == "start":
            site.set_edge(start_fail=True)
        elif phase == "commit":
            site.set_edge(commit_fail=True)
        elif phase == "handoff":
            extra = {"IMP_TEST_SIGNAL_PHASE": "handoff", "IMP_TEST_SIGNAL": "SIGTERM"}
        elif phase == "active-gateway":
            site.set_gateway(enabled=True, active=True)
        else:
            extra = {"IMP_TEST_PROVISION": phase}
        snap, tree = site.snapshot(), home_tree(site.home)
        result = direct(behind_nat=version == 2, extra=extra)
        handed_off = phase in ("commit", "handoff")
        check(
            result.rc != 0 and "Imp Direct WSS is ready" not in result.out
            and home_tree(site.edge_root) == edge_tree and site.marker()["version"] == version
            and site.marker()["pending"] is None and site.unit_state() == (False, False),
            f"v{version} {phase}: failure did not restore exact legacy files and close the edge",
            result,
        )
        if handed_off:
            check(
                len(re.findall(r"PAIRING-TOKEN:(\S+)", result.out)) == 1
                and site.events_list().count("token-generated") == 1
                and site.config().is_file() and site.gateway_state() == (True, True)
                and (site.home / ".local/share/imp/current").is_symlink()
                and all((site.home / ".config/systemd/user" / name).is_file()
                        for name in ("imp-relay.service", "imp-feed.service", "imp-gateway.service")),
                f"v{version} {phase}: post-handoff failure lost credential/runtime",
                result,
            )
        else:
            check(
                site.snapshot() == snap and home_tree(site.home) == tree and "PAIRING-TOKEN:" not in result.out,
                f"v{version} {phase}: pre-handoff failure changed retained installer state",
                result,
            )
        if phase == "active-gateway":
            check(
                len(site.wss_calls()) == 1 and "gateway is already active" in result.out
                and "token-generated" not in site.events_list() and site.gateway_state() == (True, True),
                f"v{version}: active gateway was stopped or generated a replacement identity",
                result,
            )

    print("=== retained legacy recovery rejects ownership, journal, masks, and service drift ===")
    for version, drift in itertools.product((1, 2), (
        "hostname", "topology", "uid", "config", "unit", "mask", "pending", "other-pending",
        "stale", "enabled", "active", "pid", "unloaded",
    )):
        retained_legacy(version)
        marker_path = site.edge_root / "etc/imp/direct-edge.json"
        if drift in ("uid", "pending", "other-pending"):
            marker = site.marker()
            if drift == "uid":
                marker["owner_uid"] += 1
            else:
                marker["pending"] = {
                    "op": "enable", "prior_enabled": False, "prior_active": False, "prep": None,
                }
                if drift == "other-pending":
                    marker["pending"]["transaction"] = Path("/proc/sys/kernel/random/uuid").read_text().strip()
            marker_path.write_text(json.dumps(marker, sort_keys=True, separators=(",", ":")) + "\n")
        elif drift in ("config", "unit"):
            path = site.edge_root / ("etc/imp/Caddyfile" if drift == "config" else "etc/systemd/system/imp-caddy.service")
            path.write_bytes(path.read_bytes() + b"\n# operator edit\n")
        elif drift == "mask":
            (site.edge_root / "etc/systemd/system/caddy.service").unlink()
        elif drift in ("enabled", "active"):
            site.set_edge(unit={"enabled": drift == "enabled", "active": drift == "active"})
        elif drift in ("stale", "pid", "unloaded"):
            site.set_edge(unit_properties={
                "stale": {"NeedDaemonReload": "yes"},
                "pid": {"MainPID": "4242"},
                "unloaded": {"LoadState": "not-found", "FragmentPath": ""},
            }[drift])
        if version == 2 and drift == "topology":
            site.set_edge(addrs=[GLOBAL])
        args = ["--connection", "direct", "--hostname", OTHER if drift == "hostname" else HOST]
        if (version == 2) != (drift == "topology"):
            args.append("--behind-nat")
        snap, tree, edge_tree = site.snapshot(), home_tree(site.home), home_tree(site.edge_root)
        unit, sudo_before = site.unit_state(), site.edge()["sudo_calls"]
        result = direct(args=args)
        check(
            result.rc != 0 and site.snapshot() == snap and home_tree(site.home) == tree
            and home_tree(site.edge_root) == edge_tree and site.unit_state() == unit
            and not site.wss_calls() and "token-generated" not in site.events_list()
            and "PAIRING-TOKEN:" not in result.out and site.edge()["sudo_calls"] == sudo_before
            and not any(call.startswith(("systemctl enable", "systemctl start", "systemctl restart"))
                        for call in site.edge()["calls"]),
            f"v{version} {drift}: inadmissible recovery mutated/provisioned the public edge",
            result,
        )
        if drift in ("pending", "other-pending"):
            installer_transaction(site, ["enable", "rollback"], result)
            check(
                "edge enable rc=1" in site.events_list()
                and "edge rollback rc=1" in site.events_list()
                and "public-edge restoration failed" in result.out,
                f"v{version} {drift}: existing journal did not survive actual guarded EXIT cleanup",
                result,
            )
        if drift == "topology":
            check(
                "edge enable rc=1" in site.events_list() and "different NAT topology" in result.out,
                f"v{version}: topology drift did not reach helper admission",
                result,
            )

    print("=== canonical current pending journals survive failed fresh Direct and guarded EXIT cleanup ===")
    for identified in (False, True):
        site.reset()
        options = ["--transaction", Path("/proc/sys/kernel/random/uuid").read_text().strip()] if identified else []
        site.drive("enable", HOST, str(uid), *options)
        site.events.write_text("")
        site.set_edge(calls=[], transaction_calls=[])
        snap, tree, edge_tree = site.snapshot(), home_tree(site.home), home_tree(site.edge_root)
        unit, sudo_before = site.unit_state(), site.edge()["sudo_calls"]
        result = direct()
        transaction = installer_transaction(site, ["enable", "rollback"], result)
        check(
            result.rc != 0 and site.snapshot() == snap and home_tree(site.home) == tree
            and home_tree(site.edge_root) == edge_tree and site.unit_state() == unit
            and site.marker()["pending"].get("transaction") != transaction
            and site.edge()["sudo_calls"] == sudo_before
            and "edge enable rc=1" in site.events_list()
            and "edge rollback rc=1" in site.events_list()
            and "public-edge restoration failed" in result.out
            and not site.wss_calls() and "token-generated" not in site.events_list()
            and "PAIRING-TOKEN:" not in result.out,
            f"current pending identified={identified}: failed installer adopted or mutated another journal",
            result,
        )
        check(
            all(call == "ip -j address show scope global"
                or call.startswith(("systemctl is-enabled", "systemctl is-active", "systemctl show"))
                for call in site.edge()["calls"]),
            f"current pending identified={identified}: unexpected system calls {site.edge()['calls']!r}",
            result,
        )

    print("=== no-start migration uses incoming gateway state without user-service mutation ===")
    for version, gateway in itertools.product(
        (1, 2), ((True, True), (True, False), (False, True))
    ):
        site.reset(gateway=gateway)
        site.write_credential()
        site.owned_edge(version, behind_nat=version == 2)
        site.set_edge(unit={"enabled": gateway[0], "active": gateway[1]})
        if version == 2:
            site.set_edge(addrs=["192.168.1.12"])
        snap, edge_tree = site.snapshot(), home_tree(site.edge_root)
        sudo_before = site.edge()["sudo_calls"]
        result = site.install(bundle, ["--no-start"])
        check(
            result.rc == 0 and site.snapshot() == snap and site.gateway_state() == gateway
            and site.unit_state() == gateway and site.edge()["sudo_calls"] > sudo_before
            and not site.wss_calls() and "PAIRING-TOKEN:" not in result.out,
            f"v{version} {gateway} --no-start failed migration or changed user state",
            result,
        )
        marker = site.marker()
        after = home_tree(site.edge_root)
        changed = {name for name in after.keys() | edge_tree.keys() if after.get(name) != edge_tree.get(name)}
        check(
            marker["version"] == 3 and marker["hostname"] == HOST
            and marker["behind_nat"] == (version == 2) and marker["pending"] is None
            and changed == {"etc/imp/Caddyfile", "etc/imp/direct-edge.json"}
            and site.events_list() == [
                "edge migration-needed rc=0", "edge migrate rc=0", "edge commit rc=0",
            ]
            and all(
                call.split()[0] in ("is-enabled", "is-active", "show")
                for call in (site.units / "calls.log").read_text().splitlines()
            ),
            f"v{version} {gateway} --no-start skipped migration or mutated the user manager",
            result,
        )

    print("=== migration drift and failure restore installer/runtime/root baseline without a token ===")
    for version, failure in itertools.product(
        (1, 2), ("drift", "validate", "publication", "restart", "post-verify", "commit", "sudo")
    ):
        site.reset(gateway=(True, True))
        site.write_credential()
        site.owned_edge(version, behind_nat=version == 2)
        if failure == "drift":
            path = site.edge_root / "etc/imp/Caddyfile"
            path.write_bytes(path.read_bytes() + b"\n# operator edit\n")
        elif failure == "commit":
            site.set_edge(commit_fail=True)
        elif failure == "sudo":
            site.set_edge(sudo_unavailable=True)
        else:
            site.set_edge(migration_fail=failure)
        tree, snap = home_tree(site.home), site.snapshot()
        edge_tree = home_tree(site.edge_root)
        # Make the installer modify TinyFugue before the failure, then prove rollback restores it.
        (site.home / ".tfrc").write_text("# retained startup\n")
        tree, snap = home_tree(site.home), site.snapshot()
        result = site.install(bundle, [], timeout=30)
        refuses(site, result, snap, f"v{version} migration {failure}")
        check(home_tree(site.home) == tree, f"{failure}: installer/runtime/TinyFugue not restored", result)
        check(
            home_tree(site.edge_root) == edge_tree and site.unit_state() == (True, True)
            and site.marker()["pending"] is None and "PAIRING-TOKEN:" not in result.out,
            f"{failure}: root baseline or migration journal not restored",
            result,
        )
        if failure == "restart":
            check(site.edge().get("failed_restart_state") == "inactive", "failed restart double never went inactive")
        if failure == "drift":
            check(site.events_list() == ["edge migration-needed rc=1"], "drift query reached privileged mutation", result)

    print("=== migration return signals restore legacy bytes; incomplete rollback remains retryable ===")
    for version, signame in itertools.product((1, 2), ("SIGINT", "SIGTERM", "SIGHUP")):
        site.reset(gateway=(True, True))
        site.write_credential()
        site.owned_edge(version, behind_nat=version == 2)
        tree, edge_tree = home_tree(site.home), home_tree(site.edge_root)
        snap = site.snapshot()
        result = site.install(
            bundle, [], tty=True,
            extra={"IMP_TEST_SIGNAL_PHASE": "migrate", "IMP_TEST_SIGNAL": signame},
        )
        refuses(site, result, snap, f"migration {signame}")
        check(
            home_tree(site.home) == tree and home_tree(site.edge_root) == edge_tree
            and site.unit_state() == (True, True) and site.marker()["pending"] is None,
            "migration return interruption did not restore legacy baseline",
            result,
        )
    for version in (1, 2):
        site.reset(gateway=(True, True))
        site.write_credential()
        site.owned_edge(version, behind_nat=version == 2)
        edge_tree, snap = home_tree(site.edge_root), site.snapshot()
        site.set_edge(migration_fail="restart", migration_restore_fail=True)
        result = site.install(bundle, [], timeout=30)
        refuses(site, result, snap, "incomplete migration restoration")
        check(
            site.marker()["pending"]["op"] == "migrate" and site.unit_state() == (True, False)
            and "public-edge restoration failed" in result.out,
            "failed migration restoration lost the recovery journal",
            result,
        )
        site.set_edge(migration_fail=None, migration_restore_fail=False)
        site.drive("rollback", str(uid))
        check(
            home_tree(site.edge_root) == edge_tree and site.unit_state() == (True, True),
            "pending migration recovery could not restore the exact legacy baseline",
        )

    print("=== explicit SSH stops the gateway, retains credentials, respects edge ownership ===")
    site.reset(gateway=(True, True))
    site.write_credential()
    site.drive("enable", HOST, str(uid))
    site.drive("commit", str(uid))
    before = site.config().read_bytes()
    result = site.install(bundle, ["--connection", "ssh"])
    check(result.rc == 0 and "ready for SSH" in result.out, "explicit ssh failed", result)
    check(site.gateway_state() == (False, False), "ssh left the gateway enabled or active", result)
    check(site.config().read_bytes() == before, "ssh changed gateway credentials", result)
    check(site.unit_state() == (False, False), "ssh left the owned edge enabled/active", result)
    check(
        site.marker()["pending"] is None and (site.edge_root / "etc/imp/Caddyfile").exists(),
        "ssh did not commit and retain owned edge files",
        result,
    )
    site.reset(gateway=(True, True))
    site.write_credential()
    site.drive("enable", HOST, str(uid))
    site.drive("commit", str(uid))
    snap_edge = (site.unit_state(), site.marker())
    result = site.install(bundle, ["--connection", "ssh", "--no-start"])
    check(
        result.rc == 0 and site.gateway_state() == (True, True),
        "ssh --no-start changed gateway state",
        result,
    )
    check(snap_edge == (site.unit_state(), site.marker()), "ssh --no-start changed the edge", result)
    # Operator-edited and operator-owned edges are never touched.
    site.reset(gateway=(True, True))
    site.write_credential()
    site.drive("enable", HOST, str(uid))
    site.drive("commit", str(uid))
    caddyfile = site.edge_root / "etc/imp/Caddyfile"
    caddyfile.write_text(caddyfile.read_text() + "\n# operator edit\n")
    edited = (caddyfile.read_bytes(), site.unit_state(), site.marker())
    result = site.install(bundle, ["--connection", "ssh"])
    check(
        result.rc == 0 and site.gateway_state() == (False, False),
        "ssh with operator-edited edge failed",
        result,
    )
    check(
        edited == (caddyfile.read_bytes(), site.unit_state(), site.marker()),
        "ssh touched an operator-edited edge",
        result,
    )
    site.reset(gateway=(True, True))
    site.write_credential()
    (site.edge_root / "etc/caddy").mkdir()
    (site.edge_root / "etc/caddy/Caddyfile").write_text("operator\n")
    site.set_edge(unit={"enabled": True, "active": True})
    result = site.install(bundle, ["--connection", "ssh"])
    check(result.rc == 0 and site.gateway_state() == (False, False), "ssh with operator Caddy failed", result)
    check(
        site.unit_state() == (True, True) and site.marker() is None,
        "ssh touched operator-owned Caddy",
        result,
    )
    check(
        (site.edge_root / "etc/caddy/Caddyfile").read_text() == "operator\n",
        "ssh changed operator Caddyfile",
        result,
    )

    print("=== existing Direct credentials: no setup, no claimed verification ===")
    for kind in ("file", "dangling symlink"):
        site.reset(gateway=(True, True))
        site.config().parent.mkdir(parents=True, exist_ok=True)
        if kind == "file":
            site.write_credential()
        else:
            site.config().symlink_to(site.home / "missing-credential")
        snap = site.snapshot()
        result = direct()
        check(result.rc == 0, f"existing credential ({kind}) run failed", result)
        check(
            "not verified" in result.out and "rotate --public-url" in result.out,
            "existing credential continuation missing",
            result,
        )
        check("Imp Direct WSS is ready" not in result.out, "existing credential claimed readiness", result)
        check(
            not site.wss_calls() and site.snapshot() == snap,
            f"existing credential ({kind}) was modified or provisioned",
            result,
        )
        check(
            not any(e.startswith(("edge preflight", "edge dns", "edge enable")) for e in site.events_list()),
            "existing credential touched the edge",
        )
        check(site.gateway_state() == (True, True), "existing credential changed gateway state", result)

    print("=== new Direct: success, route isolation, inherited TTY ===")
    site.reset()
    site.set_edge(dns={HOST: [GLOBAL, "2606:2800:220:1::1"]},
                  addrs=[GLOBAL, "2606:2800:220:1::1"])
    result = direct()
    check(result.rc == 0 and "Choice [2]" not in result.out
          and site.marker()["behind_nat"] is False
          and site.wss_calls()[0]["argv"] == ["setup", "--public-url", f"wss://{HOST}/state"],
          "matching dual-stack Direct was rejected or selected NAT", result)
    site.reset()
    result = direct()
    check(result.rc == 0 and "Imp Direct WSS is ready." in result.out, "Direct install failed", result)
    check(
        f"wss://{HOST}/state" in result.out and "Settings -> Connection -> Direct" in result.out,
        "Direct completion text missing",
        result,
    )
    events = [e.split(" rc=")[0] for e in site.events_list()]
    expected = [
        "edge hostname " + HOST,
        "edge preflight " + HOST,
        "edge enable",
        f"wss setup --public-url wss://{HOST}/state",
        "edge commit",
    ]
    positions = [events.index(item) for item in expected if item in events]
    check(
        len(positions) == len(expected) and positions == sorted(positions),
        f"unexpected call order {events}",
        result,
    )
    calls = site.wss_calls()
    check(
        len(calls) == 1 and calls[0]["argv"] == ["setup", "--public-url", f"wss://{HOST}/state"],
        f"bad provisioner argv {calls}",
        result,
    )
    check(
        calls[0]["stdin_tty"] == result.tty and calls[0]["stdout_tty"] == result.tty,
        "provisioner did not inherit the installer terminal",
        result,
    )
    check(calls[0]["edge_pending"]["op"] == "enable", "edge was not pending during provisioning", result)
    transaction = installer_transaction(site, ["enable", "commit"], result)
    check(calls[0]["edge_pending"]["transaction"] == transaction, "setup observed another transaction", result)
    check(
        site.marker()["pending"] is None and site.unit_state() == (True, True),
        "edge not committed enabled/active",
        result,
    )
    tokens = re.findall(r"PAIRING-TOKEN:(\S+)", result.out)
    check(len(tokens) == 1, "token was not displayed exactly once", result)
    for path in work.rglob("*"):
        if path.is_file() and not path.is_symlink() and path.stat().st_size < 1_000_000:
            check(tokens[0].encode() not in path.read_bytes(), f"token persisted in {path}")
    stock = [site.edge_root / "etc/systemd/system" / n for n in ("caddy.service", "caddy-api.service")]
    check(
        all(p.is_symlink() and os.readlink(p) == "/dev/null" for p in stock),
        "stock Caddy units are not masked",
    )
    probes = [probe[1] for probe in site.edge()["https"]["calls"]]
    check(
        {"/unknown", "/ingest", "/action-consumer"} <= set(probes), f"missing negative-route probes {probes}"
    )
    caddyfile = (site.edge_root / "etc/imp/Caddyfile").read_text()
    paths = set(re.findall(r"(?<![\w.])/[a-z-]+", caddyfile))
    check(paths == {"/state", "/action", "/healthz"}, f"edge routes expose {paths}", None)
    check(
        caddyfile.count("reverse_proxy") == 1
        and "127.0.0.1:8788" in caddyfile
        and "respond 404" in caddyfile,
        "edge is not a single loopback gateway proxy with 404 default",
    )
    check(
        "8787" not in caddyfile and "ingest" not in caddyfile and "action-consumer" not in caddyfile,
        "edge exposes relay/ingest/consumer",
    )
    check(
        site.gateway_state() == (True, True) and site.config().exists(),
        "gateway/credential not left enabled by provisioning",
        result,
    )
    check(
        "router/NAT" not in result.out, "directly-addressed install asked an unnecessary NAT question", result
    )

    print("=== unchanged owned edge is reused; hostname change and no-credential repeat are safe ===")
    site.set_gateway()
    site.config().unlink()
    edge_before = site.edge()["calls"].count("apt-get install -y --no-install-recommends caddy")
    site.events.write_text("")
    result = direct()
    check(result.rc == 0 and "Imp Direct WSS is ready." in result.out, "owned edge reuse failed", result)
    check(
        site.edge()["calls"].count("apt-get install -y --no-install-recommends caddy") == edge_before,
        "package reinstalled for reused edge",
        result,
    )
    site.set_gateway()
    site.config().unlink()
    snap_edge = (site.marker(), site.unit_state(), caddyfile)
    result = direct(args=["--connection", "direct", "--hostname", OTHER])
    check(
        result.rc != 0 and "refusing to change hostname" in result.out,
        "hostname migration was attempted",
        result,
    )
    check(
        snap_edge == (site.marker(), site.unit_state(), (site.edge_root / "etc/imp/Caddyfile").read_text()),
        "hostname rejection changed the edge",
        result,
    )
    check(len(site.wss_calls()) == 2, "hostname rejection reached provisioning", result)

    print("=== public-edge refusals leave installer and operator state untouched ===")

    def refusal(label, prepare, needle=None):
        site.reset()
        prepare()
        snap = site.snapshot()
        result = direct()
        refuses(site, result, snap, label)
        if needle:
            check(needle in result.out, f"{label}: message lacks {needle!r}", result)
        edge_files = [site.edge_root / "etc/imp" / n for n in ("Caddyfile", "direct-edge.json")]
        edge_files.append(site.edge_root / "etc/systemd/system/imp-caddy.service")
        check(not any(p.exists() for p in edge_files), f"{label}: edge files were created", result)
        check(site.unit_state() == (False, False), f"{label}: listener enabled", result)
        check(
            not any(c.startswith(("apt-get", "systemctl mask")) for c in site.edge()["calls"]),
            f"{label}: package/mask mutation",
            result,
        )

    nginx = 'tcp LISTEN 0 511 0.0.0.0:{port} 0.0.0.0:* users:(("nginx",pid=99,fd=6))'
    refusal("tcp443 listener", lambda: site.set_edge(listeners=[nginx.format(port=443)]), "in use")
    refusal("tcp80 listener", lambda: site.set_edge(listeners=[nginx.format(port=80)]), "in use")
    refusal("unattributed listener", lambda: site.set_edge(listeners=["tcp LISTEN 0 1 *:80 *:*"]), "in use")
    refusal(
        "udp443 listener",
        lambda: site.set_edge(listeners=['udp UNCONN 0 0 *:443 *:* users:(("x",pid=7,fd=3))']),
        "in use",
    )
    for directory in ("etc/caddy", "etc/nginx", "etc/apache2", "etc/apt/sources.list.d"):

        def prepare(directory=directory):
            (site.edge_root / directory).mkdir(parents=True)
            if "apt" in directory:
                (site.edge_root / directory / "caddy.list").write_text("deb x\n")

        refusal(f"preexisting {directory}", prepare)
    refusal("existing caddy binary", lambda: (site.edge_root / "usr/bin/caddy").touch(), "caddy")
    refusal("dpkg-known caddy", lambda: site.set_edge(dpkg_caddy=True), "dpkg")
    refusal(
        "unsupported distro", lambda: (site.edge_root / "etc/os-release").write_text("ID=fedora\n"), "Debian"
    )
    site.reset()
    result = direct()
    check(result.rc == 0, "setup for operator edit failed", result)
    site.set_gateway()
    site.config().unlink()
    (site.edge_root / "etc/imp/Caddyfile").write_text("operator replaced\n")
    snap_edge = (site.marker(), site.unit_state())
    result = direct()
    check(result.rc != 0 and "not exact Imp-owned" in result.out, "operator-edited edge was adopted", result)
    check(
        snap_edge == (site.marker(), site.unit_state())
        and (site.edge_root / "etc/imp/Caddyfile").read_text() == "operator replaced\n",
        "operator edge modified",
        result,
    )
    check(len(site.wss_calls()) == 1, "operator edge reached provisioning", result)
    site.reset()
    site.set_edge(sudo_unavailable=True)
    snap = site.snapshot()
    refuses(site, direct(), snap, "unavailable privilege")

    print("=== TLS/route readiness failures stop before provisioning and restore state ===")
    for (label, https), behind_nat in itertools.product(
        (
            ("ingest reachable", {"/ingest": 200}),
            ("action-consumer reachable", {"/action-consumer": 101}),
            ("redirect", {"/unknown": 301}),
            ("proxy error", {"/unknown": 502}),
            ("untrusted certificate", {"/unknown": "cert"}),
        ),
        (False, True),
    ):
        site.reset()
        site.set_edge(https=https)
        snap = site.snapshot()
        result = direct(behind_nat=behind_nat)
        refuses(site, result, snap, label)
        check(site.unit_state() == (False, False), f"{label}: public listener left enabled/active", result)
        marker = site.marker()
        check(
            marker is not None and marker["pending"] is None,
            f"{label}: edge pending state not resolved",
            result,
        )
        check(
            all(os.readlink(p) == "/dev/null" for p in stock),
            f"{label}: stock unit mask not retained",
            result,
        )
        # safe retry: owned config left disabled, retry succeeds once routes are correct
        site.set_edge(https={})
        retry = direct(behind_nat=behind_nat)
        check(
            retry.rc == 0 and "Imp Direct WSS is ready." in retry.out and site.unit_state() == (True, True),
            f"{label}: safe retry failed",
            retry,
        )
        site.reset()
    site.set_edge(apt_fail=True)
    snap = site.snapshot()
    result = direct()
    refuses(site, result, snap, "package installation failure")
    check(site.unit_state() == (False, False), "package failure left listener enabled", result)
    check(
        all(os.readlink(p) == "/dev/null" for p in stock), "package failure did not retain stock mask", result
    )

    print("=== SSH commit-return INT/TERM/HUP retain the completed connection transition ===")
    for (signame, status), behind_nat in itertools.product(
        (("SIGINT", 130), ("SIGTERM", 143), ("SIGHUP", 129)), (False, True)
    ):
        site.reset(gateway=(True, True))
        site.write_credential()
        credential = site.config().read_bytes()
        site.drive("enable", HOST, str(uid), *(["--behind-nat"] if behind_nat else []))
        site.drive("commit", str(uid))
        result = site.install(
            bundle,
            ["--connection", "ssh"],
            extra={"IMP_TEST_SIGNAL_PHASE": "commit", "IMP_TEST_SIGNAL": signame},
        )
        check(result.rc == status, f"{signame}: SSH commit-return lost signal status", result)
        check(
            site.gateway_state() == (False, False) and site.unit_state() == (False, False),
            f"{signame}: committed SSH transition restored only the Direct gateway",
            result,
        )
        check(
            site.config().read_bytes() == credential and site.marker()["pending"] is None,
            f"{signame}: SSH commit-return changed credential or reopened edge transaction",
            result,
        )
        retry = site.install(bundle, ["--connection", "ssh"])
        check(retry.rc == 0, f"{signame}: committed SSH retry failed", retry)

    print("=== actual commit child/group INT/TERM/HUP before and after durable publication ===")
    for (boundary, target), behind_nat in itertools.product(
        (("before", "child"), ("after", "child"), ("before", "group"), ("after", "group")),
        (False, True),
    ):
        for signame, status in (("SIGINT", 130), ("SIGTERM", 143), ("SIGHUP", 129)):
            site.reset(gateway=(True, True))
            site.write_credential()
            credential = site.config().read_bytes()
            site.drive("enable", HOST, str(uid), *(["--behind-nat"] if behind_nat else []))
            site.drive("commit", str(uid))
            site.events.write_text("")
            result = site.install(
                bundle,
                ["--connection", "ssh"],
                tty=True,
                extra={
                    "IMP_TEST_COMMIT_SIGNAL_BOUNDARY": boundary,
                    "IMP_TEST_COMMIT_SIGNAL_TARGET": target,
                    "IMP_TEST_SIGNAL": signame,
                },
            )
            check(result.rc == status, f"{boundary}/{signame}: native child interruption lost", result)
            expected = (False, False) if boundary == "after" else (True, True)
            check(
                site.gateway_state() == expected and site.unit_state() == expected,
                f"{boundary}/{signame}: durable commit reconciliation restored mixed state",
                result,
            )
            check(
                site.config().read_bytes() == credential and site.marker()["pending"] is None,
                f"{boundary}/{signame}: credential changed or pending transaction stranded",
                result,
            )
            signal_event = next(e for e in site.events_list() if e.startswith("commit-signal "))
            check(
                ("pending=null" in signal_event) == (boundary == "after")
                and "edge commit rc=0" not in site.events_list(),
                f"{boundary}/{signame}: test failed to distinguish interrupted commit publication",
                result,
            )

    print("=== reconciliation fork/startup and blocked-query native signal boundaries ===")
    for (signame, status, query_result, second, spawn), behind_nat in itertools.product(
        (
            ("SIGINT", 130, "ok", "SIGINT", False),
            ("SIGTERM", 143, "ok", "SIGTERM", False),
            ("SIGHUP", 129, "ok", "SIGHUP", False),
            ("SIGTERM", 143, "fail", "SIGTERM", False),
            ("SIGINT", 130, "ok", "SIGINT", True),
            ("SIGTERM", 143, "ok", "SIGTERM", True),
            ("SIGHUP", 129, "ok", "SIGHUP", True),
            ("SIGTERM", 143, "ok", "SIGINT", True),
            ("SIGHUP", 129, "ok", "SIGTERM", True),
        ),
        (False, True),
    ):
        site.reset(gateway=(True, True))
        site.write_credential()
        credential = site.config().read_bytes()
        site.drive("enable", HOST, str(uid), *(["--behind-nat"] if behind_nat else []))
        site.drive("commit", str(uid))
        site.events.write_text("")
        query = site.root / f"reconcile-{signame}-{second}-{query_result}-{spawn}-{behind_nat}"
        spawn_ready = site.root / f"spawn-{query.name}"
        result = site.install(
            bundle,
            ["--connection", "ssh"],
            tty=True,
            extra={
                "IMP_TEST_COMMIT_SIGNAL_BOUNDARY": "after",
                "IMP_TEST_COMMIT_SIGNAL_TARGET": "group",
                "IMP_TEST_SIGNAL": signame,
                "IMP_TEST_SECOND_SIGNAL": second,
                **({"IMP_TEST_RECONCILE_SPAWN": str(spawn_ready)} if spawn else {}),
                "IMP_TEST_RECONCILE_QUERY": str(query),
                "IMP_TEST_RECONCILE_QUERY_RESULT": query_result,
            },
        )
        check(result.rc == status, f"{signame}/{query_result}: repeated signal exit status lost", result)
        check(query.with_suffix(".done").exists(), f"{signame}: second signal killed query child", result)
        proof = json.loads(query.with_suffix(".done").read_text())
        check(
            proof["ignored"] == proof["helper_ignored"] == [True, True, True]
            and proof["helper"] != proof["query"],
            f"{signame}: helper/query protection not inherited across the real subprocess boundary",
            result,
        )
        if spawn:
            birth = json.loads(spawn_ready.with_suffix(".spawn").read_text())
            mask = sum(1 << (s - 1) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP))
            check(
                birth["ignored"] & mask == mask and birth["child"] == proof["helper"],
                f"{signame}/{second}: helper lacked inherited protection before its first instruction",
                result,
            )
        expected_gateway = (False, False) if query_result == "ok" else (True, True)
        check(
            site.gateway_state() == expected_gateway
            and site.unit_state() == (False, False)
            and site.config().read_bytes() == credential
            and site.marker()["pending"] is None,
            f"{signame}/{query_result}: reconciliation outcome did not control connection recovery",
            result,
        )
        expected_rc = 0 if query_result == "ok" else 1
        check(
            f"edge ssh-committed rc={expected_rc}" in site.events_list()
            and "reconcile-query timeout=120" in site.events_list(),
            f"{signame}/{query_result}: proof did not finish with the existing bounded query policy",
            result,
        )
        if query_result == "fail":
            check("injected service-query failure" in result.out, "operational proof failure hidden", result)

    print("=== controlling terminal disappears after an owned SSH transition ===")
    site.reset(gateway=(True, True))
    site.write_credential()
    (site.home / ".tfrc").write_text("; previous startup without Imp\n")
    for name in ("active-imp-relay.service", "active-imp-feed.service", "enabled-imp-feed.service"):
        (site.units / name).unlink(missing_ok=True)
    site.drive("enable", HOST, str(uid))
    site.drive("commit", str(uid))
    snap = site.snapshot()
    prior_edge = (site.unit_state(), site.marker())
    previous_backups = set(site.home.glob(".tfrc.imp-backup.*"))
    rc = site.install_hangup(bundle)
    check(rc == 129, f"closed controlling PTY lost HUP exit semantics: {rc}")
    check(site.snapshot() == snap, "closed PTY prevented filesystem/user-service restoration")
    check(site.gateway_state() == (True, True), "closed PTY left the gateway stopped/disabled")
    check((site.unit_state(), site.marker()) == prior_edge, "closed PTY left the owned edge changed")
    check(
        set(site.home.glob(".tfrc.imp-backup.*")) == previous_backups,
        "closed PTY stranded a TinyFugue backup",
    )
    rc = site.install_hangup(bundle, extra={"IMP_TEST_RESTORE_COPY_FAILURE": "yes"})
    check(rc == 129, "failed filesystem sub-step replaced the HUP exit status")
    backups = list((site.home / ".local/share/imp").glob(".install-backup.*"))
    capture = site.home / ".config/imp/capture.tf"
    check(
        len(backups) == 1 and (backups[0] / "capture.tf").read_bytes() == snap["files"][str(capture)],
        "failed restore deleted the recoverable filesystem backup",
    )
    check(
        site.gateway_state() == (True, True) and (site.unit_state(), site.marker()) == prior_edge,
        "failed filesystem sub-step prevented independent connection restoration",
    )
    shutil.copy2(backups[0] / "capture.tf", capture)
    shutil.rmtree(backups[0])
    check(site.snapshot() == snap, "failed filesystem sub-step prevented other restoration")
    site.reset(gateway=(True, True))
    site.write_credential()
    site.drive("enable", HOST, str(uid), "--behind-nat")
    site.drive("commit", str(uid))
    snap = site.snapshot()
    prior_edge = (site.unit_state(), site.marker())
    rc = site.install_hangup(bundle)
    check(rc == 129 and site.snapshot() == snap, "NAT closed-PTY cleanup lost HUP/restoration")
    check(
        site.gateway_state() == (True, True) and (site.unit_state(), site.marker()) == prior_edge,
        "NAT closed-PTY cleanup failed to restore connection state",
    )

    print("=== native TERM at each edge publication boundary, followed by retry ===")
    for publication, behind_nat in itertools.product(
        ("direct-edge.json", "Caddyfile", "imp-caddy.service"), (False, True)
    ):
        site.reset()
        snap = site.snapshot()
        result = direct(extra={"IMP_TEST_PUBLICATION_SIGNAL": publication}, behind_nat=behind_nat)
        check(
            result.rc != 0 and "PAIRING-TOKEN:" not in result.out,
            f"{publication}: publication interruption reached credential handoff",
            result,
        )
        check(
            site.snapshot() == snap and site.unit_state() == (False, False),
            f"{publication}: publication interruption did not restore installer state",
            result,
        )
        check(
            site.marker() is None
            and not (site.edge_root / "etc/imp/Caddyfile").exists()
            and not (site.edge_root / "etc/systemd/system/imp-caddy.service").exists(),
            f"{publication}: stranded a generated file/journal",
            result,
        )
        retry = direct(behind_nat=behind_nat)
        check(
            retry.rc == 0 and "Imp Direct WSS is ready" in retry.out,
            f"{publication}: normal retry failed",
            retry,
        )

    print("=== real parent INT/TERM/HUP after filesystem and connection mutations ===")
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        for phase, behind_nat in (
            ("feed", False),
            ("enable", False),
            ("disable", False),
            ("enable", True),
            ("disable", True),
        ):
            site.reset()
            (site.home / ".tfrc").write_text("; operator startup without Imp hook\n")
            if phase == "disable":
                site.write_credential()
                site.set_gateway(True, True)
                site.drive("enable", HOST, str(uid), *(["--behind-nat"] if behind_nat else []))
                site.drive("commit", str(uid))
            snap = site.snapshot()
            prior_edge = site.unit_state()
            extra = {"IMP_TEST_SIGNAL_PHASE": phase, "IMP_TEST_SIGNAL": signum.name}
            if phase == "enable":
                result = direct(extra=extra, behind_nat=behind_nat)
            else:
                mode = "ssh" if phase == "disable" else "none"
                result = site.install(bundle, ["--connection", mode], tty=True, extra=extra)
            check(result.rc == 128 + signum, f"{phase}/{signum.name}: signal exit lost", result)
            check(site.snapshot() == snap, f"{phase}/{signum.name}: installer rollback incomplete", result)
            check(site.unit_state() == prior_edge, f"{phase}/{signum.name}: edge not restored", result)
            check("PAIRING-TOKEN:" not in result.out, f"{phase}/{signum.name}: token emitted", result)

    print("=== real parent signal after child handoff, before post-child bookkeeping ===")
    for signum, behind_nat in itertools.product(
        (signal.SIGINT, signal.SIGTERM, signal.SIGHUP), (False, True)
    ):
        site.reset()
        current = site.home / ".local/share/imp/current"
        current.unlink()
        unit_dir = site.home / ".config/systemd/user"
        for name in ("imp-relay.service", "imp-feed.service", "imp-gateway.service"):
            (unit_dir / name).unlink()
        (site.home / ".tfrc").write_text("; previous startup\n")
        result = direct(
            extra={"IMP_TEST_SIGNAL_PHASE": "handoff", "IMP_TEST_SIGNAL": signum.name}, behind_nat=behind_nat
        )
        check(
            result.rc == 128 + signum and "PAIRING-TOKEN:" in result.out,
            f"{signum.name}: handoff race was not exercised",
            result,
        )
        check(
            "Imp Direct WSS is ready" not in result.out and "may have handed off" in result.out,
            f"{signum.name}: uncertain handoff recovery not reported",
            result,
        )
        check(
            current.is_symlink()
            and (current / "bin/imp-direct-wss").is_file()
            and site.config().is_file(),
            f"{signum.name}: handed-off identity/runtime deleted",
            result,
        )
        check(
            all(
                (unit_dir / name).is_file()
                for name in ("imp-relay.service", "imp-feed.service", "imp-gateway.service")
            ),
            f"{signum.name}: installed units removed after possible handoff",
            result,
        )
        check(
            "/load ~/.config/imp/capture.tf" in (site.home / ".tfrc").read_text(),
            f"{signum.name}: post-handoff TinyFugue state rolled back",
            result,
        )
        check(
            site.unit_state() == (False, False) and site.marker()["pending"] is None,
            f"{signum.name}: public edge not closed",
            result,
        )
        check(site.gateway_state() == (True, True), f"{signum.name}: local credential gateway lost", result)

    print("=== provisioning/commit failures restore the edge and installer state ===")
    site.reset()
    snap = site.snapshot()
    result = direct(extra={"IMP_TEST_PROVISION": "fail"})
    check(
        result.rc != 0 and "Imp Direct WSS is ready" not in result.out, "provision failure succeeded", result
    )
    check(
        len(site.wss_calls()) == 1 and site.wss_calls()[0]["edge_pending"] is not None,
        "provisioner did not run under pending edge",
        result,
    )
    check("edge rollback rc=0" in site.events_list(), "failure did not roll the edge back", result)
    transaction = installer_transaction(site, ["enable", "rollback"], result)
    check(site.wss_calls()[0]["edge_pending"]["transaction"] == transaction, "cleanup owner differs from setup", result)
    check(site.unit_state() == (False, False), "provision failure left public listener on", result)
    check(site.marker()["pending"] is None, "pending edge record not resolved", result)
    check(
        site.snapshot() == snap and site.gateway_state() == (False, False),
        "provision failure left installer state changed",
        result,
    )
    site.reset()
    snap = site.snapshot()
    result = direct(extra={"IMP_TEST_PROVISION": "fail_after_replacement"})
    check(
        result.rc != 0 and "PAIRING-TOKEN:" not in result.out,
        "handled pre-handoff failure printed a token/succeeded",
        result,
    )
    check(
        site.snapshot() == snap and site.unit_state() == (False, False),
        "handled pre-handoff failure prevented ordinary installer rollback",
        result,
    )
    site.reset()
    site.set_edge(commit_fail=True)
    result = direct()
    check(
        result.rc != 0 and "Imp Direct WSS is ready" not in result.out,
        "commit failure claimed success",
        result,
    )
    check(
        "edge rollback rc=0" in site.events_list() and site.unit_state() == (False, False),
        "commit failure did not restore edge",
        result,
    )
    check(site.config().exists(), "commit failure removed the already-handed-off credential", result)
    check((site.home / ".local/share/imp/current").is_symlink(), "commit failure removed the runtime", result)
    installer_transaction(site, ["enable", "commit", "rollback"], result)

    print("=== replaced pending owner refuses commit/cleanup and retains handed-off identity ===")
    for boundary in ("commit", "rollback"):
        edge_tree = retained_legacy(2)
        if boundary == "rollback":
            site.set_edge(commit_fail=True)
        result = direct(behind_nat=True, extra={"IMP_TEST_REPLACE_TRANSACTION": boundary})
        transaction = installer_transaction(site, ["enable", "commit", "rollback"], result)
        state = site.edge()
        expected_tree = dict(edge_tree)
        expected_tree["etc/imp/direct-edge.json"] = state["replacement_marker"].encode()
        expected_tree["etc/imp/Caddyfile"] = state["replacement_caddyfile"].encode()
        check(
            result.rc != 0 and "Imp Direct WSS is ready" not in result.out
            and "edge commit rc=1" in site.events_list()
            and "edge rollback rc=1" in site.events_list()
            and "public-edge restoration failed" in result.out
            and site.marker()["pending"]["transaction"] != transaction
            and home_tree(site.edge_root) == expected_tree
            and site.unit_state() == (True, True)
            and state["sudo_calls"] == state["replacement_sudo_calls"]
            and all(call.split()[0] in ("is-enabled", "is-active", "show")
                    for call in (entry.removeprefix("systemctl ")
                                 for entry in state["calls"][state["replacement_call_count"]:])),
            f"{boundary}: installer adopted or mutated a replaced pending owner",
            result,
        )
        tokens = re.findall(r"PAIRING-TOKEN:(\S+)", result.out)
        check(
            len(tokens) == 1
            and site.config().read_text() == f"IMP_GATEWAY_TOKEN_SHA256={hashlib.sha256(tokens[0].encode()).hexdigest()}\n"
            and (site.home / ".local/share/imp/current").is_symlink()
            and site.gateway_state() == (True, True),
            f"{boundary}: refused cleanup lost the handed-off credential/runtime",
            result,
        )
    print("=== NAT pre-handoff failure rolls back; post-handoff failure retains identity ===")
    for phase in ("fail", "fail_after_replacement", "commit"):
        site.reset()
        snap = site.snapshot()
        if phase == "commit":
            site.set_edge(commit_fail=True)
        result = direct(behind_nat=True, extra={"IMP_TEST_PROVISION": phase} if phase != "commit" else None)
        check(
            result.rc != 0 and "Imp Direct WSS is ready" not in result.out,
            f"NAT {phase}: claimed success",
            result,
        )
        check(
            site.unit_state() == (False, False) and site.marker()["pending"] is None,
            f"NAT {phase}: public edge left open/pending",
            result,
        )
        if phase == "commit":
            check(
                site.config().exists() and site.gateway_state() == (True, True),
                "NAT post-handoff identity removed",
                result,
            )
        else:
            check(
                site.snapshot() == snap and "PAIRING-TOKEN:" not in result.out,
                f"NAT {phase}: pre-handoff rollback incomplete",
                result,
            )

    print("=== legacy v0.3.2 .venv layout: no-mode upgrade, failed-upgrade rollback, and new-release upgrade ===")
    legacy_upgrade(work, shipping, next_shipping, bundle, uid)
    if next_shipping:
        print("=== genuine frozen A -> B upgrade and failed-B rollback ===")
        frozen_upgrade(work, shipping, next_shipping, bundle, uid)

    print("installer connection acceptance: PASS")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--edge-driver"]:
        edge_driver(sys.argv[2:])
    elif sys.argv[1:2] == ["--tf-startup-only"] and len(sys.argv) == 3:
        startup_main(sys.argv[2])
    elif len(sys.argv) in (2, 3):
        main(sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else None)
    else:
        sys.exit(f"usage: {sys.argv[0]} BUNDLE_DIR [NEXT_BUNDLE_DIR] | --tf-startup-only BUNDLE_DIR")
