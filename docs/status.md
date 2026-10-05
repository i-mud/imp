# Implementation status

Canonical status for Imp. Platform-independent checks run in WSL2; the
native shell is also runtime-verified on Windows 11 x64.

## Working end to end

Imp currently supports TinyFugue and Mudlet as MUD-client adapters. Both feed
the same client-neutral normalization and node boundary.

```text
local:

MUD <-> MUD client <-> client adapter <-> shared GMCP adapter
                                            |
                                            v
                                  same-host Imp node :8787
                                            |
                                            v
                                      Imp desktop

remote:

MUD <-> MUD client <-> client adapter <-> shared GMCP adapter
                                            |
                                            v
                                      Imp node :8787
                                       /          \
                                      /            \
                    SSH -> desktop :8789       gateway :8788
                            |                   -> TLS/WSS
                            v                       |
                       Imp desktop <---------------'
```

The desktop owns and supervises its same-host node on `127.0.0.1:8787`
independently of which node the HUD consumes. A pre-existing listener on that
producer/node port is never adopted, replaced, or killed.

Managed and External SSH use the separate consumer endpoint
`127.0.0.1:8789`, forwarding to a remote node on port `8787`. Managed mode may
adopt a healthy existing relay on `8789`; if that adopted endpoint disappears,
the supervisor can take over by starting its own SSH child once the port is
free. An unrelated listener is never killed.

Direct WSS bypasses SSH and reaches the node through the separate authenticated
gateway. The gateway authenticates before opening the upstream node connection
and exposes only desktop-facing state/action capabilities.

This separation means a local Mudlet or TinyFugue adapter can remain attached to
the desktop-owned node while the same HUD consumes a different remote node.

| Component                 | State                                                                                                                                                                             |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `packages/protocol`       | Version 2 complete: client-neutral context-bound state/actions plus transient received text and fail-closed dual decoders.                                                        |
| `services/relay`          | Loopback-only Imp node with selected-state ownership, freshness, transient text, Origin policy, and a single-flight action broker; separate authenticated gateway for remote WSS. |
| `integrations/common`     | Shared client-neutral GMCP record, normalization, and publishing layer used by supported client integrations.                                                                     |
| `integrations/tinyfugue`  | Versioned spool/feed lifecycle, exact-context selection, transient text capture, and fixed-macro trusted-action delivery.                                                         |
| `integrations/mudlet`     | Lua/profile lifecycle capture, desktop-provisioned helper, shared state publishing, and exact-context trusted-action delivery.                                                    |
| `apps/desktop` (frontend) | Compact/expanded HUD, themes, alerts, actions, and transport-independent state/action clients.                                                                                    |
| `apps/desktop` (Tauri)    | Windows-native shell with independent local-node, SSH, and optional gateway supervision plus persisted connection configuration.                                                  |

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
- shared-adapter lint, strict type checks, and tests;
- Mudlet adapter lint, strict type checks, and tests;
- TinyFugue adapter lint, strict type checks, and tests;
- deterministic fixture and trust-boundary behavior; and
- the producer-to-relay-to-subscriber loopback end-to-end test.

CI requires no Imp secrets or external infrastructure. It does not compile
the Rust crate and does not prove native or live runtime behavior.

### Manual, native, and live evidence

These checks require a native platform, real processes, or operator-controlled
infrastructure and remain separate from CI:

Slice-specific evidence below records what was exercised at that milestone,
not the current package version or release procedure. In particular, pre-Slice-16
SSH tests used local port `8787`; today's SSH consumer port is `8789`, and the
desktop-owned local node runs even in Direct mode. Historical release mechanics
are superseded by [`releases.md`](releases.md).

