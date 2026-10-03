# Repository Audit — 2026-10-03

This document records a point-in-time repository-wide audit of Imp.

Baseline: v0.2.1 plus post-release documentation/CI preparation work.

The findings below describe the repository as inspected on 2026-10-03.
They are historical audit evidence and should not be interpreted as the
current defect list after subsequent remediation.

Individual findings are addressed through later development slices rather
than editing this report retroactively.

---

# A. Executive assessment

**Imp has coherent ownership boundaries and a working build/install pipeline, but the passing gate misses several consequential failure paths.**

The audit established **11 findings: 5 Medium and 6 Low**. No Critical or High finding was established.

The priorities are:

1. Contain hostile GMCP parsing and numeric conversion without crashing adapters or leaking partial state.
2. Keep unchanged, valid state-bearing GMCP from falsely making the feed stale.
3. Isolate slow subscribers from producer ingestion and healthy subscribers.
4. Prevent gateway DEBUG logging from exposing the pairing credential.
5. Add hard-crash containment for desktop-owned child processes.

Healthy properties remained intact in the reviewed paths:

- Exact `(session, foreground, connection)` action fencing.
- One eligible consumer and one action in flight.
- No action queue, retry, or replay.
- Gateway authentication before upstream relay access.
- Client-specific lifecycle and final command dispatch outside the canonical state model.
- Failure-safe configuration persistence and installer rollback.

**No repository changes were made.** The main gate, Rust tests, packaging builds, and disposable server installation checks passed. Separate runtime probes reproduced failures that those checks do not cover.

# B. Source-derived architecture

## Runtime and ownership map

```text
MUD GMCP / received text
        │
        ├─ TinyFugue capture, world lifecycle, spool
        └─ Mudlet Lua capture, profile lifecycle, helper pipe
                         │
             integrations/common
             Record → Normalizer → RelayPublisher
                         │
                 Same-host Imp node
                 Python relay :8787
                 ├─ selected canonical state
                 ├─ retained snapshot / sequence / freshness
                 ├─ transient text
                 └─ exact-context action broker
                         │
          ┌──────────────┼─────────────────────┐
          │              │                     │
      Local HUD      SSH forward        Authenticated gateway
       :8787          local :8789         :8788 + operator TLS
          │              │                     │
          └──────────────┴─────────────────────┘
                         │
                  Desktop frontend
             StateSource + separate ActionSink
                         │
                   HUD / alerts / actions
```

Actions travel in reverse through `/action` and `/action-consumer`, ending at the client-owned trusted command API:

- **TinyFugue:** fixed bridge command, followed by the final world/context fence.
- **Mudlet:** refreshed exact-context check immediately before `send(command, false)`.

`forwarded` is adapter-specific local evidence, **not proof of MUD receipt or execution**. TinyFugue acknowledges the helper’s write/flush before the final TF fence; Mudlet acknowledges the fenced `send` call without a Lua error.

## Important boundaries

| Boundary              | Current behavior                                                                                                       |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Shared interpretation | Both adapters use `integrations/common`; observed AVATAR mappings remain MUD-specific.                                 |
| Client lifecycle      | Client integrations own authoritative selection, connection generations, capture, and final dispatch.                  |
| Node                  | Retains one selected canonical context and snapshot; sequences are process-local.                                      |
| Desktop supervision   | The packaged local node runs independently of the HUD’s selected consumption mode.                                     |
| SSH                   | Forwards the **entire relay TCP port**, including privileged producer/helper routes.                                   |
| Direct WSS            | Gateway authenticates and restricts access to state/action plus nonsensitive health.                                   |
| Loopback              | Network locality and Origin checks are not user/process authentication. Trusted local processes remain a prerequisite. |
| Settings changes      | Persisted connection changes apply on the next application start, not by hot-swapping supervisors.                     |

These transport limitations are documented design constraints, not newly discovered authentication bypasses.

## Persistence and deployment identities

| Owner                          | Stored state                                                                                                                                                    |
| ------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| WebView localStorage           | `imp.actions`, `imp.alerts`, `imp.display-mode`, `imp.theme`; `imp.alert-settings` is an intentional legacy alert migration.                                    |
| Native desktop configuration   | Historical `tunnel.json` connection configuration; Direct mode persists the client pairing token. Settings reads expose token presence, not the existing token. |
| Producer gateway configuration | `remote-access.json` / deployment gateway environment carries the digest-side configuration.                                                                    |
| Mudlet provisioning            | Versioned helper/runtime in the Imp application-data directory's `mudlet` subdirectory, `current.txt`, exported `Imp.mpackage`.                                 |
| VPS installation               | Immutable `~/.local/share/imp/releases/<version>` and `current` symlink; integration files, helper link, and user units activated around that release.          |
| TF runtime                     | Private runtime spool, ephemeral checkpoint, and private selected-context marker. Received text is not checkpointed or replayed.                                |

