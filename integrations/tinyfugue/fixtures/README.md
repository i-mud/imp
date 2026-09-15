# Replay fixtures

`session.jsonl` is a representative adapter stream, not a capture from the target MUD. It covers
identity, vitals, target acquisition, damage, and target clearing using the default mapping table.
`malformed.jsonl` contains intentionally hostile records. Its oversized line is generated at test
runtime from the `oversized` marker rather than stored as a large repository line.

A real capture must contain **one JSON object per line** in the adapter record format. Remove or
redact private content before adding it as a fixture.
