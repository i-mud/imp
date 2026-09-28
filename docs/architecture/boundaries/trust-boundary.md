# Trust boundary

## The rule

Everything that originates from the MUD is untrusted, transitively. A MUD
operator can put arbitrary bytes in a character name, a room title or a GMCP
payload. Imp treats all of it as adversarial input.

## Where untrusted data is checked

| Boundary                    | Enforced by                                                |
| --------------------------- | ---------------------------------------------------------- |
| versioned TF spool event    | `integrations/tinyfugue/src/imp_tf/events.py`              |
| offline adapter record      | `integrations/tinyfugue/src/imp_tf/records.py`             |
| GMCP -> normalized state    | `integrations/tinyfugue/src/imp_tf/normalize.py`           |
| producer output             | `publisher.py`, via protocol encoders before sending       |
| relay ingest/action/helper  | `services/relay/src/imp_relay/protocol.py`                 |
| HUD state and action result | `packages/protocol/src/decode.ts`                          |
| TF action delivery          | `action_consumer.py` plus the private exact-context marker |

Bounds and character rules are specified once in `packages/protocol/SPEC.md`.

## Host-local trust model

Loopback prevents remote network access; it does not enforce UID or same-user
ownership. On the VPS, any process in the relay's network namespace, including
one owned by another local OS user, can reach its loopback listener. The relay
itself remains unauthenticated.

The authenticated remote gateway is a separate loopback process. Remote WSS
clients must authenticate before the gateway opens a relay connection, but a
host-local process can still reach the relay directly and bypass the gateway.
Gateway authentication therefore protects the remote network boundary; it does
not turn loopback into same-user isolation.

In SSH mode, any process that can reach the workstation's local forwarded
listener has the same state/action access as the desktop.

`Origin` checks remain browser defense-in-depth against cross-site requests.
An `Origin` header is not process identity and is not authentication.

Supported deployment therefore still requires a single-user workstation and
VPS, or mutual trust among every host-local user and process. An untrusted
multi-user host is outside Imp's supported trust boundary.

## Why validation is repeated

Each consumer validates what it receives rather than trusting its producer.
Loopback peers are inside the supported trust boundary, but malformed MUD data
and implementation faults still fail closed at every consumer.

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

- The TF hook appends versioned `IMP2` events to the fixed private spool path.
  Session, generation, and world tokens come from local TinyFugue state. Raw
  GMCP remains the final data field and is parsed only by Python. No MUD value
  is evaluated as TF source or placed in a command line.
- `integrations/tinyfugue/src/imp_tf/bridge.py` uses no `shell=True`, no
  `os.system`, and constructs no subprocess from record content.
- The relay executes nothing. Its outbound broker only moves a bounded
  printable-ASCII command between WebSocket peers.
- The action helper receives the raw command only as decoded WebSocket data. It
  verifies the private exact-context marker, converts the command with the
  `textencode.tf` representation, and writes one fixed `/imp_send
<session> <foreground> <connection> <world-token> <encoded-data>` line to its
  stdout pipe. TinyFugue rechecks that locally generated context and the
  quote-pinned current world before decoding the command, calling `send()`, and
  starting a replacement helper inside the same guard. A stale line cannot
  recreate its old context. The raw command is never shell argv, shell syntax,
  a generated macro name, or evaluated TF source.
- TinyFugue's asynchronous `/quote -dexec` starts only the fixed
  `imp-action-consumer` executable with locally generated session,
  generation, and encoded-world arguments. Blank `-w` pins its output to the
  world selected when the helper started. Each registration accepts at most one
  dispatch; after one fixed write and flush the helper exits and closes stdout.
  While idle it writes no liveness bytes and observes only terminal stdout
  reader-loss events; reader loss ends the helper without a result or reconnect.
  Before any dispatch it reconnects only while its exact marker remains current.
