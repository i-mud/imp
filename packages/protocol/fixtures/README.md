# Protocol fixture corpus

Cross-language conformance suite for the Imp protocol. Both decoders run
this same corpus, which is what keeps the TypeScript and Python implementations
from drifting apart - see
`docs/architecture/decisions/0004-hand-written-validators-shared-fixtures.md`.

Consumers:

- `packages/protocol/test/fixtures.test.ts`
- `services/relay/tests/test_protocol_fixtures.py`

## File format

Each fixture is a JSON object. `frame` holds the **exact raw wire text** handed
to the decoder, which is why it is a string rather than a nested object: that is
the only way to express malformed JSON, oversized frames and smuggled control
characters.

```json
{
  "description": "ANSI escape smuggled into a character name",
  "direction": "server",
  "expect": "reject",
  "code": "invalid_field",
  "path": "state.character.name",
  "frame": "{\"type\":\"snapshot\", ... }"
}
```

| Field         | Notes                                                      |
| ------------- | ---------------------------------------------------------- |
| `direction`   | `server` -> `decodeServerMessage`, `client` -> `decodeClientMessage` |
| `expect`      | `accept` or `reject`, matching the directory               |
| `code`, `path`| rejections only; asserted exactly, in both languages       |

## Directories

- `accept/` - must decode successfully.
- `reject/` - must fail with exactly the stated `code` and `path`.

Both suites assert the corpus is non-empty, so a wrong path cannot silently
turn the conformance tests into a no-op.

## Adding a fixture

Prefer adding a case here over adding a language-specific unit test: a fixture
constrains both implementations, a unit test constrains one. Reserve unit tests
for things a JSON file cannot express - `packages/protocol/test/decode.test.ts`
covers the frame-size boundary and lone-surrogate handling for that reason.

These files are generated as needed but are checked in and edited by hand;
there is no generator to re-run.
