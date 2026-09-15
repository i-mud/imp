# StateSource

## Purpose

The HUD's transport boundary. A `StateSource` turns "something out there" into
a stream of `SourceEvent`s. It is the seam that keeps the UI ignorant of
WebSockets, of the relay, and eventually of SSH.

## Source

- `apps/desktop/src/lib/source/types.ts` - `StateSource`, `SourceEvent`,
  `ConnectionPhase`
- `apps/desktop/src/lib/source/mock.ts` - `MockStateSource`
- `apps/desktop/src/lib/source/relay.ts` - `RelayStateSource`
- `apps/desktop/src/lib/config.ts` - the only module allowed to name a concrete
  implementation

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
2. Automated SSH tunnel management must be addable later without touching the
   UI or the protocol.

Both hold because the UI's only contract is `SourceEvent`.

Precisely what automating the tunnel does and does not cost, verified against
the current source rather than asserted:

| Unaffected                                        | Will need editing                         |
| ------------------------------------------------- | ----------------------------------------- |
| `packages/protocol` - the wire format             | a new SSH process/lifecycle module        |
| `types.ts` - `StateSource`, `SourceEvent`         | `config.ts`, the composition root         |
| `model.ts`, `store.svelte.ts` - the reducer       | `App.svelte`, which wires source to store |
| every component in `apps/desktop/src/components/` | Tauri capabilities, to spawn a process    |

So "no UI or protocol change" holds. "Only the URL changes" would not: there
is deliberately **no** async pre-`start()` seam today - `start()` is
synchronous and `App.svelte` calls it immediately - so establishing a tunnel
first means adding that lifecycle step in the composition root. That is the
right place for it, and `config.ts` exists precisely to absorb this kind of
wiring, but it is an edit, not a free extension. Inventing the seam now would
be abstraction for a requirement that has no implementation yet.

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
