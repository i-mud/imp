#!/usr/bin/env python3
"""Run the native Managed diagnostic through disposable system OpenSSH fixtures."""

import argparse
import asyncio
import ctypes
import http.client
import json
import ntpath
import os
import re
import runpy
import secrets
import select
import socket
import struct
import subprocess
import sys
import tempfile
import time
from contextlib import AsyncExitStack, ExitStack, contextmanager, nullcontext
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SUPPORT = runpy.run_path(str(SCRIPTS / "test-structured-ssh.py"))
HOST = SUPPORT["HOST"]
openssh = SUPPORT["openssh"]
keygen = SUPPORT["keygen"]
reserve_port = SUPPORT["reserve_port"]
retained_client = SUPPORT["retained_client"]
wait_until = SUPPORT["wait_until"]
ENDPOINT = SCRIPTS / "test-managed-check-endpoint.py"
ALIAS = "imp-diagnostic-fixture"
JUMP_ALIAS = "imp-diagnostic-jump.invalid"
CONTAINER_PORT = 2222
ENV = {**os.environ, "SSH_AUTH_SOCK": "", "LC_ALL": "C"}
if os.name == "nt":
    ENV["PATH"] = str(openssh("ssh").parent) + os.pathsep + ENV["PATH"]


EVIDENCE_LIMIT = 16 * 1024
VERSION = rb"[0-9]{1,3}(?:\.[0-9]{1,3}){1,3}(?:p[0-9]{1,3})?"
PHASES = ("tools", "account-context", "key-generation", "key-acl", "server-config",
          "server-listener", "installed-alias", "independent-forward", "installed-listeners", "ready")
PERMISSION_ROLES = {
    "fixture-directory": ".", "server-config": "sshd.config",
    "host-key": "host_key", "authorized-keys": "authorized_keys",
    "client-key": "client_key",
}
PERMISSION_SNAPSHOT_ROLES = ("authorized-keys", "host-key", "client-key")
PERMISSION_PATH_FORMS = ("drive", "openssh-realpath", "extended-length", "cygdrive", "program-data",
                         "repeated-separators", "other")
PERMISSION_REPORTS = {
    "rejected-ace": rb"Bad permissions\. Try removing permissions for user: .+? "
                    rb"(?:\((?P<sid>S-1(?:-[0-9]{1,20}){2,16})\) )?on file (?P<path>.+)\.",
    "rejected-owner": rb"Bad owner on (?P<path>.+)",
    "descriptor-read-failed": rb"failed to retrieve the owner sid and dacl of file (?P<path>.+) with error code: [0-9]+",
    "auth-strict-refused": rb"Authentication refused\.",
    "ignored-authorized-keys": rb"Ignored authorized keys(?:: bad ownership or modes)?",
    "bad-ownership-or-modes": rb"Authentication refused: bad ownership or modes for (?:file|directory) (?P<path>.+)",
    "ignored-path-modes": rb"Ignored (?P<path>.+): bad ownership or modes",
    "access-denied": rb"Could not open authorized keys '(?P<path>.+)': Permission denied",
}
IDENTITY_ROLES = ("token-user", "login-user", "sshd-listener-user")
PRINCIPAL_ROLES = (*IDENTITY_ROLES, "token-group", "system", "administrators", "trusted-installer",
                   "builtin-users", "authenticated-users", "everyone")
ACE_LIMIT = 12
EVIDENCE = {
    "client": {
        "host-verification": (rb"Host key verification failed\.",),
        "authentication-rejected": (rb"[a-zA-Z0-9._@\\-]+: Permission denied \(publickey\)\.",),
        "key-permissions": (
            rb'Load key "[^"]{1,400}": bad permissions',
            rb"@ +WARNING: UNPROTECTED PRIVATE KEY FILE! +@",
        ),
        "bind-failed": (rb"bind \[127\.0\.0\.1\]:[0-9]{1,5}: .+",),
        "forwarding-denied": (rb"channel [0-9]+: open failed: administratively prohibited: .+",),
        "remote-connect-failed": (rb"channel [0-9]+: open failed: connect failed: .+",),
        "forwarding-failed": (rb"Could not request local forwarding\.",),
    },
    "server": {
        "account-context": (
            rb"Invalid user [a-zA-Z0-9._\\-]+ from 127\.0\.0\.1 port [0-9]{1,5}",
            rb"User [a-zA-Z0-9._\\-]+ from 127\.0\.0\.1 not allowed because not listed in AllowUsers",
            rb"unable to get security token for user .+",
            rb"get_user_token - unable to generate user token for .+ as i am not running as system",
        ),
        "required-privilege": (
            rb"CreateProcess(?:AsUser)?W failed error:1314",
            rb"LogonUserExExW failed with 1314",
            rb"generate_sshd_virtual_token: unable to assign SE_SERVICE_LOGON_NAME privilege, error: [0-9]+",
            rb"get_user_token - unable to generate sshd virtual token, ensure sshd service has TCB privileges",
        ),
        "config-directive": (
            rb".*sshd\.config: line [0-9]+: Bad configuration option: .+",
            rb'.*sshd\.config line [0-9]+: unsupported option "[^"]+"\.',
        ),
        "authentication-rejected": (rb"Failed publickey for .+ from 127\.0\.0\.1 port [0-9]{1,5} ssh2(?:: .+)?",),
        "key-permissions": tuple(PERMISSION_REPORTS.values()),
        "bind-failed": (
            rb"Bind to port [0-9]{1,5} on 127\.0\.0\.1 failed: .+",
            rb"Cannot bind any address\.",
        ),
        "forwarding-denied": (rb"refused local port forward: .+",),
        "remote-connect-failed": (
            rb"connect_to 127\.0\.0\.1 port [0-9]{1,5}: failed\.",
        ),
    },
}


