# Implementation status

Canonical status for TinyScry. Platform-independent checks run in WSL2; the
native shell is also runtime-verified on Windows 11 x64.

## Working end to end

The complete production-shaped path runs today:

```text
MUD GMCP <-> TinyFugue hook -> private versioned spool
         -> per-world normalize -> selected-context publisher -> loopback relay
         <-> SSH tunnel <-> native Windows Tauri HUD
```

The source data came from a real target-MUD session. A selected, redacted
fixture was replayed from the VPS through an external SSH tunnel into the
native Windows HUD. Rendered identity, HP, mana, movement, target acquisition,
target damage, and target clearing matched the normalized stream. Producer
stall, producer exit, relay restart, and SSH-tunnel interruption all kept last
known values visibly non-live.

Managed mode is implemented in the Tauri backend: it starts the system OpenSSH
client directly by argv, supervises one owned child, retries with bounded
backoff, and leaves credentials and host verification to OpenSSH. When a valid
TinyScry relay endpoint already holds the local port, managed mode adopts and
monitors that endpoint instead of spawning over it, and takes the forward over
with its own supervised child once the endpoint is gone. External tunnel mode
remains the default and remains supported.

| Component                 | State                                                                                                                                                                                                                                                         |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `packages/protocol`       | Version 2 complete: context-bound state/actions plus transient received text, fail-closed dual decoders, shared accept/reject corpus.                                                                                                                         |
| `services/relay`          | Loopback-only contextual state relay, transient current-subscriber text broadcast, Origin policy, and single-flight action broker.                                                                                                                            |
| `apps/desktop` (frontend) | Refined compact/expanded HUD, shared styling, Lucide controls, persisted Dark/Light/System themes, configurable local action UI, and configurable vital/text alerts implemented; Windows-native interaction and earlier live action-path acceptance complete. |
| `apps/desktop` (Tauri)    | Native shell runtime-verified on Windows; managed SSH lifecycle and adopted-endpoint takeover covered by deterministic Rust checks.                                                                                                                           |
| `integrations/tinyfugue`  | Versioned per-world feed, session-aware checkpoint, strict context marker, transient selected-world received-text capture, and fixed-macro action helper implemented.                                                                                         |

## Verification

### Automated CI coverage

`.github/workflows/ci.yml` runs on pull requests targeting `main`, pushes to
`main`, and manual dispatch. It installs the committed npm and uv dependency
state with Node 24 and Python 3.12, then runs the canonical project gate once:
`npm run check`.

That gate covers:

- protocol validation and the shared fixture corpus;
- frontend formatting, lint, type checks, tests, and production build;
- relay lint, strict type checks, and tests;
- TinyFugue adapter lint, strict type checks, and tests;
- deterministic fixture and trust-boundary behavior; and
- the producer-to-relay-to-subscriber loopback end-to-end test.

CI requires no TinyScry secrets or external infrastructure. It does not compile
the Rust crate and does not prove native or live runtime behavior.

### Manual, native, and live evidence

These checks require a native platform, real processes, or operator-controlled
infrastructure and remain separate from CI:

