# Connection lifecycle

## Why this is a process card

Two independent liveness signals decide what the HUD shows, and conflating them
is the most likely bug in this area:

- **Socket liveness** - can the HUD reach the relay? Owned by
  `apps/desktop/src/lib/source/relay.ts`.
- **Feed liveness** - is the MUD still feeding the relay? Owned by
  `services/relay/src/tinyscry_relay/state.py` and reported as `status.feed`.

A healthy socket with a dead feed is a real and common state: the relay is
fine, TinyFugue is gone. The HUD must show "no data", not confidently stale
vitals.

## Entry points

- `RelayStateSource.start()` / `.stop()`
- `RelayState.producer_connected()` / `.producer_disconnected()`
- the relay's periodic stale-window check

## Flow

```
idle
 |  start()
connecting ----------- open ----------> connected
 |                                        |
 |  close / error                         |  close / error
 v                                        v
reconnecting --- backoff, retry ----> connecting
 |  stop()
disconnected
```

On `connected` the relay sends `hello`, then the retained context-bearing
`snapshot` if it has one, then `status`. The HUD resets its `seq` high-water
mark on `hello` and on every connection transition, because the relay's counter
restarts with its process. A non-connected phase also clears the actionable
context, so stale displayed values cannot authorize an action.

Feed status, evaluated by the relay:

| Condition                                                     | `feed`  |
| ------------------------------------------------------------- | ------- |
| no producer connected                                         | `down`  |
| producer connected, selection has no matching-context publish | `stale` |
| matching-context publish arrived within the stale window      | `live`  |

The relay re-evaluates on a timer, so `live -> stale` is emitted without a
publish arriving. Without that timer a dead feed would look live forever.

Producer-side liveness has the same event-loop constraint. `RelayPublisher`
owns one long-lived reconnect task, so an idle `/ingest` disconnect is detected
without waiting for another GMCP or normalized-state event. It disposes the
closed transport, connects one replacement with bounded backoff, and reasserts
only the latest retained `select(context, state)`. It never replays a retained
`publish`, and no action state is retained or replayed.

That recovered selection is `stale`; freshness returns only after a genuine new
matching-context publish. If the latest retained selection has `context: null`,
the publisher reasserts it as non-actionable. One reconnect owner prevents a
second producer session from overlapping the first.

## HUD presentation states

`freshnessOf()` in `apps/desktop/src/lib/hud/model.ts` classifies the pipeline;
the component renders that classification. Values are shown at full confidence
only when both signals are healthy.

| Situation                                | `freshnessOf`  | Presentation                               |
| ---------------------------------------- | -------------- | ------------------------------------------ |
| connected, `feed` live, snapshot         | `fresh`        | full colour, live values                   |
| connected, `feed` down, earlier snapshot | `feed-down`    | dimmed, last known values, "no game feed"  |
| connected, `feed` stale                  | `feed-stalled` | dimmed, last known values, "feed stalled"  |
| reconnecting / connecting                | `reconnecting` | dimmed, last known values, "reconnecting"  |
| idle / disconnected                      | `offline`      | dimmed, last known values, "not connected" |
| connected, no snapshot ever              | any            | placeholders, "waiting for game data"      |

Last known values stay visible rather than blanking, because a HUD that empties
itself on a one-second network blip is worse than one that marks its values as
not fresh.

The `feed-down` row is the one that matters most and the one that was wrong
first: a relay outliving its producer keeps a perfectly healthy socket, so a
HUD that derives freshness from the socket alone renders minutes-old vitals as
current. `apps/desktop/test/model.test.ts` pins this as a regression.

## Failure boundaries

| Failure                    | Behaviour                                                                       |
| -------------------------- | ------------------------------------------------------------------------------- |
| relay down at startup      | `connecting` -> `reconnecting`, bounded backoff                                 |
| managed SSH tunnel drops   | same reconnect state, enriched with SSH detail when supervisor knows            |
| relay restarts             | HUD reconnects; publisher reconnects while idle and restores selection as stale |
| one subscriber disconnects | others unaffected                                                               |
| producer dies              | snapshot retained, `feed` -> `down`, HUD shows no-data                          |

Backoff is exponential with jitter and a cap, so a long outage does not become
a reconnect storm when the relay returns.

## Relevant tests

- `apps/desktop/test/relay-source.test.ts` - phases, backoff, `stop()`,
  diagnostic detail
- `apps/desktop/src-tauri/src/tunnel.rs` - port classification, argv,
  reconnect failure and owned-child shutdown
- `apps/desktop/test/model.test.ts` - seq reset, reconnect display rule
- `services/relay/tests/test_state.py` - feed transitions
- `services/relay/tests/test_server.py` - hello/snapshot/status ordering,
  subscriber isolation, producer-count cleanup
- `integrations/tinyfugue/tests/test_publisher.py` - idle disconnect detection,
  latest-selection reassertion, null-context recovery, and reconnect ownership
- `integrations/tinyfugue/tests/test_bridge.py` - relay-initiated producer
  close, clean handshake, and subsequent delivery
- `tests/e2e/relay_roundtrip.py` - reconnect receives the retained snapshot

## Change-impact notes

The SSH supervisor is intentionally outside this state machine. Its internal
diagnostic may refine a reconnect detail, but it does not add a `SourceEvent`
kind or a public HUD state. See `docs/architecture/objects/state-source.md` and
`docs/architecture/processes/managed-runtime.md`.

## Verification

Status: verified
Verified against: managed-runtime tests listed above and the original
connection-lifecycle runtime checks recorded in `docs/status.md`.
