# Trust boundary

## The rule

Everything that originates from the MUD is untrusted, transitively. A MUD
operator can put arbitrary bytes in a character name, a room title or a GMCP
payload. TinyScry treats all of it as adversarial input.

## Where untrusted data is checked

| Boundary                 | Enforced by                                            |
| ------------------------ | ------------------------------------------------------ |
| raw TF hook line         | `integrations/tinyfugue/src/tinyscry_tf/capture.py`    |
| TF record envelope       | `integrations/tinyfugue/src/tinyscry_tf/records.py`    |
| GMCP -> normalized state | `integrations/tinyfugue/src/tinyscry_tf/normalize.py`  |
| producer output          | `publisher.py`, via `decode_game_state` before sending |
| relay ingest             | `services/relay/src/tinyscry_relay/protocol.py`        |
| HUD ingest               | `packages/protocol/src/decode.ts`                      |

Bounds and character rules are specified once in `packages/protocol/SPEC.md`.

## Why validation is repeated

Each consumer validates what it receives rather than trusting its producer.
This is not redundancy: the relay is reachable by any local process on the VPS,
and the HUD is reachable by whatever is on the other end of the tunnel. Neither
can verify the other's diligence, so each defends itself.

## Reject, do not sanitise - except once

The protocol layer **rejects** control characters instead of stripping them.
Stripping at the protocol layer would mask an upstream normalization bug and
make the corpus unable to distinguish "handled" from "silently mangled".

Sanitising happens exactly once, deliberately, in `normalize.py`, which is the
component whose job is to convert MUD reality into protocol values. After that
point a control character is a bug, and the decoder says so.

## Shell safety

No server-provided value is ever interpolated into a shell command, in any
component. Concretely:

- The TF integration's documented path is: TinyFugue's `GMCP` hook appends
  `<epoch-seconds> <package> [JSON]` to a fixed-path file with `fwrite`;
  `tinyscry-capture` reads that file or stdin, parses each line and emits
  checked adapter JSONL; `tinyscry-bridge` consumes it through a pipe. Every
  parse happens in Python. TF is never asked to build a command line out of
  server content, and the capture filename never contains MUD data.
- `integrations/tinyfugue/src/tinyscry_tf/bridge.py` uses no `shell=True`, no
  `os.system`, and constructs no subprocess from record content.
- The relay executes nothing.
- Managed mode in `apps/desktop/src-tauri/src/tunnel.rs` is the one permitted
  desktop process-execution boundary. It constructs the system `ssh` command
  directly with `std::process::Command`, fixed SSH options and loopback
  forwarding arguments; no shell is involved. The operator-provided SSH alias
  follows `--`, and no MUD-derived value reaches process argv. OpenSSH retains
  ownership of credentials, host verification and SSH configuration.
  `TunnelSupervisor` tracks, terminates and reaps only the child it spawned;
  external mode owns no process, and a pre-existing listener or unrelated SSH
  process is never killed.

This is checkable rather than asserted. Search the execution surfaces:

```bash
grep -rnE 'shell=True|os\.system|subprocess\.|child_process|execSync|Command::new|\.spawn\(' \
  services/relay/src integrations/tinyfugue/src apps/desktop/src \
  packages/protocol/src apps/desktop/src-tauri/src
```

The expected process-execution hits are the managed OpenSSH
`Command::new("ssh")` and `.spawn()` path in `tunnel.rs`; they are permitted
because the command uses direct argv, fixed forwarding/options, and accepts no
MUD-derived input. Any relay execution hit, shell-based execution, additional
spawn path, or path carrying server content into argv requires investigation.

If TinyFugue is ever made to invoke an external command, the content must cross
the boundary as data on stdin or a pipe - never as an argument assembled from
server text.

## Secrets

TinyScry holds none.

- No authentication tokens; the relay's boundary is loopback plus SSH
  (`docs/architecture/decisions/0001-loopback-relay-and-ssh-boundary.md`).
- No password storage and no private key handling. SSH is the operator's
  existing `ssh` client and its existing agent/keys.
- `.gitignore` covers key material and `.env` files so a stray local file
  cannot be committed.

Managed tunnel mode delegates to the system SSH client and its agent. Copying
private keys into the app, persisting a password, or weakening OpenSSH host
verification is out of bounds.

## Logging

Rejected input is never logged. Errors carry a code and a dotted field path
only - see `describeError` in `packages/protocol/src/result.ts`. The reason is
direct: a rejected value may contain terminal escapes, and a log line is often
read in a terminal.

Malformed-line counts are logged so a broken feed is still visible.

## Change impact

Touching any file in the table above is a security change.

A bound is defined in **three** places, and all three must move together:

1. `packages/protocol/src/limits.ts` - `LIMITS`, the TypeScript decoder's source
2. `services/relay/src/tinyscry_relay/protocol.py` - the Python `LIMITS`
3. `integrations/tinyfugue/src/tinyscry_tf/normalize.py` - the normalizer's own
   truncation bound, which must not exceed the protocol's or it will emit state
   the relay rejects

Then `packages/protocol/SPEC.md`, which is the canonical statement of the rule,
and `packages/protocol/fixtures/`. Tightening a bound means the existing
rejection fixture may now be too lax to prove anything - a 65-character name no
longer tests a 32-character limit - so update the rejection fixture _and_ add an
accept fixture sitting exactly on the new boundary. Widening or accepting a new
character class requires a new rejection fixture, in both languages.

## Verification

Status: verified
Verified against: current protocol rejection tests, hostile-line tests in
`integrations/tinyfugue/tests/test_records.py`, and managed OpenSSH argv and
ownership tests in `apps/desktop/src-tauri/src/tunnel.rs`.