| Evidence                  | Status                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Windows Tauri runtime     | Verified on Windows 11: clean launch and console, close, topmost, drag, transparency, live updates, and a non-maximizable HUD. Slice 8 verified expanded/compact Settings and Actions sizing/restoration, width-preserving attached panels, originating-width management and its drag region, the bounded wrapped compact palette and icon trigger, native restart persistence, and keyboard focus paths. The final post-PR #6 polish/theme smoke re-checked theme switching, compact/expanded behavior, Settings, Manage Actions, native sizing, drag, non-maximizable behavior, keyboard/focus paths, and general polish. It did not add live MUD action-path or VPS evidence.                                                                                                                                                                                                                                                                                                                                                                                         |
| Actual OpenSSH child      | **Historical pre-Slice-16 evidence.** Verified in native managed mode for a child process already owned by the desktop: its tunnel dropped during a real VPS reboot and recovered automatically without an application restart. That run does not cover startup adoption of a pre-existing relay endpoint.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Adopted-endpoint takeover | **Historical pre-Slice-16 evidence.** Verified on Windows 11 after the flap fix. A manual SSH forward exposing the remote relay was running first; the desktop started in managed mode and reported adoption of the existing relay on `127.0.0.1:8787`, owned no SSH child, and showed no transient `local port 127.0.0.1:8787 is unavailable` while that forward remained. Terminating the manual forward made the same running app log `127.0.0.1:8787 is free again`, then `SSH tunnel starting (avatar -> 127.0.0.1:8787)` and `SSH tunnel established`: a new listener appeared on `127.0.0.1:8787`, `/healthz` was reachable, and the spawned process carried the exact managed argv (`-N -T`, `BatchMode=yes`, `ExitOnForwardFailure=yes`, `ServerAliveInterval=15`, `ServerAliveCountMax=3`, `-L 127.0.0.1:8787:127.0.0.1:8787`, `-- avatar`). No application restart was needed. `feed:"stale"` during the run was expected and unrelated. No VPS reboot was performed for this acceptance, and the spawned child's parent PID was not independently confirmed. |
| Real VPS/systemd behavior | Verified on a real VPS reboot: `Linger=yes` preserved the user manager; the feed and relay user services returned before interactive login.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| Interactive TinyFugue     | The fixed-path `fwrite()` blocking behavior was measured on TinyFugue 5.1.6; the hook did not block the interactive client. Build 5.2.2-3-g4f0ff34 was then used for live play and the reboot bootstrap with no observed blocking, but that measurement was not rerun on it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Real MUD/GMCP session     | Verified from live play and a redacted capture; observed normalization covered `Char.Status` and `Char.Vitals`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| External SSH runtime      | Verified with a manual local forward, including interruption and recovery.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Relay bind                | Verified through the live restart and reboot: the VPS listener remained at `127.0.0.1:8787` only.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| Non-loopback guard        | Superseded by a permanent restriction: every non-loopback host is rejected and no override exists. Deterministic coverage is current; the old live opt-in evidence no longer describes the code.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `GET /healthz`            | Tunneled HTTP 200; observed `down`, `live`, and `stale` feed states.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| Last-known safety         | Reverified live after the identity fix. Before the feed restart: `seq=3`. After it, before any fresh GMCP: `feed=down`, `producer_count=0`, `has_snapshot=true`, `seq=3` - the snapshot stayed relay-owned and the restarted feed republished nothing. Fresh material GMCP then gave `feed=live`, `producer_count=1`, `has_snapshot=true`, `seq=5`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Runtime spool and hook    | Verified after reboot: systemd recreated the private runtime spool and persistent TinyFugue hook symlink.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| TinyFugue ownership       | Verified after reboot: TinyFugue did not auto-start; it remains operator-owned and intentionally interactive.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Identity bootstrap        | Verified live after a real reboot: the ephemeral checkpoint was gone, and the next normal login produced `Char.StatusVars` followed by a full `Char.Status` carrying `character_name`, a `0600` checkpoint, and `feed: live` with a non-null sequence.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| TinyFugue GMCP login hook | Required and verified: the operator build must expose the `GMCP_LOGIN` hook its login scripts use to negotiate GMCP and send `Char.Login`. Tested with `5.2.2-3-g4f0ff34`; a version number alone does not prove `GMCP_LOGIN` is compiled in, so the capability is the invariant.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| Outbound action path      | The Slice 7 connectionless fence procedure passed on pinned TinyFugue build `5.2.2-3-g4f0ff34` (`4f0ff34145b7c3f23e6233874d45ee102d98d9e9`). Slice 8 then live-verified native UI `look` through `RelayActionSink` -> relay -> TinyFugue -> MUD with one independently observed execution. Consumer removal rejected without execution; restoration did not replay the rejected action; one fresh action executed once. `forwarded` still proves only the fixed bridge write and flush.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Tauri Rust crate          | Slice 12 Windows-native mirror: 39/39 Rust library tests passed without warnings, covering transport selection, connection-settings validation/canonicalization, token non-exposure, failure-safe persistence, and managed/external ownership behavior.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |

### Slice 14 Imp rename evidence

Slice 14 established Imp as a clean product and runtime identity break before
the canonical `v0.1.0` release was published after Slice 15.

- `git diff --check` and the complete `npm run check` gate passed, including 62
  protocol tests, 146 desktop tests, 120 relay tests, 89 TinyFugue integration
  tests, the relay end-to-end test, linting, type checking, and the production
  frontend build.
- The Windows native mirror compiled `imp-desktop`; all 39 Rust library tests
  passed. The release build produced `Imp_0.1.0_x64-setup.exe`.
- Fresh native acceptance verified Imp branding, version `0.1.0`, Compact as
  the fresh-install display mode, System as the fresh-install theme,
  Settings/Actions behavior, installation, launch, and normal exit.
- The VPS was cut over to the `imp-relay`, `imp-feed`, and `imp-gateway` user
  services. Live state reached the relay with `feed:"live"`, and a desktop
  outbound-action smoke test reached the MUD successfully. Obsolete VPS
  service, helper, configuration, state, and checkout identities were removed.
- GitHub repository rename and canonical Imp `v0.1.0` publication were completed
  after Slice 15; see the completed release history in [`roadmap.md`](roadmap.md).

### Slice 13 release-readiness evidence

- At Slice 13, the repository release version was `0.1.0`, checked
  deterministically across 15 version-bearing locations. Current checks include
  the later shared-adapter and Mudlet packages; tags must match `v<version>`.
- The native Settings surface reports the running Tauri application version
  rather than a separately hard-coded renderer version.
- The Windows candidate workflow built an x64 NSIS installer on GitHub Actions.
  Its PR run passed alongside the normal repository CI gate.
- That produced installer was installed and exercised on Windows independently
  of the WSL development checkout. With WSL shut down, the installed
  application launched, reported version `0.1.0`, configured Managed SSH from
  the native Connection UI, received live state, and sent an approved outbound
  action successfully.
- Candidate acceptance exposed one packaging-specific defect: the managed
  `ssh.exe` child opened a persistent console window in an installed release
  build. Windows process creation now applies `CREATE_NO_WINDOW` only to that
  managed SSH child. A subsequent local release build verified that Managed SSH
  connected without opening the extra console window, and the native Rust gate
  passed 40 library tests.
- Final-candidate acceptance exposed a second Windows lifecycle defect: after
  the desktop exited, its owned managed `ssh.exe` child could remain running as an
  orphan. Cleanup now runs on Tauri's actual `RunEvent::Exit` boundary rather
  than `ExitRequested`. A local release build verified both normal window close
  and the tray **Quit** action: in each case the exact managed SSH PID terminated
  and no matching managed `ssh.exe` remained.
- The Windows installer remains intentionally unsigned for this alpha. The
  candidate tested did not display a SmartScreen warning on that machine and
  download path, but absence of such a warning is not claimed generally.
