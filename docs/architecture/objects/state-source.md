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
  and wraps the native connection-settings commands exposed by the Tauri
  backend

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
  with no Imp node or MUD client
- `RelayStateSource` - WebSocket client with bounded exponential backoff

Selected by:

- `createRuntimeClients()` in `config.ts` selects the source and action sink.
  Browser development defaults to `mock` and can choose a source with
  `VITE_IMP_SOURCE`. The native desktop always uses the startup transport
  selection supplied by `connection_config`; it cannot opt into mock.
  An absent or unavailable runtime configuration disables the state and
  action consumers; it does not fall back to a loopback relay. Local mode
  uses the desktop-owned node on `127.0.0.1:8787`. External and Managed SSH
  use the separate consumer endpoint on `127.0.0.1:8789`. Direct WSS supplies
  its `wss:` state URL and transient renderer authentication token from native
  application configuration.
  A structured Managed SSH destination changes native SSH argument selection
  only. The renderer still connects to a fixed loopback address for both
  observation and actions, without authenticating the listener's owner.
- `VITE_IMP_RELAY_URL` remains a local/custom relay override. Direct WSS
  never reads a pairing token or remote URL from a build-time `VITE_*` value.

- Native connection settings use separate `connection_settings` and
  `save_connection_settings` Tauri commands. They expose safe summaries and
  a pending-restart flag, not the credential-bearing runtime tuple. They
  persist configuration for a future application start and do not mutate the
  `RuntimeConnectionConfig` selected for the running source/action clients.

Consumed by:

- `apps/desktop/src/lib/hud/store.svelte.ts`, which forwards events to the
  pure reducer in `model.ts`. Retained state/status events update `HudModel`;
  transient received-text events deliberately leave that model unchanged.

## Why this seam exists

Two requirements land on it:

1. The app must be usable immediately without a MUD client or Imp node,
   without the UI knowing it is being fed mock data.
2. Local-node and Managed-SSH diagnostics must enrich connection failures
   without exposing process argv, PIDs, or child-process lifecycle to
   components.

Both hold because the UI's only contract is `SourceEvent`. Native setup
establishes the local-node supervisor and, when configured, the SSH and gateway
supervisors. Renderer startup then loads the native startup selection and
`config.ts` constructs the matching source and action sink only when a relay
transport was selected. In a native relay build, opting out of first-use
setup leaves the HUD usable for Settings but does not synthesize game data.

The Settings UI is intentionally outside that runtime selection seam. It may
edit what the next process will select, but it does not hot-swap a
`StateSource`, `ActionSink`, or `TunnelSupervisor` in the current process.

Loopback runtime modes also supply a synchronous diagnostic accessor to
`RelayStateSource`. Local mode reads local-node diagnostics; Managed and
External SSH read tunnel diagnostics for the `8789` consumer endpoint. Direct
WSS bypasses loopback diagnostics and represents availability through the same
socket connection lifecycle. The event kind, reducer, store, and components
remain unchanged.

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
