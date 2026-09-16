# Replay fixtures

`real-session.jsonl` is a selected, sanitized capture from the target MUD. Names
are replaced, timestamps are rebased, and room, inventory, group-member, and
other private values are omitted. Resource and target numbers retain their
captured string types and transitions. The fixture covers observed
`Char.Status` identity and partial updates, complete `Char.Vitals` snapshots,
target acquisition/damage/clearing, and identity-preserving handling of the
observed but unmapped `Char.Group.List` package.

The source capture contained 249 raw hook lines over 214.662 seconds. Of those,
200 contained valid JSON. The converter safely rejected 49 `Char.Items.Add` or
`Char.Items.Remove` events because the server payloads contained unescaped
control characters inside JSON strings. Those inventory events are unrelated
to the HUD contract and are not repaired or guessed here.

`malformed.jsonl` contains intentionally hostile records. Its oversized line
is generated at test runtime from the `oversized` marker rather than stored as
a large repository line.