- A tag-driven release workflow is implemented with a read-only Windows build
  job and a separate tag-only publication job. Manual dispatch produces the
  same NSIS build shape without publishing a GitHub Release.
- Final release acceptance passed on merged commit
  `de435f05b5dc92b29f1fee53c68616c6cb2b842f`. The corrected Windows candidate
  passed clean install, version reporting, Managed SSH, live state, an approved
  action, managed-child cleanup, uninstall, and reinstall with WSL shut down.
- The superseded pre-rename `v0.1.0` tag pointed to that accepted Slice 13
  commit. Release workflow run `36410287527` and SHA-256 digest
  `05ed219a57951f96e8e8de584fd1a3256840af2d1863766e0200c7e104268b79` are
  historical evidence for that superseded publication only; they are not Imp
  release identifiers.
- The final Imp `v0.1.0` publication followed Slice 15. The superseded Slice 13
  workflow run and digest above must not be used to identify that public release.

### Slice 12 native connection settings evidence

- The Windows-native Settings surface now manages External, Managed, and Direct
  transport configuration without requiring manual edits to `tunnel.json`.
- The settings-read API exposes mode, SSH target, Direct URL, and only a
  `hasPairingToken` boolean. An existing plaintext Direct pairing token is not
  returned merely to populate the form.
- Managed-mode validation now rejects an empty SSH target. Mode-specific writes
  canonicalize the file: External retains no transport-specific fields,
  Managed retains only its SSH target, and Direct retains only its WSS state
  URL and pairing token.
- Direct settings can preserve an already stored token while changing other
  Direct fields without returning that token to the renderer. Entering Direct
  from another mode requires an explicitly supplied valid token.
- Native persistence stages and replaces the configuration as one write
  boundary. Validation or persistence failure leaves the previous in-memory
  and on-disk usable configuration unchanged.
- Saved transport changes deliberately take effect only after application
  restart; Slice 12 does not introduce live supervisor/source replacement.
- Windows-native acceptance saved the existing Managed configuration through
  the UI, restarted, recovered live state, and executed an outbound `look`
  action.
- The same UI then changed Managed -> Direct using the established WSS endpoint
  and pairing credential. After restart, live state and an outbound `look`
  action passed through Direct WSS, the UI showed the Direct URL without
  revealing the stored token, and Windows had no listener on local port 8787.
- The UI then changed Direct -> Managed with SSH target `avatar`. The persisted
  file contained only `mode` and `sshTarget`, proving the Direct URL and
  pairing token were removed. After restart, managed SSH restored live state
  and outbound action delivery.
- Browser development kept the native settings boundary inert rather than
  invoking Tauri configuration commands.
- The final Slice 12 gates passed 144 desktop tests with zero Svelte
  diagnostics and 39 Windows-native Rust library tests without warnings.
  PR #13 then passed the repository `npm run check` CI gate before merge.

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
- The operator's `received-gmcp` and `imp_capture_gmcp` hooks were both
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
5. **Blocking input starved the producer's event loop.** `imp-bridge` read
   its input with a synchronous `for` loop, so its WebSocket client never
   processed the relay's close frame: after a relay restart the producer socket
   stayed in `CLOSE-WAIT` and no later state was ever published. `process_lines`
   now awaits `asyncio.to_thread(stream.readline)`, leaving the event loop free
   for close, ping and timeout handling. Pinned by
   `integrations/tinyfugue/tests/test_bridge.py`.
6. **Identity never recovered after a VPS reboot.** The reboot correctly
   discarded the ephemeral checkpoint, but AVATAR sends the identity-bearing
   `Char.Status` only at character login and offers no refresh request, so
   Imp consumed vitals and deltas indefinitely with no character. Two
   operator-side preconditions were missing: the capture hook was loaded from
   `~/.tfrc` while the session actually started from another startup file, and
   the previously installed TinyFugue binary did not provide the `GMCP_LOGIN`
   hook its login scripts need to sequence GMCP at the right point. Loading the
   hook from the active startup file before login, on a `GMCP_LOGIN`-capable
   build, fixed it; no Imp normalization, checkpoint or protocol behavior
   changed. Pinned against identity inference by
   `integrations/tinyfugue/tests/test_normalize.py`.
7. **Startup adoption of an existing relay was terminal.** In managed mode a
   healthy relay already listening on `127.0.0.1:8787` made
   `TunnelSupervisor::managed()` report `ExternalPortInUse` and return without
   a worker, so Imp permanently relinquished supervision. Observed live on
   Windows after a VPS reboot removed the pre-existing forward: no listener or
   Imp-owned SSH child remained, `/healthz` was unreachable, and the HUD
   stayed red until Imp itself was restarted - after which it
   spawned its own forward and recovered immediately. Adoption is now a
   monitored state: the endpoint is re-probed, never killed, and the same
   supervisor spawns its own child once the port is free. Pinned by
   `apps/desktop/src-tauri/src/tunnel.rs`. This is distinct from the earlier
   owned-child reboot recovery, which already worked.

## Known deferred issues

1. **Character identity reacquisition during very fast AVATAR transitions.**
   Some rapid login/world transitions can produce later GMCP without another
   authoritative full `Char.Status.character_name` packet, leaving Imp
   without character identity. The MUD documents no refresh request and later
   status packets may be deltas; no speculative identity inference is planned.
2. **Separate TinyFugue runtime crash.** One upstream/runtime failure printed
   `Internal error: socket.c, line 3717` followed by `resize freed string`.
   Memory pressure and OOM were ruled out. This is tracked separately from the
   Imp action UI and transport.
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
SSH forward exposing the remote Imp relay was adopted with no transient
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

