# Managed runtime lifecycle

## Why this is a process card

The managed runtime crosses two machines and three independent owners. Process
ownership must stay explicit: TinyScry owns its relay, feed, and SSH child; it
never owns or daemonizes the operator's interactive TinyFugue session.

## Ownership

| Resource                                      | Owner                          | Lifecycle                                                  |
| --------------------------------------------- | ------------------------------ | ---------------------------------------------------------- |
| TinyFugue session                             | operator                       | started and stopped interactively                          |
| capture/select/action definitions             | TinyFugue startup config       | fixed named `/def`; repeated loads replace                 |
| per-dispatch action helper                    | TinyFugue `/quote`             | reader loss or one line ends it; guarded macro replaces it |
| spool, per-world normalize, selected publish  | `tinyscry-feed.service`        | one locked process, `Restart=on-failure`                   |
| loopback state/action relay                   | `tinyscry-relay.service`       | `systemd --user`, `Restart=on-failure`                     |
| authenticated remote gateway                  | `tinyscry-gateway.service`     | loopback user service; state/action only                   |
| local SSH forward                             | desktop `TunnelSupervisor`     | at most one owned child per TinyScry process               |
| pre-existing relay endpoint on the local port | whoever started it             | adopted and monitored; never owned, signalled or replaced  |
| WebSocket reconnect and public HUD state      | `RelayStateSource` / HUD model | unchanged freshness presentation                           |

Live VPS reboot verification confirmed that user lingering (`Linger=yes`) keeps
both VPS user units available without a root-owned service or administrative SSH
session: `tinyscry-feed` and `tinyscry-relay` returned before interactive login.
TinyFugue intentionally did not auto-start. The feed `Wants=` the relay but
does not `Require=` it: its existing publisher reconnect loop owns a relay outage.

## Identity bootstrap

The reboot discards the ephemeral normalized checkpoint by design, and AVATAR
sends the full identity-bearing `Char.Status` only once, at character login.
Recovery therefore depends on two operator-owned preconditions holding at that
login, both live-verified: the capture hook is loaded by the TinyFugue startup
file actually in use before anything connects, and that TinyFugue build's GMCP
support includes the `GMCP_LOGIN` hook its login scripts use to negotiate
capabilities and send `Char.Login`. The invariant is the capability, not a
version string - public version numbering does not prove `GMCP_LOGIN` is
compiled in - though the build verified live is `5.2.2-3-g4f0ff34`. The
previously installed TinyFugue binary did not provide `GMCP_LOGIN`, and
TinyScry stayed identity-less after reboot with it.

TinyScry holds no workaround for a missing identity. It does not infer the
local character from `Room.Players` or `Char.Group.List`, does not persist
identity outside `$XDG_RUNTIME_DIR`, and sends no GMCP request of its own.

## Live path

```text
interactive TinyFugue
  -> fixed-path TS2 fwrite hooks
  -> ~/.local/state/tinyscry/spool (symlink)
  -> private $XDG_RUNTIME_DIR/tinyscry/spool
  -> tinyscry-feed (strict parse -> per-world normalize -> selected publish)
  -> tinyscry-relay on 127.0.0.1:8787
  -> either:
       system OpenSSH local forward
       or authenticated gateway on 127.0.0.1:8788 -> TLS reverse proxy -> WSS
  -> RelayStateSource / separate one-shot ActionSink
```

The reverse action hop returns through the same tunnel and relay to one helper
whose context exactly matches the selection. TinyFugue owns that asynchronous,
world-pinned child. It emits at most one line and exits; the fenced
`/tinyscry_send` macro starts the next helper only when the context still
matches. If TinyFugue closes the quote-pipe reader first, the idle helper detects
the terminal descriptor state without writing and exits, allowing the shell
intermediary and TinyFugue teardown to complete.

The TinyFugue-facing hop is deliberately a drained regular file, not a FIFO.
TinyFugue's `fwrite()` performs a blocking open/write/close and has
no non-blocking mode: no reader or a full FIFO froze the interactive client.
A regular file returns immediately. The reader bounds raw runtime storage by
rotating a fully drained active inode to a retired generation and creating a
fresh active spool; it never truncates an inode that TinyFugue may already
have open. The fixed hook symlink is removed when the feed stops and recreated
when it starts again. A live reboot verified recreation of both the private
runtime spool and persistent hook symlink before operator login. Lost updates
while the feed itself is unavailable are acceptable; blocking the MUD client is not.

