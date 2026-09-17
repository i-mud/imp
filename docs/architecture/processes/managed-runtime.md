# Managed runtime lifecycle

## Why this is a process card

The managed runtime crosses two machines and three independent owners. Process
ownership must stay explicit: TinyScry owns its relay, feed, and SSH child; it
never owns or daemonizes the operator's interactive TinyFugue session.

## Ownership

| Resource                                       | Owner                          | Lifecycle                                     |
| ---------------------------------------------- | ------------------------------ | --------------------------------------------- |
| TinyFugue session                              | operator                       | started and stopped interactively             |
| GMCP hook                                      | TinyFugue startup config       | fixed named `/def`; repeated loads replace it |
| live spool, conversion, normalization, publish | `tinyscry-feed.service`        | one locked process, `Restart=on-failure`      |
| loopback relay                                 | `tinyscry-relay.service`       | `systemd --user`, `Restart=on-failure`        |
| local SSH forward                              | desktop `TunnelSupervisor`     | at most one owned child per TinyScry process  |
| WebSocket reconnect and public HUD state       | `RelayStateSource` / HUD model | unchanged four-state presentation             |

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
  -> fixed-path fwrite hook
  -> ~/.local/state/tinyscry/spool (symlink)
  -> private $XDG_RUNTIME_DIR/tinyscry/spool
  -> tinyscry-feed (check -> normalize -> publish)
  -> tinyscry-relay on 127.0.0.1:8787
  -> system OpenSSH local forward
  -> RelayStateSource
  -> existing HUD model
```

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

Port `8787` is fixed on both sides. Before spawning, managed mode distinguishes
a TinyScry-shaped `/healthz` endpoint from an unrelated listener. It uses a
verified existing endpoint or reports a conflict; it never kills the listener.
Shutdown signals only the stored child handle.

The Tauri command exposes only a transport diagnostic enum. `config.ts` feeds
a human-readable detail into `RelayStateSource`; Svelte components still see
only connection events and the public `RECONNECTING`, `DOWN`, `STALE`, and
`LIVE` presentation remains in the existing model.

## Failure boundaries

| Failure                                | Recovery / visible result                                                                                                                                                                                                                            |
| -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| relay process exits                    | systemd restarts it; feed publisher reconnects                                                                                                                                                                                                       |
| feed process exits                     | lock releases with the process; systemd starts one replacement                                                                                                                                                                                       |
| normalized checkpoint lost at reboot   | identity is re-established at the next character login only if the capture hook was loaded by the active startup file beforehand and the TinyFugue build provides `GMCP_LOGIN` for the operator login scripts; until then TinyScry publishes nothing |
| TinyFugue absent                       | services stay healthy; relay reports feed down/stale                                                                                                                                                                                                 |
| spool target replaced                  | next TinyFugue hook call reopens the stable path                                                                                                                                                                                                     |
| SSH child exits / network drops        | supervisor retries with capped backoff; HUD reconnects                                                                                                                                                                                               |
| local port occupied by TinyScry relay  | use external endpoint; spawn no child                                                                                                                                                                                                                |
| local port occupied by another service | report conflict; spawn and kill nothing                                                                                                                                                                                                              |
| TinyScry closes                        | terminate and reap only its owned SSH child                                                                                                                                                                                                          |

## Source and checks

- `integrations/tinyfugue/src/tinyscry_tf/feed.py` - feed orchestration
- `integrations/tinyfugue/src/tinyscry_tf/spool.py` - private spool and producer lock
- `integrations/tinyfugue/src/tinyscry_tf/diagnostics.py` - opt-in bounded capture
- `integrations/tinyfugue/tinyscry.tf` - idempotent fixed-path hook
- `deploy/systemd/` - VPS user units
- `apps/desktop/src-tauri/src/tunnel.rs` - SSH child ownership
- `apps/desktop/src-tauri/src/tunnel_config.rs` - mode and SSH alias
- `apps/desktop/src/lib/tunnel.ts` - transport-independent diagnostic polling

Relevant regression checks live in `integrations/tinyfugue/tests/test_spool.py`,
`test_feed.py`, `test_diagnostics.py`, `apps/desktop/src-tauri/src/tunnel.rs`,
and `apps/desktop/test/relay-source.test.ts`.

## Verification

Status: verified
Verified against: focused Python, Rust, and frontend checks plus live VPS
restart and reboot evidence recorded in `docs/status.md`.

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