Slice 11, `authenticated-wss-transport`, is complete. It adds a separate
loopback `imp-gateway`, pairing-token authentication before relay access,
native Direct-WSS desktop configuration, and runtime selection between Direct
WSS and the existing SSH modes. The relay remains permanently loopback-only
and unchanged in role; the gateway exposes only state and action capabilities.

Deterministic verification covers malformed, binary, wrong-token and timed-out
authentication; pre-auth relay isolation; Origin and endpoint rejection;
loopback listener/upstream restrictions; state/text semantics; action
round-trips; relay loss; and no automatic action retry.

Live Windows acceptance used a publicly trusted TLS endpoint and verified
Direct WSS with no local `8787` listener, live state changes, a real `look`
action, automatic recovery after a gateway interruption, and switching the
same desktop back to managed SSH without changing relay or feed configuration.
A separate live workstation WSS probe verified that an incorrect pairing token
was rejected with close code `1008` and exposed no state. Relay, feed, and
gateway user services were left enabled for boot. Relay/feed reboot survival
was previously live-verified; gateway reboot survival has not yet been
separately observed after enabling it.

Certificate renewal infrastructure was configured for the live endpoint, but
an actual renewal has not yet occurred and is not claimed as verified.

Slice 12, `native-connection-settings`, is complete. External SSH, Managed
SSH, and authenticated Direct WSS can be configured through native Settings.
Direct pairing-token reads remain write-only from the renderer's perspective:
an existing plaintext token is not returned merely to populate the form.
Windows-native acceptance exercised Managed -> Direct -> Managed using the UI,
with live state and an approved outbound action after each restart.

Slice 13, `first-release-readiness`, is complete as release-engineering work.
It established the MIT license, changelog/version contract, Windows x64 NSIS
build, tag-driven GitHub release workflow, native version display, installation
documentation, and clean Windows artifact acceptance. The original `v0.1.0`
tag/prerelease produced during that work was later intentionally withdrawn
during the product rename; it is not the current final release tag.

Slice 14, `imp-rename`, is complete. TinyScry was renamed to Imp — Interactive
MUD Peripheral — across product identity, repository/package names, Python
commands, protocol/context markers, native identifier, configuration/state
paths, services, documentation, and release artifact naming. The live VPS and
Windows desktop were smoke-tested under the renamed identity.

Slice 15, `server-install-bootstrap`, is complete. It introduced the versioned
Linux x86_64 server archive containing the relay and TinyFugue wheels, exact
locked `websockets` wheel, TinyFugue hook, user-systemd units, installer, and
internal checksum manifest. Current bundles also include the shared
`imp-adapter` wheel. An adjacent SHA-256 file protects the complete archive.

The installer creates a private CPython 3.12 virtual environment from the
bundle without network dependency resolution, uses versioned releases behind
`~/.local/share/imp/current`, installs the stable action-consumer helper and
TinyFugue hook, enables relay/feed only, and leaves Direct WSS opt-in. Failed
activation restores the previous runtime, integration files, helper link,
TinyFugue startup file, service enablement, and prior running-service state.

The reusable bundle acceptance passed on the target VPS environment for outer
and internal integrity, clean install, idempotent reinstall, package versions,
permissions, TinyFugue startup insertion, tamper rejection, final-path console
script interpreters, and complete failed-activation rollback. CI and the
release workflow build and test that server artifact; releases publish its
archive and checksum alongside the Windows installer.

Production live acceptance also passed using the exact accepted server archive.
`~/.local/share/imp/current` selected the versioned `0.1.0` runtime; relay,
feed, and the already-configured gateway ran from that runtime; the stable
action-helper link resolved into it; and fresh TinyFugue GMCP reached the
packaged feed and relay. The private selected-context marker was mode `0600`,
the packaged action consumer registered for the current TinyFugue context, the
Windows desktop recovered current HUD state over Managed SSH, and an approved
`look` action reached the MUD.

The cutover also verified fail-closed behavior across a feed restart while
TinyFugue remained connected. The restarted feed did not retain the previous
selected-action context. A fresh TinyFugue world-selection event re-established
the context marker and action consumer before actions were accepted again.

The descriptive `server-install-bootstrap` milestone tag records the completed
slice independently of the canonical `v0.1.0` release.

Slice 16, `mudlet-local-integration`, is complete.

The desktop now owns a packaged same-host Imp node independently of the HUD's
selected consumer transport. The existing Python relay is bundled as an onedir
`imp-node` sidecar, and the same executable/runtime can run either the relay or
the authenticated gateway.

The loopback roles are intentionally separate:

```text
127.0.0.1:8787  same-host producer node for Mudlet/TinyFugue adapters
127.0.0.1:8788  optional authenticated gateway for producer-side WSS
127.0.0.1:8789  SSH consumer endpoint for Managed/External desktop transport
```

The local node is supervised in every desktop connection mode. It no longer
adopts an existing relay-shaped endpoint: any pre-existing listener on `8787`
is left untouched and reported unavailable. This prevents an old/manual SSH
forward from being mistaken for the same-host producer node. Managed SSH may
still adopt a valid existing relay on `8789`, where remote consumption is the
intended role.

Local TinyFugue acceptance used the real TinyFugue integration, local
`imp-feed`, and the desktop-owned node. Injected GMCP produced live HUD state
and trusted actions sent through the local node reached TinyFugue's pinned send
path.

Local Mudlet acceptance covered real GMCP state, profile switching, foreground
ownership, Alt-Tab behavior, and trusted actions. Exactly one selected Mudlet
profile remained the producer, and a real `look` command sent through Imp
reached Mudlet and the MUD.

