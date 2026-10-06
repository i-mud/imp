# VPS deployment

Installs Imp's relay and TinyFugue live feed as `systemd --user`
services so the pipeline survives a VPS reboot and an administrator's SSH
session ending without root-owned Imp services. The authenticated Direct WSS
gateway is included in the server bundle but remains disabled unless the
operator deliberately configures Direct mode.

The relay, feed-facing endpoints, and gateway all remain loopback-only. SSH
forwards the **entire relay TCP port** to the workstation; it does not filter
relay routes or add application authentication. Direct WSS instead uses a
separate TLS reverse proxy in front of the authenticated gateway; the reverse
proxy never forwards the relay itself.

Everything below runs **on the VPS** unless labelled otherwise.

Loopback prevents remote network access but is not same-user isolation. Any
local OS user or process in the VPS network namespace can reach the relay, and
Imp provides no per-user endpoint authentication. Deploy only on a
single-user VPS or where every host-local user/process is mutually trusted;
untrusted multi-user hosts are unsupported.

## Install from a release bundle

The normal server installation does **not** require an Imp source checkout,
Node.js, npm, Rust, or `uv` on the VPS.

The published Linux server bundle supports:

- Linux x86_64;
- CPython 3.12;
- `systemd --user`;
- the TinyFugue client adapter.

Python 3.12 must include `venv` support. TinyFugue itself remains
operator-owned and must already be installed with the GMCP capabilities
described below.

Download both server assets from the matching Imp GitHub release:

```text
imp-server-<version>-linux-x86_64.tar.gz
imp-server-<version>-linux-x86_64.tar.gz.sha256
```

Verify the downloaded archive **before executing anything from it**:

```bash
sha256sum -c imp-server-<version>-linux-x86_64.tar.gz.sha256
```

Then extract it:

```bash
tar -xzf imp-server-<version>-linux-x86_64.tar.gz
cd imp-server-<version>
```

For a normal TinyFugue installation with the documented personal startup file,
run:

```bash
./install.sh
```

The installer uses the existing `~/.tfrc` by default. Imp backs it up before
adding:

```text
/load ~/.config/imp/capture.tf
```

If `~/.tfrc` does not exist, the installer stops rather than creating it.
TinyFugue can fall back to startup files relative to its working directory, so
creating a new `~/.tfrc` could otherwise change which configuration TinyFugue
loads.

If TinyFugue uses a different startup file, provide that existing file
explicitly:

```bash
./install.sh --tf-startup /path/to/active/tinyfugue/startup-file
```

This also covers custom layouts and TinyFugue launches that use `-f FILE`.

The installer:

- verifies the bundle's internal checksums;
- creates a private Python 3.12 virtual environment entirely from the bundled
  wheelhouse, without resolving packages from the network;
- installs releases under
  `~/.local/share/imp/releases/<version>/`;
- creates each Python environment directly at its final release path so Python
  console scripts never depend on a temporary installation directory;
- treats an installed version as immutable: reinstalling the exact same bundle
  reuses it, while a different bundle claiming the same version is rejected;
- atomically selects the installed runtime through
  `~/.local/share/imp/current`;
- installs the fixed `~/.local/bin/imp-action-consumer` helper link;
- installs `~/.config/imp/capture.tf`;
- installs the relay, feed, and optional gateway user units;
- enables and starts only `imp-relay.service` and `imp-feed.service`;
- preserves and rolls back the previous installation if activation fails; and
- does not change Direct WSS gateway enablement and restarts it only when it was
  already active.

If TinyFugue is already connected when the installer restarts `imp-feed`, the
feed deliberately starts without retaining the previous selected-action
context. Generate a fresh TinyFugue world-selection event after installation,
for example by switching away from and back to the active world, or reconnect
the character. Outbound actions remain fail-closed until the new selected
context is established. A reconnect may also be necessary for MUDs that send
complete character identity only during login.

Configuration and state directories are kept private to the user.

### User lingering

For the user services to survive logout and start without an interactive SSH
session, systemd user lingering must be enabled:

```bash
loginctl show-user "$USER" -p Linger
```