| Evidence                  | Status                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Windows Tauri runtime     | Verified on Windows 11: clean launch and console, close, topmost, drag, transparency, live updates, and a non-maximizable HUD. Slice 8 verified expanded/compact Settings and Actions sizing/restoration, width-preserving attached panels, originating-width management and its drag region, the bounded wrapped compact palette and icon trigger, native restart persistence, and keyboard focus paths. The final post-PR #6 polish/theme smoke re-checked theme switching, compact/expanded behavior, Settings, Manage Actions, native sizing, drag, non-maximizable behavior, keyboard/focus paths, and general polish. It did not add live MUD action-path or VPS evidence.                                                                                                                                                                                                                                                                                                                                                |
| Actual OpenSSH child      | Verified in native managed mode for a child TinyScry already owned: its tunnel dropped during a real VPS reboot and recovered automatically without a TinyScry restart. That run does not cover startup adoption of a pre-existing relay endpoint.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| Adopted-endpoint takeover | Verified on Windows 11 after the flap fix. A manual SSH forward exposing the remote TinyScry relay was running first; TinyScry started in managed mode, logged `using existing TinyScry relay on 127.0.0.1:8787`, owned no SSH child, and showed no transient `local port 127.0.0.1:8787 is unavailable` while that forward remained. Terminating the manual forward made the same running app log `127.0.0.1:8787 is free again`, then `SSH tunnel starting (avatar -> 127.0.0.1:8787)` and `SSH tunnel established`: a new listener appeared on `127.0.0.1:8787`, `/healthz` was reachable, and the spawned process carried the exact managed argv (`-N -T`, `BatchMode=yes`, `ExitOnForwardFailure=yes`, `ServerAliveInterval=15`, `ServerAliveCountMax=3`, `-L 127.0.0.1:8787:127.0.0.1:8787`, `-- avatar`). No TinyScry restart was needed. `feed:"stale"` during the run was expected and unrelated. No VPS reboot was performed for this acceptance, and the spawned child's parent PID was not independently confirmed. |
| Real VPS/systemd behavior | Verified on a real VPS reboot: `Linger=yes` preserved the user manager; `tinyscry-feed` and `tinyscry-relay` returned before interactive login.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Interactive TinyFugue     | The fixed-path `fwrite()` blocking behavior was measured on TinyFugue 5.1.6; the hook did not block the interactive client. Build 5.2.2-3-g4f0ff34 was then used for live play and the reboot bootstrap with no observed blocking, but that measurement was not rerun on it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Real MUD/GMCP session     | Verified from live play and a redacted capture; observed normalization covered `Char.Status` and `Char.Vitals`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| External SSH runtime      | Verified with a manual local forward, including interruption and recovery.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Relay bind                | Verified through the live restart and reboot: the VPS listener remained at `127.0.0.1:8787` only.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Non-loopback guard        | Superseded by a permanent restriction: every non-loopback host is rejected and no override exists. Deterministic coverage is current; the old live opt-in evidence no longer describes the code.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `GET /healthz`            | Tunneled HTTP 200; observed `down`, `live`, and `stale` feed states.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Last-known safety         | Reverified live after the identity fix. Before the feed restart: `seq=3`. After it, before any fresh GMCP: `feed=down`, `producer_count=0`, `has_snapshot=true`, `seq=3` - the snapshot stayed relay-owned and the restarted feed republished nothing. Fresh material GMCP then gave `feed=live`, `producer_count=1`, `has_snapshot=true`, `seq=5`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Runtime spool and hook    | Verified after reboot: systemd recreated the private runtime spool and persistent TinyFugue hook symlink.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| TinyFugue ownership       | Verified after reboot: TinyFugue did not auto-start; it remains operator-owned and intentionally interactive.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Identity bootstrap        | Verified live after a real reboot: the ephemeral checkpoint was gone, and the next normal login produced `Char.StatusVars` followed by a full `Char.Status` carrying `character_name`, a `0600` checkpoint, and `feed: live` with a non-null sequence.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| TinyFugue GMCP login hook | Required and verified: the operator build must expose the `GMCP_LOGIN` hook its login scripts use to negotiate GMCP and send `Char.Login`. Tested with `5.2.2-3-g4f0ff34`; a version number alone does not prove `GMCP_LOGIN` is compiled in, so the capability is the invariant.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Outbound action path      | The Slice 7 connectionless fence procedure passed on pinned TinyFugue build `5.2.2-3-g4f0ff34` (`4f0ff34145b7c3f23e6233874d45ee102d98d9e9`). Slice 8 then live-verified native UI `look` through `RelayActionSink` -> relay -> TinyFugue -> MUD with one independently observed execution. Consumer removal rejected without execution; restoration did not replay the rejected action; one fresh action executed once. `forwarded` still proves only the fixed bridge write and flush.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Tauri Rust crate          | Slice 3 Windows-native mirror: all three Rust gates clean; previously container-verified with `webkit2gtk`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |

### Slice 10 transient-text and alert evidence

- A normal received AVATAR line traversed TinyFugue capture, the private spool,
  feed context fencing, transient publisher, relay, and a current `/state`
  subscriber with the exact selected context.
- A line actually received on a background TinyFugue echo world was visible in
  that world's history but did not reach the selected-context subscriber.
- A newly connected subscriber received none of the previous unique text
  markers (`REPLAY_COUNT=0`), confirming that received text is not retained or
  replayed.
- Windows-native alert acceptance created and edited a received-text alert,
  verified persistence, repeated identical matches, sound and notification
  delivery, case-sensitive and case-insensitive behavior, and confirmed that
  notification bodies contain the configured alert label rather than raw
  received MUD text.
- Contains mode remained literal: `*` did not match ordinary text and did match
  a received line containing a literal `*`.
- Wildcard mode was live-verified after the internal matcher optimization.
  `TS10_WILD_*_END` matched both `TS10_WILD_ABC_END` and
  `TS10_WILD__END`, proving that `*` consumes arbitrary text including zero
  characters. Prefixed and suffixed lines did not match, confirming whole-line
  anchoring.