The current product identity is Imp. Historical TinyScry references do not establish an active migration contract.

# C. Findings

## Critical / High

**None established.**

## Medium

### F01 — Bounded hostile GMCP can crash adapters or leave partially applied state

**Confidence:** High; reproduced.

**Locations:**

- `integrations/common/src/imp_adapter/records.py:52–58,72–94`
- `integrations/common/src/imp_adapter/normalize.py:90–96,119–134,203–209`
- `integrations/tinyfugue/src/imp_tf/feed.py:278–298`
- `integrations/mudlet/src/imp_mudlet/bridge.py:122–145`

**Behavior and evidence:**

- A payload containing 1,100 nested arrays, within the input cap, raises `RecursionError` during recursive record validation.
- A quoted 5,000-digit vital raises `ValueError` from `int(...)`. An actual TinyFugue feed/publisher/relay probe terminated on that value.
- A 400-digit integer opponent percentage raises `OverflowError` from `float(...)`. The freshly built frozen Mudlet helper exited with code **1** on this input.
- Normalizer mutation is incremental. A record setting HP to `5` and then failing on an oversized mana value was rejected, but a subsequent valid record exposed **HP 5** from the rejected record.

Mudlet’s `ValueError` handler also labels numeric normalization failures as `invalid_lifecycle`; it does not undo preceding normalizer assignments.

**Consequence:** Untrusted MUD data can interrupt state production. A contained exception can still contaminate later state.

**Remediation:** Make recursive validation and numeric conversion bounded, normal rejection paths. Stage a record’s updates before committing normalizer state. Do not merely catch exceptions around an already-mutated normalizer.

---

### F02 — Repeated unchanged GMCP produces false stale-feed status

**Confidence:** High; reproduced end-to-end.

**Locations:**

- `integrations/tinyfugue/src/imp_tf/feed.py:285–298`
- `integrations/mudlet/src/imp_mudlet/runtime.py:121–138`
- `services/relay/src/imp_relay/state.py:56–73`

**Behavior:** Both adapters publish only when normalized state changes. Relay freshness advances only on a matching publish.

**Evidence:** A real Mudlet runtime, shared publisher, and relay received 20 valid unchanged `Char.Vitals` updates across a short stale window:

- Initially: `feed=live`, `producer_count=1`, `seq=2`.
- After continued unchanged updates: `feed=stale`, `producer_count=1`, `seq=2`.

**Consequence:** A healthy state-bearing feed can appear stalled. Staleness also suppresses/resets vital-alert baselines. This is **not** an action-authorization bypass; freshness is not the action fence.

**Remediation:** Refresh liveness from genuine current state-bearing input, including unchanged values. Matching identical snapshots are one straightforward option. Unknown packages and transient text must not manufacture freshness.

---

### F03 — A backpressured subscriber stalls ingest and healthy subscribers

**Confidence:** High; reproduced with real sockets.

**Locations:**

- `services/relay/src/imp_relay/server.py:173–174,242–245,268–278`

**Behavior:** Broadcast awaits each subscriber’s `send()` serially. Producer processing and freshness announcements await that broadcast.

**Evidence:** With one non-reading subscriber, one healthy subscriber, and a real publisher:

- Relay sequence remained at **1630** across repeated observations.
- Healthy subscriber remained at **1629**.
- Publisher had not completed.
- Aborting only the non-reading peer allowed relay and healthy subscriber to reach **20001**.

**Consequence:** One connected slow consumer can delay unrelated consumers and that producer’s ingestion until transport failure detection releases the blocked send. The probe does not establish a permanent stall.

**Remediation:** Bound per-peer sends and retire peers that exceed the bound, or isolate delivery with strictly bounded per-peer buffering. Do not introduce unbounded queues.

---

### F04 — Gateway DEBUG logging exposes the full pairing token

**Confidence:** High; reproduced through the packaged gateway CLI.

**Locations:**

- `services/relay/src/imp_relay/gateway_main.py:16–19`
- `services/relay/src/imp_relay/gateway_config.py:62–65`
- `docs/architecture/boundaries/trust-boundary.md:138–141`

