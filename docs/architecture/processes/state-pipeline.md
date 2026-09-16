# State pipeline: GMCP to pixels

## Entry points

- MUD GMCP traffic arriving in TinyFugue on the VPS
- `integrations/tinyfugue/src/tinyscry_tf/bridge.py` (real session)
- `integrations/tinyfugue/src/tinyscry_tf/replay.py` (fixture replay)
- `apps/desktop/src/lib/source/mock.ts` (no VPS involved at all)

## Flow

```
MUD
 |  GMCP packages
TinyFugue  ---- integrations/tinyfugue/tinyscry.tf
 |  <epoch-seconds> <package> [JSON]
capture.py                 (checked epoch conversion + JSON envelope)
 |  newline-delimited adapter JSON records
bridge.py
 |  records.parse_record   (fail-closed, bounded, counted rejections)
normalize.py
 |  GameState              <-- last point where GMCP concepts exist
publisher.py
 |  publish frame, validated before sending
relay /ingest
 |  protocol.decode_client_message  (fail-closed)
RelayState                  <-- owns snapshot + seq + feed status
 |  snapshot frame, broadcast
SSH port-forward            <-- see decisions/0001
relay.ts  (RelayStateSource)
 |  decodeServerMessage     (fail-closed)
 |  SourceEvent
model.ts  (applyEvent)
 |  HudModel
components/*.svelte
```

The mock source joins at `SourceEvent`, by building a frame and passing it
through the same `decodeServerMessage`. That is deliberate: the development
path exercises real validation rather than bypassing it.

## Major dependencies

| Hop             | Depends on                                  |
| --------------- | ------------------------------------------- |
| TF -> bridge    | Verified TinyFugue GMCP hook + `capture.py` |
| bridge -> relay | `websockets` client                         |
| relay -> HUD    | SSH port-forward, `websockets` server       |
| HUD             | Tauri 2 webview, Svelte 5                   |

## Validation points

State is validated three times, and this is intentional rather than redundant:

1. `records.parse_record` - the TF record envelope, closest to the MUD
2. `publisher.py` - the producer validates its own output so it never sends
   something the relay would reject
3. `relay /ingest` and `relay.ts` - each consumer independently validates what
   it receives, because neither trusts its peer's diligence

## Failure boundaries

| Failure                     | Behaviour                                                         |
| --------------------------- | ----------------------------------------------------------------- |
| malformed TF record         | line skipped and counted; bridge stays up                         |
| unrecognised GMCP package   | previous state retained unchanged                                 |
| malformed `publish`         | rejected whole; stored state untouched; producer closed           |
| relay closes producer       | socket closes; next material state reconnects before further read |
| producer disconnects        | snapshot retained; `feed` becomes `down`                          |
| malformed `snapshot` at HUD | `protocol-error`; HUD state untouched                             |
| unknown message `type`      | silently ignored (forwards compatibility)                         |
| relay unreachable           | publisher retries with bounded exponential backoff                |

## Relevant tests

- `packages/protocol/test/` and `services/relay/tests/test_protocol_fixtures.py`
  (shared corpus, both languages)
- `services/relay/tests/test_server.py` (loopback server and producer lifecycle)
- `integrations/tinyfugue/tests/test_bridge.py` (producer close and reconnect)
- `integrations/tinyfugue/tests/test_normalize.py` (GMCP mapping)
- `apps/desktop/test/` (reducer and both sources)
- `tests/e2e/relay_roundtrip.py` (producer -> relay -> subscriber)

## Change-impact notes

Changing the hop boundaries is the expensive kind of change. Adding a field is
cheap and follows the chain in `docs/architecture/objects/game-state.md`.

The TF -> bridge hop was verified against TinyFugue 5.1.6-4-ga15a165
on the VPS and a target-MUD capture. Mappings in `normalize.py` contain only
observed `Char.Status` and `Char.Vitals` fields. Valid unrecognized packages
remain identity-preserving. Invalid inventory JSON containing unescaped
control characters is rejected rather than repaired.

## Verification

Status: verified end to end

Verified against: the test suites listed above; the sanitized
`integrations/tinyfugue/fixtures/real-session.jsonl`; a VPS loopback relay; a
manual SSH local forward; and the native Windows Tauri HUD. The runtime
exercise covered initial identity/resources, damage and recovery, target
acquisition/damage/clearing, producer stall and exit, relay restart, and SSH
tunnel interruption. The evidence is tabulated in `docs/status.md`; that table
is the canonical record.