Normal operation persists no raw diagnostic capture. An explicit
`--diagnostic-capture` option writes private, size-rotated files outside the
repository.

## Desktop tunnel boundary

`apps/desktop/src-tauri/src/tunnel.rs` directly spawns the platform `ssh`
client with argv, loopback forwarding, `ExitOnForwardFailure`, keepalives, and
bounded reconnect backoff. It does not parse SSH config or handle credentials.
The target is an existing SSH `Host` alias, so OpenSSH continues to own agent,
`IdentityFile`, `ProxyJump`, `known_hosts`, and host verification.

Port `8787` is fixed on both sides. Managed mode distinguishes a TinyScry-shaped
`/healthz` endpoint from an unrelated listener, and never kills either.

A verified existing endpoint is _adopted_, not owned: TinyScry reports
`ExternalPortInUse`, spawns no child on top of it, holds no handle to it, and
re-probes it about once a second. Adoption is therefore temporary rather than
terminal. When the adopted endpoint disappears and the port is free, the same
supervisor spawns and supervises its own SSH child in its place, with no
application restart. If the endpoint stops answering as a relay while some
process still holds the port, the forward is reported unavailable and TinyScry
keeps waiting rather than binding or killing over it. An unrelated listener
already holding the port at startup is a refusal instead: no child, no
supervision. `down`, `stale` and `live` are all valid relay health states, so a
stale feed never triggers takeover. Shutdown signals only the stored child
handle.

Each watch cycle classifies the port exactly once - relay endpoint, foreign
owner, or free - and then applies one diagnostic transition, so a healthy
adopted endpoint is never published as a conflict in passing. The health read
has its own, longer deadline than the connect probe: an adopted endpoint is
normally reached through the SSH forward, where the local connect is instant
and only the answer pays network latency. Sharing the connect window made a
slow but valid relay read as a foreign listener and the diagnostic flap.

The Tauri command exposes only a transport diagnostic enum. `config.ts` feeds
a human-readable detail into `RelayStateSource`; Svelte components still see
only connection events and the public `RECONNECTING`, `DOWN`, `STALE`, and
`LIVE` presentation remains in the existing model.

Native connection configuration remains in the historical application-config
`tunnel.json`, but Slice 12 adds a separate management boundary around it.
`ConnectionConfigStore` exposes a renderer-safe settings projection: mode, SSH
target, Direct URL, and only whether a Direct pairing token exists. Reading
settings does not return the existing token.

Writes are canonical by mode. External clears SSH and Direct fields; Managed
requires a non-empty trimmed SSH target and clears Direct material; Direct
requires the existing strict `wss:` state-endpoint shape plus a canonical
256-bit pairing token and clears the SSH target. A Direct update may preserve
the existing token without sending it back through the renderer. Switching
away from Direct removes that credential rather than keeping stale secret
material.

Configuration persistence stages a complete replacement in the destination
directory before replacing `tunnel.json`; on Unix the staged file is mode
`0600`. Failed validation or persistence leaves both the previous file and the
store's previous in-memory configuration intact.

This settings lifecycle remains separate from the active transport lifecycle.
`RuntimeConnectionConfig` and `TunnelSupervisor` are constructed once during
Tauri setup. Saving settings changes the next application start only; it does
not replace the running source, action sink, or SSH supervisor.

## Failure boundaries

| Failure                                | Recovery / visible result                                                                                                                                                                                                                            |
| -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| relay process exits                    | systemd restarts it; feed publisher reconnects; gateway state clients disconnect and reconnect from the desktop                                                                                                                                      |
| gateway process exits                  | systemd restarts it; relay/feed continue unaffected; direct-WSS desktop reconnects                                                                                                                                                                   |
| feed process exits                     | lock releases with the process; systemd starts one replacement                                                                                                                                                                                       |
| normalized checkpoint lost at reboot   | identity is re-established at the next character login only if the capture hook was loaded by the active startup file beforehand and the TinyFugue build provides `GMCP_LOGIN` for the operator login scripts; until then TinyScry publishes nothing |
| TinyFugue absent                       | services stay healthy; relay reports feed down/stale                                                                                                                                                                                                 |
| spool target replaced                  | next TinyFugue hook call reopens the stable path                                                                                                                                                                                                     |
| SSH child exits / network drops        | supervisor retries with capped backoff; HUD reconnects                                                                                                                                                                                               |
| local port occupied by TinyScry relay  | adopt and monitor that endpoint; spawn no child                                                                                                                                                                                                      |
| adopted relay endpoint disappears      | the same supervisor spawns its own SSH child once the port is free; no application restart                                                                                                                                                           |
| local port occupied by another service | report unavailable; spawn, bind and kill nothing                                                                                                                                                                                                     |
| TinyFugue closes action pipe reader    | idle helper writes nothing, closes its WebSocket, and exits; no reconnect or action result                                                                                                                                                           |
| TinyScry closes                        | terminate and reap only its owned SSH child                                                                                                                                                                                                          |

