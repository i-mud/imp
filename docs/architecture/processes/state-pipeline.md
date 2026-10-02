# State pipeline: GMCP to pixels

## Entry points

- GMCP traffic received by TinyFugue or Mudlet
- `integrations/tinyfugue/src/imp_tf/feed.py`
- `integrations/tinyfugue/src/imp_tf/replay.py`
- `integrations/mudlet/src/imp_mudlet/runtime.py`
- `integrations/common/src/imp_adapter/records.py`
- `integrations/common/src/imp_adapter/normalize.py`
- `integrations/common/src/imp_adapter/publisher.py`
- `apps/desktop/src/lib/source/mock.ts`

## Flow

```text
MUD
 |
 | GMCP packages
 v
MUD client (TinyFugue / Mudlet)
 |
 | client-specific capture + lifecycle
 v
client adapter
 |
 | Record(at, package, payload)
 v
shared GMCP adapter / normalize.py
 |
 | canonical GameState       <-- last point where GMCP concepts exist
 v
shared publisher.py
 |
 | select / matching-context publish
 v
same-host Imp node /ingest
 |
 | protocol decode (fail closed)
 v
RelayState                    <-- owns selected context + snapshot + seq + feed
 |
 | desktop-facing state/action capability
 |---------------- local loopback ------------------------.
 |                                                       |
 |-- desktop :8789 -> SSH -> remote node ----------------|
 |                                                       |
 `-- authenticated gateway -> TLS/WSS -------------------|
                                                         |
relay.ts (RelayStateSource) <----------------------------'
 |
 | decodeServerMessage (fail closed)
 v
SourceEvent
 |
 v
HudModel
 |
 v
