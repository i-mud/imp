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

Outbound commands use a separate boundary:

- `apps/desktop/src/lib/action/types.ts` - `ActionSink` and terminal result.
- `apps/desktop/src/lib/action/mock.ts` - deterministic, network-free mock.
- `apps/desktop/src/lib/action/relay.ts` - one ephemeral `/action` connection
  per invocation, with no retry.
- `apps/desktop/src/lib/config.ts` - also the only module allowed to select a
  concrete action sink.

## Relationships

Implemented by:

- `MockStateSource` - synthesises changing vitals locally; makes the app usable
  with no relay and no TinyFugue
- `RelayStateSource` - WebSocket client with bounded exponential backoff

Selected by:

- `createRuntimeClients()` in `config.ts`. An explicit `VITE_TINYSCRY_SOURCE`
  is authoritative. Without it, browser/development builds default to `mock`,
  while a Tauri build identified by `TAURI_ENV_PLATFORM` defaults to `relay`.
- In relay mode, `connection_config` supplies the native runtime transport
  tuple. External and managed SSH modes use the local relay URL; Direct WSS
  supplies its `wss:` state URL and transient renderer authentication token
  from native application configuration.
- `VITE_TINYSCRY_RELAY_URL` remains a local/custom relay override. Direct WSS
  never reads a pairing token or remote URL from a build-time `VITE_*` value.

Consumed by:

- `apps/desktop/src/lib/hud/store.svelte.ts`, which forwards events to the
  pure reducer in `model.ts`. Retained state/status events update `HudModel`;
  transient received-text events deliberately leave that model unchanged.

## Why this seam exists

Two requirements land on it:

1. The app must be usable immediately without TinyFugue or the VPS, without the
   UI knowing it is being fed mock data.
2. Managed SSH diagnostics must enrich relay connection failures without
   exposing SSH argv, PIDs, or child-process lifecycle to components.

Both hold because the UI's only contract is `SourceEvent`. The Rust
`TunnelSupervisor` is established during native setup. Renderer startup then
loads the native connection tuple before mounting the application and
`config.ts` constructs the matching source and action sink.

Local relay mode also supplies a synchronous diagnostic accessor to
`RelayStateSource`. A relay reconnect event therefore carries the best known
SSH transport detail when evidence exists. Direct WSS bypasses SSH diagnostics;
its availability is represented by the same socket connection lifecycle. The
event kind, reducer, store, and components remain unchanged.

The tunnel does not introduce a second top-level state machine. The public HUD
states still derive from relay socket phase plus feed liveness. See
`docs/architecture/processes/managed-runtime.md`.

`ActionSink` is deliberately not a method on `StateSource`. Observation has a
long-lived reconnecting lifecycle; an action is a one-shot, context-bound
request whose ambiguous result must not be retried. Keeping them separate
prevents source reconnect behavior from replaying a command.

## Change impact

- Adding an event kind: `types.ts`, `model.ts` (exhaustive switch), the reducer
  tests, and any component that renders the new information.
- Adding a source or transport selection: implement the interface and register
  it in `config.ts` only. If a change requires touching a HUD component, the
  seam has been violated.
- Changing reconnect policy affects `docs/architecture/processes/connection-lifecycle.md`.
- Adding an action sink: implement `ActionSink` and register it in `config.ts`;
  do not add outbound methods to `StateSource`.

## Invariants

- No component imports `mock.ts` or `relay.ts`. Only `config.ts` does.
- `stop()` must cancel every timer and prevent further reconnects.
- Decode failures emit `protocol-error` and never a state change.
- `unknown_type` is ignored silently - it is forwards compatibility, not a
  fault. See `packages/protocol/SPEC.md`.
- The mock feeds its frames through the real decoder, so both sources share one
  validation path and the mock cannot drift from the protocol.
- Mock mode selects both mock implementations and never opens an action
  network connection.
- The relay action sink validates a command before opening its one-shot socket
  and never retries an `unknown` result.

## Verification

Status: verified
Verified against: current `config.ts` selection logic and `vite.config.ts`
environment exposure; HUD rendering mock vitals confirmed in a browser.
