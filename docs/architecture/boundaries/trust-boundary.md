# Trust boundary

## The rule

Everything that originates from the MUD is untrusted, transitively. A MUD
operator can put arbitrary bytes in a character name, a room title or a GMCP
payload. Imp treats all of it as adversarial input.

## Where untrusted data is checked

| Boundary                        | Enforced by                                                                      |
| ------------------------------- | -------------------------------------------------------------------------------- |
| versioned TF spool event        | `integrations/tinyfugue/src/imp_tf/events.py`                                    |
| Mudlet helper/Lua protocol      | `integrations/mudlet/src/imp_mudlet/protocol.py` plus the fixed Lua adapter      |
| client-neutral GMCP record      | `integrations/common/src/imp_adapter/records.py`                                 |
| GMCP -> normalized state        | `integrations/common/src/imp_adapter/normalize.py`                               |
| producer output                 | shared `publisher.py`, via protocol encoders                                     |
| node ingest/action/helper       | `services/relay/src/imp_relay/protocol.py`                                       |
| HUD state and action result     | `packages/protocol/src/decode.ts`                                                |
| TinyFugue final action delivery | `action_consumer.py` plus the private exact-context marker and `/imp_send` fence |
| Mudlet final action delivery    | Lua exact-context check immediately before `send(command, false)`                |

Bounds and character rules are specified once in `packages/protocol/SPEC.md`.

## Host-local trust model

Loopback prevents remote network access; it does not enforce UID or same-user
ownership.

Any process that can reach an Imp node's loopback listener can reach the
host-local endpoints allowed on that node. On a VPS this includes processes
owned by another local OS user. On a workstation it includes the desktop-owned
node used by local Mudlet/TinyFugue adapters.

The relay/node itself remains unauthenticated. In particular, its privileged
producer and action-consumer endpoints rely on the supported host-local trust
model rather than per-process authentication.

The authenticated remote gateway is a separate loopback process. Remote WSS
clients authenticate before the gateway opens a node connection, and the
gateway exposes only the desktop-facing state/action routes. A host-local
process can still reach the node directly; gateway authentication therefore
protects the remote network boundary, not local process isolation.

In SSH mode, the workstation listener on `127.0.0.1:8789` forwards the remote
node's entire loopback port, not a route-filtered state/action capability.
Host-local processes that can reach that listener can also reach `/ingest` and
`/action-consumer` without an `Origin` header. SSH authenticates the transport;
it does not add per-route authentication to the node.

`Origin` checks remain browser defense-in-depth. An `Origin` header is neither
process identity nor authentication.

Supported deployment therefore requires a single-user host, or mutual trust
among every host-local user and process that can reach these loopback
listeners. An untrusted multi-user host remains outside Imp's supported trust
boundary.

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

## Shell and process-execution safety

No MUD-derived value is interpolated into a shell command.

TinyFugue:

- The TF hook appends versioned `IMP2` events to a fixed private spool path.
  Session, generation, and world tokens come from local TinyFugue state. Raw
  GMCP remains data parsed by Python rather than evaluated TF source.
- `integrations/tinyfugue/src/imp_tf/bridge.py` uses no `shell=True` or
  `os.system`.
- The action helper converts the bounded command into the fixed `/imp_send`
  bridge representation. TinyFugue rechecks locally generated context and the
  quote-pinned world immediately before decoding and calling `send()`.
- `/quote -dexec` starts only the fixed `imp-action-consumer` executable with
  locally generated context arguments. Raw action text is never shell argv.

Mudlet:

- The package resolves a locally provisioned helper path and starts that helper
  with Mudlet's process API. MUD/GMCP values do not choose the executable path
  or become process argv.
- GMCP and lifecycle material cross the Lua/helper boundary as JSONL data.
- Trusted action text returns as data, is checked against the exact current
  context, and is passed directly to `send(command, false)`. It is not shell
  syntax, generated Lua source, or a Mudlet alias expansion.

Desktop native runtime:

- `NodeSupervisor` starts only the packaged `imp-node` sidecar with fixed
  loopback node arguments.
- `GatewaySupervisor` starts the same packaged sidecar in `gateway` mode with
  loopback endpoints and locally configured pairing-token digest material.
