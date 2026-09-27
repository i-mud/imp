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
- a separate loopback `tinyscry-gateway` exposes only state and action;
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
configurable through TinyScry's native Settings UI rather than requiring
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
through the UI. Each saved mode was activated by restarting TinyScry and
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

## Candidate work

Unordered, and deliberately without slice numbers.

### MUD-agnostic normalization

Still important, no longer next. The objective is to keep TinyScry's own
normalized state while reducing AVATAR-specific assumptions in the integration
and normalization layer, so that GMCP- and MUD-specific concepts stay upstream
of the protocol, relay, and HUD boundary - ADR 0002 already requires that
direction.

This is incremental. No single slice promises universal MUD compatibility.

### Character identity reacquisition

Rapid AVATAR login and world transitions can produce later GMCP without another
authoritative `Char.Status.character_name`, leaving TinyScry without character
identity. Tracked as a current deferred defect in [`status.md`](status.md).

Constraints for any fix: identity is never inferred from ambiguous group or
player data, and TinyScry never invents an identity it was not told.

### Raw/ANSI GMCP robustness

Captured GMCP material containing raw ANSI or control bytes has produced
`invalid_raw_json` rejections. The work is to find the correct
capture/parsing/normalization boundary for that material. Sanitization
semantics are deliberately unchosen: fail-closed rejection is the current
behavior, and replacing it requires evidence about where the bytes are
introduced.

### TinyFugue runtime crash

An upstream/runtime failure printing `Internal error: socket.c, line 3717`
followed by `resize freed string`. Tracked separately from TinyScry feature
sequencing; it is upstream investigation and possibly upstream PR work, not a
TinyScry slice.

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