## Source and checks

- `integrations/tinyfugue/src/tinyscry_tf/feed.py` - per-world feed orchestration
- `integrations/tinyfugue/src/tinyscry_tf/spool.py` - private spool and producer lock
- `integrations/tinyfugue/src/tinyscry_tf/action_consumer.py` - context-bound
  fixed-macro delivery
- `integrations/tinyfugue/src/tinyscry_tf/diagnostics.py` - opt-in bounded capture
- `integrations/tinyfugue/tinyscry.tf` - idempotent fixed-path hooks
- `deploy/systemd/` - VPS user units
- `apps/desktop/src-tauri/src/tunnel.rs` - SSH child ownership and adopted-endpoint watch
- `apps/desktop/src-tauri/src/tunnel_config.rs` - mode and SSH alias
- `apps/desktop/src/lib/tunnel.ts` - transport-independent diagnostic polling

Relevant regression checks live in `integrations/tinyfugue/tests/test_spool.py`,
`test_feed.py`, `test_action_consumer.py`, `test_diagnostics.py`,
`apps/desktop/src-tauri/src/tunnel.rs`, and desktop source/action tests.

## Verification

### Slice 11 authenticated WSS live verification — 2026-09-27

Live operator verification covered both desktop transport modes:

- The public TLS edge exposed only port 443 through Caddy. The relay remained
  bound to `127.0.0.1:8787` and the authenticated gateway remained bound to
  `127.0.0.1:8788`.
- A publicly trusted TLS certificate was used for the Direct WSS endpoint.
  `/healthz` succeeded through the public edge, while `/ingest` and
  `/action-consumer` returned `404`.
- With no Windows listener on local port 8787, an invalid pairing token was
  rejected with WebSocket close code `1008`. A valid token received
  `hello`, retained `snapshot`, and `status` frames through Direct WSS.
- The Windows Tauri application loaded current character state through Direct
  WSS, received live state changes, and successfully sent an outbound action.
- Stopping the gateway and starting it again caused the running desktop to
  disconnect and recover automatically. State and outbound actions worked
  again without restarting the application.
- Restoring the previous managed-SSH desktop configuration re-established the
  local SSH forward and restored live state and outbound actions without any
  relay or feed reconfiguration.
- The relay, feed, and gateway user services were left enabled for boot.

Status: verified for the established service/tunnel lifecycle, including
adopted-endpoint takeover - a Windows-native run adopted a manual SSH forward
exposing the remote TinyScry relay and, when that forward was terminated, took
the forward over with its own supervised child without a restart. That run
performed no VPS reboot and did not confirm the child's parent PID. The
context/action path remains deterministic only; its pending live procedure is
in `integrations/tinyfugue/README.md`.

The deterministic gate and the live evidence prove different things, and neither
substitutes for the other. The gate proves unit-level invariants: that a
restarted feed does not publish a retained checkpoint without fresh input, and
that the relay keeps its snapshot and advances its sequence only on new state
(`integrations/tinyfugue/tests/test_feed.py`,
`services/relay/tests/test_server.py`, `tests/e2e/relay_roundtrip.py`). It
cannot observe a real VPS reboot, actual `systemd` lingering, real
OpenSSH/network recovery, the VPS loopback listener, or the post-reboot
AVATAR/TinyFugue identity bootstrap; those are the `Real VPS/systemd behavior`,
`Actual OpenSSH child`, `Relay bind`, `Identity bootstrap`, and
`TinyFugue GMCP login hook` rows of the live-evidence table in `docs/status.md`.