- `TunnelSupervisor` invokes the system `ssh` executable directly by argv with
  fixed forwarding/options. The operator-provided SSH alias follows `--`;
  no MUD-derived value reaches its argv.
- Each supervisor tracks and terminates only children it spawned. Existing
  listeners are never killed merely because they occupy a desired port.

The relay/node itself executes no MUD command. Its action broker only transfers
a bounded printable-ASCII command between protocol peers.

The process-execution surfaces are deliberately inspectable:

```bash
grep -rnE 'shell=True|os\.system|subprocess\.|child_process|execSync|Command::new|\.spawn\(|\bspawn\('   services/relay/src   integrations/common/src   integrations/mudlet   integrations/tinyfugue/src   apps/desktop/src   apps/desktop/src-tauri/src   packages/protocol/src
```

Any new shell-based execution, executable path derived from MUD data, or raw
MUD/action value placed into process argv requires explicit security review.

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
retained action history, node payload retention, retries, or reconnect resend.
The node/client-adapter no-replay contract is unchanged.

Managed tunnel mode delegates credentials and host verification to the system
SSH client. Copying private keys into the app, persisting an SSH password, or
weakening OpenSSH host verification remains out of bounds.

## Desktop process ownership

Only runtime children created by this desktop instance enter native lifetime
ownership: the same-host node, optional producer gateway, and Managed SSH.
Ownership comes from the spawn handle, never an executable-name, port, PID-file,
or command-line search. Existing listeners, adopted SSH forwards, Mudlet-owned
helpers, and VPS/systemd services remain outside this boundary.
Owned SSH target connections explicitly disable shared-master reuse/persistence
and authentication-triggered backgrounding. They do not inspect, modify, or
terminate an external control socket/master. On Unix, nested jump/proxy clients
must also have foreground configuration; the target's options do not propagate
to OpenSSH's generated jump client.

Windows assigns children to a private kill-on-close Job Object atomically at
process creation. The desktop alone owns its non-inheritable job handle;
ordinary descendants inherit job membership without breakaway permission.
Abrupt desktop termination closes that handle in the kernel. A container or
creation failure is a startup failure, never permission to run an uncontained
child. Normal supervisor shutdown still terminates and reaps individual children.

The Unix guardian/private-process-group mechanism and its narrower guarantees
are described in
[`../processes/managed-runtime.md`](../processes/managed-runtime.md).
Lifetime containment is not a sandbox and does not confer ownership of remote
services, broker-created processes, or unrelated local listeners.

## Logging

Rejected input is never logged. Errors carry a code and a dotted field path
only - see `describeError` in `packages/protocol/src/result.ts`. The reason is
direct: a rejected value may contain terminal escapes, and a log line is often
read in a terminal.

Malformed-line counts are logged so a broken feed is still visible.

Relay `serve()`, gateway `serve()`, and both gateway upstream `connect()` paths
explicitly use the Imp-owned `imp_relay.websocket` logger. WebSocket DEBUG
handshake/frame diagnostics include raw authentication and close payloads, so
this logger has an INFO security floor. Its threshold never decreases: it
also preserves existing Imp/application/root and dependency parent/server/client
restrictions, including across later component startup and restart.

The process-global `websockets` logger configuration is untouched. Explicit
DEBUG settings on its descendants cannot enable diagnostics on Imp-owned
connections. Unrelated WebSocket consumers retain their caller-owned policy.
Application DEBUG remains available, and connection lifecycle INFO is available
when no stricter restriction applies. Successful, rejected, malformed, and
interrupted authentication must not log credentials.

## Change impact

Touching any file in the table above is a security change.

A bound is defined in **three** places, and all three must move together:

1. `packages/protocol/src/limits.ts` - `LIMITS`, the TypeScript decoder's source
2. `services/relay/src/imp_relay/protocol.py` - the Python `LIMITS`
3. `integrations/common/src/imp_adapter/normalize.py` - the normalizer's own
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
TinyFugue context/action-helper tests, Mudlet helper/lifecycle/action tests,
node Origin/action tests, authenticated-gateway pre-auth/origin/route/loopback
tests, local-node/gateway ownership tests, Managed OpenSSH argv/ownership tests,
and the live local/SSH/Direct-WSS evidence recorded in `docs/status.md`.
