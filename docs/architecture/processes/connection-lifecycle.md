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

On `connected` the relay sends `hello`, then the retained `snapshot` if it has
one, then `status`. The HUD resets its `seq` high-water mark on `hello` and on
every connection transition, because the relay's counter restarts with its
process.

Feed status, evaluated by the relay:

| Condition                                          | `feed`  |
| -------------------------------------------------- | ------- |
| no producer connected                              | `down`  |
| producer connected, no publish in the stale window | `stale` |
| otherwise                                          | `live`  |

The relay re-evaluates on a timer, so `live -> stale` is emitted without a
publish arriving. Without that timer a dead feed would look live forever.

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

| Failure                    | Behaviour                                              |
| -------------------------- | ------------------------------------------------------ |
| relay down at startup      | `connecting` -> `reconnecting`, backoff                |
| SSH tunnel drops           | indistinguishable from relay down, same path           |
| relay restarts             | new `hello`, `seq` reset, fresh snapshot               |
| one subscriber disconnects | others unaffected                                      |
| producer dies              | snapshot retained, `feed` -> `down`, HUD shows no-data |

Backoff is exponential with jitter and a cap, so a long outage does not become
a reconnect storm when the relay returns.

## Relevant tests

- `apps/desktop/test/relay-source.test.ts` - phases, backoff, `stop()`
- `apps/desktop/test/model.test.ts` - seq reset, reconnect display rule
- `services/relay/tests/test_state.py` - feed transitions
- `services/relay/tests/test_server.py` - hello/snapshot/status ordering,
  subscriber isolation
- `tests/e2e/relay_roundtrip.py` - reconnect receives the retained snapshot

## Change-impact notes

The SSH tunnel is invisible here on purpose: to the HUD it is indistinguishable
from a local relay. Automating tunnel management adds a step before `start()`
and changes the URL - it does not add a state to this machine. See
`docs/architecture/objects/state-source.md` and
`docs/architecture/decisions/0001-loopback-relay-and-ssh-boundary.md`.

## Verification

Status: verified
Verified against: bootstrap test runs listed above.
