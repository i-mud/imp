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

Slug: `authenticated-wss-transport`. In implementation.

The goal is to make SSH optional for desktop-to-VPS transport without exposing
the existing relay or its privileged TinyFugue-facing endpoints to the network.

The implementation follows
[`0010-authenticated-remote-gateway.md`](architecture/decisions/0010-authenticated-remote-gateway.md):

- the existing relay remains permanently loopback-only on its current trust
  boundary;
- a separate loopback `tinyscry-gateway` exposes only remote state and action
  capabilities;
- a normal reverse proxy terminates public TLS and forwards WSS to that gateway;
- direct desktop mode requires `wss:` and authenticates with a 256-bit pairing
  token before normal protocol-v2 traffic begins;
- the server retains only the SHA-256 digest of the pairing token;
- the plaintext token is native desktop configuration, never URL/query data,
  build-time configuration, WebView `localStorage`, or logged diagnostic data;
- `/ingest` and `/action-consumer` are never reachable through the gateway;
- SSH external/manual and managed modes remain supported unchanged;
- the gateway adds no state retention, text replay, action retry, context
  mutation, or alternative action semantics.

Deterministic acceptance must prove:

1. unauthenticated, malformed, binary, wrong-token, and timed-out authentication
   connections receive no relay data and never open an upstream relay
   connection;
2. a valid token permits state streaming through the gateway, including retained
   snapshots, feed status, and transient text with the same semantics as a
   direct relay subscriber;
3. a valid token permits one normal context-bound action round trip without
   changing `forwarded`, `rejected`, or `unknown` semantics;
4. the gateway rejects `/ingest`, `/action-consumer`, unknown routes, and
   untrusted browser origins before touching the relay;
5. the gateway listener and its configured upstream relay are both restricted
   to loopback;
6. pairing-token material is absent from URLs, logs, health responses, protocol
   frames forwarded to the relay, and persisted renderer storage;
7. a relay outage causes the state path to disconnect rather than retain or
   synthesize state, while action requests are never automatically retried;
8. existing external/manual SSH and managed-SSH behavior remains regression
   clean.

Native/live acceptance must additionally prove, over a real trusted TLS
endpoint, that the Windows Tauri application can:

- connect directly over `wss:` without an SSH listener on local port 8787;
- receive live state and transient received-text events;
- send one action through the existing context-bound action path;
- reject an incorrect pairing token without exposing state;
- recover its state connection after a gateway/reverse-proxy interruption; and
- switch back to the existing SSH transport without changing relay/feed
  configuration.

Automated VPS installation, reverse-proxy provisioning, token-generation UX,
and general TinyFugue deployment scripts are intentionally deferred to the next
distribution slice. Slice 11 establishes the stable transport they will deploy.

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

Later platform work, and exploratory. The current desktop arrangement - a Tauri
shell plus an OpenSSH-owned tunnel to a loopback relay - does not transfer
unchanged to a mobile platform, so this starts as a feasibility question rather
than a port.
