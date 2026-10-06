# Managed runtime lifecycle

## Why this is a process card

The runtime has independent process owners on both producer and consumer hosts.
Imp may own a same-host node, an optional authenticated gateway, and a Managed
SSH child, while the MUD client remains operator-owned.

Those lifecycles must stay separate. A MUD-client adapter always talks to the
Imp node on its own host; remote access is node-to-UI transport rather than
client-adapter transport.

## Ownership

| Resource                                    | Owner                           | Lifecycle                                                       |
| ------------------------------------------- | ------------------------------- | --------------------------------------------------------------- |
| TinyFugue session                           | operator                        | started and stopped interactively                               |
| TinyFugue capture/select/action definitions | TinyFugue startup config        | fixed named `/def`; repeated loads replace                      |
| TinyFugue per-dispatch action helper        | TinyFugue `/quote`              | reader loss or one line ends it; guarded macro replaces it      |
| TinyFugue spool/feed                        | `imp-feed.service`              | one locked process, `Restart=on-failure`                        |
| Mudlet profile/session                      | operator / Mudlet               | normal Mudlet profile lifecycle                                 |
| Mudlet Lua adapter + helper                 | Mudlet profile / spawned helper | package loads per profile; helper follows package lifecycle     |
| VPS Imp node                                | `imp-relay.service`             | `systemd --user`, `Restart=on-failure`                          |
| desktop same-host Imp node                  | desktop `NodeSupervisor`        | supervised in every desktop connection mode                     |
| VPS authenticated gateway                   | `imp-gateway.service`           | loopback user service; state/action only                        |
| desktop authenticated gateway               | desktop `GatewaySupervisor`     | active only when producer-side WSS is enabled                   |
| Managed SSH consumer forward on `8789`      | desktop `TunnelSupervisor`      | at most one owned child per Imp process                         |
| healthy pre-existing relay on `8789`        | whoever started it              | Managed mode may adopt and monitor it; never owns or signals it |
| WebSocket reconnect and HUD state           | `RelayStateSource` / HUD model  | transport-independent freshness presentation                    |

The installed-bundle `imp-direct-wss` command provisions the VPS gateway under
the same target user's `systemd --user` manager; it does not configure the
public hostname, DNS, TLS certificate, or reverse proxy. A clean bundle install
leaves this service disabled; upgrades preserve its existing enablement and
active state.

A pre-existing listener on the desktop node port `8787` is never adopted,
regardless of whether it looks like an Imp relay. The desktop node supervisor
either owns the child itself or reports the port unavailable.

Live VPS reboot verification confirmed that user lingering (`Linger=yes`) keeps
the VPS user units available without a root-owned service or administrative SSH
session: `imp-feed` and `imp-relay` returned before interactive login.
TinyFugue intentionally did not auto-start. The feed `Wants=` the relay but
does not `Require=` it: its publisher reconnect loop owns a relay outage.

## TinyFugue identity bootstrap

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
Imp stayed identity-less after reboot with it.

Imp holds no workaround for a missing identity. It does not infer the
local character from `Room.Players` or `Char.Group.List`, does not persist
identity outside `$XDG_RUNTIME_DIR`, and sends no GMCP request of its own.

## Runtime paths

### Desktop-local adapters

```text
MUD
  -> Mudlet or local TinyFugue
  -> client-specific adapter
  -> shared GMCP normalization/publisher
  -> desktop-owned Imp node on 127.0.0.1:8787
  -> RelayStateSource / separate one-shot ActionSink
```

The local node remains present even when the HUD itself is using Managed SSH,
External SSH, or Direct WSS to consume another node.

### TinyFugue on a VPS

```text
interactive TinyFugue
  -> fixed-path IMP2 fwrite hooks
  -> ~/.local/state/imp/spool (symlink)
  -> private $XDG_RUNTIME_DIR/imp/spool
  -> imp-feed (strict parse -> shared normalize -> selected publish)
  -> VPS Imp node on 127.0.0.1:8787
  -> either:
       desktop 127.0.0.1:8789 -> SSH -> VPS 127.0.0.1:8787
       or authenticated gateway on VPS 127.0.0.1:8788
          -> TLS reverse proxy -> WSS
  -> RelayStateSource / separate one-shot ActionSink
```

The TinyFugue reverse action hop reaches one helper whose context exactly
matches the selection. TinyFugue owns that asynchronous, world-pinned child.
It emits at most one line and exits; the fenced `/imp_send` macro starts the
next helper only when the context still matches.