Remote Mudlet transport was live-verified over both supported paths. For SSH, a
reverse OpenSSH tunnel exposed the Windows local node on VPS loopback; a remote
client read Tesla's retained state and sent a trusted `look` that returned
`forwarded` and reached the MUD. For authenticated WSS, the VPS gateway was
temporarily stopped and its loopback `8788` replaced by a reverse tunnel to the
Windows desktop-owned gateway. Existing Caddy TLS termination then exposed that
gateway through the established public endpoint. Public `/healthz` succeeded,
`/ingest` remained `404`, authenticated WSS returned Tesla's exact state/context,
and a real `look` traversed WSS -> gateway -> local relay -> Mudlet -> MUD. The
temporary reverse tunnel was removed afterwards and the normal VPS
`imp-gateway.service` was restored and verified healthy.

The desktop transport separation was then live-verified on Windows with Mudlet
left connected while the HUD was switched to Managed SSH. `imp-node.exe`
continued to own `127.0.0.1:8787`, while the desktop-owned `ssh.exe` separately
owned `127.0.0.1:8789` with:

```text
-L 127.0.0.1:8789:127.0.0.1:8787
```

The local and remote `/healthz` responses carried different retained sequence
numbers, proving they were distinct nodes. The HUD consumed the VPS node through
`8789`, and trusted `look` delivery to the remote TinyFugue/MUD path still
worked.

During that final remote TinyFugue acceptance, the first reconnect again
produced state without authoritative character identity/vitals while trusted
actions still worked; a second reconnect restored the normal name and vitals.
That matches the separately tracked TinyFugue character-identity reacquisition
defect and is not a transport failure.

Mudlet release distribution is also complete. The release workflow builds
`Imp.mpackage` before the Windows job, downloads it into the desktop Mudlet
resources, and requires it when building the frozen desktop helper. On launch,
the installed desktop provisions both its versioned copy and the stable
user-facing path:

```text
%LOCALAPPDATA%\Imp\mudlet\Imp.mpackage
```

The user installs that small package once per Mudlet profile through Mudlet's
package manager; the shared native helper/runtime remains desktop-managed. Imp
does not modify Mudlet profiles automatically.

The temporary desktop-side WSS credential used for acceptance was removed after
testing and `remote-access.json` returned to disabled state.

### Slice 17 - Input boundary correctness

Slice 17, `input-boundary-correctness`, is complete. It remediates F01, F06, and
F07 from the [historical repository audit](audits/2026-10-03-full-repository-audit.md)
without changing the protocol or extending the observed AVATAR mappings.

Shared GMCP records permit at most 32 nested payload containers. JSON recursion
failures at the shared and client-envelope boundaries are ordinary rejections.
Mapped vitals must be integers in `0..1_000_000_000`; opponent percentages must
be finite numbers in `0..100`. Invalid conversions and out-of-range numbers
reject the whole record rather than being clamped.

The normalizer stages identity, vitals, target, combat position, and checkpoint
provenance before committing any accumulator. Rejection leaves both exposed
state and unpublished buffered fields untouched; later valid records cannot
reveal changes from the rejected record.

The Python protocol decoder range-checks integers without converting them to
float and applies finiteness checks only to floats. Oversized integers follow
the existing invalid-frame policy close (`1008`), not an internal-error close.
Shared fixtures protect the unchanged contract in both Python and TypeScript.

Relay stale-feed and gateway authentication durations must be finite and
positive in config objects and runtime constructors. CLI arguments and the
gateway timeout environment setting reject infinities, NaN, zero, and negatives.

Focused adapter/client, protocol/server, and duration regressions passed.
The complete `npm run check` gate also passed.
Loopback smoke runs exercised the real TinyFugue feed and Mudlet JSONL bridge,
publisher, relay, and subscriber: hostile nesting and numeric input were
rejected, later valid input continued, and prior vitals/target remained intact.
Actual relay/gateway CLI invocations rejected invalid durations with exit `2`
and no traceback. This is Python-runtime evidence, not new native-client or
live-MUD acceptance.

### Slice 18 - Freshness correctness

Slice 18, `freshness-correctness`, is complete. It remediates F02 without new
wire messages, heartbeat infrastructure, or transport abstractions.

The shared normalizer distinguishes valid mapped canonical-state observation
from ignored/rejected input. TinyFugue and Mudlet publish accepted observations
through their existing selected-context fences, including identical identity,
vital, and target values. Empty/unmapped records, unknown packages, transient
text, rejected records, buffered pre-identity vitals, and combat-position history
alone do not refresh freshness; a position transition that clears an existing
target remains a canonical-state observation.

The relay refreshes the stale window on matching-context publication. Identical
state retains the existing snapshot, timestamp, and sequence without a duplicate
subscriber snapshot. Selections still emit snapshots and invalidate freshness;
reconnect reasserts only the retained selection, not an observation. Publication
attempts one send on the ready transport; an unavailable or failed transport
drops observation evidence while retaining canonical values for recovery. Only
a new authoritative observation after recovery may restore live. Identical
observations do not invalidate transient-text delivery on the selected
connection. Status transitions remain independent. Action authorization and
transient/action no-replay semantics are unchanged.

TinyFugue selection authority is generation-ordered rather than arrival-ordered.
Within a session, foreground generations never decrease, including null
selections. Equal generations preserve the known world while allowing
reassertion or a newer connection; selections cannot revive an older cached
world connection. A new session resets ordering history.

Regression coverage includes shared observation classification, TinyFugue
foreground/background and connection-generation boundaries, actual spool
retired-inode interleaving, clock-controlled adapter/publisher/relay outages,
an in-flight observation interrupted by transport replacement, and transient
text during an identical observation send. Relay sequence/retention/stale
restoration, subscriber status recovery without duplicate snapshots, and
vital-alert baseline retention with exactly one threshold-crossing alert are
also covered.

