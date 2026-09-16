# StateSource

## Purpose

The HUD's transport boundary. A `StateSource` turns "something out there" into
a stream of `SourceEvent`s. It is the seam that keeps the UI ignorant of
WebSockets, the relay, and SSH process details.

## Source

- `apps/desktop/src/lib/source/types.ts` - `StateSource`, `SourceEvent`,
  `ConnectionPhase`
- `apps/desktop/src/lib/source/mock.ts` - `MockStateSource`
- `apps/desktop/src/lib/source/relay.ts` - `RelayStateSource`
- `apps/desktop/src/lib/config.ts` - the only module allowed to name a concrete
  implementation
- `apps/desktop/src/lib/tunnel.ts` - polls transport-independent diagnostics
  exposed by the Tauri backend

## Relationships

Implemented by:

- `MockStateSource` - synthesises changing vitals locally; makes the app usable
  with no relay and no TinyFugue
- `RelayStateSource` - WebSocket client with bounded exponential backoff

Selected by:

- `createStateSource()` in `config.ts`, from `VITE_TINYSCRY_SOURCE` and
  `VITE_TINYSCRY_RELAY_URL`

Consumed by:

- `apps/desktop/src/lib/hud/store.svelte.ts`, which forwards every event to the
  pure reducer in `model.ts`

## Why this seam exists

Two requirements land on it:

1. The app must be usable immediately without TinyFugue or the VPS, without the
   UI knowing it is being fed mock data.
2. Managed SSH diagnostics must enrich relay connection failures without
   exposing SSH argv, PIDs, or child-process lifecycle to components.

Both hold because the UI's only contract is `SourceEvent`. The Rust
`TunnelSupervisor` runs before the webview, while `config.ts` supplies a
synchronous diagnostic accessor to `RelayStateSource`. A relay reconnect event
therefore carries the best known transport detail when evidence exists; the
event kind, reducer, store, and components are unchanged.

The tunnel does not introduce a second top-level state machine. The public HUD
states still derive from relay socket phase plus feed liveness. See
`docs/architecture/processes/managed-runtime.md`.

## Change impact

- Adding an event kind: `types.ts`, `model.ts` (exhaustive switch), the reducer
  tests, and any component that renders the new information.
- Adding a source: implement the interface and register it in `config.ts` only.
  If a change requires touching a component, the seam has been violated.
- Changing reconnect policy affects `docs/architecture/processes/connection-lifecycle.md`.

## Invariants

- No component imports `mock.ts` or `relay.ts`. Only `config.ts` does.
- `stop()` must cancel every timer and prevent further reconnects.
- Decode failures emit `protocol-error` and never a state change.
- `unknown_type` is ignored silently - it is forwards compatibility, not a
  fault. See `packages/protocol/SPEC.md`.
- The mock feeds its frames through the real decoder, so both sources share one
  validation path and the mock cannot drift from the protocol.

## Verification

Status: verified
Verified against: `apps/desktop` at bootstrap; reducer and source unit tests
passing, HUD rendering mock vitals confirmed in a browser.
