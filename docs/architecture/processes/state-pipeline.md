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
 |  newline-delimited JSON records
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

| Hop             | Depends on                                     |
| --------------- | ---------------------------------------------- |
| TF -> bridge    | TinyFugue GMCP capture (**partly unverified**) |
| bridge -> relay | `websockets` client                            |
| relay -> HUD    | SSH port-forward, `websockets` server          |
| HUD             | Tauri 2 webview, Svelte 5                      |

## Validation points

State is validated three times, and this is intentional rather than redundant:

1. `records.parse_record` - the TF record envelope, closest to the MUD
2. `publisher.py` - the producer validates its own output so it never sends
   something the relay would reject
3. `relay /ingest` and `relay.ts` - each consumer independently validates what
   it receives, because neither trusts its peer's diligence

## Failure boundaries

| Failure                     | Behaviour                                               |
| --------------------------- | ------------------------------------------------------- |
| malformed TF record         | line skipped and counted; bridge stays up               |
| unrecognised GMCP package   | previous state retained unchanged                       |
| malformed `publish`         | rejected whole; stored state untouched; producer closed |
| producer disconnects        | snapshot retained; `feed` becomes `down`                |
| malformed `snapshot` at HUD | `protocol-error`; HUD state untouched                   |
| unknown message `type`      | silently ignored (forwards compatibility)               |
| relay unreachable           | `reconnecting` with bounded exponential backoff         |

## Relevant tests

- `packages/protocol/test/` and `services/relay/tests/test_protocol_fixtures.py`
  (shared corpus, both languages)
- `services/relay/tests/test_server.py` (loopback server behaviour)
- `integrations/tinyfugue/tests/test_normalize.py` (GMCP mapping)
- `apps/desktop/test/` (reducer and both sources)
- `tests/e2e/relay_roundtrip.py` (producer -> relay -> subscriber)

## Change-impact notes

Changing the hop boundaries is the expensive kind of change. Adding a field is
cheap and follows the chain in `docs/architecture/objects/game-state.md`.

The hop marked **partly unverified** is TF -> bridge: our MUD's actual GMCP
packages have not been observed yet. Everything downstream of `normalize.py` is
verified against fixtures, so replacing the TF capture does not require
touching the relay or the HUD. See `integrations/tinyfugue/README.md` for the
list of specific unknowns.

## Verification

Status: verified from `normalize.py` downstream; the TF capture hop is
unverified by design.

Verified against: the test suites listed above, plus a live run of the whole
VPS-side chain - `integrations/tinyfugue/fixtures/session.jsonl` replayed into
a loopback relay, with the rendered HUD values matching the replay output. The
evidence, including what was _not_ verified, is tabulated in `docs/status.md`;
that table is the canonical record, not this card.
