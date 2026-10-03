# Roadmap

Tracks completed implementation slices, the current selection point, and
provisional work queued behind it.

- What exists today, and the evidence behind it, lives in
  [`status.md`](status.md). If a claim appears in both files, `status.md` wins.
- The active slice may record remaining scope while implementation is underway.
- Candidate work remains provisional. Live evidence that exposes a more urgent
  defect can reorder it; a roadmap entry is not a commitment to build it next.

No dates. Planned work describes intent rather than behavior; implemented
claims belong in `status.md` and the relevant architecture documents.

## Progress

| Work                                                  | Status      | Historical marker                     |
| ----------------------------------------------------- | ----------- | ------------------------------------- |
| Slice 1 - Bootstrap baseline                          | ✅ Complete | `bootstrap-baseline`                  |
| Slice 2 - Real GMCP end to end                        | ✅ Complete | `real-gmcp-e2e`                       |
| Post-Slice 2 - Managed runtime implementation         | ✅ Complete | `managed-runtime`                     |
| Slice 3 - CI baseline                                 | ✅ Complete | `ci-baseline`                         |
| Slice 4 - Managed runtime verification                | ✅ Complete | `managed-runtime-e2e`                 |
| Slice 5 - HUD UI refinement                           | ✅ Complete | `hud-ui-refinement`                   |
| Slice 6 - Alerts and window polish                    | ✅ Complete | `alerts-window-polish`                |
| Slice 7 - Trusted outbound actions                    | ✅ Complete | `trusted-outbound-actions`            |
| Slice 8 - Configurable action UI                      | ✅ Complete | `configurable-action-ui`              |
| Slice 9 - Managed tunnel takeover                     | ✅ Complete | `managed-tunnel-takeover`             |
| Post-Slice 9 - Desktop polish and runtime hardening   | ✅ Complete | `desktop-polish`; later work untagged |
| Slice 10 - Configurable notification triggers         | ✅ Complete | `configurable-notification-triggers`  |
| Slice 11 - Authenticated WSS transport                | ✅ Complete | `authenticated-wss-transport`         |
| Slice 12 - Native connection settings                 | ✅ Complete | `native-connection-settings`          |
| Slice 13 - First release readiness                    | ✅ Complete | `first-release-readiness`             |
| Slice 14 - Imp rename                                 | ✅ Complete | `imp-rename`                          |
| Slice 15 - Server installation / bootstrap            | ✅ Complete | `server-install-bootstrap`            |
| Post-Slice 15 - Public release and release automation | ✅ Complete | `v0.1.0`; later work untagged         |
| Slice 16 - Mudlet and local client integration        | ✅ Complete | `mudlet-local-integration`            |
| Slice 17 - Input boundary correctness                 | Complete    | Untagged                              |

Slice numbers and milestone tags are development-history markers. They have no
relationship to release versions. Release versions are derived independently
from Conventional Commits as documented in [`releases.md`](releases.md).

## ✅ Slice 1 - Bootstrap baseline

Milestone tag: `bootstrap-baseline`. Complete.

Slice 1 established the first complete Imp architecture, then still named
TinyScry, around a deliberately narrow protocol and loopback-only relay.

Delivered:

- protocol version 1 with bounded types, fail-closed decoding, and a shared
  fixture corpus;
- the loopback relay with `/state`, `/ingest`, `/healthz`, retained state, and
  producer freshness tracking;
- the first Svelte HUD with mock and relay state sources;
- the TinyFugue-side adapter, normalizer, publisher, bridge, replay tooling, and
  deterministic fixtures;
- stale/down/reconnecting presentation so retained values were never presented
  as live; and
- the first fixture-driven producer -> relay -> HUD end-to-end path.

The native Tauri shell existed and compiled, but real GMCP capture and native
runtime behavior were intentionally still unverified. Those became the next
slice rather than being guessed from fixtures.

## ✅ Slice 2 - Real GMCP end to end

Milestone tag: `real-gmcp-e2e`. Complete.

Slice 2 replaced fixture assumptions with observed MUD behavior and proved the
desktop path on native Windows.

Delivered and verified:

- real TinyFugue GMCP capture from the target MUD;
- checked conversion into a redacted reproducible fixture;
- observed-only normalization for the GMCP packages actually seen in live play;
- safe rejection of malformed inventory material rather than speculative
  parsing;
