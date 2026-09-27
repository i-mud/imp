# VPS deployment

Runs `services/relay`, the authenticated remote gateway, and
`integrations/tinyfugue`'s live feed as `systemd --user` services, so the
pipeline survives a VPS reboot and an administrator's SSH session ending
without root-owned TinyScry services.

The relay, feed-facing endpoints, and gateway all remain loopback-only. SSH may
still expose the relay to one workstation as before. Direct WSS instead uses a
separate TLS reverse proxy in front of the authenticated gateway; the reverse
proxy never forwards the relay itself.

Everything below runs **on the VPS** unless labelled otherwise.

Loopback prevents remote network access but is not same-user isolation. Any
local OS user or process in the VPS network namespace can reach the relay, and
TinyScry provides no per-user endpoint authentication. Deploy only on a
single-user VPS or where every host-local user/process is mutually trusted;
untrusted multi-user hosts are unsupported.

## 1. Sync the repository (in WSL/repo)

The `tinyscry-tinyfugue` package depends on `tinyscry-relay` by relative path
(see its `pyproject.toml` `[tool.uv.sources]`), so sync the whole repository
rather than individual subdirectories.

The repository includes the canonical sync command:

```bash
npm run vps:sync
```

It copies `~/src/tinyscry/` to the SSH host alias `avatar` at `~/tinyscry/`
while excluding Git metadata, dependency/build directories, caches,
environment files, and common key material.

For another host or destination, override the defaults:

```bash
TINYSCRY_VPS_HOST=<host-alias> \
TINYSCRY_VPS_DEST='~/tinyscry/' \
npm run vps:sync
```

Re-run the sync after every source change that needs to reach the VPS; the WSL2
checkout stays authoritative.

## 2. Install the Python environments (on VPS)

```bash
cd ~/tinyscry
uv sync --project services/relay
uv sync --project integrations/tinyfugue
mkdir -p ~/.local/bin
ln -sfn \
  "$HOME/tinyscry/integrations/tinyfugue/.venv/bin/tinyscry-action-consumer" \
  "$HOME/.local/bin/tinyscry-action-consumer"
```

The project-local environments back the systemd units. The fixed
`~/.local/bin/tinyscry-action-consumer` link is the only helper path invoked by
the TinyFugue hook; action text is pipe data and never argv.

## 3. Install the systemd user units (on VPS)

```bash
mkdir -p ~/.config/systemd/user
cp ~/tinyscry/deploy/systemd/tinyscry-relay.service ~/.config/systemd/user/
cp ~/tinyscry/deploy/systemd/tinyscry-feed.service ~/.config/systemd/user/
cp ~/tinyscry/deploy/systemd/tinyscry-gateway.service ~/.config/systemd/user/
systemctl --user daemon-reload
```

## 4. Configure the authenticated gateway (on VPS)

Direct WSS access uses one 256-bit pairing token. TinyScry's gateway stores only
the SHA-256 digest of the token. The plaintext token is copied once to the
desktop configuration later; do not put it in a URL, shell history, service
unit, repository file, or reverse-proxy configuration.

Until pairing/rotation UX is automated, generate a token and its digest
manually:

```bash
mkdir -p ~/.config/tinyscry
chmod 700 ~/.config/tinyscry

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

printf 'TINYSCRY_GATEWAY_TOKEN_SHA256=%s\n' "$digest" > ~/.config/tinyscry/gateway.env
chmod 600 ~/.config/tinyscry/gateway.env

printf 'Pairing token: %s\n' "$token"
```

Record the displayed pairing token in the intended desktop configuration and
then clear the shell variables:

```bash
unset token digest
```

`gateway.env` contains only the digest, not the plaintext pairing token. The
gateway refuses to start without a valid 64-hex-character digest.

The gateway listens only on `127.0.0.1:8788` and connects only to the relay at
`ws://127.0.0.1:8787`. Never proxy port 8787 or the relay's `/ingest` or
`/action-consumer` endpoints to the Internet.

### Public TLS/WSS edge for Direct mode

Direct WSS requires a publicly trusted TLS endpoint. TLS termination belongs to
a normal reverse proxy such as Caddy or nginx, not to TinyScry's Python
gateway.

The public proxy must expose only `/state`, `/action`, and optionally
`/healthz`, forwarding them to `127.0.0.1:8788`. All other paths should be
rejected.

A minimal Caddy route shape is:

```text
https://tinyscry.example {
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

The native desktop configuration file is the same `tunnel.json` used by SSH
modes:

- Linux: `~/.config/dev.tinyscry.hud/tunnel.json`
- Windows: `%APPDATA%\dev.tinyscry.hud\tunnel.json`
- macOS: `~/Library/Application Support/dev.tinyscry.hud/tunnel.json`

For Direct WSS:

```json
{
  "mode": "direct",
  "remoteUrl": "wss://tinyscry.example/state",
  "pairingToken": "<43-character pairing token>"
}
```

`remoteUrl` must use `wss:`, contain no credentials/query/fragment, and end in
`/state`. The pairing token must be the exact plaintext token whose SHA-256
digest is configured on the gateway.

Do not put the token in a URL, reverse-proxy configuration, `VITE_*`
environment value, or WebView `localStorage`.

## 5. Enable lingering (on VPS, once)

Without lingering, `systemd --user` (and everything it manages) stops the
moment your last session logs out, and `$XDG_RUNTIME_DIR` may not exist at
boot for a user with no active login:

```bash
loginctl enable-linger "$USER"
loginctl show-user "$USER" -p Linger   # expect: Linger=yes
```

## 6. Enable and start the services (on VPS)

```bash
systemctl --user enable --now \
  tinyscry-relay.service \
  tinyscry-feed.service \
  tinyscry-gateway.service

systemctl --user status \
  tinyscry-relay.service \
  tinyscry-feed.service \
  tinyscry-gateway.service
```

`tinyscry-feed.service` `Wants=` (not `Requires=`) the relay: if the relay is
briefly down, the feed keeps running and reconnects with the publisher's
existing bounded backoff rather than failing.

## 7. Install the TinyFugue hook (on VPS)

Copy the unchanged hook to TinyScry's config directory:

```bash
mkdir -p ~/.config/tinyscry
cp ~/tinyscry/integrations/tinyfugue/tinyscry.tf ~/.config/tinyscry/capture.tf
```

Then add this line to the startup file used by the operator's actual
TinyFugue invocation:

```text
/load ~/.config/tinyscry/capture.tf
```

Do not have the installer create or overwrite an operator startup file.
TinyFugue's `-f FILE` option loads `FILE` instead of the normal personal
config, so `~/.tfrc` is not necessarily active. For example, when starting
from `~/avatar/tf` with `tf -f./.tfrc -n`, add the line to
`~/avatar/tf/.tfrc`.

Load the hook before anything in that startup path can connect or log in to
the MUD. AVATAR sends the full identity-bearing `Char.Status` only once, during
initial character login, and provides no supported way to request another full
snapshot; later `Char.Status` messages are deltas that may omit
`character_name`. Loading is additive: TinyScry does not own or replace the
operator's GMCP negotiation or connection macros. Repeated loads replace the
named TinyScry definitions rather than duplicating them.
The TinyScry `GMCP`, `CONNECT`, and `WORLD` hooks are defined at priority 2
with fall-through (`-Fp2`) so they observe without consuming operator events.
`GMCP_LOGIN` remains an operator login-script prerequisite but is not a
TinyScry capture hook.
TinyScry runs ahead of default priority-1 handlers such as `received-gmcp`; the
`-F` flag lets those handlers run afterward. Two same-priority
non-fall-through GMCP hooks previously lost whole events intermittently. Do
not drop `-F` and do not change the operator's own hooks. A higher-priority
non-fall-through hook can still prevent TinyScry from running, so re-verify
coexistence when operator priorities differ.

Confirm the GMCP definitions inside TinyFugue: the operator's handler should
remain at its existing priority and `tinyscry_capture_gmcp` must appear as
`-Fp2`.

The hook writes to the fixed path `~/.local/state/tinyscry/spool`.
`tinyscry-feed` owns that path as a symlink into its private runtime
directory and replaces it on every (re)start; nothing about the hook file
changes when the feed restarts. See
[`integrations/tinyfugue/README.md`](../integrations/tinyfugue/README.md) for
why this is a plain drained file rather than a FIFO.

## 8. Confirm the TinyFugue GMCP login prerequisite (on VPS)

Because that identity message is sent once, capture depends on the operator's
TinyFugue performing GMCP login sequencing at the right negotiation point. The
invariant is a **build whose GMCP support includes the `GMCP_LOGIN` hook**,
driving operator login scripts that use it to run their GMCP capability
negotiation and send `Char.Login`. Without `GMCP_LOGIN` the operator login path
cannot be relied on to sequence this correctly, and TinyScry then keeps
consuming `Char.Vitals` and `Char.Status` deltas without ever observing
`character_name`, so the HUD stays down.

This is stated as a capability, not a version. The build verified live was
TinyFugue `5.2.2-3-g4f0ff34`; an upstream or distribution version number does
not by itself prove `GMCP_LOGIN` is compiled into the binary in use, so confirm
the capability. The conclusive signal is on the first login after installing
the hook: login produces a full `Char.Status` carrying `character_name`, and
TinyScry obtains a snapshot and writes its checkpoint - visible as
`has_snapshot: true` with a non-null `seq` on `/healthz`. In the verified run,
`Char.StatusVars` was observed immediately followed by that full identity-bearing
`Char.Status`; AVATAR does not document that ordering as a guarantee, so treat
it as an observation rather than a requirement. When the signal is missing,
[bounded diagnostic capture](#diagnostic-raw-capture-opt-in-vps) shows which
packages did arrive.

The build, the upgrade and the login scripts are operator-owned; TinyScry
never modifies them and sends no GMCP itself.

## Verifying the deployment

```bash
# Relay and gateway listen on loopback only
ss -ltnp | grep -E ':(8787|8788)[[:space:]]'
# expect 127.0.0.1:8787 and 127.0.0.1:8788,
# with no 0.0.0.0 listener for either service

