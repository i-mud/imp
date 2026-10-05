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

## Direct WSS gateway (advanced/manual)

Direct WSS access uses one 256-bit pairing token. Imp's gateway stores only
the SHA-256 digest of the token. The plaintext token is entered once in the
desktop Connection settings later; do not put it in a URL, shell history,
service unit, repository file, or reverse-proxy configuration.

Until pairing/rotation UX is automated, generate a token and its digest
manually:

```bash
mkdir -p ~/.config/imp
chmod 700 ~/.config/imp

read -r token digest <<EOF
$(python3 - <<'PY2'
import base64
import hashlib
import secrets

token = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
digest = hashlib.sha256(token.encode("ascii")).hexdigest()
print(token, digest)
PY2
)
EOF

printf 'IMP_GATEWAY_TOKEN_SHA256=%s\n' "$digest" > ~/.config/imp/gateway.env
chmod 600 ~/.config/imp/gateway.env

printf 'Pairing token: %s\n' "$token"
```

Record the displayed pairing token securely for the desktop Connection step,
then clear the shell variables:

```bash
unset token digest
```

`gateway.env` contains only the digest, not the plaintext pairing token. The
gateway refuses to start without a valid 64-hex-character digest.

The release installer does not enable Direct WSS. After deliberately
configuring `gateway.env`, enable the gateway separately:

```bash
systemctl --user enable --now imp-gateway.service
systemctl --user status imp-gateway.service
```

The gateway listens only on `127.0.0.1:8788` and connects only to the relay at
`ws://127.0.0.1:8787`. Never proxy port 8787 or the relay's `/ingest` or
`/action-consumer` endpoints to the Internet.

### Public TLS/WSS edge for Direct mode

Direct WSS requires a publicly trusted TLS endpoint. TLS termination belongs to
a normal reverse proxy such as Caddy or nginx, not to Imp's Python
gateway.

The public proxy must expose only `/state`, `/action`, and optionally
`/healthz`, forwarding them to `127.0.0.1:8788`. All other paths should be
rejected.

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

Certificate acquisition and renewal are currently operator-owned. If
certificates are provisioned outside the reverse proxy, renewal must also reload
the proxy after replacing its readable certificate/key copies.

The Slice 11 live acceptance used Caddy and a publicly trusted certificate and
verified the public `/healthz`, `/state`, and `/action` path while
`/ingest` and `/action-consumer` remained unreachable. Automated
reverse-proxy/certificate provisioning remains future distribution work.

### Direct desktop configuration

On the workstation, open Imp and use **Settings -> Connection -> Direct**.

Enter:

- the public `wss:` state endpoint, for example
  `wss://imp.example/state`; and
- the exact 43-character plaintext pairing token whose SHA-256 digest is stored
  in `gateway.env`.

Save the connection and restart Imp. The URL must use `wss:`, contain no
credentials, query, or fragment, and end in `/state`.

The pairing token is persisted by the native application. Imp's
settings-read path does not return the stored plaintext token merely to populate
the form; an existing Direct token can therefore remain unchanged when editing
other Direct settings.

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