**Behavior:** Root DEBUG logging enables WebSocket frame logging, including the first authentication frame.

**Evidence:** The freshly built node executable was started in gateway mode with `--log-level DEBUG`. Authenticating with a disposable dummy token caused its **complete plaintext value** to appear in captured gateway output.

**Consequence:** Opt-in diagnostics can persist a credential granting remote state observation and context-bound action requests. Default INFO logging did not establish this exposure.

**Remediation:** Keep raw WebSocket authentication frames out of logs, including when application debugging is enabled. Verify using an actual authentication exchange. Rotate any real token captured in existing DEBUG logs.

---

### F05 — Desktop-owned children lack hard-crash containment

**Confidence:** High for source ownership gap; native crash outcome remains partly inferred.

**Locations:**

- `apps/desktop/src-tauri/src/lib.rs:146–159`
- `apps/desktop/src-tauri/src/node.rs:117–125,135–140,251–312`
- Corresponding child spawning/shutdown paths in `gateway.rs` and `tunnel.rs`

**Behavior:** Explicit cleanup runs on orderly Tauri `RunEvent::Exit`. The reviewed spawning paths do not establish OS-enforced child termination on abrupt parent loss.

**Evidence:** A disposable Rust `std::process::Command` launcher started the freshly frozen node with piped output. After killing the parent with SIGKILL, the child’s health endpoint remained available. The recorded child was then explicitly cleaned up.