The expected result is:

```text
Linger=yes
```

If it is disabled, enable it:

```bash
loginctl enable-linger "$USER"
```

If policy prevents the user from doing that, an administrator can run:

```bash
sudo loginctl enable-linger "$USER"
```

The installer reports a warning when lingering is not enabled; it does not
silently perform privileged system configuration.

## Direct WSS gateway (advanced)

Direct WSS uses one 256-bit pairing token. The gateway stores only its
SHA-256 digest. The plaintext token is shown once by the bundle's provisioning
command for deliberate terminal handoff to the desktop. On the VPS, never put
the plaintext token in arguments, environment variables, files, URLs, service
units, reverse-proxy configuration, or logs.

The bundled `imp-direct-wss` command is installed at the stable user link
`~/.local/bin/imp-direct-wss`. Invoke that link from the installed Linux
target user with a working user systemd manager; it operates only on that
user's installed bundle and fixed HOME paths. From a source checkout, invoke
`uv run --directory services/relay imp-direct-wss OPERATION`, replacing
`OPERATION` with exactly one of `setup`, `rotate`, or `status` (for example,
`uv run --directory services/relay imp-direct-wss setup`). Both forms retain
the installed-target-user safety boundary; this is not a general systemd manager.

Run setup from an interactive terminal:

```bash
~/.local/bin/imp-direct-wss setup
```

Setup requires stdin and stdout terminals before mutation. With no existing
configuration it securely generates a token, writes only its digest to
`~/.config/imp/gateway.env` (private `0700` config directory and `0600` file),
enables/starts the supported gateway user service, checks exact minimal HTTP
health `{"status":"ok"}`, and authenticates `/state` requiring the first
protocol-v2 `hello`. The token is printed once to the terminal only after
success; do not capture or redirect command output. If a secure, syntactically
valid single-digest configuration already exists, setup is idempotent: it
generates no token, makes no service/network calls, and changes nothing.
If configuration is missing while the gateway is already active, setup refuses
before mutation; use `rotate` only when rotation is deliberate. Unsafe existing
files or service arrangements are rejected without modification. Paths must not
be symlinks; unsupported custom gateway unit files, systemd drop-ins, linked or
masked units, and manager state requiring daemon reload are rejected rather
than adopted. The supported unit must match the installed shipped service;
systemd's resolved fragment, executable, environment-file, and restart policy
must agree with that unit.

Rotate a compromised or otherwise replaced token using:

```bash
~/.local/bin/imp-direct-wss rotate
```

Rotate has the same terminal requirement and handoff discipline. If the
gateway was active, it is restarted and its exact minimal HTTP health plus
authenticated `/state` protocol-v2 hello are verified. If it was inactive, it
remains inactive (and retains its previous enabled/disabled state); no
network preflight or activation occurs.

Handled failures attempt to restore the previous configuration bytes and
enabled/active service state. Rollback is best effort: if recovery is incomplete,
the command reports recovery-required and attempts to stop the gateway, rather
than claiming success. These protections cover handled operation failures, not
power loss or cross-filesystem/systemd atomicity.

If the prior digest is restored after a failed rotation, the old token
authenticates again. Treat that as immediate containment work: stop the gateway
and address the failure before retrying rotation. Do not assume failed rotation
invalidated a compromised credential.

Check without changing files, locks, credentials, or service state:

```bash
~/.local/bin/imp-direct-wss status
```

Status separately reports configuration path safety, syntax validity, file and
directory modes, gateway unit policy, systemd enabled/active state, and minimal
health without disclosing credential material. An unsafe path names the
offending directory and reason. The fixed paths are `~/.config/imp/gateway.env`,
`~/.config/systemd/user/imp-gateway.service`, and
`~/.local/share/imp/current/.venv/bin/imp-gateway`. The config's only accepted
content is exactly one `IMP_GATEWAY_TOKEN_SHA256=<64 hex characters>`
assignment, with full-line comments and surrounding whitespace allowed; NUL
bytes are rejected anywhere, including comments. It is not a general environment
file. The config directory and file must be owned by the target user with modes
exactly `0700` and `0600`, respectively.