Disposable local runtime smoke exercised a real TinyFugue spool/feed and a
Mudlet decoded-input runtime, shared publisher, real relay, subscriber, and HTTP
health observation. With a 0.3-second stale window, repeated identical vitals
kept each feed live over 0.6 seconds with `producer_count=1`, `seq=2`, and only
the original selection/changed-state snapshots. Unknown, text, rejected, and
wrong-context traffic subsequently allowed staleness; an unchanged valid status
observation restored live; silence became stale normally. All components and
temporary spool/runtime directories were cleaned up. This is Python-runtime
evidence, not new native-client, live-MUD, or notification-delivery acceptance.

Follow-up local loopback smoke used a 0.15-second stale window. Twenty identical
observations over 0.7 seconds kept both adapters live without changing the
retained sequence/timestamp or duplicating subscriber snapshots. During
0.32-second relay outages, unchanged and changed values were retained, recovered
selections remained stale, and a new unchanged observation restored live without
another snapshot. Actual retired-inode interleaving delivered an older Alpha
selection after a newer Beta selection; Beta remained authoritative and Alpha
observations could not refresh it. Negative traffic, transient text, and silence
did not refresh the feed. Disposable components and temporary files were
cleaned up.

Focused common-adapter, TinyFugue, Mudlet, relay, and alert tests passed.
The full `npm run check` gate passed, including both protocol decoders/corpus,
desktop tests and production build, Python lint/format/strict typing, adapter
and relay tests, documentation/version checks, and loopback end-to-end delivery.
`git diff --check` also passed.

### Slice 19 - Subscriber isolation

Slice 19, `subscriber-isolation`, remediates F03 from the
[historical repository audit](audits/2026-10-03-full-repository-audit.md).
`RelayServer` owns one writer and a FIFO limited to 16 encoded frames per
subscriber, plus at most one in-flight frame. Broadcast enqueues without waiting
for peer sends. Overflow or a 5-second send deadline removes only that peer and
aborts its transport; disconnect/shutdown cancel and await its writer.

Startup is atomically queued as `hello`, retained `snapshot` if present, then
current `status`; live snapshots/status/text follow in emission order.
Delivery buffers are transient, not a second retained-state model. `RelayState`
and Slice 18 snapshot/sequence/timestamp/freshness semantics are unchanged.
Text remains context-bound, nonretained, nonreplayed, and non-freshness-bearing.
Slow gateway state upstreams follow the ordinary subscriber retirement policy;
delivery is not guaranteed to peers that cannot keep up.

A disposable baseline probe using actual WebSockets stalled relay and healthy
subscriber at sequence 132, with only 3,833 of 20,000 producer sends completed.
Aborting only the non-reading socket let both reach 20,001. After isolation,
the post-test loopback smoke completed all 20,000 publications in 5.474 seconds;
relay and healthy subscriber reached sequence 20,001 without manual slow-peer
removal. Actual transport backpressure was observed at sequence 86, with
32,807 transport write-buffer bytes and two queued frames. Slow-peer overflow
retirement was observed after producer send 105, at 0.511 seconds; maximum
observed FIFO depth was exactly 16.

While that peer was still blocked and registered, the healthy subscriber
received watchdog staleness within 0.369 seconds and transient text within
0.0006 seconds. A new subscriber received the ordered retained-state handshake
without replaying that text. Cleanup left zero registered subscribers and zero
pending tasks. This is disposable local Python/WebSocket runtime evidence,
not deployment, release, live-MUD, or native-client acceptance.

A separate actual-socket probe stopped publishing once a send was blocked.
The send deadline retired that peer 4.996 seconds later with only three frames
queued; the healthy subscriber still received `live -> stale`. Cleanup again
left zero pending tasks.

Six added regressions cover actual TCP pressure with two ordered healthy peers,
exact queue capacity/overflow, blocked startup, watchdog/text isolation,
send deadline, pending-delivery disconnect, writer failure/cancellation,
shutdown overlapping retirement, and a real authenticated gateway proxy under
remote-client backpressure. The 174-test relay suite, Python lint/format/strict
typing, 64 TypeScript protocol tests, cross-component E2E, documentation checks,
and `git diff --check origin/main` passed.
The full `npm run check` gate also passed, including desktop/adapter/client
tests and the frontend production build.

The gateway backpressure regression allows buffered frames to drain after
upstream retirement and requires a normal remote stream close. Its 5-second
receive guard detects inactivity, not a total drain-time contract.

### Slice 20 - Diagnostic hardening

Slice 20, `diagnostic-hardening`, remediates F04/F11 from the
[historical repository audit](audits/2026-10-03-full-repository-audit.md);
that audit remains immutable.

A baseline DEBUG loopback probe exposed both accepted and rejected disposable
credentials through `websockets.server` and `websockets.client`. Independent
review found that the initial parent-level suppression could be bypassed by
explicit DEBUG descendants and could weaken stricter caller configuration.

Relay/gateway listeners and both gateway upstream paths now explicitly supply
the Imp-owned `imp_relay.websocket` logger. It has an INFO floor and preserves
the highest existing Imp/application/root and dependency parent/server/client
severity threshold, including restrictions established by earlier startup.
Global dependency loggers are untouched. Application DEBUG and permitted
connection lifecycle INFO remain available; no token fragments, redaction
framework, or authentication changes were introduced.

Logging regressions exercise explicit server/client DEBUG under every supported
application level, with a NOTSET capture handler that can observe propagated
DEBUG even under root CRITICAL. Actual WebSockets cover successful auth,
wrong-token rejection, malformed JSON, wrong auth shape, binary auth, closure
during auth, and an auth-shaped payload traversing the action upstream client.
Additional regressions cover WARNING/ERROR/CRITICAL caller restrictions, both
components alone, changing root level between component starts, restarts, and
unrelated consumer DEBUG remaining visible. Credentials, serialized auth frames,
and their hex representations are absent from Imp-owned connection diagnostics.

