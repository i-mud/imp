# TinyFugue adapter

TinyScry keeps MUD-specific data outside the desktop application:

```text
MUD GMCP -> TinyFugue hook -> private spool -> tinyscry-feed -> relay ingest (loopback) -> SSH tunnel -> desktop HUD
```

`tinyscry-feed` connects only to `ws://127.0.0.1:8787/ingest` by default. The relay is deliberately
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

## Running (normal production path)

Copy the unchanged hook to `~/.config/tinyscry/capture.tf`, then add this
line to the startup file used by the operator's actual TinyFugue invocation:

```text
/load ~/.config/tinyscry/capture.tf
```

TinyFugue's `-f FILE` option loads `FILE` instead of the normal personal
config. For example, when starting from `~/avatar/tf` with
`tf -f./.tfrc -n`, the load belongs in `~/avatar/tf/.tfrc`, not an assumed
`~/.tfrc`. It must run before anything in that startup path can connect or
log in. The hook is additive and does not own or replace the operator's GMCP
negotiation or connection macros, and TinyScry sends no GMCP of its own.
Installation must not automatically create or overwrite an operator startup
file. See [`../../deploy/README.md`](../../deploy/README.md) for the full VPS
systemd setup.

The hook writes to the fixed path `~/.local/state/tinyscry/spool`.
`tinyscry-feed` creates that path as a symlink into its own private runtime
directory and owns everything downstream of it - no manual file
pre-creation is needed, unlike the earlier `gmcp.raw` setup:

```sh
# Terminal 1
uv run --directory services/relay tinyscry-relay

# Terminal 2
uv run --directory integrations/tinyfugue tinyscry-feed
```

`tinyscry-feed` replaces the earlier three-process
`tail -F gmcp.raw | tinyscry-capture | tinyscry-bridge` pipeline for normal
use. It:

- acquires an exclusive runtime lock so a second accidental invocation - a
  duplicate manual start, or a stray process left over from a crash - fails
  immediately with a clear message instead of racing the running feed for the
  same TinyFugue hook;
- creates the private runtime spool and the hook's fixed-path symlink;
- polls the spool, converts and normalizes each line with the same checked
  parsing `tinyscry-capture` always used, and publishes only material state
  changes, with the same bounded reconnect backoff `tinyscry-bridge` always
  used.

To stop the feed without closing TinyFugue, stop the `tinyscry-feed` process
(or `systemctl --user stop tinyscry-feed.service` under the VPS deployment);
the hook keeps trying to write and simply loses updates until a feed is
running again. To stop the hook itself, use `/undef tinyscry_capture_gmcp`
inside TinyFugue.

All filenames and commands are fixed operator input. MUD data flows only
through direct file APIs; it is never interpolated into a shell or TF
command.

## Identity bootstrap prerequisite

AVATAR sends the full identity-bearing `Char.Status` only once, during initial
character login, and documents no way to request another full snapshot; later
`Char.Status` messages are deltas that may omit `character_name`. Capture of
that single message therefore depends on the operator's TinyFugue sequencing
GMCP login at the right negotiation point.

The invariant is a TinyFugue build whose GMCP support includes the
`GMCP_LOGIN` hook, with operator login scripts that use it to run their GMCP
capability negotiation and send `Char.Login`. Without `GMCP_LOGIN` the operator
login path cannot be relied on to sequence this correctly: TinyScry keeps
normalizing `Char.Vitals` and `Char.Status` deltas but never observes identity,
so no state is published.

State it as a capability rather than a version: the build verified live was
`5.2.2-3-g4f0ff34`, but an upstream or distribution version number does not by
itself prove `GMCP_LOGIN` is compiled into the binary in use. The conclusive
signal is that login produces a full `Char.Status` carrying `character_name`
and TinyScry publishes a snapshot and writes its checkpoint. The verified run
observed `Char.StatusVars` immediately followed by that full `Char.Status`;
AVATAR does not document the ordering as a guarantee, so it is an observation,
not a protocol requirement TinyScry relies on - the normalizer accumulates
packages in whatever order they arrive.

TinyScry does not work around a missing identity: it never infers the local
character from `Room.Players` or `Char.Group.List`, never persists identity
across a reboot, and implements no `Char.Status` refresh request.