The TinyFugue-facing spool is deliberately a drained regular file, not a FIFO.
TinyFugue's `fwrite()` performs a blocking open/write/close and has no
non-blocking mode: no reader or a full FIFO froze the interactive client. A
regular file returns immediately. The feed rotates fully drained generations
rather than truncating an inode TinyFugue may already have open.

Normal operation persists no raw diagnostic capture. An explicit
`--diagnostic-capture` option writes private, size-rotated files outside the
repository.

## Desktop tunnel boundary

The desktop node and desktop consumer transport are deliberately independent.

`NodeSupervisor` owns the bundled same-host Imp node on `127.0.0.1:8787`.
It never adopts a pre-existing listener, even one with a valid Imp `/healthz`
response. If another process owns the port, Imp reports the local node
unavailable and waits without replacing or signalling that process.

`TunnelSupervisor` owns only Managed SSH consumer transport. It invokes the
platform `ssh` client directly by argv and forwards:

```text
127.0.0.1:8789 -> SSH -> remote 127.0.0.1:8787
```

OpenSSH continues to own agent, `IdentityFile`, `ProxyJump`, `known_hosts`, and
host verification.

The desktop-owned client adds `-S none` and `-o ForkAfterAuthentication=no`.
OpenSSH's documented `none` control path disables both creation and reuse of
shared masters; the separate fork override prevents authentication-triggered
backgrounding. `ControlMaster=no` alone would still reuse an existing socket.
These command-line overrides do not edit SSH configuration, contact an existing
control socket, or stop an external master. See
[`ssh(1)`](https://man.openbsd.org/ssh.1#S) and
[`ssh_config(5)`](https://man.openbsd.org/ssh_config.5#ForkAfterAuthentication).

Unix support requires the proxy/jump client itself to stay foreground as well.
OpenSSH does not propagate the destination's `-S`/`-o` options to its generated
ProxyJump client. For each jump alias, put these before broader SSH defaults:

```sshconfig
Host my-jump
    ControlPath none
    ForkAfterAuthentication no
```

ProxyCommand programs must likewise avoid persistent multiplexing, background
forks, or independent sessions. An authenticated stock OpenSSH probe with
persistent jump-host defaults left a detached jump master after owner death,
even though the target client and forward were removed. This is an observed
limit, not hypothetical. The approved contract requires foreground proxy
configuration; Imp does not rewrite SSH configuration or construct proxy
commands to enforce it. Independently persistent nested clients are outside
the supported Unix runtime contract, not claimed contained.

Managed mode classifies the `8789` consumer port as a usable Imp relay,
unrelated listener, or free port. A usable existing relay is adopted and
monitored without ownership. If it disappears and the port becomes free, the
same supervisor starts its own SSH child. An unrelated listener is left alone
and reported unavailable. Feed states `down`, `stale`, and `live` are all valid
relay health states and do not themselves trigger takeover.

External mode also consumes `127.0.0.1:8789`, but Imp owns no SSH process in
that mode. Local mode consumes the desktop node directly on `8787`. Direct WSS
uses neither consumer loopback port for the HUD connection.

Runtime diagnostics preserve the same UI boundary. Local mode reads
`NodeSupervisor` diagnostics; Managed/External loopback consumption uses tunnel
diagnostics; Direct WSS uses its socket lifecycle. Components still receive only
the existing connection/freshness presentation.

Native connection configuration remains in the historical application-config
`tunnel.json`. `ConnectionConfigStore` exposes a renderer-safe projection:
mode, SSH target, Direct URL, and only whether a Direct pairing token exists.
Reading settings does not return the existing token.

Writes are canonical by mode. Local and External clear unrelated transport
material; Managed requires a non-empty trimmed SSH target; Direct requires the
strict `wss:` endpoint plus a canonical 256-bit pairing token. Switching away
from Direct removes the stored Direct credential.

Configuration persistence stages a complete replacement before replacing
`tunnel.json`; failed validation or persistence leaves the previous usable
configuration intact.

Runtime clients and supervisors are constructed during Tauri setup. Saving
settings changes the next application start only; it does not hot-swap the
running source, action sink, node, gateway, or SSH supervisor.

## Native desktop lifetime ownership

One `RuntimeOwner` is initialized before the desktop supervisors. Node and
gateway keep Tauri's bundled sidecar resolution and argv; Managed SSH uses the
system OpenSSH client with the foreground/nonpersistent policy above. All three
launch through the same ownership boundary. Saving settings still applies on
next start, and existing startup/adoption/retry decisions are unchanged.

The spawn API accepts executable, argv, and stdout policy rather than silently
discarding `std::process::Command` settings. Runtime children inherit cwd and
environment, use null stdin and piped stderr, and are hidden on Windows. Node
and gateway stdout remains piped for diagnostics; Managed SSH stdout goes to
the null device, including synchronous `LocalCommand` output.

### Windows

The desktop holds the sole non-inheritable handle to an unnamed Job Object
with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. `CreateProcessW` uses
`PROC_THREAD_ATTRIBUTE_JOB_LIST` to assign membership before the child can run;
there is no spawn-then-assign or suspended-but-unassigned interval.
Only the child's standard-input/output/error handles are inherited.
Normal descendants inherit the job; breakaway is not enabled. External
services and processes created through independent brokers are not claimed.

Normal exit keeps the existing gateway/node/tunnel shutdown, kill, wait, and
supervisor joining. Abrupt desktop termination closes the job handle in the
kernel and kills remaining members without Rust destructors or Tauri callbacks.
An incompatible enclosing job, failed container setup, or failed process
creation returns an error rather than launching outside ownership.
Windows 10 or newer is required by the atomic job-list creation attribute.

Executable lookup follows Rust 1.98.1
[`Command`](https://doc.rust-lang.org/1.98.1/std/process/struct.Command.html#platform-specific-behavior)
before process creation, rather than `CreateProcessW`'s implicit search.
Bare names search the application,
system, Windows, then inherited PATH directories; cwd is not added implicitly.
Explicit paths keep Rust's extension and path-conversion rules. The resolved
image is passed as non-null `lpApplicationName` without changing Job List
assignment, handle inheritance, creation flags, or stdio.

### Linux and macOS source builds

Each owned tree has a small guardian running the current executable before
Tauri initialization. The desktop alone holds the write end of its lifetime
pipe. Kernel EOF after desktop death causes the guardian to kill the child's
private process group and reap it; a group alone would not provide that
parent-death trigger. The guardian has a separate session. Linux enables
subreaper reaping of in-group descendants and gives the direct runtime child
`PR_SET_PDEATHSIG(SIGKILL)` with a parent recheck before exec. That signal is
not inherited by grandchildren. macOS uses group signalling and waits for the
group to disappear; its init process, not a Linux-style subreaper, reaps orphans.
Both preserve the runtime's raw exit status/signals through the control socket.

This is narrower than Windows Job containment: a descendant that deliberately
changes process group/session can escape. A stopped or failed guardian also
cannot provide the same kernel-enforced tree guarantee. Neither source-build
target has desktop release acceptance; see the current evidence and limits in
[`../../status.md`](../../status.md).

Tauri dev/Cargo wrappers and the frontend dev server are not runtime-owned.
Only explicit native runtime spawns enter containment; WebView processes
remain platform-owned.

## Failure boundaries

| Failure                                        | Recovery / visible result                                                                        |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| desktop-owned node exits or becomes unhealthy  | `NodeSupervisor` terminates only its owned child and retries with bounded backoff                |
| desktop node port `8787` already occupied      | report local node unavailable; never adopt, replace, bind over, or kill the owner                |
| VPS relay process exits                        | systemd restarts it; publishers reconnect; remote HUD clients reconnect                          |
| gateway process exits                          | its owning supervisor/systemd restarts it; node/feed continue independently                      |
| feed process exits                             | lock releases with the process; systemd starts one replacement                                   |
| normalized TinyFugue checkpoint lost at reboot | identity returns only after fresh identity-bearing GMCP under the documented login prerequisites |
| TinyFugue absent                               | VPS services stay healthy; node reports feed down/stale                                          |
| spool target replaced                          | next TinyFugue hook call reopens the stable path                                                 |
| Managed SSH child exits / network drops        | supervisor retries with capped backoff; HUD reconnects                                           |
| healthy Imp relay already owns `8789`          | Managed mode adopts and monitors it; spawns no child                                             |
| adopted `8789` relay disappears                | Managed mode starts its own SSH child once the port is free                                      |
| unrelated process owns `8789`                  | report transport unavailable; spawn, bind, and kill nothing                                      |
| Mudlet helper exits                            | that profile stops producing/consuming until its adapter lifecycle starts a helper again         |
| TinyFugue closes action pipe reader            | idle helper writes nothing, closes its WebSocket, and exits                                      |
| Imp closes                                     | terminate and reap only node, gateway, and SSH children owned by that Imp process                |

## Source and checks

- `integrations/tinyfugue/src/imp_tf/feed.py` - per-world feed orchestration
- `integrations/tinyfugue/src/imp_tf/spool.py` - private spool and producer lock
- `integrations/tinyfugue/src/imp_tf/action_consumer.py` - context-bound
  fixed-macro delivery
- `integrations/tinyfugue/src/imp_tf/diagnostics.py` - opt-in bounded capture
- `integrations/tinyfugue/imp.tf` - idempotent fixed-path hooks
- `deploy/systemd/` - VPS user units
- `apps/desktop/src-tauri/src/tunnel.rs` - SSH child ownership and adopted-endpoint watch
- `apps/desktop/src-tauri/src/node.rs` and `gateway.rs` - sidecar supervision
- `apps/desktop/src-tauri/src/runtime.rs` and `runtime/` - platform lifetime ownership
- `apps/desktop/src-tauri/src/tunnel_config.rs` - native connection validation,
  canonical persistence, runtime projection, and renderer-safe settings store
- `apps/desktop/src/lib/tunnel.ts` - transport-independent diagnostic polling
  plus native connection-settings command wrappers
- `apps/desktop/src/components/ConnectionDialog.svelte` - native transport
  management UI

Relevant regression checks live in `integrations/tinyfugue/tests/test_spool.py`,
`test_feed.py`, `test_action_consumer.py`, `test_diagnostics.py`,
`apps/desktop/src-tauri/src/tunnel.rs`,
`apps/desktop/src-tauri/src/tunnel_config.rs`, and
`apps/desktop/test/tunnel.test.ts`.

Native subprocess regressions live in
`apps/desktop/src-tauri/tests/runtime_ownership.rs` and
`apps/desktop/src-tauri/tests/windows_executable_resolution.rs`, enabled by the
`runtime-acceptance` feature in Windows CI. Windows/Linux observers register
retained process identities before remaining metadata validation or handshakes;
other Unix observers register connected self-exit channels before response
validation. Partial setup failures retain their original error during cleanup.
Real window-close/hard-termination acceptance uses
`scripts/test-native-ownership.ps1`; its isolated config/build procedure is in
[`../../development.md`](../../development.md).

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
exposing the remote Imp relay and, when that forward was terminated, took
the forward over with its own supervised child without a restart. That run
performed no VPS reboot and did not confirm the child's parent PID.

The context/action path is also live-verified. The connectionless TinyFugue
procedure in `integrations/tinyfugue/README.md` exercised exact-current
delivery, context fences, helper replacement/loss, relay restart, and prompt
shutdown on the pinned TinyFugue build. Separate native UI acceptance sent a
real `look` action through the relay and TinyFugue path to the MUD. The precise
scope of that evidence remains recorded in `docs/status.md`.

### Slice 16 desktop-local node transport verification — 2026-10-02

Windows-native acceptance verified the desktop-local node as a producer-neutral
transport boundary:

- The desktop-owned same-host node remained on `127.0.0.1:8787` regardless of
  whether the HUD consumed Local, Managed SSH, or Direct WSS transport.
- A pre-existing listener on the local-node port is refused rather than adopted,
  so an old SSH forward cannot masquerade as the adapter's same-host node.
- Managed and External SSH consume through the separate loopback endpoint
  `127.0.0.1:8789`, forwarding to remote relay port `8787`.
- With producer-side WSS enabled, the same desktop also owned an authenticated
  gateway on `127.0.0.1:8788`.
- The packaged node and gateway used the same frozen `imp-node`
  executable/runtime and returned their distinct expected `/healthz` shapes.
- The gateway rejected a wrong pairing token with WebSocket close code `1008`,
  authenticated before relay access, preserved exact protocol-v2 state/context,
  and routed trusted actions through the relay broker.
- Local TinyFugue and local Mudlet both produced state and completed trusted
  action delivery through the desktop-owned node.
- A reverse OpenSSH tunnel to the VPS carried Mudlet-backed state and a real
  trusted `look` action from a remote client through the local node.
- For public-WSS acceptance, the established VPS Caddy TLS edge was temporarily
  connected by reverse SSH to the Windows desktop-owned gateway. `/healthz`
  succeeded publicly, `/ingest` remained `404`, Mudlet-backed state arrived over
  WSS, and a real `look` action traversed the full path to the MUD.
- The temporary tunnel was removed afterwards and the normal VPS
  `imp-gateway.service` was restored on loopback `8788` and verified healthy.
- Final simultaneous acceptance left Mudlet attached to the local node while
  the HUD ran in Managed mode. Windows showed `imp-node.exe` listening on
  `8787` and the Imp-owned `ssh.exe` listening on `8789` with
  `-L 127.0.0.1:8789:127.0.0.1:8787`. The two health endpoints exposed
  different retained sequence numbers, and the remote TinyFugue trusted-action
  path still delivered `look`.

This evidence verifies that MUD-client adapters remain attached to a same-host
node while SSH and authenticated WSS remain independent node-to-UI transport
choices.

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