def evidence_lines(data):
    # Match complete printable lines only; never render input from OpenSSH.
    for line in data[:EVIDENCE_LIMIT].split(b"\n")[:-1]:
        line = line.removesuffix(b"\r")
        if re.fullmatch(rb"[ -~]{1,512}", line):
            yield line


def diagnostic_lines(data):
    for line in evidence_lines(data):
        yield re.sub(rb"^(?:debug[123]|error|fatal): ", b"", line).removesuffix(b" [preauth]")


def classify_evidence(data, side):
    found = set()
    for line in diagnostic_lines(data):
        for category, patterns in EVIDENCE[side].items():
            if any(re.fullmatch(pattern, line) for pattern in patterns):
                found.add(category)
    return sorted(found) or ["unidentified"]


def ssh_version(data):
    for line in evidence_lines(data):
        match = re.fullmatch(rb"OpenSSH_for_Windows_(" + VERSION + rb")(?:, [ -~]{1,80})?", line)
        if match:
            return match[1].decode("ascii")
    return "unidentified"


def permission_reports(data, paths, program_data=None):
    def windows_path(path):
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        path = ntpath.normcase(path)
        form = "drive"
        if path.startswith("\\\\?\\"):
            path, form = path[4:], "extended-length"
        elif re.match(r"^\\[a-z]:\\", path):
            # Win32's internal OpenSSH realpath form is /x:/dir/file.
            path, form = path[1:], "openssh-realpath"
        elif cygdrive := re.fullmatch(r"\\cygdrive\\([a-z])\\(.+)", path):
            path, form = f"{cygdrive[1]}:\\{cygdrive[2]}", "cygdrive"
        elif path.startswith("__programdata__\\") and program_data:
            _, _, suffix = path.partition("\\")
            base = ntpath.normcase(program_data).rstrip("\\")
            path, form = base + "\\" + suffix.lstrip("\\"), "program-data"
        if re.match(r"^[a-z]:\\", path):
            normalized = re.sub(r"\\{2,}", r"\\", path)
            if path != normalized and form == "drive":
                form = "repeated-separators"
            return normalized, form
        return path, "other"

    known = {windows_path(str(path))[0]: role for role, path in paths.items()}
    templates = set()
    reports = {}
    for line in diagnostic_lines(data):
        for reason, pattern in PERMISSION_REPORTS.items():
            if match := re.fullmatch(pattern, line):
                templates.add(reason)
                groups = match.groupdict()
                if raw_path := groups.get("path"):
                    path, form = windows_path(raw_path.decode("ascii"))
                    if (role := known.get(path)) and (role not in reports or reason == "rejected-ace"):
                        sid = groups.get("sid")
                        reports[role] = {"reason": reason, "pathForm": form, "sid": sid.decode("ascii") if sid else None}
                break
    return sorted(templates), reports
def inspect_permissions(root, login, server_process, templates, reports):
    executable = openssh("sshd").parent.parent / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    pid = server_process.pid if server_process is not None and server_process.poll() is None else None
    roles = reports.keys() or PERMISSION_SNAPSHOT_ROLES
    paths = {role: reports.get(role, {}).get("sid") for role in roles}
    request = {"root": str(root), "login": login, "serverPid": pid, "paths": paths, "limit": ACE_LIMIT}
    try:
        with tempfile.TemporaryFile() as output:
            subprocess.run(
                [executable, "-NoProfile", "-NonInteractive", "-File", SCRIPTS / "inspect-managed-check-acl.ps1"],
                input=json.dumps(request).encode("ascii"), stdout=output, stderr=subprocess.DEVNULL,
                check=True, timeout=10,
            )
            output.seek(0)
            data = output.read(EVIDENCE_LIMIT + 1)
        result = json.loads(data) if len(data) <= EVIDENCE_LIMIT else None
    except (OSError, ValueError, RecursionError, subprocess.SubprocessError):
        result = None
    if not isinstance(result, dict):
        result = {}
    identities = result.get("identities", {})
    descriptors = result.get("descriptors", {})
    permissions = {
        "templates": templates,
        "identities": {role: identities.get(role) if isinstance(identities, dict) else None for role in IDENTITY_ROLES},
        "files": {
            role: {
                "reason": reports.get(role, {}).get("reason"),
                "pathForm": reports.get(role, {}).get("pathForm"),
                "descriptor": descriptors.get(role) if isinstance(descriptors, dict) else None,
            }
            for role in roles
        },
    }
    try:
        render_permissions(permissions)
    except (AttributeError, KeyError, TypeError, ValueError):
        permissions["identities"] = dict.fromkeys(IDENTITY_ROLES)
        for report in permissions["files"].values():
            report["descriptor"] = None
    return permissions


