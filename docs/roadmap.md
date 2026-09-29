# Roadmap

Tracks the active implementation slice and work queued behind it.

- What exists today, and the evidence behind it, lives in
  [`status.md`](status.md). If a claim appears in both files, `status.md` wins.
- The active slice may record remaining scope while implementation is underway.
- Candidate work remains provisional. Live evidence that exposes a more urgent
  defect can reorder it; a roadmap entry is not a commitment to build it next.

No dates. Planned work describes intent rather than behavior; implemented
claims belong in `status.md` and the relevant architecture documents.

## Slice 10 - Configurable notification triggers

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

## Slice 11 - Authenticated WSS transport

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

Provisioning and onboarding remain deliberately separate from the transport
itself: automated VPS installation, reverse-proxy/certificate setup,
pairing-token generation/rotation UX, and general TinyFugue installation
workflow are candidate distribution work rather than unfinished Slice 11 scope.

## Slice 12 - Native connection settings

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

VPS installation, reverse-proxy/certificate provisioning, server-side
pairing-token generation/rotation, live transport hot-switching, multi-user
credentials, and first-run deployment automation remain separate future work.

## Slice 13 - First release readiness

Slug: `first-release-readiness`. Complete.

Slice 13 prepares Imp's first intentionally distributable release:
`v0.1.0`, an alpha release with Windows x64 as the supported desktop binary
target.

The slice turns the existing development build into a reproducible release
without changing Imp's protocol, transport, action, alert, or MUD
integration semantics.

### Scope

- Add the repository's declared MIT license as a tracked `LICENSE` file.
- Add `CHANGELOG.md` and establish the release-history format beginning with
  `0.1.0`.
- Establish one release-version contract and deterministic checks that the
  version-bearing manifests remain aligned.
- Surface the running native application's version from Tauri rather than from
  a separately hard-coded renderer string.
- Add a reproducible Windows x64 native release build producing an NSIS
  installer.
- Add a tag-driven GitHub Actions release workflow for `v*` tags.
- Require the release tag version to match the application version before
  publishing artifacts.
- Document installation from the released Windows artifact rather than
  requiring a source checkout for the desktop application.
- Document the `v0.1.0` support boundary: alpha quality, Windows x64 release
  artifact, and unsigned Windows installer.
- Perform clean-install Windows acceptance using the produced release artifact.

The existing source-development paths remain supported for contributors.
Linux and macOS may continue to build from source, but Slice 13 does not claim
release support for platforms without native release acceptance.

### Current state

Slice 13 is complete:

- the MIT license and changelog are tracked;
- the `0.1.0` version contract and tag/version check are implemented;
- the native application reports its Tauri-owned version;
- Windows x64 NSIS builds run through the release workflow;
- end-user Windows installation and connection setup are documented;
- release acceptance passed clean install, live state, an approved outbound
  action, managed-SSH cleanup, uninstall, and reinstall independently of the
  WSL/native development mirror;
- the managed-SSH console-window and orphaned-child defects found during
  release acceptance were fixed and reverified before tagging; and
- the original `v0.1.0` tag and prerelease were subsequently withdrawn during
  the Imp rename so the final first Imp release can include the renamed product
  identity and the packaged server installation added in Slice 15.

### Release contract

The final `v0.1.0` tag is created only after the release candidate has passed
the normal repository checks, Windows release acceptance, and the packaged
server-install acceptance added in Slice 15.

Clean-install acceptance must begin from the produced installer rather than an
existing development tree and prove:

1. the installer completes on Windows x64 and Imp launches successfully;
2. the installed application reports version `0.1.0`;
3. native Connection settings can configure a supported transport without
   editing application configuration by hand;
4. after restart, live state reaches the installed HUD;
5. one operator-approved outbound action traverses the installed application;
6. uninstall/reinstall does not depend on the WSL/native development mirror;
   and
7. the final release artifact is the same build shape produced by the release
   workflow.

Slice 15 adds a second release artifact to that contract. The Linux x86_64
server archive must install without a repository checkout or development
toolchain, survive an idempotent reinstall, reject tampered bundle contents,
roll back a failed activation, run relay/feed from stable installed paths, and
work with the released desktop through Managed SSH.

Only after both desktop and server acceptance pass is the final `v0.1.0` tag
published as Imp's first release.

### Out of scope

- automatic application updates;
- Windows code-signing certificate procurement;
- Linux or macOS release binaries;
- Microsoft Store or other application-store packaging;
- automatic VPS, reverse-proxy, or certificate provisioning;
- automated pairing-token generation or rotation;
- live transport hot-switching;
- multi-user credentials;
- repository visibility changes; and
- MUD/client generalization work.

Those are independent follow-up decisions and should not expand the first
release boundary.

## Slice 14 - Imp rename

Slug: `imp-rename`. Complete.

Slice 14 renamed TinyScry to **Imp — Interactive MUD Peripheral** across the
desktop, packages, Python commands, configuration/state paths, systemd units,
protocol identity, documentation, repository, and release-facing artifact
names.

The rename preserved the existing architecture and trust boundaries. The
canonical wire protocol is `IMP2`, the private context marker is `IMPCTX 2`,
and the native application identifier is `dev.imud.imp`.

The historical TinyScry `v0.1.0` prerelease/tag was removed rather than
retained as Imp's first release. The descriptive `imp-rename` milestone tag
records Slice 14 independently of the eventual semantic release.

## Slice 15 - Server installation / bootstrap

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

## Candidate work

Unordered, and deliberately without slice numbers.

### MUD-agnostic normalization

Still important, no longer next. The objective is to keep Imp's own
normalized state while reducing AVATAR-specific assumptions in the integration
and normalization layer, so that GMCP- and MUD-specific concepts stay upstream
of the protocol, relay, and HUD boundary - ADR 0002 already requires that
direction.

This is incremental. No single slice promises universal MUD compatibility.

### Character identity reacquisition

Rapid AVATAR login and world transitions can produce later GMCP without another
authoritative `Char.Status.character_name`, leaving Imp without character
identity. Tracked as a current deferred defect in [`status.md`](status.md).

Constraints for any fix: identity is never inferred from ambiguous group or
player data, and Imp never invents an identity it was not told.

### Raw/ANSI GMCP robustness

Captured GMCP material containing raw ANSI or control bytes has produced
`invalid_raw_json` rejections. The work is to find the correct
capture/parsing/normalization boundary for that material. Sanitization
semantics are deliberately unchosen: fail-closed rejection is the current
behavior, and replacing it requires evidence about where the bytes are
introduced.

### TinyFugue runtime crash

An upstream/runtime failure printing `Internal error: socket.c, line 3717`
followed by `resize freed string`. Tracked separately from Imp feature
sequencing; it is upstream investigation and possibly upstream PR work, not a
Imp slice.

### Mudlet integration

Adapter work for a second client, sensible only after the normalization
boundary is generalized. The core HUD must not gain a direct Mudlet
dependency; whatever appears is another producer upstream of the protocol.

### Mobile feasibility

Later platform work, and exploratory. The current desktop application is still
a Tauri/native-WebView design, but Direct WSS removes the desktop's previous
hard dependency on an OpenSSH-owned local forward. Mobile feasibility should
therefore evaluate the remaining UI/native-runtime constraints and whether the
authenticated gateway can serve as the transport boundary, rather than assuming
the desktop SSH topology must be reproduced unchanged.