The ancestors `~/.config`, `~/.config/systemd`, and `~/.config/systemd/user`
must be owned by the target user and never world-writable. They may be
group-writable only when their group is the target user's primary group and the
system account database enumeration (`getent passwd`/`getent group`) lists the
target user, no other account with that primary group, and no other
supplementary member in any group record with that GID. A group-writable
ancestor that also carries an extended access ACL is rejected. That covers the
ordinary Ubuntu local user-private-group layout (`0775` with umask `002`), so
those directories need no permission change. A group-writable ancestor whose
group is shared, or cannot be resolved or enumerated, is rejected.

This check sees only enumerable accounts and groups. On hosts whose account
sources do not fully enumerate users and groups, such as some SSSD or LDAP
configurations, it is not proof that no other principal shares the group. If any
other account can write such an ancestor through directory-service membership
or another mechanism, remove group write from that ancestor.

The normal release installer does not create pairing credentials or enable
`imp-gateway.service`. Upgrade preserves the gateway's existing enablement and
restarts it only if it was already active; see the installer behavior above.
Provision the gateway separately and deliberately with `setup`.

The gateway listens only on `127.0.0.1:8788` and connects only to the relay at
`ws://127.0.0.1:8787`. Never proxy port 8787 or the relay's `/ingest` or
`/action-consumer` endpoints to the Internet.

### Public TLS/WSS edge for Direct mode

The operator owns the public hostname, DNS, publicly trusted TLS certificate
acquisition/renewal, and reverse-proxy lifecycle. Imp's Python gateway does
not terminate TLS. The reverse proxy must forward only `/state`, `/action`,
and optionally `/healthz` to `127.0.0.1:8788`; reject every other path.

A minimal Caddy route shape is:

```text
https://imp.example {
    @gateway path /state /action /healthz

    handle @gateway {
        reverse_proxy 127.0.0.1:8788
    }

    handle {
        respond 404
    }
}
```

If certificates are provisioned outside the reverse proxy, renewal must also
reload the proxy after replacing its readable certificate/key copies.

The Slice 11 live acceptance used Caddy and a publicly trusted certificate and
verified the public `/healthz`, `/state`, and `/action` path while
`/ingest` and `/action-consumer` remained unreachable. This is historical
evidence only; it does not verify Slice 24 provisioning or current live
acceptance.

### Bounded public verification

Run this from a machine with network reachability to the public hostname, using
the installed bundle's CPython 3.12 environment (which includes the Imp relay
package and `websockets`), or another environment that already has both
dependencies on its import path. It probes authenticated state, auth
rejection, and HTTP plus WebSocket 404s for privileged/unknown routes without
an `Origin` header, and optionally minimal health. It sends no action frame.
The token is read from /dev/tty using `getpass`, never argv or environment.
Both secret prompts fail closed if terminal echo control is unavailable, before
reading fallback input or starting network activity. Errors are intentionally
generic so exception text cannot expose credentials or response payloads.

Save the snippet as a local script (not in a repository or shared location),
then run it interactively with the approved WSS state endpoint as its sole
argument. It rejects credentials, query/fragment, non-WSS URLs, and endpoints
not ending in `/state`; derives sibling routes while retaining any path prefix.
The optional previous-token prompt proves revocation after rotation; its value
is read only from /dev/tty and never appears in output. TLS verification stays
enabled, redirects are refused, and no insecure TLS options are used. Each HTTP
probe has a five-second monotonic elapsed deadline covering headers and body,
in addition to the socket timeout. It uses `SIGALRM` to interrupt even a
drip-fed response, so it requires Unix, the main thread, and an unarmed
`ITIMER_REAL`; it refuses to probe if a real-time timer is already armed.

For example, after saving the snippet as `public_wss_check.py`:

```bash
~/.local/share/imp/current/.venv/bin/python public_wss_check.py 'wss://imp.example/state'
```