def render_permissions(permissions):
    def fields(value, *expected):
        if not isinstance(value, dict) or value.keys() != set(expected):
            raise ValueError

    def principals(value):
        if isinstance(value, list) and len(value) <= len(PRINCIPAL_ROLES) and all(
            role in PRINCIPAL_ROLES for role in value
        ):
            return "+".join(value) or "other"
        raise ValueError

    def number(value, bound):
        if type(value) is int and 0 <= value <= bound:
            return value
        raise ValueError

    fields(permissions, "templates", "identities", "files")
    templates = permissions["templates"]
    if not isinstance(templates, list) or not 1 <= len(templates) <= len(PERMISSION_REPORTS) or any(
        template not in PERMISSION_REPORTS for template in templates
    ):
        raise ValueError
    identities = permissions["identities"]
    files = permissions["files"]
    fields(identities, *IDENTITY_ROLES)
    if not isinstance(files, dict) or not files or not files.keys() <= PERMISSION_ROLES.keys():
        raise ValueError
    output = ["  permission templates: " + ",".join(templates)]
    unattributed = all(isinstance(report, dict) and report.get("reason") is None for report in files.values())
    if unattributed:
        if set(files) != set(PERMISSION_SNAPSHOT_ROLES):
            raise ValueError
        output.append("  permission rejection: no known fixture path matched; snapshots are not attribution")
    output.append("  identities: " + "; ".join(
        f"{role}={principals(identities[role]) if identities[role] is not None else 'unavailable'}"
        for role in IDENTITY_ROLES
    ) + "; auth-child-token=not-observed")
    for role, report in files.items():
        fields(report, "reason", "pathForm", "descriptor")
        reason = report["reason"]
        form = report["pathForm"]
        if unattributed and reason is None and form is None:
            prefix = f"  descriptor {role}: rejection=not-attributed; snapshot=after-rejection"
        elif reason in templates and form in PERMISSION_PATH_FORMS:
            prefix = f"  descriptor {role}: rejection={reason}; path-form={form}; snapshot=after-rejection"
        else:
            raise ValueError
        descriptor = report["descriptor"]
        if descriptor is None:
            output.append(prefix + "; unavailable")
            continue
        fields(descriptor, "owner", "control", "reportedSidMatches", "aceCount", "aces")
        owner = principals(descriptor["owner"]) if descriptor["owner"] is not None else "not-observed"
        control = number(descriptor["control"], 0xffff)
        match = descriptor["reportedSidMatches"]
        dacl = descriptor["aceCount"]
        aces = descriptor["aces"]
        if not isinstance(aces, list) or len(aces) > ACE_LIMIT:
            raise ValueError
        if dacl is None:
            if aces or match not in (None, 0):
                raise ValueError
        elif len(aces) > number(dacl, 0xffff):
            raise ValueError
        if match is not None:
            number(match, dacl or 0)
        output.append(
            f"{prefix}; owner={owner}; control=0x{control:04x}; "
            f"protected={'yes' if control & 0x1000 else 'no'}; "
            f"auto-inherited={'yes' if control & 0x0400 else 'no'}; "
            f"dacl={'null' if dacl is None else dacl}; captured-aces={len(aces)}; "
            f"reported-sid-ace-matches={'no-sid' if match is None else match}"
        )
        for ace in aces:
            fields(ace, "index", "type", "flags", "mask", "principal", "matchesReportedSid")
            index = number(ace["index"], dacl - 1)
            kind = number(ace["type"], 0xff)
            flags = number(ace["flags"], 0xff)
            mask = ace["mask"]
            if mask is not None and (not isinstance(mask, str) or not re.fullmatch(r"[0-9a-f]{8}", mask)):
                raise ValueError
            relation = principals(ace["principal"]) if ace["principal"] is not None else "not-observed"
            reported = ace["matchesReportedSid"]
            if type(reported) is not bool:
                raise ValueError
            output.append(
                f"    ace[{index}]: type={kind}; flags=0x{flags:02x} "
                f"({'inherited' if flags & 0x10 else 'explicit'}); "
                f"mask={'unavailable' if mask is None else '0x' + mask}; principal={relation}; "
                f"matches-reported-sid={'yes' if reported else 'no'}"
            )
    return output


def render_evidence(summary):
    unavailable = "Windows OpenSSH fixture startup evidence unavailable"
    if not isinstance(summary, dict) or summary.get("phase") not in PHASES:
        return unavailable
    config = summary.get("config")
    observation = summary.get("observation")
    exit_code = summary.get("exitCode")
    if config not in ("not-checked", "rejected", "accepted", "timed-out", "error") or observation not in (
        "ready", "process-exited", "timed-out", "listener-observation-error", "error"
    ):
        return unavailable
    if observation == "process-exited":
        if type(exit_code) is not int or not -0x80000000 <= exit_code <= 0xFFFFFFFF:
            return unavailable
    elif exit_code is not None:
        return unavailable
    output = [f"Windows OpenSSH fixture: phase={summary['phase']}; sshd -t={config}; observation={observation}"]
    if exit_code is not None:
        output[0] += f"; exit={exit_code}"
    for side, executable in (("client", "ssh.exe"), ("server", "sshd.exe")):
        version = summary.get(side + "Version")
        categories = summary.get(side)
        allowed = (*EVIDENCE[side], "unidentified")
        if not isinstance(version, str) or not (
            version == "unidentified" or re.fullmatch(VERSION.decode("ascii"), version)
        ) or not isinstance(categories, list) or not (1 <= len(categories) <= len(allowed)) or any(
            item not in allowed for item in categories
        ):
            return unavailable
        output.append(f"  {side} System32/OpenSSH/{executable}: version={version}; evidence={','.join(categories)}")
    if "independentForward" in summary:
        if summary["independentForward"] != "relay-health-live":
            return unavailable
        output.append("  independent forward: live production relay health verified")
    if "permissions" in summary:
        try:
            output.extend(render_permissions(summary["permissions"]))
        except (AttributeError, KeyError, TypeError, ValueError):
            return unavailable
    return "\n".join(output)


def read_startup_evidence(root):
    try:
        with (root / "startup-evidence.json").open("rb") as file:
            data = file.read(EVIDENCE_LIMIT + 1)
        summary = json.loads(data) if len(data) <= EVIDENCE_LIMIT else None
    except (ValueError, OSError, RecursionError):
        summary = None
    return render_evidence(summary)


class StartupFailed(Exception):
    pass


