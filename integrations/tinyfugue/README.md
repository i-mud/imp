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

Create a private capture file before loading the hook. `fwrite()` appends to the
file but does not set its permissions:

```sh
install -d -m 700 "$HOME/.local/state/tinyscry"
: >"$HOME/.local/state/tinyscry/gmcp.raw"
chmod 600 "$HOME/.local/state/tinyscry/gmcp.raw"
```

In TinyFugue, load `tinyscry.tf` by absolute path:

```text
/load /absolute/path/to/integrations/tinyfugue/tinyscry.tf
```

The verified TF 5.1.6 hook appends `<epoch-seconds> <package> [JSON]` lines.
`tinyscry-capture` checks and converts those lines to the adapter record
contract. For a cold start, run the loopback relay and persistent pipeline in
separate VPS terminals:

```sh
# Terminal 1
uv run --directory services/relay tinyscry-relay

# Terminal 2
tail -n +1 -F "$HOME/.local/state/tinyscry/gmcp.raw" |
  PYTHONUNBUFFERED=1 uv run --directory integrations/tinyfugue tinyscry-capture |
  PYTHONUNBUFFERED=1 uv run --directory integrations/tinyfugue tinyscry-bridge
```

`tail -n +1` replays the accumulated capture once so the relay receives an
initial snapshot, then follows new records. Use `-n 0` only when deliberately
ignoring existing state.

The bridge reads stdin or a FIFO outside the asyncio event-loop thread. This is
required: WebSocket close frames and keepalive traffic must still run while the
input stream is idle. If the relay closes the producer socket, the old
connection completes its close handshake. The next material state reconnects
with bounded backoff and is sent before the bridge reads another record. Valid
unknown packages and duplicate states remain intentional no-ops.

All filenames and commands are fixed operator input. MUD data flows only
through direct file and pipe APIs; it is never interpolated into a shell or TF
command. To stop capture without closing TF, use
`/undef tinyscry_capture_gmcp`.

Convert an existing raw capture or replay adapter JSONL without a relay.
Converted records are unredacted MUD data, so write them outside the
repository:

```sh
uv run --directory integrations/tinyfugue tinyscry-capture \
  "$HOME/.local/state/tinyscry/gmcp.raw" \
  --output "$HOME/.local/state/tinyscry/records.jsonl"
uv run --directory integrations/tinyfugue python -m tinyscry_tf.replay \
  fixtures/real-session.jsonl --dry-run
```

`--dry-run` prints each changed, complete normalized state as protocol JSON
and opens no socket. Use `--relay-url` and `--interval` for a paced live
replay; non-loopback relay URLs also require `--allow-non-loopback`.

## Verified TinyFugue boundary

The VPS build is TinyFugue 5.1.6-4-ga15a165 with `+gmcp` and `+GMCP`.
Its `GMCP` hook receives one raw positional string in the form `Package JSON`.
Its `fwrite(filename, data)` function appends data and a newline directly to a
fixed filename. Its `time()` function returns epoch seconds with six
fractional digits, which `tinyscry-capture` converts to integer epoch
milliseconds.

The real capture contained 249 hook lines across 214.662 seconds. The converter
accepted 200 records. It rejected 49 inventory events whose MUD payloads
contained unescaped control characters inside JSON strings; it logged only
bounded error codes and did not repair or execute the input.

## Observed real-session schema

The redacted fixture establishes these mappings:

- `Char.Status.character_name` supplies identity.
- `Char.Status.health`, `health_max`, `mana`, `mana_max`, `movement`, and
  `movement_max` provide full or partial resource updates.
- `Char.Vitals.hp`, `maxhp`, `mp`, `maxmp`, `mv`, and `maxmv` provide complete
  resource snapshots. All observed resource values are decimal strings.
- `Char.Status.opponent_name` supplies the target label.
  `opponent_health` is a percentage string; every acquisition paired it with
  `opponent_health_max` equal to `"100"`. Later damage updates supplied only
  `opponent_health`.
- An empty `opponent_name` with zero health fields clears the target.

Every field required by the current HUD was present. The stream did not expose
absolute target hit points; only the percentage-scale opponent fields were
available. Room, group, and inventory packages remain deliberately unmapped,
so valid unknown records preserve the exact previous HUD state.