- live identity, vitals, target acquisition, target damage, and target clearing;
- the manual SSH-forward path from the VPS relay to the Windows desktop; and
- native Tauri launch, always-on-top behavior, dragging, resizing,
  transparency, WebView updates, and transport lifecycle presentation.

This slice also fixed the bridge event-loop starvation defect that prevented a
producer from recovering after relay restart.

## ✅ Post-Slice 2 - Managed runtime implementation

Milestone tag: `managed-runtime`. Complete.

After Slice 2 proved the real GMCP path, Imp gained the production-shaped
runtime needed to stop relying on a manually maintained SSH forward.

This unnumbered implementation milestone added:

- managed SSH mode in the Tauri backend using the system OpenSSH executable
  directly by argv;
- explicit child ownership, supervision, bounded retry, and clean shutdown;
- External SSH remaining available as a supported mode;
- the private TinyFugue spool and feed runtime;
- per-user systemd units for the VPS relay and feed; and
- deployment and runtime documentation for the new process boundaries.

The implementation deliberately stopped short of claiming production runtime
acceptance. Live managed-child recovery, user-systemd behavior, reboot safety,
and post-reboot identity bootstrap were left for Slice 4.

## ✅ Slice 3 - CI baseline

Milestone tag: `ci-baseline`. Complete.

Slice 3 established the deterministic GitHub Actions baseline around the
repository's canonical `npm run check` gate.

CI verifies the protocol, frontend, relay, TinyFugue integration, fixtures, and
loopback end-to-end behavior without pretending to replace evidence that needs
a native platform, real VPS processes, interactive TinyFugue, or a real MUD
session.

Native Rust checks and operator-controlled live verification therefore remained
separate responsibilities. The next numbered slice returned to the managed
runtime and verified that production-shaped path end to end.

## ✅ Slice 4 - Managed runtime verification

Milestone tag: `managed-runtime-e2e`. Complete.

Slice 4 turned the managed runtime from an implemented design into a
production-shaped, live-verified path and fixed the defects that acceptance
exposed.

The work included:

- making native Tauri builds default to the real relay source rather than the
  browser mock source;
- live managed-SSH supervision and recovery on Windows;
- VPS user-systemd lifecycle and reboot verification;
- private runtime spool/checkpoint behavior without stale-state replay;
- TinyFugue startup integration and verified GMCP identity bootstrap;
- requiring the operator TinyFugue build to provide the `GMCP_LOGIN`
  capability used by the login flow;
- hardening target lifecycle across combat transitions and feed restarts; and
- changing the observational TinyFugue GMCP capture hook to priority 2 with
  fall-through so it runs ahead of ordinary priority-1 handlers without
  consuming their event.

The live work also exposed two issues that remain explicitly deferred today:
very-fast character/world transitions can still lose authoritative identity,
and some raw ANSI/control-byte GMCP material is still rejected rather than
silently sanitized.

## ✅ Slice 5 - HUD UI refinement

Milestone tag: `hud-ui-refinement`. Complete.

Slice 5 turned the initial functional HUD into the compact/expanded native
desktop surface used by later features. It refined layout, sizing, interaction,
and native-window behavior while keeping freshness and retained-state semantics
owned by the existing state model rather than presentation code.

The resulting HUD was exercised in browser/mock development and on the native
Windows Tauri runtime before alerts and outbound controls were layered onto it.

## ✅ Slice 6 - Alerts and window polish

Milestone tag: `alerts-window-polish`. Complete.

Slice 6 added the first desktop-local alert behavior and closed several native
window rough edges.

Delivered:

- low-health alert evaluation;
- independently configurable bundled sound and native-notification effects;
- validated persisted alert preferences and Settings presentation;
- native sizing refinements; and
- a non-maximizable HUD so an accidental maximize could no longer create a
  transparent full-screen input surface.

These alerts remained presentation-only behavior. They did not cross the
outbound command boundary.

## ✅ Slice 7 - Trusted outbound actions

Milestone tag: `trusted-outbound-actions`. Complete.

Slice 7 introduced outbound commands as a protocol-level capability rather than
letting UI code write arbitrary material toward TinyFugue.

It was a protocol-v2 cutover adding:

- exact `(session, foreground, connection)` context on selected state;
- per-world normalization and session-aware ephemeral checkpoints;
- endpoint-specific relay Origin policy;
- a one-consumer, one-in-flight action broker with no retry or replay;
- a strict private context marker and fixed TinyFugue macro boundary; and
- a separate desktop `ActionSink` abstraction with a network-free mock.

The connectionless acceptance procedure verified generation fences, helper
replacement, context pinning, reader loss, relay recovery, and no replay.
`forwarded` deliberately means only that the fixed TinyFugue bridge accepted
and flushed the command; it does not claim MUD execution.

## ✅ Slice 8 - Configurable action UI

Milestone tag: `configurable-action-ui`. Complete.

Slice 8 put the trusted action path behind user-defined desktop controls.

Delivered:

- validated, ordered persisted action definitions;
- create/edit/delete management in the desktop UI;
- compact and expanded action surfaces;
- bounded definition counts and preserved command text;
- exact-context `ActionSink` invocation; and
- result wording that does not overstate `forwarded`.

Live acceptance sent an approved `look` through the real desktop -> relay ->
TinyFugue -> MUD path exactly once. Removing the matching consumer rejected the
action without execution, restoring it replayed nothing, and a new action then
executed once.

## ✅ Slice 9 - Managed tunnel takeover

Milestone tag: `managed-tunnel-takeover`. Complete.

Slice 9 made managed SSH coexist correctly with an already-running relay
forward.

Managed mode now:

- recognizes and adopts an existing healthy Imp relay endpoint;
- monitors that endpoint without owning or signalling it;
- waits when the port belongs to something else;
- takes over by spawning its own supervised SSH child once an adopted endpoint
  disappears and the port becomes free; and
- preserves the established shutdown and ownership rules.

Windows-native acceptance started with a manual SSH forward, observed Imp adopt
it without conflict, terminated that forward, and verified that the same
running desktop took over the tunnel without an application restart.

## ✅ Post-Slice 9 - Desktop polish and runtime hardening

The `desktop-polish` milestone tag marks the initial polish pass. Later
lifecycle and tray hardening in this unnumbered interval was not separately
tagged.

A set of focused unnumbered improvements followed Slice 9 before notification
work began.

The initial polish pass added Dark, Light, and System themes; Lucide icon
controls; improved compact and expanded presentation; runtime action-strip
measurement; tighter Settings sizing; and keyboard, focus, and accessibility
cleanup.

Follow-up live work then hardened behavior outside that visual milestone:

- TinyFugue connection generations became `CONNECT`-owned rather than being
  reset again by `GMCP_LOGIN`;
- character/world switching stopped starting duplicate action consumers or
  temporarily selecting stale connection generations;
- the native HUD reinforced its always-on-top lifecycle and disabled
  inapplicable Windows minimize/system-menu behavior;
- a tray icon added temporary alert muting and clean quit; and
- the desktop began restoring its previous screen position while preserving
  frontend-owned dynamic sizing.

Freshness remained represented by the status model rather than by fading or
otherwise making retained HUD data ambiguous.

## ✅ Slice 10 - Configurable notification triggers

Slug: `configurable-notification-triggers`. Complete.

Slice 10 generalizes the original low-health rule into persisted configurable
vital and received-text alerts. It includes transient selected-context text
delivery, configurable sound/notification effects, quick enable/disable
controls, and compact/expanded Bell surfaces.

Text alerts support two intentionally bounded modes:

- Contains: literal substring matching, including literal `*`.
- Wildcard: whole-line matching where only `*` is special and means zero or
  more characters.

Case sensitivity is independent of match mode. Existing text definitions
without an explicit match mode migrate to Contains. Unknown modes are rejected.

Wildcard matching remains data-only and bounded. Slice 10 introduces no shell,
`eval`, command synthesis, regular-expression engine, captures, retained text,
or automatic text-triggered outbound actions.

More advanced pattern matching and capture semantics remain future work and
should receive their own design before any reaction/automation feature crosses
the outbound trust boundary.

Implemented behavior and final verification evidence live in
[`status.md`](status.md) and
[`architecture/objects/desktop-alerts.md`](architecture/objects/desktop-alerts.md).

## ✅ Slice 11 - Authenticated WSS transport

