# TinyFugue adapter

TinyScry keeps MUD-specific data outside the desktop application:

```text
MUD GMCP -> TinyFugue adapter record -> tinyscry-bridge -> relay ingest (loopback) -> SSH tunnel -> desktop HUD
```

The bridge connects only to `ws://127.0.0.1:8787/ingest` by default. The relay is deliberately
loopback-only and has no application authentication: the SSH tunnel and the VPS loopback interface
are the security boundary. A different host is rejected unless `--allow-non-loopback` is explicit.
Never put a relay directly on a public interface.

## Adapter record contract

The TF-facing adapter emits exactly one UTF-8 JSON object per line:

```json
{ "at": 1710000000000, "package": "Char.Vitals", "payload": { "hp": "120", "maxhp": "150" } }
```

- `at` is a non-negative epoch-millisecond integer.
- `package` is the GMCP package name, with no control characters.
- `payload` is the parsed JSON value sent by the MUD; it is not a stringified JSON fragment.

The bridge rejects malformed, non-object, oversize, or unsafe records without logging their raw
content. It logs only a bounded rejection code and continues. The Python normalizer owns all GMCP
JSON parsing and conversion; it never executes data received from a MUD.

## Running

On the VPS, start the relay then let the TF-side adapter write records to the bridge's standard input
or to a FIFO:

```sh
uv run --project integrations/tinyfugue tinyscry-bridge < records.jsonl
mkfifo /run/user/$UID/tinyscry-gmcp.fifo
uv run --project integrations/tinyfugue tinyscry-bridge --fifo /run/user/$UID/tinyscry-gmcp.fifo
```

Replay the supplied fixture without a relay:

```sh
uv run --project integrations/tinyfugue python -m tinyscry_tf.replay fixtures/session.jsonl --dry-run
```

`--dry-run` prints each changed, complete normalized state as protocol JSON and opens no socket.
Use `--relay-url` and `--interval` for a paced live replay; non-loopback relay URLs also require
`--allow-non-loopback`.

## TinyFugue script and safe capture path

`tinyscry.tf` documents the proposed hook for a TF build that exposes raw GMCP events. Do **not**
use TF string interpolation to construct JSON or a shell command from GMCP. Vanilla TF has no
verified JSON escaping API in this project, so the primary safe path is a small, build-specific TF
hook that writes the complete raw GMCP event to a FIFO using an API that performs a direct write
(not `/sh`, `/quote`, command substitution, or a shell). A local wrapper must then JSON-encode the
record envelope before it reaches this bridge. Until that verified hook exists, use a capture/replay
file generated outside TF; do not guess at quoting.

## Required real-session verification

Everything marked `UNVERIFIED:` in code or `tinyscry.tf` is an interface placeholder validated only
against fixtures. Before using this against a real session, capture and preserve a redacted stream,
then confirm all of the following:

- Which GMCP packages this MUD actually sends and whether its package casing is stable.
- The exact `Char.Vitals` keys and value types for current/max HP, mana, and movement.
- Whether current and maximum values arrive together in `Char.Vitals` or in separate packages.
- Whether `Char.Name` or `Char.Base` supplies the player name, and its exact key spelling.
- The actual target/enemy package name, its label key, whether health is absolute or percentage,
  and its percentage key spelling.
- How target clearing/death is signalled (null payload, an empty object, a separate package, or
  another explicit field).
- The TinyFugue version and the precise GMCP hook/direct-write capability installed on the VPS.

The default target mapping is the documented IRE `IRE.Target.Info` shape (`short_desc`, `hpperc`),
but it is **not** evidence that this MUD emits it. Edit only the mapping table in
`src/tinyscry_tf/normalize.py` after the verification capture is covered by tests.