components/*.svelte
```

TinyFugue and Mudlet differ before the shared `Record` boundary. MUD-specific
GMCP interpretation is shared after that boundary.

Outbound actions are a separate reverse path:

```text
desktop ActionSink
  -> selected node /action
     (local directly, SSH through :8789, or authenticated WSS gateway)
  -> one matching /action-consumer
  -> client-specific final hop:

     TinyFugue:
       private context check
       -> fixed /imp_send bridge line
       -> synchronous TF context/world fence
       -> send()

     Mudlet:
       helper -> Lua adapter
       -> exact context recheck
       -> send(command, false)
```

The broker permits one action in flight and has no queue, automatic retry,
fan-out, or replay. `ActionSink` remains separate from `StateSource`.

The mock source joins at `SourceEvent` by building a frame and passing it
through the real decoder, so development still exercises protocol validation.

## Major dependencies

| Hop                  | Depends on                                                         |
| -------------------- | ------------------------------------------------------------------ |
| TinyFugue -> adapter | fixed TF hooks, private drained spool, `imp-feed`                  |
| Mudlet -> adapter    | installed Lua package and desktop-provisioned helper               |
| adapter -> node      | shared Python record/normalizer/publisher runtime and `websockets` |
| node -> local HUD    | loopback WebSocket                                                 |
| remote node -> HUD   | SSH consumer endpoint `8789`, or authenticated gateway + TLS/WSS   |
| HUD                  | Tauri 2 webview, Svelte 5                                          |

## Validation points

State is validated at each trust boundary rather than assuming an upstream
component behaved correctly:

1. the client adapter validates its client-specific event/input boundary;
2. `Record` establishes the shared client-neutral GMCP shape;
3. the shared normalizer projects MUD data into canonical Imp state;
4. `publisher.py` emits protocol-valid producer messages;
5. the node `/ingest` decoder independently validates producer frames; and
6. `relay.ts` independently validates desktop-facing frames.

Actions are validated before the desktop opens its request socket, again at the
node, and at the client-specific final hop.

TinyFugue uses its private exact-context marker plus the synchronous
world/context fence immediately before `send()`. Mudlet refreshes profile focus
and rechecks `(session, foreground, connection)` immediately before
`send(command, false)`.

## Failure boundaries

| Failure                           | Behaviour                                                                                                          |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| malformed client-specific event   | rejected/skipped by that adapter boundary without creating partial canonical state                                 |
| unrecognised GMCP package         | previous canonical state remains unchanged                                                                         |
| non-authoritative client context  | never becomes the selected published context until its lifecycle makes it authoritative                            |
| malformed `select` / `publish`    | rejected whole; stored state untouched; producer closed                                                            |
| mismatched-context `publish`      | ignored                                                                                                            |
| node closes producer              | shared publisher reconnects and reasserts only retained selection; freshness remains stale until a genuine publish |
| TinyFugue feed exits              | systemd restarts one lock-protected replacement                                                                    |
| TinyFugue spool target missing    | TinyFugue loses updates but never blocks                                                                           |
| Mudlet helper disappears          | that profile stops producing/consuming until helper lifecycle is re-established                                    |
| desktop local node exits          | native supervisor retries; adapters reconnect                                                                      |
| Managed SSH child exits           | transport supervisor retries; HUD remains reconnecting                                                             |
| Direct WSS authentication fails   | no upstream node connection is opened                                                                              |
| gateway or reverse proxy drops    | desktop reconnects with bounded backoff; no state/action replay                                                    |
| producer disconnects              | snapshot retained; `feed` becomes `down`                                                                           |
| binary or malformed HUD frame     | `protocol-error`; socket closes; state remains untouched                                                           |
| unknown message `type`            | ignored for forwards compatibility                                                                                 |
| node unreachable                  | producer/desktop reconnect according to their bounded policies                                                     |
| action context/helper unavailable | rejected without dispatch                                                                                          |
| action times out after dispatch   | `unknown`; never retried automatically                                                                             |

## Relevant tests

- `packages/protocol/test/` and
  `services/relay/tests/test_protocol_fixtures.py` - shared wire corpus
- `integrations/common/tests/` - client-neutral records, normalization, and
  publishing
- `integrations/tinyfugue/tests/` - TF event/feed/context/action lifecycle
- `integrations/mudlet/tests/` - Mudlet lifecycle, bridge, state publishing,
  and action delivery
- `services/relay/tests/test_server.py` - loopback producer/subscriber/action
  lifecycle
- `services/relay/tests/test_gateway.py` and `test_gateway_config.py` -
  authenticated remote state/action boundary
- `apps/desktop/test/` - source, action sink, reducer, and runtime selection
- `apps/desktop/src-tauri/src/node.rs` - local-node ownership/supervision tests
- `apps/desktop/src-tauri/src/tunnel.rs` - SSH consumer ownership/adoption tests
- `apps/desktop/src-tauri/src/gateway.rs` - local gateway ownership tests
- `tests/e2e/relay_roundtrip.py` - producer -> node -> subscriber

## Change-impact notes

Changing a hop boundary is substantially more expensive than adding a canonical
state field. State-field changes follow
`docs/architecture/objects/game-state.md`.

MUD-specific interpretation belongs in the shared GMCP adapter, not in a
TinyFugue- or Mudlet-specific implementation. Client-specific changes should
stop at the shared `Record` boundary unless they introduce genuinely new
client-neutral lifecycle metadata.

TinyFugue macro/context changes still require rerunning the connectionless
procedure in `integrations/tinyfugue/README.md` before claiming changed
TinyFugue behavior is live-verified. Mudlet lifecycle/final-hop changes require
the corresponding Mudlet integration acceptance.

Current normalization remains limited to observed AVATAR GMCP behavior rather
than guessed mappings.

## Verification

Status: state and trusted-action paths live-verified for TinyFugue and Mudlet.

Shared state evidence includes the protocol/relay/adapter test suites, the
sanitized TinyFugue fixture, local and VPS Imp nodes, local/Managed SSH and
authenticated-WSS desktop transports, and the native Windows Tauri HUD.

TinyFugue live evidence covers exact-current delivery, foreground and
connection-generation fences, pinned-world no-retarget behavior, one-shot
helper replacement, relay restart, helper loss/recovery, and prompt shutdown.
The connectionless echo-world procedure proves the TinyFugue bridge/fences but
does not by itself prove MUD-server command execution; separate native
acceptance sent a real `look` through the TinyFugue/MUD path.

Mudlet live evidence covers GMCP state, profile switching, foreground
authority, OS focus changes, exact-context trusted actions, and a real `look`
through Mudlet to the MUD. Remote Mudlet-backed state/actions were also
verified through SSH and authenticated WSS transport without moving remote
transport concerns into the Mudlet adapter.

`docs/status.md` remains the canonical evidence record.