class WindowsStartup:
    def __init__(self, installed_root=None):
        self.installed_root = installed_root
        self.summary = {"phase": "tools", "config": "not-checked", "observation": "error"}
        for side in EVIDENCE:
            self.summary[side] = ["unidentified"]
            self.summary[side + "Version"] = "unidentified"

    @contextmanager
    def stage(self, phase):
        self.summary["phase"] = phase
        try:
            yield
        except subprocess.CalledProcessError as error:
            self.summary.update(observation="process-exited", exitCode=error.returncode)
            raise StartupFailed from None
        except subprocess.TimeoutExpired:
            self.summary["observation"] = "timed-out"
            raise StartupFailed from None
        except (AssertionError, OSError, RuntimeError, subprocess.SubprocessError, ValueError):
            raise StartupFailed from None

    def wait_for_listener(self, port, process, label):
        def probe():
            try:
                status = process.poll()
                if status is None:
                    return listening(port, process.pid)
            except (AssertionError, OSError, ValueError, struct.error):
                self.summary["observation"] = "listener-observation-error"
                raise StartupFailed from None
            raise subprocess.CalledProcessError(status, ())

        try:
            wait_until(probe, label)
        except AssertionError:
            self.summary["observation"] = "timed-out"
            raise StartupFailed from None

    def observe(self, side, log, *, root=None, login=None, server_process=None):
        end = log.seek(0, os.SEEK_END)
        start = max(0, end - EVIDENCE_LIMIT)
        # Inspect the preceding byte so a truncated line cannot look like a diagnostic.
        log.seek(max(0, start - 1))
        data = log.read(EVIDENCE_LIMIT + bool(start))
        if start:
            data = data.partition(b"\n")[2]
        self.summary[side] = classify_evidence(data, side)
        if side != "server" or root is None:
            return
        self.summary.pop("permissions", None)
        if "key-permissions" in self.summary[side]:
            root = root.resolve()
            templates, reports = permission_reports(
                data, {role: root / name for role, name in PERMISSION_ROLES.items()}, os.environ.get("ProgramData"),
            )
            self.summary["permissions"] = inspect_permissions(root, login, server_process, templates, reports)

    def publish(self):
        if self.installed_root is None:
            print(render_evidence(self.summary), flush=True)
        else:
            (self.installed_root / "startup-evidence.json").write_text(json.dumps(self.summary), encoding="ascii")

    def ready(self):
        self.summary.update(phase="ready", observation="ready")
        self.publish()


def listening(port, pid=None):
    """Passively check both TCP families, including wildcard listeners."""
    if os.name == "nt":
        from ctypes import wintypes

        table = ctypes.WinDLL("iphlpapi").GetExtendedTcpTable
        table.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
                          wintypes.ULONG, ctypes.c_int, wintypes.ULONG)
        table.restype = wintypes.DWORD
        # MIB_TCPROW_OWNER_PID / MIB_TCP6ROW_OWNER_PID strides and field offsets.
        for family, stride, local_port, owner in (
            (socket.AF_INET, 24, 8, 20),
            (socket.AF_INET6, 56, 20, 52),
        ):
            size = wintypes.DWORD()
            buffer = None
            for _ in range(4):
                result = table(buffer, ctypes.byref(size), False, family, 3, 0)  # OWNER_PID_LISTENER
                if result != 122:  # ERROR_INSUFFICIENT_BUFFER
                    break
                buffer = ctypes.create_string_buffer(size.value)
            if result != 0:
                raise ctypes.WinError(result)
            if buffer is None:
                continue
            count, = struct.unpack_from("=I", buffer)
            for row in range(4, 4 + count * stride, stride):
                local, = struct.unpack_from("!H", buffer, row + local_port)
                if local == port and (pid is None or struct.unpack_from("=I", buffer, row + owner)[0] == pid):
                    return True
        return False

    owned = None
    if pid is not None:
        owned = set()
        try:
            for handle in Path(f"/proc/{pid}/fd").iterdir():
                try:
                    owned.add(os.readlink(handle))
                except FileNotFoundError:
                    pass
        except FileNotFoundError:
            return False
    for path in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        try:
            rows = path.read_text().splitlines()[1:]
        except FileNotFoundError:
            if path.name == "tcp6":  # The kernel can be built without IPv6.
                continue
            raise
        for row in rows:
            fields = row.split()
            if fields[3] == "0A" and int(fields[1].rsplit(":", 1)[1], 16) == port:
                if owned is None or f"socket:[{fields[9]}]" in owned:
                    return True
    return False


def stop(process):
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


def lines(path):
    return path.read_text().splitlines() if path.exists() else []


def ssh_path(path):
    return path.as_posix().replace("%", "%%")