# TinyScry user services are active
systemctl --user is-active \
  tinyscry-relay.service \
  tinyscry-feed.service \
  tinyscry-gateway.service

# Gateway health contains process health only
curl -s http://127.0.0.1:8788/healthz
# expect: {"status":"ok"}

# When Direct WSS is configured, verify the public TLS edge separately:
# curl -s https://<public-host>/healthz
# curl -s -o /dev/null -w '%{http_code}\n' https://<public-host>/ingest
# the second command must report 404

# The feed created the runtime spool, hook symlink, and private context marker
ls -l ~/.local/state/tinyscry/spool
stat -c '%a %n' ~/.local/state/tinyscry/context   # 600 after a world selection

# Recent lifecycle logs, never raw GMCP or action text
journalctl --user \
  -u tinyscry-relay.service \
  -u tinyscry-feed.service \
  -u tinyscry-gateway.service \
  -n 50
```

## Common failure diagnostics

| Symptom                                                                  | Check                                                                                                                                                                                                                                           |
| ------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tinyscry-feed` exits immediately with "another TinyScry feed holds ..." | A duplicate instance is running - manual invocation while the service is active, or a second service instance. `systemctl --user status tinyscry-feed.service`, then stop the extra process.                                                    |
| Relay reachable but no HUD data                                          | In SSH mode, check `curl http://127.0.0.1:8787/healthz` through the forward. In Direct mode, check both local gateway health and the public TLS `/healthz`; then confirm a relay producer is attached and inspect `tinyscry-feed.service`.      |
| Direct WSS repeatedly reconnects                                         | Confirm the public certificate is trusted/current, the reverse proxy forwards `/state` and `/action` to `127.0.0.1:8788`, and the desktop pairing token matches the digest in `gateway.env`. Never move the token into the URL while debugging. |
| TinyFugue shows an `fwrite` error line                                   | The feed is down or the hook symlink target directory is missing. TinyFugue is not blocked by this - it is the intended fail-open behaviour - but no HUD update reaches the relay until the feed is running again.                              |
| Services do not survive a reboot                                         | Confirm `loginctl show-user "$USER" -p Linger` reports `Linger=yes`; without it, user units never start without an interactive login.                                                                                                           |
| Relay bound to more than loopback                                        | The current relay refuses every non-loopback host and has no override. Restore the shipped unit and executable if this occurs.                                                                                                                  |
| Feed consuming GMCP but relay reports `has_snapshot: false`              | No `Char.Status.character_name` has been observed since the feed started. Confirm the hook was loaded by the active startup file before login, and that the TinyFugue build provides `GMCP_LOGIN` (step 7); then log the character in again.    |

## Diagnostic raw capture (opt-in, VPS)

Normal operation keeps raw GMCP only in the bounded private runtime spool
under `$XDG_RUNTIME_DIR`; it retains no raw diagnostic history. To capture a
bounded, private diagnostic trail temporarily:

```bash
systemctl --user edit tinyscry-feed.service
```

Add an override:

```ini
[Service]
ExecStart=
ExecStart=%h/tinyscry/integrations/tinyfugue/.venv/bin/tinyscry-feed --diagnostic-capture
```

Then `systemctl --user daemon-reload && systemctl --user restart
tinyscry-feed.service`. Files land privately under
`~/.local/state/tinyscry/diagnostics/`, rotate at 1 MiB, and keep at most 5
files. Remove the override (`systemctl --user revert tinyscry-feed.service`)
when done; this is a debugging aid, not a default.