Slug: `authenticated-wss-transport`. Complete.

Slice 11 made SSH optional for desktop-to-VPS transport without exposing the
existing relay or its privileged TinyFugue-facing endpoints.

Implemented behavior follows
[`0010-authenticated-remote-gateway.md`](architecture/decisions/0010-authenticated-remote-gateway.md):

- the relay remains permanently loopback-only and unauthenticated;
- a separate loopback `imp-gateway` exposes only state and action;
- pairing-token authentication completes before the gateway contacts the relay;
- Direct desktop mode requires `wss:` while external/manual and managed SSH
  remain supported;
- the gateway stores only the SHA-256 digest of the 256-bit pairing token;
- the plaintext token is native desktop configuration rather than URL,
  WebView-storage, build-time, or log material;
- the gateway retains no state/text and performs no action retry or replay.

Deterministic acceptance passed. Windows-native live acceptance over a
publicly trusted TLS endpoint verified Direct WSS state, live state updates,
outbound action delivery, reconnect after gateway interruption, and switching
back to managed SSH without relay/feed changes. A separate live workstation
WSS probe verified wrong-token rejection without exposing state.

Detailed evidence lives in [`status.md`](status.md) and
[`architecture/processes/managed-runtime.md`](architecture/processes/managed-runtime.md).

At Slice 11 completion, VPS installation and general TinyFugue installation
workflow were still deferred; Slice 15 later added the packaged server installer.
Reverse-proxy/certificate setup and pairing-token generation/rotation UX remain
separate from the transport itself.

## ✅ Slice 12 - Native connection settings

Slug: `native-connection-settings`. Complete.

Slice 12 made External SSH, Managed SSH, and authenticated Direct WSS
configurable through Imp's native Settings UI rather than requiring
operators to edit `tunnel.json` by hand.

Implemented behavior includes:

- a renderer-safe settings-read API that never returns an existing plaintext
  Direct pairing token merely to populate the form;
- validated and canonical per-mode persistence that removes fields and
  credentials owned by other modes;
- explicit Direct-token replacement or preservation without silently inventing
  credentials;
- failure-safe native configuration replacement that preserves the previous
  usable configuration when validation or persistence fails;
- restart-only activation, leaving the established runtime transport lifecycle
  unchanged; and
- browser/mock development remaining independent of native settings commands.

Windows-native acceptance configured Managed -> Direct -> Managed entirely
through the UI. Each saved mode was activated by restarting Imp and
verified with live state plus an outbound action. Direct mode owned no local
SSH listener, and returning to Managed removed the persisted Direct URL and
pairing token.

Detailed implementation and verification evidence lives in
[`status.md`](status.md),
[`architecture/processes/managed-runtime.md`](architecture/processes/managed-runtime.md),
and
[`architecture/boundaries/trust-boundary.md`](architecture/boundaries/trust-boundary.md).

VPS installation was later delivered in Slice 15. Reverse-proxy/certificate
provisioning, server-side pairing-token generation/rotation UX, live transport
hot-switching, and multi-user credentials remain separate future work.

## ✅ Slice 13 - First release readiness

Slug: `first-release-readiness`. Complete.

Slice 13 established the release-engineering boundary needed for Imp's first
distributable Windows build, before the subsequent product rename.

Delivered:

- the repository's MIT license and changelog;
- a single checked release-version contract across version-bearing manifests;
- native application version reporting owned by Tauri;
- reproducible Windows x64 NSIS packaging;
- the initial tag-driven GitHub release workflow;
- installation documentation for released Windows artifacts; and
- clean-install Windows acceptance covering live state, an approved outbound
  action, uninstall, reinstall, and managed-SSH lifecycle behavior.

Release acceptance also exposed Windows-specific managed-SSH console and
orphaned-child defects, which were fixed and reverified.

The original TinyScry `v0.1.0` tag and prerelease created during this work were
later withdrawn during Slice 14. The final Imp `v0.1.0` release was published
only after the rename and the packaged server work in Slice 15.

The current release/versioning contract is no longer owned by this historical
slice; it lives in [`releases.md`](releases.md).

## ✅ Slice 14 - Imp rename

Slug: `imp-rename`. Complete.