def ssh_config(label, root, username, target_port, config_port, *, trust="trusted", alias=False,
               jump_port=None):
    config = root / f"{label}.config"
    known_hosts = root / f"{label}.known-hosts"
    key = root / ("wrong_key" if label == "unauthorized" else "client_key")
    pin = root / ("wrong_key.pub" if trust == "changed" else "host_key.pub")
    addresses = dict.fromkeys(p for p in (target_port, jump_port) if p is not None)
    known_hosts.write_text(
        "".join(f"[{HOST}]:{port} {pin.read_text().strip()}\n" for port in addresses)
        if trust != "unknown" else "",
        encoding="ascii",
    )
    config.write_text(
        f"""Host {ALIAS if alias else HOST}
 HostName {HOST}
 Port {config_port}
 User {username if alias else 'imp_wrong_fixture_user'}
 IdentityFile "{ssh_path(key)}"
 IdentitiesOnly yes
 IdentityAgent none
 UserKnownHostsFile "{ssh_path(known_hosts)}"
 GlobalKnownHostsFile none
 StrictHostKeyChecking yes
 UpdateHostKeys no
 HostKeyAlgorithms ssh-ed25519
{f" ProxyJump {JUMP_ALIAS}" if jump_port is not None else ""}
""",
        encoding="utf-8",
    )
    if jump_port is not None:
        with config.open("a", encoding="utf-8") as file:
            file.write(f"""
Host {JUMP_ALIAS}
 HostName {HOST}
 Port {jump_port}
 User {username}
 IdentityFile "{ssh_path(root / 'jump_key')}"
 IdentitiesOnly yes
 IdentityAgent none
 UserKnownHostsFile "{ssh_path(known_hosts)}"
 GlobalKnownHostsFile none
 StrictHostKeyChecking yes
 UpdateHostKeys no
 HostKeyAlgorithms ssh-ed25519
 BatchMode yes
 VerifyHostKeyDNS no
 NoHostAuthenticationForLocalhost no
 ExitOnForwardFailure yes
 ControlMaster no
 ControlPath none
 ControlPersist no
 ForkAfterAuthentication no
 ClearAllForwardings yes
 ForwardAgent no
 ForwardX11 no
 PermitLocalCommand no
""")
    return config, known_hosts


def authorize(root, permit, jump_target):
    options = 'restrict,port-forwarding,permitopen="127.0.0.1:8787"' if permit else "restrict"
    keys = f"{options} {(root / 'client_key.pub').read_text().strip()}\n"
    if jump_target is not None:
        keys += (f'restrict,port-forwarding,permitopen="{HOST}:{jump_target}" '
                 f"{(root / 'jump_key.pub').read_text().strip()}\n")
    return keys


@contextmanager
def docker_server(root):
    support = runpy.run_path(str(SCRIPTS / "run-guided-forward-fixture.py"))
    docker = support["docker"]
    image = support["build_image"](SCRIPTS.parent / "services" / "relay")
    name = "imp-managed-check-" + secrets.token_hex(8)
    docker("run", "--rm", "--init", "--detach", "--name", name,
           "--publish", f"{HOST}::{CONTAINER_PORT}", image, "sleep", "600")
    try:
        def permit(allowed):
            docker("exec", "-i", name, "sh", "-ec", "cat >/run/imp-authorized",
                   data=authorize(root, allowed, CONTAINER_PORT).encode("ascii"))

        permit(True)
        config = support["SERVER"].replace(
            "PermitOpen 127.0.0.1:8787", f"PermitOpen {HOST}:8787 {HOST}:{CONTAINER_PORT}"
        )
        docker("exec", "-i", name, "sh", "-ec", "cat >/run/imp-sshd.conf",
               data=config.encode("ascii"))
        docker("exec", name, "ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", "/run/imp-host")
        (root / "host_key.pub").write_text(docker("exec", name, "cat", "/run/imp-host.pub") + "\n")
        docker("cp", str(ENDPOINT), name + ":/run/imp-endpoint.py")
        docker("exec", name, "/usr/sbin/sshd", "-f", "/run/imp-sshd.conf")
        yield ("impfixture", support["published_port"](name, CONTAINER_PORT), CONTAINER_PORT, permit,
               ["docker", "exec", "-i", name, "python", "-u", "/run/imp-endpoint.py"])
    finally:
        docker("rm", "-f", name)


def windows_account(data, hostname):
    identity = data.removesuffix(b"\n").removesuffix(b"\r").lower()
    match = re.fullmatch(rb"([a-z0-9][a-z0-9-]{0,62})\\([a-z0-9][a-z0-9._-]{0,19})", identity)
    if not match or match[1].decode("ascii") != hostname.split(".", 1)[0].lower():
        raise ValueError("fixture requires a local Windows account")
    return identity.decode("ascii"), match[2].decode("ascii")