```python
import base64
import getpass
import json
import secrets
import signal
import sys
import time
import warnings
from urllib.parse import urlsplit, urlunsplit
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

from imp_relay.websocket_logging import websocket_logger
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect

logger = websocket_logger()
HTTP_DEADLINE = 5


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, new_url):
        return None


def http_get(url):
    def expired(signum, frame):
        raise TimeoutError

    if any(signal.getitimer(signal.ITIMER_REAL)):
        fail()
    deadline = time.monotonic() + HTTP_DEADLINE
    previous_handler = signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, max(deadline - time.monotonic(), 0.000001))
        with opener.open(Request(url, method="GET"), timeout=5) as response:
            return response.status, response.read(128)
    except HTTPError as exc:
        exc.close()
        return exc.code, b""
    except Exception:
        fail()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def fail():
    print("Verification failed.")
    raise SystemExit(1)


def endpoint(path):
    return urlunsplit(("wss", parts.netloc, path, "", ""))


def canonical_token(value):
    try:
        raw = base64.urlsafe_b64decode(value + "=")
        return (len(value) == 43 and len(raw) == 32
                and base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") == value)
    except Exception:
        return False


def rejection(path, token):
    try:
        with connect(endpoint(path), proxy=None, open_timeout=5, logger=logger) as ws:
            if token is not None:
                ws.send('{"type":"auth","token":"' + token + '"}')
            ws.recv(timeout=10)
    except ConnectionClosed as exc:
        if exc.code == 1008:
            return
    except Exception:
        pass
    fail()


def hidden(path):
    try:
        with connect(endpoint(path), proxy=None, open_timeout=5, logger=logger):
            pass
    except InvalidStatus as exc:
        if exc.response.status_code == 404:
            return
    except Exception:
        pass
    fail()


if len(sys.argv) != 2:
    fail()
try:
    parts = urlsplit(sys.argv[1])
    valid_url = (parts.scheme == "wss" and parts.hostname
                 and parts.username is None and parts.password is None
                 and not parts.query and not parts.fragment
                 and parts.path.endswith("/state"))
    parts.port
except ValueError:
    fail()
if (not valid_url or "?" in sys.argv[1] or "#" in sys.argv[1]):
    fail()
prefix = parts.path[:-len("/state")]
state_path = prefix + "/state"
action_path = prefix + "/action"
health_path = prefix + "/healthz"
try:
    with open("/dev/tty", "w") as tty, warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        token = getpass.getpass("Pairing token: ", stream=tty)
        previous_token = getpass.getpass(
            "Previous token to verify revocation (optional): ", stream=tty)
    if not canonical_token(token) or (previous_token and not canonical_token(previous_token)):
        fail()
except Exception:
    fail()

try:
    with connect(endpoint(state_path), proxy=None, open_timeout=5, logger=logger) as ws:
        ws.send('{"type":"auth","token":"' + token + '"}')
        hello = json.loads(ws.recv(timeout=5))
        if hello.get("type") != "hello" or hello.get("protocol") != 2:
            fail()
except SystemExit:
    raise
except Exception:
    fail()

wrong_token = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
while wrong_token in (token, previous_token):
    wrong_token = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
for route in (state_path, action_path):
    rejection(route, wrong_token)
    rejection(route, None)
    if previous_token:
        rejection(route, previous_token)

opener = build_opener(ProxyHandler({}), HTTPSHandler(), NoRedirect())
for route in ("/ingest", "/action-consumer", "/unknown"):
    status, _ = http_get(f"https://{parts.netloc}{prefix}{route}")
    if status != 404:
        fail()
    hidden(prefix + route)

status, body = http_get(f"https://{parts.netloc}{health_path}")
if status not in (200, 404):
    fail()
if status == 200:
    try:
        if json.loads(body) != {"status": "ok"}:
            fail()
    except Exception:
        fail()

print("Public gateway verification passed.")
```

Use only a disposable local copy of the script and remove it after use. Do not
enable websocket protocol/frame logging. The configured Imp `websockets`
logger's INFO security floor is part of the credential confidentiality
boundary.

### Separate live acceptance authorization

