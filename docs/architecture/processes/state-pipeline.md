# State pipeline: GMCP to pixels

## Entry points

- MUD GMCP traffic arriving in TinyFugue on the VPS
- `integrations/tinyfugue/src/tinyscry_tf/feed.py` (live state)
- `integrations/tinyfugue/src/tinyscry_tf/replay.py` (fixture replay)
- `apps/desktop/src/lib/source/mock.ts` (no VPS involved at all)

## Flow

```text
MUD
 |  GMCP packages
TinyFugue  ---- integrations/tinyfugue/tinyscry.tf
 |  TS2 events: session + world + foreground/connection generations
private drained spool
 |  tinyscry-feed: strict event parsing and per-world normalization
normalize.py
 |  GameState              <-- last point where GMCP concepts exist
publisher.py
 |  select / matching-context publish, validated before sending
relay /ingest
 |  protocol.decode_client_message  (fail-closed)
RelayState                  <-- owns selected context + snapshot + seq + feed
 |  desktop-facing state/action capability
 |-- SSH local forward -------------------------------.
 |                                                   |
 `-- authenticated gateway -> TLS reverse proxy -> WSS
                                                     |
relay.ts  (RelayStateSource) <-----------------------'
 |  decodeServerMessage     (fail-closed)
 |  SourceEvent
model.ts  (applyEvent)
 |  HudModel
components/*.svelte
```

Outbound actions are a separate reverse path:

```text
desktop ActionSink
  -> SSH-forwarded relay /action
     OR authenticated gateway /action -> relay /action
  -> one matching /action-consumer
  -> private context check -> one fixed /tinyscry_send <encoded-data> TF line
  -> helper exit -> synchronous TF context/world fence -> textdecode() -> send()
  -> guarded replacement helper
  -> idle reader loss -> silent helper exit, with no reconnect
```

It has one action in flight, no queue/retry/replay, and never becomes a method
on `StateSource`.

The mock source joins at `SourceEvent`, by building a frame and passing it
through the same `decodeServerMessage`. That is deliberate: the development
path exercises real validation rather than bypassing it.

## Major dependencies

| Hop           | Depends on                                                 |
| ------------- | ---------------------------------------------------------- |
| TF -> feed    | verified TinyFugue hook + private drained spool            |
| feed -> relay | `websockets` client                                        |
| relay -> HUD  | system OpenSSH forward, or authenticated gateway + TLS/WSS |
| HUD           | Tauri 2 webview, Svelte 5                                  |

## Validation points

State is validated at each trust boundary, intentionally rather than
redundantly:

1. `events.parse_tf_event` - versioned TF event envelope and raw GMCP
2. `publisher.py` - producer output through the canonical encoder
3. relay `/ingest` and `relay.ts` - each consumer independently validates what
   it receives, because neither trusts its peer's diligence

Actions are checked before the desktop opens a socket, by both protocol
decoders, and against the private context marker immediately before the helper
writes TinyFugue input.

## Failure boundaries

| Failure                           | Behaviour                                                                                                               |
| --------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| malformed TF event                | line skipped and counted; feed stays up                                                                                 |
| unrecognised GMCP package         | that world's previous state remains unchanged                                                                           |
| inactive-world GMCP               | cached per world; never published until selected                                                                        |
| malformed `select`/`publish`      | rejected whole; stored state untouched; producer closed                                                                 |
| mismatched-context `publish`      | ignored                                                                                                                 |
| relay closes producer             | publisher reconnects while idle and reasserts only its retained selection; feed remains `stale` until a genuine publish |
| feed process exits                | systemd restarts one lock-protected replacement                                                                         |
| hook spool target missing         | TinyFugue loses updates but never blocks                                                                                |
| managed SSH child exits           | supervisor retries; HUD remains in reconnecting presentation                                                            |
| Direct WSS authentication fails   | no relay connection is opened; desktop socket closes and reconnects                                                     |
| gateway or reverse proxy drops    | desktop state socket reconnects with bounded backoff; no state/action replay                                            |
| producer disconnects              | snapshot retained; `feed` becomes `down`                                                                                |
| binary or malformed HUD frame     | `protocol-error`; current socket closes, state remains untouched, and reconnect starts                                  |
| unknown message `type`            | silently ignored (forwards compatibility)                                                                               |
| relay unreachable                 | publisher retries with bounded exponential backoff                                                                      |
| action context/helper unavailable | rejected without dispatch                                                                                               |
| action times out after dispatch   | `unknown`; never retried automatically                                                                                  |
| TinyFugue closes idle action pipe | helper writes nothing, closes its consumer socket, and exits; no reconnect or result                                    |

## Relevant tests

- `packages/protocol/test/` and `services/relay/tests/test_protocol_fixtures.py`
  (shared corpus, both languages)
- `services/relay/tests/test_server.py` (loopback, Origin policy, producer, and
  action lifecycle)
- `services/relay/tests/test_gateway.py` and `test_gateway_config.py`
  (authentication, endpoint/origin policy, loopback gateway boundary, state
  and action bridging)
- `integrations/tinyfugue/tests/test_feed.py` (per-world state, checkpoints,
  selection, and cancellation)
- `integrations/tinyfugue/tests/test_action_consumer.py` (private context and
  fixed-macro delivery)
- `integrations/tinyfugue/tests/test_normalize.py` (GMCP mapping)
- `apps/desktop/test/` and Rust tunnel tests (source, action sink, reducer, SSH)
- `tests/e2e/relay_roundtrip.py` (producer -> relay -> subscriber)

## Change-impact notes

Changing the hop boundaries is the expensive kind of change. Adding a field is
cheap and follows the chain in `docs/architecture/objects/game-state.md`.

The original TF -> bridge hop was verified against TinyFugue
5.1.6-4-ga15a165 on the VPS and a target-MUD capture. The current versioned
spool and action path have deterministic protocol/process coverage, but the
macro test is structural and does not execute TinyFugue. Run the connectionless
outbound procedure in `integrations/tinyfugue/README.md` before recording live
action evidence. Mappings in `normalize.py` remain limited to observed
`Char.Status` and `Char.Vitals` fields.

## Verification

Status: state path verified end to end; outbound TinyFugue bridge live-verified

Verified state evidence includes the test suites listed above; the sanitized
`integrations/tinyfugue/fixtures/real-session.jsonl`; the VPS loopback relay;
both SSH and authenticated Direct-WSS desktop transports; and the native
Windows Tauri HUD. Runtime exercises covered initial identity/resources,
damage and recovery, target acquisition/damage/clearing, producer stall and
exit, relay restart, SSH-tunnel interruption, valid and invalid Direct-WSS
authentication, and recovery after a gateway interruption.

The outbound bridge was live-verified using real TinyFugue
`5.2.2-3-g4f0ff34`, pinned to
`4f0ff34145b7c3f23e6233874d45ee102d98d9e9`, with connectionless echo worlds.
The evidence covered exact-current delivery, synchronous foreground and
connection-generation fences, pinned-world no-retarget behavior, one-shot
helper replacement, relay restart, helper loss and recovery, and prompt
shutdown. The pinning probe proved only that the raced action was not
retargeted to the newly foregrounded world; it did not establish whether the
action completed on its originally pinned world or was synchronously
suppressed. Because the echo worlds had no MUD connections, this does not prove
command execution by a real MUD server. `docs/status.md` remains the canonical
evidence record.