- With `*TS10_WILD_ABC_END*`, a prefixed-and-suffixed line matched. A wildcard
  pattern containing no `*` matched only the exact whole line. A lower-case
  received line also matched an upper-case wildcard pattern when case
  sensitivity was disabled.
- Existing text definitions without `matchMode` migrate to Contains, while
  unknown modes are rejected by deterministic validation.
- Compact and expanded Bell quick lists operate on the same persisted alert
  definitions as the manager. Disabling a matching text alert through Bell
  suppressed the next matching line immediately; re-enabling it caused the
  next identical line to alert.
- The final Windows-native UI smoke verified compact and expanded Bell panels,
  synchronized quick toggles, intrinsic expanded Settings/Alerts heights with
  no large blank region, native-height restoration while switching panels, and
  compact panel dismissal behavior.
- Compact Actions now mirrors Alerts at zero definitions: its Swords trigger
  remains available and opens a `No actions defined.` state rather than
  disappearing. The expanded action strip remains absent when there are no
  actions.

### Slice 8 native and live action evidence

- The Windows Tauri app launched cleanly and remained non-maximizable.
  Expanded and compact Settings and Actions sizing/restoration were exercised
  live. Compact attached panels retained the HUD width, Manage Actions retained
  its originating HUD width and remained draggable, and the compact action
  palette used bounded wrapped buttons behind its icon trigger.
- Native restart persistence passed. Create, edit, and delete changes survived
  as expected, and exact command text with intentional leading and trailing
  spaces survived a restart unchanged. Keyboard Tab, Enter, and Escape paths
  and focus restoration passed.
- A native UI `look` action traversed the real `RelayActionSink`, relay,
  TinyFugue helper, and MUD path and executed exactly once. The UI displayed
  `Forwarded to TinyFugue. Final MUD delivery is not confirmed.` The execution
  was established by separate observation; the `forwarded` result alone does
  not establish MUD receipt or execution.
- Removing the matching TinyFugue action consumer caused an action to be
  rejected and not executed. Restoring a matching consumer did not replay it.
  One new action after restoration executed exactly once. No queue, retry,
  replay, reconnect resend, or duplicate invocation was observed.
- Deterministic tests cover `unknown` result semantics and its no-retry
  wording, and manual browser mock acceptance checked that presentation. No
  live `unknown` result was deliberately manufactured.

Slice 4 live evidence:

- Named targets acquired and their health updated. Explicit `opponent_name:""`
  clearing remained supported, and a `"Fight"` -> non-`"Fight"` transition
  cleared a target when no explicit clear arrived. A target-bearing checkpoint
  survived a feed restart during confirmed `"Fight"`, continued updating, and
  cleared when combat ended.
- The operator's `received-gmcp` and `tinyscry_capture_gmcp` hooks were both
  generic priority-1 non-fall-through GMCP hooks. They intermittently lost
  whole events: named target acquisitions reached diagnostics while the
  production checkpoint remained `target:null`. The canonical capture hook was
  changed to `-Fp2`; repeated live fights then acquired, updated, and cleared
  targets correctly. Priority 2 is the shipped, live-verified configuration,
  not the only claimed valid priority.
- An intermittent stale character-name state was observed during repeated
  character relogs after the GMCP hook remediation. Normal relogs under the
  final canonical runtime subsequently updated identity correctly. A narrower
  rapid login/world-transition case can still miss the one authoritative full
  `Char.Status.character_name` packet; that known limitation is deferred below
  rather than addressed through speculative identity inference.

CI does not replace any row in this table and must not be cited as evidence for
Windows Tauri behavior, actual OpenSSH supervision, VPS/systemd behavior,
interactive TinyFugue, a real MUD/GMCP session, or the post-reboot identity
bootstrap - that last one depends on an operator-owned TinyFugue build and login
scripts the deterministic gate never executes.

## Bugs found and fixed during bootstrap

Recorded because each was caught by verification rather than by review, and
each is the kind that comes back.

1. **Stale vitals rendered as live.** The HUD derived freshness from the socket
   alone, so a relay that outlived its producer kept showing minutes-old vitals
   at full confidence - the one failure a companion HUD must not have.
   Freshness now lives in `freshnessOf()` in `apps/desktop/src/lib/hud/model.ts`
   and requires both a live socket and a live feed. Pinned by a regression test
   in `apps/desktop/test/model.test.ts`.