This is an operator-run acceptance plan only; no live change is authorized by
this document, and none is being performed now. Before any mutation, obtain
explicit approval naming the existing VPS, desktop, public hostname,
maintenance window, and one harmless MUD action. Approval must specifically
cover securely parking the exact existing `gateway.env` bytes in a mode-`0600`
backup containing only the digest assignment plus permitted comments/whitespace,
stopping/disabling the gateway to establish the clean initial state,
setup/rotation/reinstall and desktop changes, and restoring the prior approved
configuration and service state. Any old plaintext token
may be held only in the operator's approved secure terminal/native credential
store, never in a VPS file or log. If approval or the existing Caddy
environment is absent, stop before mutation. Reuse the existing service and
port `8788`; do not provision infrastructure or change Caddy, DNS, or TLS.

1. Establish and record the installed-bundle baseline with `~/.local/bin/imp-direct-wss status`: no gateway digest, service disabled/inactive, and Direct WSS not enabled; preserve the prior digest-only configuration and enabled/active state as authorized.
2. Run the supported setup command `~/.local/bin/imp-direct-wss setup` interactively.
3. Receive the newly generated token once through the intentional terminal handoff; never redirect, capture, or log output.
4. Confirm `~/.config/imp/gateway.env` contains only the SHA-256 digest assignment, with the supported private ownership/modes and no plaintext token.
5. Confirm `imp-gateway.service` is enabled and active on the existing user manager.
6. Confirm setup completed its local minimal-health check and authenticated `/state` protocol-v2 hello; record `~/.local/bin/imp-direct-wss status`.
7. Run the bounded verifier against the existing public `wss:` `/state` URL and confirm trusted TLS plus authenticated state access.
8. Confirm the verifier receives 404 for privileged/unknown HTTP and WebSocket routes without an `Origin` header.
9. Configure the existing desktop's Direct pairing through Settings with the public `/state` URL and the new token; save and restart.
10. Confirm live MUD state arrives over Direct WSS.
11. Send only the separately approved action through the desktop UI and independently observe its execution; do not synthesize an action in a probe.
12. While the gateway is active, run `~/.local/bin/imp-direct-wss rotate`; receive the replacement token only by terminal handoff and confirm the service remains active.
13. Rerun the bounded verifier, entering the replacement token and old token at its secure prompts; confirm the old credential and missing/wrong credentials close with `1008`, while the replacement succeeds.
14. Save the replacement token in the existing desktop Direct settings, restart, and confirm Direct state succeeds.
15. Stop and restart the gateway once; confirm the desktop's existing reconnect behavior recovers state without action replay.
16. Reinstall/upgrade from the already-approved extracted bundle on the same VPS by running `./install.sh`; confirm the digest, enabled state, and active state are preserved and the active gateway is restarted as expected.
17. Confirm `ss -ltnp` shows relay `8787` and gateway `8788` listening only on loopback; restore the parked digest-only configuration, prior service enablement/active state, and approved desktop credential, then record only observed results and restore outcome.

### Direct desktop configuration

On the workstation, open Imp and use **Settings -> Connection -> Direct**.

Enter the public `wss:` state endpoint (for example `wss://imp.example/state`)
and the 43-character pairing token displayed by `imp-direct-wss setup` or
`rotate`. Save and restart Imp. The URL must use `wss:`, contain no
credentials, query, or fragment, and end in `/state`.

The pairing token is persisted by the native application. Imp's settings-read
path does not return the stored plaintext token merely to populate the form;
an existing Direct token can therefore remain unchanged when editing other
Direct settings.

Do not put the token in a URL, reverse-proxy configuration, `VITE_*`
environment value, or WebView `localStorage`.

For troubleshooting only, the native configuration is persisted at:

- Linux: `~/.config/dev.imud.imp/tunnel.json`
- Windows: `%APPDATA%\dev.imud.imp\tunnel.json`
- macOS: `~/Library/Application Support/dev.imud.imp/tunnel.json`

Normal setup should use the Connection UI rather than editing this file by
hand.

## TinyFugue integration details

The installer copies the shipped hook to:

```text
~/.config/imp/capture.tf
```

By default the installer adds the Imp `/load` line idempotently to an existing
`~/.tfrc`. With `--tf-startup FILE`, it instead backs up and updates the
explicitly selected existing startup file.