This exercises the underlying process-lifetime behavior, **not a full Tauri crash**. The shell plugin’s documented process path uses ordinary child spawning and explicit termination: [tauri-plugin-shell source](https://docs.rs/tauri-plugin-shell/2.3.6/src/tauri_plugin_shell/process/mod.rs.html), [shared_child source](https://docs.rs/shared_child/1.1.2/src/shared_child/lib.rs.html).

**Consequence:** `[INFERENCE]` An abrupt desktop failure can leave owned services/listeners behind. The next desktop deliberately refuses an existing node listener, so this can prevent normal local-node startup.

**Remediation:** Bind owned children to application lifetime using appropriate platform containment. Preserve the rule that unowned/adopted endpoints are not killed. Verify forced termination and relaunch on Windows.

## Low

### F06 — Oversized JSON integers escape the Python protocol decoder

**Confidence:** High; reproduced at the server boundary.

**Locations:**

- `services/relay/src/imp_relay/protocol.py:248–258,340–346`
- `services/relay/src/imp_relay/server.py:226–240`

**Behavior:** `math.isfinite()` converts arbitrary Python integers before range rejection.

**Evidence:** A frame containing a 400-digit integer raised `OverflowError`. An actual `/action-consumer` connection closed with **1011**, rather than the normal invalid-frame policy close **1008**.

**Consequence:** Malformed input becomes an internal handler failure. It did not mutate retained state or terminate the relay process.

**Remediation:** Range-check integers without floating-point conversion; apply finiteness checks to floats.

**Rejected claim:** Deep nesting in an ignored protocol field did **not** reproduce a protocol decoder failure on the audited Python 3.12 runtime, including depth 6,000. F01 concerns the adapter’s recursive validation, not that disproved protocol-parser claim.

---

### F07 — Infinite configuration durations defeat finite bounds

**Confidence:** High; configuration acceptance and freshness consequence reproduced.

**Locations:**

- `services/relay/src/imp_relay/config.py:22,29–30`
- `services/relay/src/imp_relay/state.py:31–34,68–73`
- `services/relay/src/imp_relay/gateway_config.py:51–54,74–75`

**Evidence:** Both `--stale-after inf` and `--auth-timeout inf` were accepted. After publishing, a connected relay remained `live` at a simulated billion seconds after its last publish.

**Consequence:** Unusual operator configuration can disable stale detection or remove the intended finite pre-authentication deadline. Defaults are unaffected.

**Remediation:** Require finite, positive durations at configuration and constructor boundaries.

---

### F08 — Shared HUD feedback incorrectly identifies TinyFugue

**Confidence:** High; rendered-module behavior checked.

**Locations:**

- `apps/desktop/src/lib/action/presentation.ts:3,7,19–20`
- `apps/desktop/src/components/Hud.svelte:786`

**Evidence:** The shared presentation function returns “Forwarded to TinyFugue” for every successful action, including the Mudlet path. Missing-context guidance also names TinyFugue. The rejection branch still matches the obsolete `no matching TinyFugue consumer` detail, while the broker emits a client-neutral detail.

**Consequence:** Mudlet operators receive incorrect destination and troubleshooting information. Dispatch fencing itself is unaffected.

**Remediation:** Use client-neutral shared feedback and the current rejection contract. No new client metadata is needed.

---

### F09 — Custom radio groups omit expected keyboard behavior

**Confidence:** High; reproduced in the actual browser surface.

**Locations:**

- `apps/desktop/src/components/SettingsPanel.svelte:41–54,61–75`
- The same pattern in connection-mode and alert-choice controls.

**Evidence:** After selecting Dark and pressing ArrowRight:

- Focus remained on Dark.
- Dark remained selected.
- All five display/theme radio-role buttons had `tabIndex=0`.

**Consequence:** Controls announce radio semantics without radio keyboard navigation. Tab and button activation remain usable.

**Remediation:** Prefer native radio inputs, or implement standard arrow navigation and roving focus. Verify the actual DOM rather than only pure helper functions.

---

### F10 — The TinyFugue acceptance procedure calls a removed factory

**Confidence:** High; browser module exports checked.

**Locations:**

- `integrations/tinyfugue/README.md:315–323`
- `apps/desktop/src/lib/config.ts:103–147`

**Evidence:** The documented `createActionSink` export is absent. The current export is asynchronous `createRuntimeClients`, whose result contains `actionSink`.

**Consequence:** The prescribed production-sink acceptance setup fails before the action checks can run.

**Remediation:** Update the procedure to the current asynchronous runtime factory. Do not restore an obsolete API solely to preserve the snippet.

---

### F11 — Relay hello advertises the obsolete implementation version

**Confidence:** High; actual hello checked.

**Locations:**

- `services/relay/src/imp_relay/server.py:55,289`
- Relay CLI startup and package version metadata.

**Evidence:** Repository/package version is **0.2.1**, but a running relay advertised:

```json
{ "name": "Imp relay", "version": "0.1.0" }
```

**Consequence:** Handshake diagnostics misidentify the running implementation, complicating release and incident correlation. Protocol version remains 2.

**Remediation:** Obtain the implementation version from authoritative package/build metadata rather than a stale default literal.

# D. Cross-cutting assessment

| Area               | Assessment                                                                                                                                                                              |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Architecture       | Sound core separation. Client lifecycle/final dispatch, shared interpretation, canonical node state, transport, and presentation have distinct owners.                                  |
| State correctness  | Context and sequence fencing are strong. Freshness conflates state change with continued observation; malformed-record mutation is not atomic.                                          |
| Actions            | No demonstrated retargeting, replay, or retry defect. Keep the distinction between local forwarding evidence and MUD execution.                                                         |
| Transport/security | Gateway authentication and route filtering held in local probes. Loopback and SSH trusted-host limitations are intentional. DEBUG credential logging is the concrete credential defect. |
| Lifecycle          | Orderly shutdown and reconnect paths are covered better than abrupt process loss.                                                                                                       |
| Error handling     | Normal invalid inputs produce bounded errors; numeric/recursive extremes escape those paths.                                                                                            |
| Observability      | Feed/socket distinction is useful. Wrong implementation version, wrong client wording, and raw DEBUG frames undermine diagnostics.                                                      |
| MUD neutrality     | Client-neutral architecture is real; universal MUD support is not. Mudlet currently captures the documented Status/Vitals packages.                                                     |
| Packaging/release  | Locked builds, immutable releases, inventory/checksum validation, exact-tag release flow, and rollback are coherent. No concrete packaging defect survived review and execution.        |
| Dependencies       | Existing dependencies/platform capabilities suffice for the identified fixes. This audit does not establish that all dependencies are CVE-free.                                         |

**Documented operational limitation:** A TinyFugue feed restart discards selected-action authority. Fresh GMCP alone does not restore it; a fresh client-owned selection event is required. This was reproduced and is explicitly recorded in `docs/status.md:554–557`. Do not “fix” recovery by trusting checkpointed selection as fresh authority. If automatic recovery is desired, it needs fresh evidence from TinyFugue.

# E. Test coverage and gaps

## What the existing checks protect well

- Shared TypeScript/Python protocol fixture conformance.
- Context replacement, retained snapshots, process-local sequence handling.
- Action busy rejection, context mismatch, consumer replacement, and ambiguous disconnect.
- Publisher reconnect and selection reassertion.
- TF spool rotation, partial writes, checkpoint/session boundaries.
- Gateway authentication and upstream isolation.
- Desktop reducers, action/alert/persistence helpers.
- Native configuration validation and supervisor decisions.
- Server bundle inventory, installation idempotence, and rollback.

The cross-component check uses **real WebSockets in-process**. It is meaningful integration coverage, but not full client/native/end-user acceptance.

## Missing behavioral protection

1. Bounded nesting and extreme numeric GMCP, including continued processing and rejected-record atomicity.
2. Repeated identical state-bearing input across the stale threshold.
3. Non-reading subscriber isolation.
4. Credential-safe DEBUG authentication logging.
5. Forced desktop termination followed by relaunch.
6. Oversized protocol integers yielding normal rejection.
7. Nonfinite configuration values.
8. Actual radio keyboard interaction and rendered feedback.
9. Executable acceptance documentation.

Additional maintenance risks:

- Most shared normalization regression cases remain under TinyFugue tests; a common-only test selection omits them. The root gate does run both.
- Python tests do not execute Mudlet Lua’s final dispatch fence or the TF macro interpreter.
- Frontend tests use the Node environment, not Svelte DOM interaction.
- Manual native/client acceptance is valuable evidence, but not an automated regression tripwire.
- Action deadline/late-result and gateway cancellation transitions deserve focused checks; no current supported-client defect was established from their absence.

# F. Documentation and implementation drift

- **Credential logging promise:** trust-boundary documentation says plaintext pairing tokens never enter logs; F04 disproves that under DEBUG.
- **Acceptance snippet:** TinyFugue README uses a removed factory; F10.
- **Client-neutral presentation:** current Mudlet support conflicts with TinyFugue-only shared feedback; F08.
- **Protocol comments:** `packages/protocol/src/messages.ts:3,11` still describes the canonical context as TinyFugue-specific. Similar stale ownership language remains in state/limits comments.
- **Subscriber isolation:** the relay card’s “one dead subscriber cannot break delivery to the others” is supported for closed peers, but does not establish isolation from connected slow peers; F03.
- **Version diagnostics:** hello metadata is inconsistent with the current release; F11.
- **Specification precision:** the specification could explicitly state timestamp units and root character/target omission behavior accepted by both decoders.
- **Historical evidence:** older restart and port observations must not be treated as current acceptance. Later status entries explicitly supersede several of them.

The architecture routing table and current client-neutral topology broadly match the implementation. No parallel architecture or compatibility shim is warranted.

# G. High-confidence dead/stale candidates

| Candidate                           | Evidence                                                                                           | Disposition                                                |
| ----------------------------------- | -------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| `boundedThresholdPercent`           | `apps/desktop/src/lib/alerts/definitions.ts:169–172`; reference lookup found only the declaration. | Remove if no current consumer is introduced.               |
| `setAlwaysOnTop` wrapper            | `apps/desktop/src/lib/window.ts:49–51`; reference lookup found only the declaration.               | Remove; current topmost behavior is established elsewhere. |
| TinyFugue-specific rejection branch | `action/presentation.ts:19–20`; broker emits a different current detail.                           | Remove/update during F08.                                  |
| Removed factory in README           | F10.                                                                                               | Replace the procedure, not the API.                        |

Not classified as dead:

- Legacy alert migration.
- Historical native configuration filename.
- TF normalized-record bridge tooling.
- HUD diagnostic model fields merely because the current component does not display them.
- Client-specific lifecycle/fence implementations.

# H. Commands, runtime probes, and results

## Repository validation

| Command/check                                      | Result                                                   |
| -------------------------------------------------- | -------------------------------------------------------- |
| `npm run check`                                    | **PASS**                                                 |
| Protocol TypeScript tests                          | **62 passed**                                            |
| Desktop tests                                      | **149 passed**                                           |
| Relay Python tests                                 | **125 passed**                                           |
| Common Python tests                                | **3 passed**                                             |
| Mudlet Python tests                                | **25 passed**                                            |
| TinyFugue Python tests                             | **89 passed**                                            |
| Svelte check                                       | **0 errors, 0 warnings**                                 |
| Root lint/docs/version/type checks                 | **PASS**                                                 |
| Cross-component WebSocket check                    | Published state and retained reconnect snapshot **PASS** |
| Frontend production build                          | **PASS**                                                 |
| `cargo fmt --check` against isolated source copy   | **PASS**                                                 |
| `cargo test --locked` against isolated source copy | **66 passed**                                            |

## Packaging and installation

Run from an isolated source copy, with outputs outside the repository:

- `scripts/build-server-bundle.sh` — **PASS**.
- `scripts/test-server-bundle.sh <archive>` — **PASS**, including disposable installation/idempotence/rollback acceptance.
- `scripts/build-desktop-node.py` — **PASS**.
- `scripts/build-mudlet-package.py --package-only` — **PASS**, using Muddler.
- `scripts/build-mudlet-package.py --desktop-resource --require-package` — **PASS**.

Warnings, separate from errors:

- Disposable installer checks warned that user lingering was not enabled.
- `uv` fell back from hardlinks to copying during isolated helper builds.
- No build/test error remained.

## Targeted runtime results

| Scenario                         | Observed result                                                        |
| -------------------------------- | ---------------------------------------------------------------------- |
| Nested adapter payload           | `RecursionError`                                                       |
| Extreme TF vital                 | Feed terminated with `ValueError`                                      |
| Extreme frozen Mudlet percentage | Helper exited 1 with `OverflowError`                                   |
| Failed-record atomicity          | Rejected HP value appeared later                                       |
| Unchanged valid GMCP             | Feed became stale despite continued input                              |
| Slow state subscriber            | Ingest/healthy-peer progress stalled; recovered after bad peer removal |
| Packaged gateway DEBUG auth      | Complete dummy token logged                                            |
| Oversized protocol integer       | Actual connection closed 1011                                          |
| Infinite duration configuration  | Accepted; stale detection defeated                                     |
| Rust parent killed abruptly      | Frozen node listener survived; explicitly cleaned up                   |
| Browser radio ArrowRight         | Focus/selection did not advance                                        |
| Browser factory/feedback         | Removed export absent; TinyFugue-only feedback confirmed               |

Also checked real local routes: privileged node access without Origin succeeded as designed; browser-Origin privileged access returned **403**; gateway privileged route returned **404**.

**Limits:** No live MUD command was sent. No real VPS deployment or release was performed. Windows NSIS installation, a full Windows Tauri crash, and real TF/Mudlet interpreter behavior were not exercised here. Linux/WSL builds do not replace those acceptance checks.

Disposable source trees, probe programs, and processes were removed. Validation logs remain outside the repository under `/tmp/imp-audit-*.log`.

# I. Remediation slices

| Slice                              | Scope                                                                                                                               | Dependencies                                                    | Priority before feature work                              |
| ---------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- | --------------------------------------------------------- |
| **1. Input-boundary correctness**  | F01, F06, F07: bounded conversion/validation, atomic normalization, ordinary rejection paths.                                       | None.                                                           | **Yes**, before expanding ingest/MUD support.             |
| **2. Freshness correctness**       | F02: unchanged current state-bearing input refreshes liveness without promoting unknown/text traffic.                               | Independent; easier after slice 1 hardens acceptance semantics. | **Yes**, before alert/feed features.                      |
| **3. Subscriber isolation**        | F03: bounded slow-peer handling, healthy-peer and ingest progress check.                                                            | None.                                                           | **Yes**, before broader multi-consumer use.               |
| **4. Credential-safe diagnostics** | F04 and F11: safe DEBUG logging and authoritative implementation version.                                                           | None.                                                           | **Yes** for diagnostic/remote-access work.                |
| **5. Native crash ownership**      | F05: OS-appropriate lifetime containment for owned children; forced-exit/relaunch acceptance.                                       | None.                                                           | **Yes**, before broader desktop release confidence.       |
| **6. UI and acceptance contract**  | F08–F10: neutral feedback, native/complete radio behavior, current acceptance setup. Remove the obsolete rejection branch with F08. | None.                                                           | Before client expansion; accessibility fix need not wait. |

Each slice can be reviewed and verified independently. No generic retry framework, new transport abstraction, compatibility factory, or dependency is needed.

# J. Durable memory update

Submitted one newly established ownership/convention fact for retention:

- Connection saves intentionally apply next start.
- `tunnel.json` remains an intentional historical identity.
- WebView preferences are separate from native connection credentials.
- `imp.alert-settings` is intentional migration state.

Audit findings were **not** stored as durable architecture facts.

# K. Final repository state

Executed:

```text
git status --short
```

**Exact stdout:** empty.  
**Exit code:** `0`.  
**Stderr:** empty.

All **347 tracked files** matched their pre-audit SHA-256 values. No tracked or untracked source change remains. Validation may have refreshed ignored build/cache outputs. No fixes, tests, documentation edits, commits, releases, or deployments were made.

PONYTAIL: PASS — Read-only audit; no implementation additions or scope expansion.
Nothing to cut; disposable probes and isolated build trees were removed.
