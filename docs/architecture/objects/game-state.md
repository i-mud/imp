# GameState

## Purpose

The normalized character/target state that TinyScry owns. Every component
downstream of MUD normalization speaks this shape and only this shape.

## Canonical reference

`packages/protocol/SPEC.md` - the shape, bounds, message set and versioning
policy. This card does not restate them; it records relationships and change
impact.

## Source

- `packages/protocol/src/state.ts` - types, `EMPTY_STATE`, `vitalFraction`
- `packages/protocol/src/messages.ts` - envelope types, `PROTOCOL_VERSION`
- `packages/protocol/src/limits.ts` - `LIMITS`, `isSafeText`
- `packages/protocol/src/decode.ts` - the TypeScript decoder
- `services/relay/src/tinyscry_relay/protocol.py` - the Python decoder
- `packages/protocol/fixtures/` - the shared conformance corpus

## Relationships

Produced by:

- `integrations/tinyfugue/src/tinyscry_tf/normalize.py` (from GMCP records)
- `apps/desktop/src/lib/source/mock.ts` (development source)

Validated by:

- both decoders listed above, independently, against the same corpus

Carried by:

- `snapshot` and `publish` messages

Consumed by:

- `apps/desktop/src/lib/hud/model.ts`, then the HUD components

## Change impact

Known first-order impacts of adding or changing a field:

1. `packages/protocol/src/state.ts` and `packages/protocol/src/decode.ts`
2. `services/relay/src/tinyscry_relay/protocol.py` - the dataclass, the
   decoder and the encoders; must move in lockstep or the corpus test fails
3. `packages/protocol/fixtures/` - at least one accept case; a rejection case
   if new bounds were added
4. `integrations/tinyfugue/src/tinyscry_tf/normalize.py` - the mapping table,
   and its own name/value bounds if the change is a bound
5. `integrations/tinyfugue/src/tinyscry_tf/publisher.py` - `state_to_wire()`
   and its per-field helpers spell the JSON by hand, so a new field is silently
   dropped on publish if this is missed
6. `apps/desktop/src/lib/source/mock.ts` - the mock must keep producing valid
   state
7. `apps/desktop/src/components/Hud.svelte` - **both** render branches, the
   fresh one and the last-known one
8. `packages/protocol/SPEC.md` - it is the canonical description

Then the test literals. Because `Character` fields are required rather than
optional, every constructed fixture in these files stops compiling or asserting
correctly until it is updated:

- `packages/protocol/test/decode.test.ts`
- `apps/desktop/test/model.test.ts`
- `apps/desktop/test/relay-source.test.ts`
- `services/relay/tests/test_protocol_units.py`
- `services/relay/tests/test_server.py`
- `integrations/tinyfugue/tests/test_normalize.py`
- `integrations/tinyfugue/tests/test_publisher.py`
- `tests/e2e/relay_roundtrip.py`

That breakage is the intended tripwire: an incomplete protocol change cannot
pass `npm run check`.

## Invariants

- `current > max` is valid and must be preserved in the numerals.
- Absent and `null` are equivalent on decode.
- Unknown keys are dropped, never retained - the decoder is a projection onto
  the owned shape, so a newer peer cannot inject fields into HUD state.
- Control characters are rejected here, not stripped. Stripping is the
  normalizer's job, upstream of the protocol.
- A rejected frame yields no value at all, so no caller can apply a partial
  state.

## Verification

Status: verified
Verified against: `packages/protocol` at bootstrap; 44 TypeScript protocol
tests passing over a 10-accept / 22-reject corpus.