TinyFugue's documented personal configuration is `$HOME/.tfrc`, but TinyFugue
also supports alternate startup files. Operators using a different startup
file should pass that same file to `--tf-startup`.

Load the hook before anything in that startup path can connect or log in to
the MUD. AVATAR sends the full identity-bearing `Char.Status` only once, during
initial character login, and provides no supported way to request another full
snapshot; later `Char.Status` messages are deltas that may omit
`character_name`. Loading is additive: Imp does not own or replace the
operator's GMCP negotiation or connection macros. Repeated loads replace the
named Imp definitions rather than duplicating them.
The Imp `GMCP`, `CONNECT`, and `WORLD` hooks are defined at priority 2
with fall-through (`-Fp2`) so they observe without consuming operator events.
`GMCP_LOGIN` remains an operator login-script prerequisite but is not a
Imp capture hook.
Imp runs ahead of default priority-1 handlers such as `received-gmcp`; the
`-F` flag lets those handlers run afterward. Two same-priority
non-fall-through GMCP hooks previously lost whole events intermittently. Do
not drop `-F` and do not change the operator's own hooks. A higher-priority
non-fall-through hook can still prevent Imp from running, so re-verify
coexistence when operator priorities differ.

Confirm the GMCP definitions inside TinyFugue: the operator's handler should
remain at its existing priority and `imp_capture_gmcp` must appear as
`-Fp2`.

The hook writes to the fixed path `~/.local/state/imp/spool`.
`imp-feed` owns that path as a symlink into its private runtime
directory and replaces it on every (re)start; nothing about the hook file
changes when the feed restarts. See
[`integrations/tinyfugue/README.md`](../integrations/tinyfugue/README.md) for
why this is a plain drained file rather than a FIFO.

## TinyFugue GMCP login prerequisite

Because that identity message is sent once, capture depends on the operator's
TinyFugue performing GMCP login sequencing at the right negotiation point. The
invariant is a **build whose GMCP support includes the `GMCP_LOGIN` hook**,
driving operator login scripts that use it to run their GMCP capability
negotiation and send `Char.Login`. Without `GMCP_LOGIN` the operator login path
cannot be relied on to sequence this correctly, and Imp then keeps
consuming `Char.Vitals` and `Char.Status` deltas without ever observing
`character_name`, so the HUD stays down.

