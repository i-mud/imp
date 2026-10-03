# Connection lifecycle

## Why this is a process card

Two independent liveness signals decide what the HUD shows, and conflating them
is the most likely bug in this area:

- **Socket liveness** - can the HUD reach its selected desktop transport and
  receive the relay stream? Owned by `apps/desktop/src/lib/source/relay.ts`.
- **Feed liveness** - is the MUD still feeding the relay? Owned by
  `services/relay/src/imp_relay/state.py` and reported as `status.feed`.

A healthy socket with a dead feed is a real and common state: the node is
reachable but its selected MUD-client producer is gone. The HUD must show
"no data", not confidently stale vitals.

## Entry points

- `RelayStateSource.start()` / `.stop()`
- `RelayState.producer_connected()` / `.producer_disconnected()`
- the relay's periodic stale-window check

## Flow

```
idle
 |  start()
connecting
 |-- local/SSH WebSocket open ----------------------> connected
 |
 `-- Direct WSS open -> send auth -> relay `hello` -> connected

close / error
     |
     v
reconnecting --- bounded backoff, retry ---> connecting
     |
     | stop()
     v
disconnected
```

For the local/SSH transport, WebSocket `open` confirms the connection
immediately. For authenticated Direct WSS, `open` is not enough: the client
first sends the pairing-token authentication frame and reports `connected`
only after the gateway has accepted it and the relay's `hello` arrives. A wrong
token therefore cannot briefly appear connected or reset reconnect backoff.

After the relay path is established it sends `hello`, then the retained
context-bearing `snapshot` if it has one, then `status`. The HUD resets its
`seq` high-water mark on `hello` and on every connection transition, because
the relay's counter restarts with its process. A non-connected phase also
clears the actionable context, so stale displayed values cannot authorize an
action.

Feed status, evaluated by the relay:

| Condition                                                     | `feed`  |
| ------------------------------------------------------------- | ------- |
| no producer connected                                         | `down`  |
| producer connected, selection has no matching-context publish | `stale` |
| matching-context publish arrived within the stale window      | `live`  |

The relay re-evaluates on a timer, so `live -> stale` is emitted without a
publish arriving. Without that timer a dead feed would look live forever.

Matching publishes represent accepted observations, not only state changes.
The shared normalizer decides whether GMCP contributes valid canonical-state
evidence; each client adapter applies its existing selected-context fence before
publishing. Repeated identical mapped identity/vital/target observations refresh
the relay's stale window. Unknown/unmapped input, text, and rejected records do
not. Buffered vitals without identity and combat-position history alone do not
establish canonical observation.

An identical matching publish does not replace the retained snapshot, advance
`seq` or `at`, or broadcast a duplicate snapshot. The relay still announces
`stale -> live` when appropriate. Thus unchanged observations keep alert
baselines fresh without manufacturing state transitions or duplicate alerts.

Producer-side liveness has the same event-loop constraint. `RelayPublisher`
owns one long-lived reconnect task, so an idle `/ingest` disconnect is detected
without waiting for another GMCP or normalized-state event. It disposes the
closed transport, connects one replacement with bounded backoff, and reasserts
only the latest retained `select(context, state)`. Observations attempt one send
on the transport available when publication starts. They do not wait for an
unavailable transport or retry on its replacement; canonical values received
during the outage still update the retained selection. No observation or action
evidence is queued for replay.

That recovered selection is `stale`; freshness returns only after a new
authoritative observation received after the producer connection is ready.
This also applies when an old observation send was in flight at disconnect.
If the latest retained selection has `context: null`, the publisher reasserts it
as non-actionable. One reconnect owner prevents overlapping producer sessions.

An identical observation does not change the publisher's retained-selection
revision, so it cannot disable transient text on an already selected transport
while its send is pending. Text still drops while disconnected or selection is
not ready; it never waits, retries, or replays.

## HUD presentation states

`freshnessOf()` in `apps/desktop/src/lib/hud/model.ts` classifies the pipeline;
the component renders that classification through the status indicator. HUD
content remains at full opacity for every freshness state.

| Situation                                | `freshnessOf`  | Presentation                          |
| ---------------------------------------- | -------------- | ------------------------------------- |
| connected, `feed` live, snapshot         | `fresh`        | full-opacity live values              |
| connected, `feed` down, earlier snapshot | `feed-down`    | full-opacity last known values        |
| connected, `feed` stale                  | `feed-stalled` | full-opacity last known values        |
| reconnecting / connecting                | `reconnecting` | full-opacity last known values        |
| idle / disconnected                      | `offline`      | full-opacity last known values        |
| connected, no snapshot ever              | any            | placeholders, "waiting for game data" |

Feed freshness is indicated by the status indicator; stale/down states do not
reduce HUD opacity.

Last known values stay visible rather than blanking, because a HUD that empties
itself on a one-second network blip is worse than one that retains useful
context while the status indicator marks it as not fresh.

The `feed-down` row is the one that matters most and the one that was wrong
first: a relay outliving its producer keeps a perfectly healthy socket, so a
HUD that derives freshness from the socket alone renders minutes-old vitals as
current. `apps/desktop/test/model.test.ts` pins this as a regression.

## Failure boundaries

| Failure                    | Behaviour                                                                       |
| -------------------------- | ------------------------------------------------------------------------------- |
| relay down at startup      | `connecting` -> `reconnecting`, bounded backoff                                 |
| managed SSH tunnel drops   | same reconnect state, enriched with SSH detail when supervisor knows            |
| Direct WSS token rejected  | never reaches connected; socket closes and bounded reconnect applies            |
| gateway/proxy drops        | Direct WSS enters reconnecting and recovers without retained gateway state      |
| relay restarts             | HUD reconnects; publisher reconnects while idle and restores selection as stale |
| one subscriber disconnects | others unaffected                                                               |
| producer dies              | snapshot retained, `feed` -> `down`, HUD shows no-data                          |

Backoff is exponential with jitter and a cap, so a long outage does not become
a reconnect storm when the relay returns.

## Relevant tests

- `apps/desktop/test/relay-source.test.ts` - phases, backoff, `stop()`,
  diagnostic detail, and authenticated connection confirmation on `hello`
- `apps/desktop/src-tauri/src/node.rs` - local-node ownership and health
  supervision
- `apps/desktop/src-tauri/src/tunnel.rs` - SSH-consumer port classification,
  argv, reconnect failure and owned-child shutdown
- `apps/desktop/test/model.test.ts` - seq reset, reconnect display rule
- `services/relay/tests/test_state.py` - feed transitions
- `services/relay/tests/test_server.py` - hello/snapshot/status ordering,
  subscriber isolation, producer-count cleanup
- `integrations/tinyfugue/tests/test_publisher.py` - idle disconnect detection,
  latest-selection reassertion, null-context recovery, and reconnect ownership
- `integrations/tinyfugue/tests/test_bridge.py` - relay-initiated producer
  close, clean handshake, and subsequent delivery
- `integrations/mudlet/tests/test_runtime.py` - focused-profile producer and
  action-consumer lifecycle
- `tests/e2e/relay_roundtrip.py` - reconnect receives the retained snapshot

## Change-impact notes

The local-node supervisor, SSH supervisor, and authenticated gateway are
intentionally outside this HUD state machine. Local/Managed diagnostics may
refine reconnect detail, while Direct WSS uses the same socket phases without
a loopback transport diagnostic. Neither adds
a `SourceEvent` kind or public HUD state. See
`docs/architecture/objects/state-source.md`,
`docs/architecture/objects/gateway.md`, and
`docs/architecture/processes/managed-runtime.md`.

## Verification

Status: verified
Verified against: the tests listed above, the original managed-SSH lifecycle
runtime checks, and Slice 11 live Direct-WSS recovery after a gateway
interruption as recorded in `docs/status.md`.
Slice 18 additionally verified injected-clock freshness regressions and real
loopback TinyFugue spool/feed and Mudlet runtime paths with unchanged observations,
ignored/rejected traffic, stale restoration, silence, and retained sequence/state.
