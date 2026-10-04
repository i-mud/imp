# Relay

## Purpose

The same-host Imp node that owns the selected game context and its current
state. It accepts normalized state from one local producer, fans it out to HUD
subscribers, and brokers one context-bound outbound action at a time. The same
relay runtime is used on a VPS or beside a local desktop MUD client.

## Source

- `services/relay/src/imp_relay/state.py` - `RelayState`: snapshot,
  sequence counter, feed status, health payload. No transport knowledge.
- `services/relay/src/imp_relay/server.py` - WebSocket endpoints,
  per-subscriber delivery/writer lifecycle, health endpoint.
- `services/relay/src/imp_relay/action.py` - one eligible client-adapter
  consumer and one in-flight action.
- `services/relay/src/imp_relay/protocol.py` - canonical Python decoder.
- `services/relay/src/imp_relay/config.py` - bind host/port, stale window.

## Endpoints

See `packages/protocol/SPEC.md` for the message-level contract.

| Endpoint           | Role          | Behaviour                                            |
| ------------------ | ------------- | ---------------------------------------------------- |
| `/state`           | subscriber    | `hello`, retained `snapshot`, `status`, live traffic |
| `/ingest`          | producer      | accepts `select` and matching-context `publish`      |
| `/action`          | requester     | accepts one action and returns its terminal status   |
| `/action-consumer` | client helper | registers and acknowledges context-bound dispatches  |
| `/healthz`         | operator      | HTTP JSON, served on the same port                   |

`/healthz` is served through `websockets`' `process_request` hook, which is why
there is no second listener and no HTTP framework.

## Relationships

Fed by:

- `integrations/common/src/imp_adapter/publisher.py`

Read by:

- `apps/desktop/src/lib/source/relay.ts`, directly in Local mode or through
  the separate SSH consumer endpoint in External/Managed mode
- `imp-gateway`, as the authenticated Direct-WSS transport's loopback
  upstream

Depends on:

- `websockets` (sole runtime dependency)

Reused by:

- `integrations/common`, which supplies the shared publisher/normalizer;
- `integrations/tinyfugue`; and
- `integrations/mudlet`.

## Change impact

- Changing `RelayState`'s feed rules changes what the HUD renders as
  "no data" vs. "stale" - see `docs/architecture/processes/connection-lifecycle.md`.
- The default relay port `8787` is intentionally duplicated across process
  boundaries rather than imported from one language/runtime. Before changing
  it, search the repository for `8787`: current consumers include relay
  configuration/server startup, shared publisher defaults, TinyFugue and
  Mudlet action consumers, the desktop-owned local node, remote SSH targets,
  gateway upstream defaults, deployment units, and documentation. The desktop
  SSH consumer endpoint is separately fixed at `8789`. Tests frequently
  use ephemeral ports and will not enumerate every operational default.
- Changing endpoint paths (`/state`, `/ingest`, `/action`,
  `/action-consumer`, `/healthz`) breaks the corresponding source, publisher,
  action sink, helper, and `SPEC.md` transport table. `/state`, `/action`, and
  `/healthz` also affect the authenticated gateway and public reverse-proxy
  route set.
- Changing the bind default is a security change; read
  `docs/architecture/decisions/0001-loopback-relay-and-ssh-boundary.md` and
  `docs/architecture/decisions/0010-authenticated-remote-gateway.md` first.
- `RelayState` is deliberately transport-free and clock-injected so its rules
  are unit-testable without a socket. Keep it that way; putting broadcast logic
  into it would make the feed rules untestable.

## Invariants

- Binds loopback only. A non-loopback host is rejected and has no override.
- Public Direct WSS terminates at the separate authenticated gateway. The
  relay itself never accepts Internet-facing authentication or a public bind.
- Loopback prevents remote access but does not isolate OS users. Imp has
  no per-user authentication, so the VPS and workstation must be single-user
  or trust every host-local process.
- Browser-facing endpoints permit only no `Origin`, the Vite development
  origin, or the Tauri origin. Producer and helper endpoints require no
  `Origin`; this is browser defense-in-depth, not authentication.
- Only a `publish` whose context exactly equals the selected context mutates
  retained state.
- The last snapshot is retained across producer disconnects; freshness is
  communicated by `feed`, not by dropping state.
- Matching publishes refresh freshness even when state is identical. Identical
  state retains the existing snapshot (`seq` and `at` included) without a
  duplicate broadcast; status transitions remain independent.
- Each subscriber has one serialized writer and a FIFO of at most 16 encoded
  frames, plus at most one frame in flight. Startup frames are enqueued together
  before registration; live snapshot/status/text frames follow relay emission
  order. Broadcast only enqueues without waiting for socket progress.
- Queue overflow or a send taking 5 seconds retires only that subscriber:
  remove it from broadcast membership and abort its transport without waiting
  for a close handshake. Disconnect cancels and awaits its writer; shutdown
  aborts subscriber transports and awaits all handlers/writers.
- The FIFO is transient delivery, not retained state. `RelayState` remains the
  sole snapshot/freshness owner. No retry, replay, coalescing, or delivery
  guarantee exists for a subscriber that cannot keep up. Gateway upstreams use
  the same policy as other subscribers.
- One matching helper and one action in flight are permitted. There is no
  queue, retry, fan-out, or replay; post-dispatch ambiguity returns `unknown`.
- A malformed frame is rejected whole, leaves stored state untouched, and
  closes the connection. Only the error code and path are logged - never the
  frame.
- `seq` advances for selections and changed-state snapshots, is monotonic within
  a process, and restarts with it.
- A blocked, slow, or closed subscriber cannot hold up producer ingestion,
  watchdog announcements, or healthy subscribers.

## Verification

Status: verified
Verified against: relay unit/loopback server tests,
`tests/e2e/relay_roundtrip.py`, gateway integration tests that bridge the
relay only after authentication, and live SSH/Direct-WSS transport evidence in
`docs/status.md`.
Slice 19 additionally verified real TCP backpressure with a non-reading
subscriber, healthy-peer ordered progress, bounded overflow/deadline retirement,
watchdog/text delivery, and subscriber writer cleanup.