2. **Python 3.14-only syntax under a 3.12 floor.** The TF adapter used
   unparenthesised `except A, B:` (PEP 758) while declaring
   `requires-python = ">=3.12"`, which is a `SyntaxError` on any 3.12/3.13 VPS.
   It passed lint because the project pinned ruff's `target-version` and mypy's
   `python_version` to the local interpreter. Both overrides are gone; ruff now
   derives its floor from `requires-python`, so lint catches a recurrence.
3. **Vite watched locked Cargo executables on Windows.** The first native
   launch compiled the Rust crate but then failed with `EBUSY` while Vite tried
   to watch a generated Cargo executable. `vite.config.ts` now ignores the
   native source tree; the next native launch succeeded.
4. **The panel did not fill the `320x210` native window.** At the configured
   viewport the panel measured 226 pixels high, clipping its bottom; after
   expansion it left a transparent dead region below the content. The HUD now
   fills `100vh` and uses compact spacing. Runtime measurement is exactly
   `320x210`, with the target row ending at pixel 205.
5. **Blocking input starved the producer's event loop.** `tinyscry-bridge` read
   its input with a synchronous `for` loop, so its WebSocket client never
   processed the relay's close frame: after a relay restart the producer socket
   stayed in `CLOSE-WAIT` and no later state was ever published. `process_lines`
   now awaits `asyncio.to_thread(stream.readline)`, leaving the event loop free
   for close, ping and timeout handling. Pinned by
   `integrations/tinyfugue/tests/test_bridge.py`.
6. **Identity never recovered after a VPS reboot.** The reboot correctly
   discarded the ephemeral checkpoint, but AVATAR sends the identity-bearing
   `Char.Status` only at character login and offers no refresh request, so
   TinyScry consumed vitals and deltas indefinitely with no character. Two
   operator-side preconditions were missing: the capture hook was loaded from
   `~/.tfrc` while the session actually started from another startup file, and
   the previously installed TinyFugue binary did not provide the `GMCP_LOGIN`
   hook its login scripts need to sequence GMCP at the right point. Loading the
   hook from the active startup file before login, on a `GMCP_LOGIN`-capable
   build, fixed it; no TinyScry normalization, checkpoint or protocol behavior
   changed. Pinned against identity inference by
   `integrations/tinyfugue/tests/test_normalize.py`.
7. **Startup adoption of an existing relay was terminal.** In managed mode a
   healthy relay already listening on `127.0.0.1:8787` made
   `TunnelSupervisor::managed()` report `ExternalPortInUse` and return without
   a worker, so TinyScry permanently relinquished supervision. Observed live on
   Windows after a VPS reboot removed the pre-existing forward: no listener or
   TinyScry-owned SSH child remained, `/healthz` was unreachable, and the HUD
   stayed red until TinyScry itself was restarted - after which it
   spawned its own forward and recovered immediately. Adoption is now a
   monitored state: the endpoint is re-probed, never killed, and the same
   supervisor spawns its own child once the port is free. Pinned by
   `apps/desktop/src-tauri/src/tunnel.rs`. This is distinct from the earlier
   owned-child reboot recovery, which already worked.

## Known deferred issues

1. **Character identity reacquisition during very fast AVATAR transitions.**
   Some rapid login/world transitions can produce later GMCP without another
   authoritative full `Char.Status.character_name` packet, leaving TinyScry
   without character identity. The MUD documents no refresh request and later
   status packets may be deltas; no speculative identity inference is planned.
2. **Separate TinyFugue runtime crash.** One upstream/runtime failure printed
   `Internal error: socket.c, line 3717` followed by `resize freed string`.
   Memory pressure and OOM were ruled out. This is tracked separately from the
   TinyScry action UI and transport.
3. **Raw/ANSI GMCP parsing robustness.** Some captured GMCP material with raw
   ANSI/control bytes has produced `invalid_raw_json`. The correct
   capture/parsing/normalization boundary remains to be investigated.

## Completed implementation

Slices 5 (`hud-ui-refinement`) and 6 (`alerts-window-polish`) are complete.
Their native Windows evidence is recorded above. Slice 6 delivered
desktop-local low-HP alerts, independent bundled sound and native notification
effects, validated persisted alert preferences, settings presentation, and a
non-maximizable native window.

Slice 7, `trusted-outbound-actions`, is complete. It is a clean protocol-v2
cutover that adds:

- an exact `(session, foreground, connection)` context to selected state;
- per-world TinyFugue normalization and a session-aware ephemeral checkpoint;
- a permanently loopback-only relay, endpoint-specific Origin rules, and a
  one-consumer/one-in-flight action broker with no retry or replay;