Slice 14 renamed TinyScry to **Imp — Interactive MUD Peripheral** across the
desktop, packages, Python commands, configuration/state paths, systemd units,
protocol identity, documentation, repository, and release-facing artifact
names.

The rename preserved the existing architecture and trust boundaries. The
canonical WebSocket protocol uses JSON with `protocol: 2`; `IMP2` is the
TinyFugue spool event marker and `IMPCTX 2` is its private context marker.
The native application identifier is `dev.imud.imp`.

The historical TinyScry `v0.1.0` prerelease/tag was removed rather than
retained as Imp's first release. The descriptive `imp-rename` milestone tag
records Slice 14 independently of the final Imp `v0.1.0` release.

## ✅ Slice 15 - Server installation / bootstrap

Slug: `server-install-bootstrap`. Complete.

Slice 15 removes the VPS source-checkout/development-toolchain requirement from
the normal release installation path.

Delivered:

- versioned `imp-server-<version>-linux-x86_64.tar.gz` release bundles;
- an adjacent archive SHA-256 file plus an internal checksum manifest;
- wheels for `imp-relay`, `imp-tinyfugue`, and the exact locked CPython 3.12
  `websockets` dependency;
- a per-user installer using
  `~/.local/share/imp/releases/<version>/` and an atomic `current` link;
- stable relay, feed, gateway, and TinyFugue action-helper paths independent of
  a repository checkout;
- optional idempotent TinyFugue startup-file integration with backup;
- relay/feed activation by default while Direct WSS remains opt-in;
- installation-wide rollback if activation fails;
- bundle acceptance covering clean install, reinstall idempotency, package
  versions, permissions, tamper rejection, and failed-activation rollback;
- Linux bundle build/acceptance in CI; and
- tag-driven publication of the server archive and checksum alongside the
  Windows NSIS artifact.

The reusable bundle acceptance passed on the target VPS environment. The
production VPS was then cut from the development checkout runtime to the exact
accepted packaged `0.1.0` server bundle. Relay, feed, gateway, TinyFugue state
capture, Managed SSH desktop state delivery, and an approved outbound `look`
action all passed from the versioned installed runtime.

The live cutover also exercised an already-running TinyFugue session. Restarting
the feed intentionally cleared its selected-context marker; a fresh TinyFugue
world-selection event re-established the private context and packaged action
consumer before outbound actions resumed.

Slice 15 is complete. The descriptive `server-install-bootstrap` milestone
tag records this work independently of the semantic `v0.1.0` release.

Direct WSS gateway provisioning, TLS/certificate automation, pairing-token
generation/rotation UX, TinyFugue installation itself, and MUD/client
generalization remain outside this slice.

## ✅ Post-Slice 15 - Public release and release automation

No numbered slice was assigned to the release/publication work that followed
Slice 15. The `v0.1.0` tag marks the first public Imp release; the subsequent
public-repository and release-automation work was untagged development history.

Completed work includes:

- publication of Imp `v0.1.0` as the first public alpha release;
- Windows x64 NSIS and Linux x86_64 server release artifacts;
- public-repository contribution and release documentation;
- Conventional Commit validation for release semantics;
- automatic semantic-release execution after successful CI on `main`;
- CI-generated SSH-signed release metadata commits and annotated version tags;
  and
- reusable tag-based artifact verification and GitHub Release publication.

The current release contract and recovery procedure live in
[`releases.md`](releases.md).

## Completed work

### Slice 16 - Mudlet and local client integration

Slug: `mudlet-local-integration`. Complete.

Slice 16 makes MUD-client choice independent from where the Imp UI runs.

The architectural rule is that a MUD-client adapter talks only to an Imp node
on the same host. Remote access belongs to Imp's state/action transport rather
than to the adapter.

The target topologies are:

1. **Mudlet local:** Mudlet -> local Imp node -> local Imp desktop.
2. **Mudlet remote:** Mudlet -> Imp node on machine A -> authenticated Imp
   transport -> Imp desktop on machine B.
3. **TinyFugue local:** TinyFugue -> local Imp node -> local Imp desktop.
4. **TinyFugue remote:** the existing VPS TinyFugue -> Imp node -> SSH or
   authenticated WSS -> desktop path remains supported.

This slice must preserve the existing separation between normalized state,
trusted actions, and transport. Mudlet-specific concepts must stop at its
adapter boundary, just as TinyFugue-specific concepts do today.