## Live feed transport: a drained spool, not a FIFO

TinyFugue's `fwrite()` is `fopen(path, "a")`, one write, `fclose()` - a fresh,
blocking open/write/close on every hook call, with no non-blocking option.
Measured against the real TF 5.1.6-4-ga15a165 binary:

| Target                                                       | Result                                                                                             |
| ------------------------------------------------------------ | -------------------------------------------------------------------------------------------------- |
| FIFO, no reader attached                                     | `fwrite()` blocked TinyFugue for the full call; killing the process was the only way to unblock it |
| FIFO, a reader that stops reading once the pipe buffer fills | `fwrite()` blocked TinyFugue indefinitely                                                          |
| Regular file, reader absent or slow                          | `fwrite()` returned immediately every time; TinyFugue never paused                                 |
| Regular file with its directory removed                      | `fwrite()` failed and printed one error line; TinyFugue continued in the same call                 |

A FIFO on the TinyFugue-facing hop can freeze the MUD client, which this
project's fail-open requirement forbids, and TinyFugue's `fwrite()` gives no
way to harden that: it is not TinyScry code and offers no non-blocking mode.
The live transport is therefore a private regular file that
`tinyscry-feed` continuously drains. Once a fully drained generation crosses
64 KiB, the reader renames that inode to `spool.retired` and creates a fresh
active spool instead of truncating an inode TinyFugue may already have open.
The retired generation remains readable long enough to collect any append that
raced the rename, then is removed after the next active generation reaches the
rotation threshold. At most two bounded runtime generations are retained.

`fopen(path, "a")` reopens the stable hook path on every call, so the next
TinyFugue event follows the current spool generation without a TinyFugue
restart. When the feed stops, the hook-facing symlink is removed; the systemd
unit also removes it after abnormal service termination, so feed absence drops
updates instead of accumulating an unbounded raw file.

`tinyscry-bridge --fifo` is unrelated to this hop and unchanged: it reads
already-normalized adapter records (not raw hook output) from a FIFO some
other producer writes to, for cases such as feeding pre-converted records
into a relay by hand. Its writer is always TinyScry-controlled code, not
TinyFugue, so the blocking-writer problem above does not apply to it.

## Diagnostic raw capture (opt-in)

Normal operation retains raw GMCP only in the private, bounded,
ephemeral live spool. It does not retain raw diagnostic history.
`tinyscry-feed --diagnostic-capture` writes each raw hook line, unparsed, to a
private, size-rotated file set
under `~/.local/state/tinyscry/diagnostics/` (mode `0700` directory, `0600`
files, 1 MiB per file, 5 files kept). This is a debugging aid for a specific
session, not a default; see
[`../../deploy/README.md`](../../deploy/README.md) for enabling it under the
VPS systemd deployment.

## Offline conversion and replay

Convert an existing raw capture or replay adapter JSONL without a relay.
Converted records are unredacted MUD data, so write them outside the
repository:

```sh
uv run --directory integrations/tinyfugue tinyscry-capture \
  "$HOME/.local/state/tinyscry/diagnostics/gmcp.raw" \
  --output "$HOME/.local/state/tinyscry/records.jsonl"
uv run --directory integrations/tinyfugue python -m tinyscry_tf.replay \
  fixtures/real-session.jsonl --dry-run
```

`--dry-run` prints each changed, complete normalized state as protocol JSON
and opens no socket. Use `--relay-url` and `--interval` for a paced live
replay; non-loopback relay URLs also require `--allow-non-loopback`.

## Verified TinyFugue boundary

The hook contract was established against TinyFugue 5.1.6-4-ga15a165 with
`+gmcp` and `+GMCP`, and is unchanged on the current VPS build
5.2.2-3-g4f0ff34, which additionally provides the `GMCP_LOGIN` hook the
operator's login scripts need (see
[identity bootstrap prerequisite](#identity-bootstrap-prerequisite)).
The `GMCP` hook receives one raw positional string in the form `Package JSON`.
`fwrite(filename, data)` appends data and a newline directly to a
fixed filename. `time()` returns epoch seconds with six
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