- a strict private context marker and fixed-macro action helper that carries
  raw commands only as data; and
- a separate desktop `ActionSink`, with a network-free mock implementation.

Deterministic verification covers the protocol and process boundaries. The
connectionless outbound procedure in `integrations/tinyfugue/README.md` was
also completed in a fresh operator process on the pinned TinyFugue build. It
live-verified exact-current delivery and helper replacement, both synchronous
generation fences, the three corrected `/eval` scope boundaries, no-retarget
quote pinning, idle relay recovery, no replay across helper loss, and prompt
reader-loss shutdown. Relay `forwarded` still means only that the fixed
TinyFugue bridge was written and flushed; that Slice 7 connectionless procedure
does not itself establish real MUD-server command execution.

Slice 8, `configurable-action-ui`, is complete. It adds validated ordered action
definitions in renderer-local plaintext storage, a same-window create/edit/delete
surface, bounded expanded and compact action controls, exact-context
`ActionSink` injection, and result wording that does not overstate `forwarded`.
At most 64 definitions are retained. Saved definitions are reusable templates,
not queued or retained dispatched-action history, and do not change the Slice 7
no-retry/no-replay transport.

The canonical automated gate, manual browser mock acceptance, Windows-native
acceptance, and live real-MUD action-UI acceptance passed. The live run
independently observed exact-once execution for an approved `look`, rejection
without execution when the matching consumer was absent, no replay when it
returned, and exact-once execution for a fresh action. Live `unknown` behavior
was not manufactured or claimed.

Slice 9, `managed-tunnel-takeover`, is complete. Managed mode no longer treats
an already-running relay endpoint as a terminal outcome: it adopts and monitors
that endpoint, never signals it, and spawns its own supervised SSH child once
the endpoint is gone and the port is free. A port held by anything else is
reported unavailable and left alone. Rust checks cover classification, adoption,
takeover, refusal, shutdown while monitoring, a slow adopted endpoint, a
segmented stale health response, and shutdown during a stalled health read.

A first Windows-native run exposed a diagnostic flap while the adopted endpoint
was healthy: the health read reused the connect probe's 500 ms window, so an
endpoint answering at SSH-forward latency intermittently read as a foreign
listener. A watch cycle now classifies the port once and applies a single
transition, and the health read has its own longer deadline.

Windows-native acceptance was then re-run after that fix and passed: a manual
SSH forward exposing the remote TinyScry relay was adopted with no transient
conflict logged, and terminating that forward made the same running app take
the forward over with its own supervised `ssh` child - established, listening,
and serving `/healthz` - without a restart. That acceptance involved no VPS
reboot; the reboot was the earlier event that exposed the original defect, and
the child's parent PID was not independently confirmed.

### Post-Slice 9 desktop polish and theming - complete

The HUD now has refined compact and expanded presentations, shared UI styling
cleanup, Lucide icon controls, improved light-theme contrast, and Dark, Light,
and System themes. Theme preference is persisted locally; System mode follows
live OS-theme changes. The action strip is measured at runtime, Settings uses
its current sizing, and panel/focus behavior was tightened for accessibility.

HUD content remains full opacity while live, stale, down, reconnecting, or
offline. The status indicator remains the authority for freshness; retained
last-known values do not become actionable when the context is cleared.

Browser and deterministic checks passed, followed by a Windows-native smoke of
theme switching; compact/expanded behavior; Settings; Manage Actions; native
sizing; drag; non-maximizable behavior; keyboard/focus paths; and general
polish. That smoke did not deliberately manufacture a stale/down transport
failure, re-test the live MUD action path, or verify new VPS behavior.

Slice 10, `configurable-notification-triggers`, is complete. It generalizes the
original low-health alert into persisted configurable vital and received-text
alerts, adds transient selected-context received-text delivery without retained
HUD state, and provides bounded Contains and whole-line Wildcard matching with
independent case sensitivity. Only `*` is special in Wildcard mode; there is no
regular-expression engine or capture behavior.

Windows-native acceptance covered creation/editing/persistence, sound and
notification effects, configured-label-only notification bodies, optimized
wildcard behavior, quick enable/disable through Bell surfaces, compact and
expanded alert presentation, intrinsic Settings/Alerts sizing, and the
zero-definition compact Actions/Alerts affordances. Alerts remain local
presentation behavior and do not dispatch outbound commands.

For future candidate work, see [`roadmap.md`](roadmap.md).
