# Relay

## Purpose

The single point that owns "current game state" on the VPS. It accepts
normalized state from one local producer and fans it out to HUD subscribers.
It is the only component that holds state on behalf of others.

## Source

- `services/relay/src/tinyscry_relay/state.py` - `RelayState`: snapshot,
  sequence counter, feed status, health payload. No transport knowledge.
- `services/relay/src/tinyscry_relay/server.py` - WebSocket endpoints,
  broadcast, health endpoint.
- `services/relay/src/tinyscry_relay/protocol.py` - canonical Python decoder.
- `services/relay/src/tinyscry_relay/config.py` - bind host/port, stale window.

## Endpoints

See `packages/protocol/SPEC.md` for the message-level contract.

| Endpoint   | Role       | Behaviour                                            |
| ---------- | ---------- | ---------------------------------------------------- |
| `/state`   | subscriber | `hello`, then retained `snapshot`, then live updates |
| `/ingest`  | producer   | accepts `publish`; closes on a malformed frame       |
| `/healthz` | operator   | HTTP JSON, served on the same port                   |

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
- **The default port `8787` is hardcoded in five places**, none of which read
  from the others, so changing it means editing all of them:
  `services/relay/src/tinyscry_relay/server.py` (twice - the class and the
  start helper), `services/relay/src/tinyscry_relay/config.py` (the env
  fallback), `integrations/tinyfugue/src/tinyscry_tf/publisher.py`
  (`DEFAULT_RELAY_URL`), and `apps/desktop/src/lib/config.ts`
  (`DEFAULT_RELAY_URL`). No test will catch a partial change, because the tests
  use ephemeral ports. Also update `README.md`, `docs/development.md` and
  `packages/protocol/SPEC.md`, which quote the port in runnable commands.
- Changing endpoint paths (`/state`, `/ingest`, `/healthz`) breaks the same
  two `DEFAULT_RELAY_URL` constants plus `SPEC.md`'s transport table.
- Changing the bind default is a security change; read
  `docs/architecture/decisions/0001-loopback-relay-and-ssh-boundary.md` first.
- `RelayState` is deliberately transport-free and clock-injected so its rules
  are unit-testable without a socket. Keep it that way; putting broadcast logic
  into it would make the feed rules untestable.

## Invariants

- Binds `127.0.0.1` unless explicitly overridden, and logs prominently if it
  is not loopback.
- No authentication, by design, and it must not acquire any as a way to make
  public exposure acceptable.
- The last snapshot is retained across producer disconnects; freshness is
  communicated by `feed`, not by dropping state.
- A malformed producer frame is rejected whole, leaves stored state untouched,
  and closes the producer connection. Only the error code and path are logged -
  never the frame.
- `seq` is monotonic within a process and restarts with it.
- One dead subscriber cannot break delivery to the others.

## Verification

Status: verified
Verified against: `services/relay` at bootstrap; unit and loopback server tests
plus `tests/e2e/relay_roundtrip.py` passing.
