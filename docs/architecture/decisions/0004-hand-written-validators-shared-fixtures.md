# 0004 - Hand-written decoders in both languages, pinned by a shared fixture corpus

Status: accepted
Date: 2026-09-15

## Context

The protocol must be validated in TypeScript (HUD) and Python (relay, TF
adapter). The usual answer is a schema library per language - Zod for
TypeScript, `jsonschema` or Pydantic for Python.

## Decision

Both decoders are hand-written against the same spec, and both test suites run
the same JSON fixture corpus in `packages/protocol/fixtures/`.

- TypeScript: `packages/protocol/src/decode.ts`
- Python: `services/relay/src/imp_relay/protocol.py`
- Corpus consumers: `packages/protocol/test/fixtures.test.ts` and
  `services/relay/tests/test_protocol_fixtures.py`

## Rationale

Two different validation libraries produce two different semantics at exactly
the edges that matter here: integer vs. float coercion, surrogate handling,
unknown-key policy, and error paths. A shared corpus would then be documenting
a divergence rather than preventing one.

The protocol is small and closed - five message types, one nested state object.
Hand-writing it is a few hundred lines per language, adds no supply-chain
surface to a security boundary, and lets both implementations agree on error
codes and error paths exactly. The fixtures make that agreement executable:
each rejection fixture asserts a specific `code` and `path` in both languages.

## Consequences

- A protocol change is not done until both decoders and the corpus are updated.
  The corpus tests assert the corpus is non-empty, so a path typo cannot turn
  the conformance suite into a silent no-op.
- This is only defensible while the protocol stays small. If it grows a
  genuinely complex schema, revisit rather than keep hand-writing.
