# Relay

## Purpose

The single point that owns the selected game context and its current state on
the VPS. It accepts normalized state from one local producer, fans it out to
HUD subscribers, and brokers one context-bound outbound action at a time.

## Source

- `services/relay/src/tinyscry_relay/state.py` - `RelayState`: snapshot,
  sequence counter, feed status, health payload. No transport knowledge.
- `services/relay/src/tinyscry_relay/server.py` - WebSocket endpoints,
  broadcast, health endpoint.
- `services/relay/src/tinyscry_relay/action.py` - one eligible TinyFugue
  consumer and one in-flight action.
- `services/relay/src/tinyscry_relay/protocol.py` - canonical Python decoder.
- `services/relay/src/tinyscry_relay/config.py` - bind host/port, stale window.

## Endpoints

See `packages/protocol/SPEC.md` for the message-level contract.

| Endpoint           | Role       | Behaviour                                           |
| ------------------ | ---------- | --------------------------------------------------- |
| `/state`           | subscriber | `hello`, retained `snapshot`, then live updates     |
| `/ingest`          | producer   | accepts `select` and matching-context `publish`     |
| `/action`          | requester  | accepts one action and returns its terminal status  |
| `/action-consumer` | TF helper  | registers and acknowledges context-bound dispatches |
| `/healthz`         | operator   | HTTP JSON, served on the same port                  |

`/healthz` is served through `websockets`' `process_request` hook, which is why
there is no second listener and no HTTP framework.

## Relationships

Fed by:

- `integrations/tinyfugue/src/tinyscry_tf/publisher.py`

Read by:

- `apps/desktop/src/lib/source/relay.ts`, across an SSH port-forward

Depends on:

- `websockets` (sole runtime dependency)

Reused by:

- `integrations/tinyfugue`, which depends on this project purely to share the
  Python protocol codec rather than duplicate it

## Change impact

- Changing `RelayState`'s feed rules changes what the HUD renders as
  "no data" vs. "stale" - see `docs/architecture/processes/connection-lifecycle.md`.
- **The default port `8787` is hardcoded in six source locations**, none of
  which read from the others, so changing it means editing
  `services/relay/src/tinyscry_relay/server.py` (twice - the class and the
  start helper), `services/relay/src/tinyscry_relay/config.py` (the env
  fallback), `integrations/tinyfugue/src/tinyscry_tf/publisher.py`,
  `integrations/tinyfugue/src/tinyscry_tf/action_consumer.py`, and
  `apps/desktop/src/lib/config.ts`. Tests use ephemeral ports, so also update
  `README.md`, `docs/development.md`, and `packages/protocol/SPEC.md`.
- Changing endpoint paths (`/state`, `/ingest`, `/action`,
  `/action-consumer`, `/healthz`) breaks the corresponding source, publisher,
  action sink, helper, and `SPEC.md` transport table.
- Changing the bind default is a security change; read
  `docs/architecture/decisions/0001-loopback-relay-and-ssh-boundary.md` first.
- `RelayState` is deliberately transport-free and clock-injected so its rules
  are unit-testable without a socket. Keep it that way; putting broadcast logic
  into it would make the feed rules untestable.

## Invariants

- Binds loopback only. A non-loopback host is rejected and has no override.
- Loopback prevents remote access but does not isolate OS users. TinyScry has
  no per-user authentication, so the VPS and workstation must be single-user
  or trust every host-local process.
- Browser-facing endpoints permit only no `Origin`, the Vite development
  origin, or the Tauri origin. Producer and helper endpoints require no
  `Origin`; this is browser defense-in-depth, not authentication.
- Only a `publish` whose context exactly equals the selected context mutates
  retained state.
- The last snapshot is retained across producer disconnects; freshness is
  communicated by `feed`, not by dropping state.
- One matching helper and one action in flight are permitted. There is no
  queue, retry, fan-out, or replay; post-dispatch ambiguity returns `unknown`.
- A malformed frame is rejected whole, leaves stored state untouched, and
  closes the connection. Only the error code and path are logged - never the
  frame.
- `seq` is monotonic within a process and restarts with it.
- One dead subscriber cannot break delivery to the others.

## Verification

Status: verified
Verified against: `services/relay` at bootstrap; unit and loopback server tests
plus `tests/e2e/relay_roundtrip.py` passing.