Before correction, the added uncensored review regressions had 35 failures and
one pass. The final 42 logging cases, 34 gateway tests, and 217 relay tests
passed. Post-review CLI probes explicitly enabled both dependency descendants
at DEBUG and repeated the full authentication/upstream matrix without leaking
credentials or auth/close payloads. The rebuilt Linux frozen sidecar repeated
that matrix successfully; F11 package identity remained `0.2.4`.

Relay hello now reads `importlib.metadata.version("imp-relay")`, without an
override or fallback. Existing release automation updates the relay's
`pyproject.toml` and lockfiles. uv installs editable metadata for source runs;
server wheels carry installed metadata; PyInstaller explicitly copies it into
the frozen sidecar. Wire compatibility remains `protocol: 2`.

Normal uv CLI, disposable wheel-installed CLI, and Linux frozen-sidecar probes
all observed hello version `0.2.4`, independently matched against package
metadata. All three DEBUG gateway smokes delivered hello/status after valid
auth, rejected an invalid credential with `1008`, and excluded both credentials
and auth payloads from captured stdout/stderr while retaining startup/connection
diagnostics. Server-bundle build/install acceptance also passed and now checks
the actual hello against installed metadata and bundle `VERSION`.

The full `npm run check` gate, separate protocol/E2E checks, and
`git diff --check origin/main` passed, including documentation/version checks,
all frontend/adapter/client tests, strict typing, and the production build.

This is local diagnostic/package-runtime evidence, not deployment, release,
live-MUD, or native-client acceptance. Other audit findings remain out of scope.

### Slice 21 - Native runtime ownership

Slice 21, `native-runtime-ownership`, addresses F05 from the immutable
[historical audit](audits/2026-10-03-full-repository-audit.md). Disposable
baseline Rust launchers reproduced the leak on Linux and Windows: after
SIGKILL/TerminateProcess killed the launcher, its frozen node still answered
`/healthz`. These were production spawn-primitive probes, not Tauri crash tests.

The desktop now initializes one runtime owner before creating supervisors.
Node and optional gateway retain bundled sidecar resolution and argv. Owned
Managed SSH intentionally adds `-S none` and `ForkAfterAuthentication=no`, so
the target client neither shares a master nor backgrounds after authentication.
Existing ports, auth, filtering, relay routes, package identity, Slice 20 logging,
and reconnect/adoption decisions are unchanged. External/adopted listeners,
masters, and services never enter ownership.

Windows uses an unnamed, non-inheritable kill-on-close Job Object. Children
enter the job atomically through `PROC_THREAD_ATTRIBUTE_JOB_LIST` at process
creation; there is no suspended-but-unassigned gap. Normal descendants inherit
membership without breakaway permission. Setup/creation failures fail closed.
Linux/macOS source builds use an independent guardian and lifetime pipe to
clean up private child process groups; that is not a non-escapable kernel tree
container. Platform limits are recorded in the
[runtime card](architecture/processes/managed-runtime.md).

Independent-review remediation reproduced three real OpenSSH failures before
changing policy: a synchronous 1 MiB LocalCommand blocked on the lost null
stdout despite a bound listener; an owned ControlPersist master and usable
forward survived owner death; and persistent target SSH through ProxyJump lost
its proxy/forward while the desktop harness remained alive. These are observed
stock-configuration failures, not hypothetical malicious-child cases.

The narrowed spawn API restores null Managed SSH stdout without logging it.
Node/gateway stdout and stderr remain piped; all children inherit cwd/environment
and use null stdin (the sidecars do not read their former unused input pipe).
Linux 10.2 and Windows 9.5 OpenSSH effective-config probes verified that
`-S none` removes ControlPath and the separate fork override resolves to `no`,
even with configured ControlMaster auto/yes and ControlPersist yes. Explicit
Master/Persist overrides proved redundant and were removed.