Required outcomes:

- define a client-neutral local Imp-node boundary;
- provide a local-node runtime suitable for a normal desktop installation,
  without requiring a source checkout;
- support the existing TinyFugue producer/action integration against a local
  node;
- add a Mudlet integration that observes GMCP and received text locally and
  publishes normalized Imp state through that node;
- map Mudlet lifecycle into the existing exact-current context guarantees
  without exposing Mudlet-specific identifiers downstream;
- deliver trusted outbound actions through Mudlet with the existing
  no-queue, no-retry, and no-replay guarantees;
- preserve the existing remote SSH and authenticated-WSS paths so a node beside
  Mudlet can be consumed by Imp on another machine; and
- live-verify local Mudlet, remote Mudlet, local TinyFugue, and the existing
  remote TinyFugue topology.

The slice does **not** implement Imp-to-Imp chaining. A future desktop or mobile
Imp may consume one node and expose that state to another Imp peer, so this
slice must avoid a design that prevents that later topology.

MUD-specific GMCP interpretation is client-neutral. Mudlet, TinyFugue, and
future MUD-client adapters emit the same validated GMCP record shape; shared
GMCP adapters translate those records into Imp's canonical state model. A
client package must not embed a MUD-specific normalization implementation.

The implemented shared `Normalizer` retains the mapping proved with AVATAR.
A selectable GMCP-adapter interface, automatic MUD selection, and a generic
fallback are not implemented; ADR 0012 places any future selection in the shared
layer rather than in individual client integrations.

Implementation and live verification cover:

- a packaged desktop-owned same-host Imp node on `127.0.0.1:8787`, independent
  of which transport the desktop HUD itself consumes;
- one shared frozen `imp-node` executable/runtime capable of running either the
  relay or authenticated gateway;
- strict local-node ownership: a pre-existing listener on the adapter endpoint
  is never adopted, replaced, or killed;
- a distinct SSH consumer endpoint on `127.0.0.1:8789`, preventing Managed or
  External SSH from occupying the adapter's local-node port;
- local TinyFugue state and trusted-action delivery through the local node;
- local Mudlet state, profile-selection lifecycle, and trusted actions;
- desktop supervision of the local node and optional authenticated gateway,
  including ownership-safe startup, health checking, restart, and shutdown;
- remote Mudlet state and a real `look` action through SSH;
- remote Mudlet state and a real `look` action through authenticated public WSS
  while `/ingest` remained inaccessible at the TLS edge;
- the existing remote TinyFugue Managed-SSH topology on the separated consumer
  port, including trusted-action delivery; and
- normal Windows release distribution of `Imp.mpackage`: the release workflow
  builds the package before the Windows job, embeds it with the desktop Mudlet
  resources, and the installed application provisions the stable
  `%LOCALAPPDATA%\Imp\mudlet\Imp.mpackage` path.

Live acceptance also ran local Mudlet and remote TinyFugue transport
simultaneously: `imp-node.exe` owned local `8787` while the desktop-owned
`ssh.exe` owned `8789` and forwarded it to VPS `8787`. This directly verified
that the MUD-client adapter boundary no longer changes with the HUD connection
mode.

Slice 16 is complete. Imp-to-Imp chaining, TinyFugue identity reacquisition, and
raw/ANSI GMCP robustness remain separate candidate work.

### Slice 17 - Input boundary correctness

Slug: `input-boundary-correctness`. Complete; untagged.

Remediates F01, F06, and F07 from the
[full repository audit](audits/2026-10-03-full-repository-audit.md):

- bounded shared GMCP validation and conversion, with atomic record
  normalization across TinyFugue and Mudlet;
- overflow-safe relay numeric rejection through the existing invalid-frame
  policy; and
- finite, positive stale-feed and gateway-authentication durations at both
  configuration and runtime construction boundaries.

Regression and loopback runtime evidence is recorded in [`status.md`](status.md).
The completed audit remains a historical snapshot; its other findings are
outside this slice.

## Candidate work

Unordered, deliberately without slice numbers, and provisional. Live evidence
that exposes a more urgent defect can reorder this work.

### Extensibility

#### Plugin / consumer interface

Define a stable extension boundary so optional consumers can subscribe to Imp's
normalized data without becoming part of a client integration, node, or HUD
implementation.