This is stated as a capability, not a version. The build verified live was
TinyFugue `5.2.2-3-g4f0ff34`; an upstream or distribution version number does
not by itself prove `GMCP_LOGIN` is compiled into the binary in use, so confirm
the capability. The conclusive signal is on the first login after installing
the hook: login produces a full `Char.Status` carrying `character_name`, and
Imp obtains a snapshot and writes its checkpoint - visible as
`has_snapshot: true` with a non-null `seq` on `/healthz`. In the verified run,
`Char.StatusVars` was observed immediately followed by that full identity-bearing
`Char.Status`; AVATAR does not document that ordering as a guarantee, so treat
it as an observation rather than a requirement. When the signal is missing,
[bounded diagnostic capture](#diagnostic-raw-capture-opt-in-vps) shows which
packages did arrive.

The build, the upgrade and the login scripts are operator-owned; Imp
never modifies them and sends no GMCP itself.

## Verifying the deployment

For the normal Managed/External SSH installation:

```bash
# Required services are active.
systemctl --user is-active \
  imp-relay.service \
  imp-feed.service

# Required services are enabled for future user-systemd starts.
systemctl --user is-enabled \
  imp-relay.service \
  imp-feed.service

# The unauthenticated relay remains loopback-only.
ss -ltnp | grep -E ':8787[[:space:]]'
# expect 127.0.0.1:8787, never 0.0.0.0:8787

# The selected runtime is the versioned release installation.
readlink ~/.local/share/imp/current

# TinyFugue actions use the stable helper path.
readlink ~/.local/bin/imp-action-consumer

# Relay process/state health.
curl -s http://127.0.0.1:8787/healthz

# The feed created the runtime spool and private context marker.
ls -l ~/.local/state/imp/spool
stat -c '%a %n' ~/.local/state/imp/context  # 600 after a world selection

# Recent lifecycle logs; raw GMCP and action text are not logged.
journalctl --user \
  -u imp-relay.service \
  -u imp-feed.service \
  -n 50
```

When Direct WSS is deliberately configured, additionally verify:

```bash
systemctl --user is-active imp-gateway.service

ss -ltnp | grep -E ':8788[[:space:]]'
# expect 127.0.0.1:8788, never a public bind

curl -s http://127.0.0.1:8788/healthz
# expect: {"status":"ok"}

# Verify the public TLS edge independently:
# curl -s https://<public-host>/healthz
# curl -s -o /dev/null -w '%{http_code}\n' https://<public-host>/ingest
# the second command must report 404
```

## Common failure diagnostics

| Symptom                                                        | Check                                                                                                                                                                                                                                                                   |
| -------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `imp-feed` exits immediately with "another Imp feed holds ..." | A duplicate instance is running - manual invocation while the service is active, or a second service instance. `systemctl --user status imp-feed.service`, then stop the extra process.                                                                                 |
| Relay reachable but no HUD data                                | In SSH mode, check `curl http://127.0.0.1:8787/healthz` through the forward. In Direct mode, check both local gateway health and the public TLS `/healthz`; then confirm a relay producer is attached and inspect `imp-feed.service`.                                   |
| Direct WSS repeatedly reconnects                               | Confirm the public certificate is trusted/current, the reverse proxy forwards `/state` and `/action` to `127.0.0.1:8788`, and the desktop pairing token matches the digest in `gateway.env`. Never move the token into the URL while debugging.                         |
| Direct WSS path is unsafe or unit policy unsupported           | `imp-direct-wss status` names the directory and reason. For a shared group-writable or world-writable ancestor, remove that write access or give the directory the user's private group; do not loosen `~/.config/imp` or `gateway.env`.                                |
| TinyFugue shows an `fwrite` error line                         | The feed is down or the hook symlink target directory is missing. TinyFugue is not blocked by this - it is the intended fail-open behaviour - but no HUD update reaches the relay until the feed is running again.                                                      |
| Services do not survive a reboot                               | Confirm `loginctl show-user "$USER" -p Linger` reports `Linger=yes`; without it, user units never start without an interactive login.                                                                                                                                   |
| Relay bound to more than loopback                              | The current relay refuses every non-loopback host and has no override. Restore the shipped unit and executable if this occurs.                                                                                                                                          |
| Feed consuming GMCP but relay reports `has_snapshot: false`    | No `Char.Status.character_name` has been observed since the feed started. Confirm the hook was loaded by the active startup file before login, and that the TinyFugue build provides `GMCP_LOGIN` (the TinyFugue integration section); then log the character in again. |

## Diagnostic raw capture (opt-in, VPS)

Normal operation keeps raw GMCP only in the bounded private runtime spool
under `$XDG_RUNTIME_DIR`; it retains no raw diagnostic history. To capture a
bounded, private diagnostic trail temporarily:

```bash
systemctl --user edit imp-feed.service
```

Add an override:

```ini
[Service]
ExecStart=
ExecStart=%h/.local/share/imp/current/.venv/bin/imp-feed --diagnostic-capture
```

Then `systemctl --user daemon-reload && systemctl --user restart
imp-feed.service`. Files land privately under
`~/.local/state/imp/diagnostics/`, rotate at 1 MiB, and keep at most 5
files. Remove the override (`systemctl --user revert imp-feed.service`)
when done; this is a debugging aid, not a default.

## Development checkout deployment

The source-tree VPS workflow remains available for contributors and local
development, but it is not the release installation path.

From the canonical development checkout:

```bash
npm run vps:sync
```

By default this synchronizes the repository to the configured development VPS
checkout. Developers may then use the project-local `uv` environments for
iteration and testing.

Released installations should use the versioned server archive and
`install.sh` instead. Normal Imp users do not need a VPS Git checkout, Node.js,
npm, Rust, or `uv`.