An additional authenticated probe showed that the generated ProxyJump client
does not inherit the target's options: a persistently configured jump master
survived owner death, although the target and forward did not. The approved
scope requires foreground jump/proxy configuration rather than rewriting SSH
connection logic. Independently persistent nested clients are explicitly
outside the supported Unix contract; the
[runtime card](architecture/processes/managed-runtime.md#desktop-tunnel-boundary)
gives the required jump-alias settings.

Initial Slice 21 Windows 11 acceptance used the actual release desktop with only a
disposable config identifier changed. Its node and gateway answered their real
health endpoints. Abrupt TerminateProcess killed both, and a separate Managed
case killed the actual system SSH child as well. The SSH peer intentionally
stalled before authentication: no live tunnel, VPS, credentials, or MUD were
used. Exact spawn PIDs, retained process handles, and desktop parent lineage
established ownership; no executable-name or port scavenging was used.

The same native run crashed node/gateway/SSH individually and observed
supervisor replacements, exercised real normal window close, and relaunched
without stale `8787`/`8788` listeners. An external frozen node with the same
executable remained healthy throughout. All three desktop cases completed
within the 15-second acceptance bound. Measured totals were approximately
2.1–2.3 seconds including health probes, not raw kernel termination latency.

Initial Windows validation passed 67 library tests and seven integration
cases, including private-job flags/handle inheritance, invalid-job startup
with a runnable positive control, abrupt death of two owned trees with
grandchildren, external safety, normal shutdown/relaunch, actual child crash,
failed executable startup, argument preservation, and an enclosing/nested job.
The `runtime-acceptance` tests are enabled in both Windows Build and release
native validation. Real window acceptance remains a separate procedure in
[`development.md`](development.md).

Initial Linux validation passed 66 library tests and four ownership integration cases;
the two ignored entries are subprocess dispatchers, not skipped acceptance
scenarios. A separate production-primitive smoke launched the actual frozen
node, gateway, and system OpenSSH with a local pre-authentication stalled peer.
SIGKILL of each owning harness stopped its recorded runtime and guardian within
the ten-second bound (observed 2.6–14.1 ms including observation/probes).
Normal node/gateway shutdown and same-port relaunch passed; an external frozen
node stayed healthy. The disposable observer reaped exact orphaned guardian
PIDs, rather than relying on WSL PID 1 as a fixture reaper. Linux Clippy passed
without warnings.
Additional Linux probes killed the guardian itself and observed the direct
runtime child die via `PDEATHSIG`, with owner wait surfacing failure. A separate
owned runtime deliberately forked a `setsid()` descendant: the owned root and
guardian died after desktop-harness SIGKILL, but that escaped descendant stayed
alive. Its exact fixture PID was then terminated and reaped. This demonstrates,
rather than merely infers, the source-build containment limitation.

The Unix ownership module type-checked for `aarch64-apple-darwin` against the
existing libc dependency. This checks Darwin API/cfg availability only: it is
not a full Tauri/macOS build or native runtime acceptance.

The Windows native sidecar/helper builds and NSIS packaging passed; the
installer was not installed or published. `npm run check` passed, including
frontend/adapter/client/relay/protocol checks and the production frontend build.
Windows Clippy passed with three existing unnecessary-cast warnings in
`topmost.rs`; strict `-D warnings` remains blocked by those untouched warnings.

Remediation revalidation:

- Linux default Rust: 65 passing library tests plus the ignored helper
  dispatcher. Ownership feature: the same library suite and eight passing
  integration cases (one additional dispatcher). Windows default Rust:
  66 passing library tests; feature: 66 library plus nine integration cases.
- Exact binary stdout/stderr capture and null-stdin EOF passed on both hosts;
  a synchronous 1 MiB null-stdout writer completed without a reader/logger.
  Disposable subprocess probes also verified inherited cwd/environment.
- The intentionally uncontained launcher failed its death assertion on Linux
  and Windows; unwind guards then stopped the exact root/grandchild fixtures.
  Linux's deliberate `setsid()` survivor was similarly observed before cleanup.
  Linux pidfd/subreaper observation reaps exact adopted fixture identities.
- The actual frozen Linux node/gateway passed hard death, normal shutdown,
  same-port relaunch, external safety, and direct-child guardian-death checks.
  The authenticated SSH fixture passed target/client LocalCommand readiness,
  both foreground proxy paths, normal/abrupt cleanup, and relaunch. A separately
  persistent external master kept its forward and unchanged socket/config.
- Windows 11 release desktop acceptance was rerun with the disposable
  identifier: Local/Managed hard death, real normal close, all runtime
  crash/replacements, external frozen-node survival, and port release passed.
  Normal cases took approximately 2.06–2.39 seconds including HTTP probes.
  `-InjectFailureAfterSpawn` exited 1 as intended after Managed replacements,
  only after reporting all retained fixture handles exited.
- Native frozen-node/Mudlet-helper builds and production/disposable-identifier
  NSIS builds passed without installing or publishing an installer.
- Darwin production/test cfg and Unix fixture cleanup paths type-checked for
  `aarch64-apple-darwin`; shared SSH policy is platform-independent source.
  No native macOS execution or full Tauri/macOS build was performed.

Final targeted-review remediation:

- Windows cwd-shadowing was reproduced before changes: baseline Rust `Command`
  selected installed OpenSSH 9.5; the owned spawn selected the benign cwd
  sentinel. Executable resolution now follows Rust 1.98.1 before atomic
  creation and supplies a non-null application image. Production-path parity
  checks cover PATH/application/system precedence, implicit cwd exclusion,
  explicit relative/absolute paths, extensions, Unicode/spaces, and long paths.
- Observer handshake failure was reproduced on Linux and Windows: setup timed
  out while the uncontained grandchild remained alive. Exact identities now
  enter cleanup ownership before fallible setup. The regression still observes
  the timeout, then confirms death before independent safety cleanup and
  listener release; both uncontained generations have cleanup guards.
- Windows default Rust passed 66 library tests. Ownership-feature validation
  passed 66 library, ten ownership, and three executable-resolution integration
  tests; two parity dispatchers are intentionally ignored outside subprocesses.
  Linux passed 65 library and nine ownership integration tests, with its helper
  dispatchers ignored outside subprocesses. Platform containment, binary stdio,
  negative-boundary cleanup, and external-process survival remain covered.
- The authenticated Linux SSH/proxy fixture and actual frozen node/gateway
  lifecycle passed again. Darwin production, harness, and observer test cfg
  typechecking passed; no native macOS execution is claimed.
- Final Windows release desktop acceptance passed Local and Managed hard death,
  normal window close, runtime replacements, relaunch/port release, and external
  frozen-node survival. Retained SSH handles identified installed
  `C:\Windows\System32\OpenSSH\ssh.exe`; both foreground options remained present.
  The original 15-second startup timeout did not recur. Failure injection exited
  1 after reporting cleanup success for all exact fixture handles.
- Final formatting, convention Clippy, desktop TypeScript tests, diff whitespace
  checks, and `npm run check` passed. Windows Clippy still reports the three
  existing untouched `topmost.rs` cast warnings.

This evidence does not establish native macOS runtime or Linux/macOS desktop
release acceptance. A descendant that deliberately changes Unix group/session,
a stopped/failed Unix guardian, and independently broker-created Windows
processes are not claimed by the containment contract. F08–F10 and all other
audit findings remain out of scope; no deployment or release was performed.

For future candidate work, see [`roadmap.md`](roadmap.md).