A database logger is the first concrete motivating consumer, but the boundary
should be generic enough for history, analytics, and other integrations.

The first design should prefer an isolated consumer interface over arbitrary
code injection into the Tauri application. It should also determine whether the
existing normalized protocol/relay boundary can be reused or extended rather
than inventing a second competing event model.

Design questions include:

- what is exposed: snapshots, deltas, transient text, action results, or some
  explicitly versioned combination;
- whether consumers are subprocesses, localhost subscribers, library users, or
  another isolated form;
- ordering, reconnect, discovery, backpressure, and failure isolation;
- configuration and lifecycle ownership;
- versioning and compatibility; and
- capability and trust boundaries.

Consumers should be read-only by default. Any consumer capability that can
issue outbound actions must be designed explicitly around the existing
exact-context, operator-trust, no-retry, and no-replay guarantees rather than
inheriting write access accidentally.

### Reliability

#### Character identity reacquisition

Rapid AVATAR login and world transitions can produce later GMCP without another
authoritative `Char.Status.character_name`, leaving Imp without character
identity. This remains a deferred defect in [`status.md`](status.md).

Any fix must preserve the current invariant: identity is never inferred from
ambiguous group/player data, and Imp never invents an identity it was not told.

#### Raw/ANSI GMCP robustness

Captured GMCP material containing raw ANSI or control bytes has produced
`invalid_raw_json` rejections.

The work is to identify the correct capture/parsing/normalization boundary for
that material before changing today's fail-closed behavior. Sanitization
semantics should not be guessed.

#### TinyFugue runtime crash

An upstream/runtime failure has printed:

```text
Internal error: socket.c, line 3717
resize freed string
```

This has been investigated separately and has an upstream fix/PR effort. It
remains outside Imp feature sequencing rather than being presented as an Imp
feature slice.

### Interoperability

#### MUD-agnostic normalization

Reduce AVATAR-specific assumptions incrementally while keeping Imp's normalized
protocol, relay, and HUD independent of MUD-specific concepts. This follows the
boundary established by
[ADR 0002](architecture/decisions/0002-imp-owned-protocol.md): GMCP-specific
concepts stop at normalization.

This is not a promise of universal MUD compatibility in one slice. New mappings
should continue to be driven by observed protocol behavior rather than guessed
schemas.

### Distribution and onboarding

#### Direct WSS provisioning and onboarding

Reduce the operator-owned setup around the authenticated Direct WSS transport.

Today the released server installer deliberately does not enable Direct WSS,
pairing-token generation and rotation are manual, and the public TLS reverse
proxy and certificate lifecycle remain operator-owned.

A future design can evaluate how much of gateway enablement, token lifecycle,
reverse-proxy/TLS setup, and first-run guidance Imp should automate while
preserving the existing boundaries:

- the relay remains loopback-only;
- the gateway exposes only the intended remote capabilities;
- Imp authentication remains separate from TLS; and
- plaintext pairing tokens do not leak into URLs, logs, or server-side stored
  configuration.

#### Live connection reconfiguration

Native Connection settings currently configure the next application start.
Saving settings does not replace the running state source, action sink, or SSH
supervisor.

A future slice could design safe live switching between Local, External SSH,
Managed SSH, and Direct WSS without weakening the existing ownership, credential,
and no-replay guarantees.

#### Desktop distribution hardening

The first public release intentionally leaves two normal-user distribution
limitations in place:

- the Windows installer is unsigned; and
- automatic application updates are not implemented.

Code signing and update delivery are independent distribution problems and
can be sequenced separately rather than being bundled into an unrelated
feature slice.

#### Additional desktop release targets

The Linux x86_64 artifact published today is the server bundle, not a Linux
desktop release.

Linux and macOS desktop artifacts remain potential future work and should only
be called supported releases after their native packaging and acceptance paths
are established.

### Platforms

#### Mobile feasibility

Still exploratory.

Direct WSS substantially changes the feasibility boundary because a mobile
client would no longer have to reproduce the desktop's OpenSSH local-forward
topology.

Any mobile work should first evaluate Tauri/native-runtime constraints, HUD
interaction design, secure credential storage, notifications, and whether the
authenticated gateway remains the appropriate transport boundary.