def restrict_windows_key(path, identity, system):
    subprocess.run(
        [system / "icacls.exe", path, "/inheritance:r", "/grant:r", f"{identity}:(F)",
         "*S-1-5-18:(F)", "*S-1-5-32-544:(F)"], check=True, timeout=10,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


@contextmanager
def windows_server(root, startup):
    with startup.stage("tools"):
        client, server, generator = (openssh(name) for name in ("ssh", "sshd", "ssh-keygen"))
        if not all(tool.is_file() for tool in (client, server, generator)):
            raise FileNotFoundError("Windows stock OpenSSH is required")
        for side, tool in (("client", client), ("server", server)):
            with tempfile.TemporaryFile() as log:
                # Older sshd reports its release in usage and exits nonzero for -V.
                subprocess.run([tool, "-V"], env=ENV, timeout=10, stdin=subprocess.DEVNULL,
                               stdout=log, stderr=log)
                log.seek(0)
                startup.summary[side + "Version"] = ssh_version(log.read(EVIDENCE_LIMIT))
    system = client.parent.parent
    with startup.stage("account-context"), tempfile.TemporaryFile() as identity_log:
        subprocess.run([system / "whoami.exe"], check=True, timeout=10, stdin=subprocess.DEVNULL,
                       stdout=identity_log, stderr=subprocess.DEVNULL)
        identity_log.seek(0)
        identity, username = windows_account(identity_log.read(256), socket.gethostname())

    names = ("host_key", "client_key")
    if startup.installed_root is None:
        names += ("wrong_key", "jump_key")
    with startup.stage("key-generation"):
        for name in names:
            subprocess.run(
                [generator, "-q", "-t", "ed25519", "-N", "", "-f", root / name],
                check=True, timeout=10, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    authorized = root / "authorized_keys"
    config = root / "sshd.config"
    with startup.stage("server-config"), reserve_port() as port:
        jump_target = port if (root / "jump_key.pub").exists() else None
        jump_permit = f" {HOST}:{jump_target}" if jump_target is not None else ""

        def permit(allowed):
            authorized.write_text(authorize(root, allowed, jump_target), encoding="ascii")

        with startup.stage("key-acl"):
            permit(True)
            for path in (*[root / name for name in names], authorized):
                restrict_windows_key(path, identity, system)
        startup.summary["phase"] = "server-config"
        config.write_text(
            f"""ListenAddress {HOST}
Port {port}
HostKey "{(root / 'host_key').as_posix()}"
AuthorizedKeysFile "{authorized.as_posix()}"
AllowUsers {username}
PubkeyAuthentication yes
AuthenticationMethods publickey
PasswordAuthentication no
KbdInteractiveAuthentication no
AllowTcpForwarding local
PermitOpen {HOST}:8787{jump_permit}
AllowAgentForwarding no
MaxSessions 0
LogLevel DEBUG3
""",
            encoding="utf-8",
        )
    with startup.stage("server-config"), tempfile.TemporaryFile() as log:
        startup.summary["config"] = "error"
        try:
            subprocess.run([server, "-t", "-e", "-f", config], env=ENV, check=True, timeout=10,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log)
        except subprocess.CalledProcessError:
            startup.summary["config"] = "rejected"
            raise
        except subprocess.TimeoutExpired:
            startup.summary["config"] = "timed-out"
            raise
        finally:
            startup.observe("server", log, root=root, login=identity)
        startup.summary["config"] = "accepted"
    with tempfile.TemporaryFile() as log:
        process = None
        try:
            with startup.stage("server-listener"):
                process = subprocess.Popen([server, "-D", "-e", "-f", config], env=ENV,
                                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log)
                startup.wait_for_listener(port, process, "disposable Windows sshd")
            yield username, port, jump_target, permit, [sys.executable, "-u", str(ENDPOINT)]
        finally:
            try:
                if startup.summary["phase"] != "ready":
                    startup.observe("server", log, root=root, login=identity, server_process=process)
            finally:
                if process is not None:
                    stop(process)


def probe_relay(port):
    connection = http.client.HTTPConnection(HOST, port, timeout=3)
    try:
        connection.request("GET", "/healthz")
        response = connection.getresponse()
        body = response.read(1025)
        assert response.status == 200 and len(body) <= 1024, "independent relay health request failed"
        health = json.loads(body)
        assert (
            isinstance(health, dict) and health.get("feed") == "live" and health.get("has_snapshot") is True
            and health.get("seq") == 2
        ), "independent forward did not reach the live fixture relay"
    except (http.client.HTTPException, ValueError):
        raise AssertionError("independent relay health request failed") from None
    finally:
        connection.close()


@contextmanager
def independent(root, username, port, backend, startup=None):
    with tempfile.TemporaryFile() as log:
        process = None
        try:
            with startup.stage("independent-forward") if startup else nullcontext():
                config, _ = ssh_config("independent", root, username, port, port, alias=True)
                with reserve_port() as local_port:
                    pass
                process = subprocess.Popen(
                    [openssh("ssh"), "-v", "-F", config, "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
                     "-N", "-L", f"{HOST}:{local_port}:{HOST}:8787", ALIAS],
                    env=ENV, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
                )
                if startup:
                    startup.wait_for_listener(local_port, process, "independent authenticated forward")
                else:
                    wait_until(lambda: listening(local_port, process.pid), "independent authenticated forward", process)
                with tempfile.TemporaryDirectory(dir=root, prefix="independent-") as directory, endpoint(
                    backend, "live", Path(directory) / "relay.audit",
                ):
                    probe_relay(local_port)
                if startup:
                    startup.summary["independentForward"] = "relay-health-live"

            def alive():
                assert process.poll() is None and listening(local_port, process.pid), "independent SSH was disturbed"

            yield alive
        finally:
            if process is not None:
                stop(process)
            if startup and startup.summary["phase"] != "ready":
                startup.observe("client", log)


@contextmanager
def endpoint(command, mode, log_path):
    with log_path.open("wb") as log, log_path.with_suffix(".stderr").open("wb") as error:
        process = subprocess.Popen([*command, mode], env=ENV, stdin=subprocess.PIPE,
                                   stdout=log, stderr=error)
        try:
            wait_until(lambda: "READY 8787" in lines(log_path), "fixture /state endpoint", process)

            def release():
                process.stdin.write(b"release\n")
                process.stdin.flush()

            yield log_path, release if mode in ("live", "stale", "down") else None
            process.communicate(b"stop\n", timeout=10)
            assert process.returncode == 0, "fixture endpoint failed or dispatched an action"
        finally:
            stop(process)


def check(harness, root, label, config, destination, expected, audit, *, control=None,
          ssh_stall=None, occupied=None):
    audit_path, release = audit if audit is not None else (None, None)
    killed_at = None
    log_path = root / f"{label}.stdout"
    error_path = root / f"{label}.stderr"
    started = time.monotonic()
    with log_path.open("wb") as log, error_path.open("wb") as error:
        process = subprocess.Popen(
            [harness, "managed-check", openssh("ssh"), config, json.dumps(destination)],
            cwd=root, env=ENV, stdin=subprocess.PIPE, stdout=log, stderr=error,
        )
        try:
            with ExitStack() as handles:
                exited = None
                if control or release or expected == "timeout":
                    def spawned():
                        return next((line for line in lines(log_path) if line.startswith("pid ")), None)

                    wait_until(spawned, f"{label} SSH identity", process)
                    pid = int(spawned().split()[-1])
                    exited = handles.enter_context(retained_client(pid))
                    assert not exited(), f"{label} SSH died before observation"
                    if audit_path is not None:
                        wait_until(lambda: "B OPEN /state" in lines(audit_path), f"{label} /state", process)
                    if occupied is None:
                        assert not listening(8789), f"{label} opened a listener while in flight"
                    else:
                        assert not select.select([occupied], [], [], 0)[0], "diagnostic probed the occupied 8789"
                    if ssh_stall is not None:
                        assert select.select([ssh_stall], [], [], 3)[0], "SSH handshake did not connect"
                    if control == "kill":
                        killed_at = time.monotonic()
                        process.kill()
                    elif control == "shutdown":
                        process.stdin.close()
                    elif control == "cancel":
                        process.stdin.write(b"cancel\n")
                        process.stdin.flush()
                    if release is not None:
                        release()

                if expected is not None:
                    wait_until(lambda: any(line.startswith("result ") for line in lines(log_path)),
                               f"{label} result")
                elapsed = time.monotonic() - started
                if not process.stdin.closed:
                    process.stdin.close()
                result = process.wait(timeout=10)
                assert (result == 0) == (control != "kill"), (label, result)
                if exited is not None:
                    wait_until(exited, f"{label} exact SSH exit")
            stdout = lines(log_path)
            result_lines = [line for line in stdout if line.startswith("result ")]
            cleanup_lines = [line for line in stdout if line.startswith("cleanup_us ")]
            assert all(line.startswith(("pid ", "result ", "cleanup_us ")) for line in stdout), (
                label, "unexpected harness output",
            )
            assert result_lines == ([f"result {expected}"] if expected else []), (label, result_lines)
            assert len(cleanup_lines) == (killed_at is None), (label, "missing or extra retirement measurement")
            assert not error_path.stat().st_size, f"{label} leaked diagnostic stderr"
            if expected == "timeout":
                assert 4.5 <= elapsed < 7.5, (label, elapsed)
            else:
                assert elapsed < 7.5, (label, elapsed)
        finally:
            process.stdin.close()
            stop(process)
    if audit_path is not None:
        wait_until(lambda: "B CLOSED /state" in lines(audit_path), f"{label} forwarded channel cleanup")
        observed = lines(audit_path)
        fixture = [line for line in observed if line.startswith("B FIXTURE ")]
        if fixture:
            feed = expected if expected in ("live", "stale", "down") else "live"
            assert fixture == [
                "B FIXTURE OPEN /ingest",
                "B FIXTURE OPEN /action-consumer",
                f"B FIXTURE READY {feed} retained=2",
            ], (label, fixture)
        assert [line for line in observed if line not in fixture] == [
            "READY 8787", "B OPEN /state", "B CLOSED /state",
        ], (label, observed)
        if release is not None or control is not None:
            assert fixture, f"{label} did not exercise production relay and consumer"
    if killed_at is None:
        cleanup_ms = int(cleanup_lines[0].split()[1]) / 1000
        interval = f"owned retirement {cleanup_ms:.3f}ms"
    else:
        interval = f"kill to owned SSH and forwarded peer closed {(time.monotonic() - killed_at) * 1000:.3f}ms"
    print(f"{sys.platform} {label}: PASS (total {elapsed:.3f}s, {interval})", flush=True)


def exercise(harness, root, server, startup=None):
    username, port, jump_target, permit, backend = server

    def case(label, expected, *, mode=None, alias=False, explicit=True, trust="trusted", control=None,
             server_port=port, ssh_stall=None, jump_port=None, occupied=None):
        with reserve_port() as unused:
            config, known_hosts = ssh_config(
                label, root, username, server_port, unused if explicit else server_port,
                trust=trust, alias=alias, jump_port=jump_port,
            )
            original = config.read_bytes(), known_hosts.read_bytes()
            destination = {"sshTarget": ALIAS} if alias else {"sshDestination": {
                "host": HOST, "username": username, **({"port": server_port} if explicit else {}),
            }}
            with ExitStack() as endpoints:
                audit = endpoints.enter_context(endpoint(backend, mode, root / f"{label}.audit")) if mode else None
                check(harness, root, label, config, destination, expected, audit,
                      control=control, ssh_stall=ssh_stall, occupied=occupied)
            assert (config.read_bytes(), known_hosts.read_bytes()) == original, f"{label} changed SSH files"

    assert not listening(8789), "stock diagnostic port must be free before the fixture"
    with independent(root, username, port, backend, startup) as alive:
        if startup:
            startup.ready()
        case("structured-default-port", "live", mode="live", explicit=False)
        with socket.socket() as occupied:
            if os.name == "nt":
                occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            occupied.bind((HOST, 8789))
            occupied.listen()
            case("structured-explicit-port-occupied-8789", "live", mode="live", occupied=occupied)
            assert not select.select([occupied], [], [], 0)[0], "diagnostic probed the existing listener"
            with socket.create_connection((HOST, 8789), timeout=3) as probe, occupied.accept()[0] as accepted:
                probe.sendall(b"owned by fixture")
                accepted.settimeout(3)
                assert accepted.recv(64) == b"owned by fixture"
        case("legacy-alias", "live", mode="live", alias=True, explicit=False)
        # The jump key can reach only sshd; only the target key can reach the relay.
        case("foreground-proxy-jump", "live", mode="live", alias=True, explicit=False,
             server_port=jump_target, jump_port=port)
        case("unknown-host-key", "host_verification", trust="unknown")
        case("changed-host-key", "host_verification", trust="changed")
        case("unauthorized", "authentication")
        permit(False)
        try:
            case("forwarding-denied", "forwarding_unavailable")
        finally:
            permit(True)
        case("relay-absent", "forwarding_unavailable")
        case("invalid-upgrade", "invalid_protocol", mode="invalid-upgrade")
        case("invalid-frame", "invalid_protocol", mode="invalid-frame")
        case("live-without-snapshot", "no_usable_state", mode="empty-live")
        case("stale-with-retained-snapshot", "stale", mode="stale")
        case("down-with-retained-snapshot", "down", mode="down")
        case("cancel", "cancelled", mode="hold", control="cancel")
        case("shutdown", "cancelled", mode="hold", control="shutdown")
        case("abrupt-death", None, mode="hold", control="kill")
        with socket.socket() as stalled:
            stalled.bind((HOST, 0))
            stalled.listen()
            case("timeout-ssh-handshake", "timeout", server_port=stalled.getsockname()[1], ssh_stall=stalled)
        case("timeout-http-upgrade", "timeout", mode="upgrade-stall")
        case("timeout-state-status", "timeout", mode="status-stall")
        alive()
        assert not listening(8789), "diagnostic left a forwarding listener"


@contextmanager
def install_alias(root, username, port, startup):
    with startup.stage("installed-alias"):
        config, _ = ssh_config("installed", root, username, port, port, alias=True)
        folder = Path.home() / ".ssh"
        profile = folder / "config"
        if folder.is_symlink() or folder.is_junction():
            raise RuntimeError("refusing to use redirected SSH profile directory")
        existed = folder.exists()
        folder.mkdir(exist_ok=True)
        alias_config = config.read_text(encoding="utf-8").replace(
            f"Host {ALIAS}\n", f"Host {ALIAS} {HOST}\n", 1
        ).encode("utf-8")
        with profile.open("xb") as file:
            file.write(alias_config)
    try:
        yield
    finally:
        if profile.read_bytes() != alias_config:
            raise RuntimeError("SSH profile config changed during installed fixture")
        profile.unlink()
        if not existed:
            folder.rmdir()


async def installed_endpoints(root, username, port, startup):
    async with AsyncExitStack() as listeners:
        with startup.stage("installed-listeners"):
            support = runpy.run_path(str(ENDPOINT))
            commands = root / "commands.log"
            commands.touch(exist_ok=False)
            events = listeners.enter_context((root / "events.log").open("x", encoding="ascii"))
        def record(event):
            print(event, file=events, flush=True)

        with startup.stage("installed-listeners"):
            running = support["ProductionEndpoint"]("A", record)
            diagnostic = support["ProductionEndpoint"]("B", record)
            await listeners.enter_async_context(running.listen(8789, "live", gated=False))
            await listeners.enter_async_context(diagnostic.listen(8787, "hold", gated=True))
            manifest = root / "manifest.json"
            staging = manifest.with_suffix(".tmp")
            staging.write_text(json.dumps({
                "host": HOST, "username": username, "port": port, "alias": ALIAS,
                "backendPid": os.getpid(),
                "runningName": running.name, "runningHp": running.hp,
                "diagnosticName": diagnostic.name, "diagnosticHp": diagnostic.hp,
            }), encoding="ascii")
            staging.rename(manifest)
        startup.ready()
        with commands.open(encoding="ascii") as queue:
            while True:
                at = queue.tell()
                command = queue.readline()
                if not command.endswith("\n"):
                    queue.seek(at)
                    await asyncio.sleep(0.02)
                    continue
                command = command.strip()
                if command in ("hold", "live", "down"):
                    await diagnostic.set_mode(command)
                elif command == "update":
                    await running.update()
                elif command != "stop":
                    raise ValueError("invalid installed fixture command")
                record(f"COMMAND {command}")
                if command == "stop":
                    return


@contextmanager
def report_windows_startup(installed_root=None):
    startup = WindowsStartup(installed_root)
    try:
        yield startup
    except StartupFailed:
        startup.publish()
        raise SystemExit("Windows OpenSSH fixture startup failed") from None


def main():
    if not __debug__:
        raise RuntimeError("run the SSH acceptance with Python assertions enabled")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("container", "windows", "installed", "startup-evidence"))
    parser.add_argument("path", type=Path, help="harness binary or installed fixture directory")
    args = parser.parse_args()
    if args.mode == "startup-evidence":
        print(read_startup_evidence(args.path), flush=True)
        return
    if args.mode != "container" and os.name != "nt":
        parser.error(f"{args.mode} requires native Windows")
    if args.mode != "container" and os.environ.get("IMP_ACCEPTANCE_RUNNER") != "github-hosted":
        parser.error("use container mode outside the disposable GitHub-hosted Windows runner")
    if args.mode == "installed":
        root = args.path.resolve()
        root.mkdir(exist_ok=True)
        assert not any(root.iterdir()), "installed fixture directory must be empty"
        assert not (listening(8787) or listening(8789)), "installed fixture requires free 8787 and 8789"
        with report_windows_startup(root) as startup, windows_server(root, startup) as fixture:
            username, port, _, _, backend = fixture
            with install_alias(root, username, port, startup):
                with independent(root, username, port, backend, startup):
                    pass
                asyncio.run(installed_endpoints(root, username, port, startup))
    else:
        harness = args.path.resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="imp-managed-check-") as directory:
            root = Path(directory)
            if args.mode == "container":
                for name in ("client_key", "wrong_key", "jump_key"):
                    keygen(root / name)
                with docker_server(root) as fixture:
                    exercise(harness, root, fixture)
            else:
                assert not listening(8787), "hosted Windows fixture requires free 8787"
                with report_windows_startup() as startup, windows_server(root, startup) as fixture:
                    exercise(harness, root, fixture, startup)


if __name__ == "__main__":
    main()