- Managed mode in `apps/desktop/src-tauri/src/tunnel.rs` is the one permitted
  desktop process-execution boundary. It constructs the system `ssh` command
  directly with `std::process::Command`, fixed SSH options and loopback
  forwarding arguments; no shell is involved. The operator-provided SSH alias
  follows `--`, and no MUD-derived value reaches process argv. OpenSSH retains
  ownership of credentials, host verification and SSH configuration.
  `TunnelSupervisor` tracks, terminates and reaps only the child it spawned;
  external mode owns no process, and a pre-existing listener or unrelated SSH
  process is never killed.

This is checkable rather than asserted. Search the process-execution surfaces:

```bash
grep -rnE 'shell=True|os\.system|subprocess\.|child_process|execSync|Command::new|\.spawn\(' \
  services/relay/src integrations/tinyfugue/src apps/desktop/src \
  packages/protocol/src apps/desktop/src-tauri/src
```

The expected source hit is the managed OpenSSH `Command::new("ssh")` and
`.spawn()` path in `tunnel.rs`; it is permitted because the command uses direct
argv, fixed forwarding/options, and accepts no MUD-derived input. The
TinyFugue `/quote` boundary is declarative TF source rather than a Python or
desktop spawn and is constrained as described above. Any relay execution hit,
shell-based execution, additional spawn path, or path carrying server content
into argv requires investigation.

## Credentials and local command text

Imp supports two remote desktop transports with different credential
boundaries.

SSH mode still requests, stores, and manages no SSH password or private key.
The system OpenSSH client retains ownership of its existing agent, keys, host
verification, and configuration.

Direct WSS mode uses one 256-bit pairing token for the authenticated remote
gateway:

- the gateway receives only the SHA-256 digest of the textual pairing token;
- the plaintext token is never a URL, query parameter, fragment, WebSocket
  subprotocol, build-time `VITE_*` value, reverse-proxy credential, or log
  field;
- the native desktop configuration owns the persisted plaintext token;
- the settings-read API returns only whether a stored token exists, never the
  existing plaintext token merely to populate the management UI;
- an operator-entered replacement token crosses the renderer/native boundary
  only for the explicit save operation; an existing Direct token can instead
  be preserved without returning it to the renderer;
- switching from Direct to another mode removes the persisted Direct token and
  URL rather than retaining stale credential material;
- native configuration replacement is staged before replacing the previous
  file, and failed validation or persistence leaves the previous usable
  configuration intact;
- the renderer may hold the active token transiently only to authenticate a
  direct WSS connection;
- possession of the token grants remote state observation and context-bound
  action requests for that Imp installation, so compromise requires
  rotation.

The relay itself remains unauthenticated and permanently loopback-only.
`/ingest` and `/action-consumer` are not exposed by the gateway. See
`docs/architecture/decisions/0001-loopback-relay-and-ssh-boundary.md` and
`docs/architecture/decisions/0010-authenticated-remote-gateway.md`.

`.gitignore` covers key material and `.env` files so a stray local file cannot
be committed.

Saved desktop action definitions are a separate class of data: operator-authored
local application configuration. Their exact command strings are stored in
plaintext WebView `localStorage`. Operators must not use saved actions to store
passwords or other secrets.

Persisted definitions are reusable command templates, not queued dispatches,
retained action history, relay payload retention, retries, or reconnect resend.
The relay/TinyFugue no-replay contract is unchanged.

Managed tunnel mode delegates credentials and host verification to the system
SSH client. Copying private keys into the app, persisting an SSH password, or
weakening OpenSSH host verification remains out of bounds.

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
2. `services/relay/src/imp_relay/protocol.py` - the Python `LIMITS`
3. `integrations/tinyfugue/src/imp_tf/normalize.py` - the normalizer's own
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
Verified against: protocol rejection tests, hostile event/record tests,
context-marker and action-helper tests, relay Origin/action tests, authenticated
gateway pre-auth/origin/route/loopback tests, managed OpenSSH argv and ownership
tests, and the live Direct-WSS boundary recorded in `docs/status.md`.
