# Imp protocol, version 2

Canonical reference for the Imp wire format. This document is authoritative
for the _shape and rules_; the implementations are authoritative for behaviour:

| Role                            | Implementation                                   |
| ------------------------------- | ------------------------------------------------ |
| TypeScript decoder (HUD)        | `src/decode.ts`                                  |
| Python decoder (relay/adapters) | `../../services/relay/src/imp_relay/protocol.py` |
| Conformance corpus              | `fixtures/`                                      |

The format is owned by Imp and is deliberately independent of the MUD and
of any specific MUD client - see
`docs/architecture/decisions/0002-imp-owned-protocol.md`. Version 2 is a clean
break from version 1: every state update and outbound action is bound to an
explicit client-adapter context.

## Transport

JSON text frames over WebSocket. One message per frame. Binary or malformed
frames close the connection. Unknown message types have consumer-specific
handling described under Error codes below.

| Endpoint                              | Direction                    | Messages                                                    |
| ------------------------------------- | ---------------------------- | ----------------------------------------------------------- |
| `ws://127.0.0.1:8787/state`           | relay -> subscriber          | `hello`, `snapshot`, `status`, `text`                       |
| `ws://127.0.0.1:8787/ingest`          | producer -> relay            | `select`, `publish`, `text`                                 |
| `ws://127.0.0.1:8787/action`          | requester -> relay -> result | `action`, then `action-result`                              |
| `ws://127.0.0.1:8787/action-consumer` | client helper <-> relay      | `consumer`, `consumer-ready`, `dispatch`, `consumer-result` |
| `http://127.0.0.1:8787/healthz`       | operator -> relay            | plain HTTP JSON                                             |

`/state`, `/action`, and `/healthz` accept no `Origin` header or exactly
`http://localhost:1420` or `http://tauri.localhost`. `/ingest` and
`/action-consumer` reject every request carrying an `Origin` header. This is
browser defense-in-depth against cross-site requests, not authentication.
Loopback prevents direct remote network access but does not enforce UID
ownership; Imp provides no per-user endpoint authentication. Both producer and
consumer hosts must be single-user or trust every host-local process. SSH
forwards the entire node port; only the authenticated WSS gateway filters
remote access to `/state` and `/action`.

## Context

A non-null state context is:

```json
{ "session": "opaque_session_123", "foreground": 4, "connection": 9 }
```

- `session` identifies one client-adapter lifetime.
- `foreground` identifies the generation of the currently authoritative
  profile/world selection.
- `connection` identifies the generation of the active MUD connection.

The tuple is opaque downstream. Equality means exact equality of all three
fields. The relay holds only one active context. A `select` replaces it and
invalidates feed freshness until a matching `publish` arrives. A `select` with a
null context means that no game connection is selected. A `publish` for any
context other than the active one is ignored.

## Normalized state

```json
{
  "character": {
    "name": "Example",
    "hp": { "current": 842, "max": 1000 },
    "mana": { "current": 371, "max": 500 },
    "moves": { "current": 189, "max": 200 }
  },
  "target": { "name": "Ancient Troll", "healthPercent": 72 }
}
```

- `character` and `target` are `null` when unknown.
- `hp`, `mana` and `moves` are independently `null` when the MUD has not
  reported them. An absent key and an explicit `null` are equivalent.
- `healthPercent` is `null` when a target is named but its health is unknown.
- `current > max` is **valid**. Overheal and temporary buffs are real; the bar
  clamps, the numerals do not.

## Messages

Every message carries `"protocol": 2`. Unknown object keys are ignored.

| `type`            | Direction           | Fields                                                     |
| ----------------- | ------------------- | ---------------------------------------------------------- |
| `hello`           | relay -> subscriber | `at`, `relay: { name, version }`                           |
| `snapshot`        | relay -> subscriber | `seq`, `at`, `context`, `state`                            |
| `status`          | relay -> subscriber | `at`, `feed: "live" \| "stale" \| "down"`, `detail`        |
| `text`            | both                | non-null `context`, `at`, `text`                           |
| `select`          | producer -> relay   | `context` (nullable), `state`                              |
| `publish`         | producer -> relay   | non-null `context`, `state`                                |
| `action`          | requester -> relay  | non-null `context`, `command`                              |
| `action-result`   | relay -> requester  | `status: "forwarded" \| "rejected" \| "unknown"`, `detail` |
| `consumer`        | helper -> relay     | non-null `context`                                         |
| `consumer-ready`  | relay -> helper     | non-null `context`                                         |
| `dispatch`        | relay -> helper     | `id`, non-null `context`, `command`                        |
| `consumer-result` | helper -> relay     | `id`, `status: "forwarded" \| "rejected"`                  |

`seq` is a monotonic counter **per relay process**. It restarts when the relay
restarts, so consumers reset their high-water mark on `hello` and on reconnect.

`feed` describes the upstream game feed, not the socket:

| Value   | Meaning                                                             |
| ------- | ------------------------------------------------------------------- |
| `live`  | a producer published for the active context within the stale window |
| `stale` | a producer is connected but has not published the active context    |
| `down`  | no producer is connected                                            |

The relay retains its last snapshot across producer disconnects, so a
reconnecting HUD gets state immediately; `feed` is what tells it whether that
state is fresh.

`text` is a transient received-MUD-line event. The relay accepts it only for
the active context and broadcasts it only to subscribers connected at that
moment. It is never stored in `RelayState`, does not increment `seq`, does not
affect feed freshness, and is never replayed to a later or reconnecting
subscriber.

## Action semantics

An action is accepted only when its context equals the relay's active context
and one client helper is registered for that same context. The broker permits
one action in flight globally. It has no queue, retry, fan-out, or replay:

- mismatched context, no matching helper, duplicate helper, or a busy broker is
  rejected;
- a helper reports `forwarded` only after its client-specific final hop has
  accepted the dispatch according to that adapter's contract;
- TinyFugue reports `forwarded` after writing and flushing the fixed
  `/imp_send` bridge line, before the final synchronous TinyFugue fence;
- Mudlet reports `forwarded` only after rechecking the exact current context and
  calling `send(command, false)` without a Lua error;
- disconnect or timeout after dispatch is `unknown` when the relay cannot prove
  a terminal consumer result; and
- an `unknown` result is never retried automatically.

`forwarded` is intentionally weaker than MUD delivery or execution. Its exact
local proof is adapter-specific, but it never proves that the MUD server
received or executed the command.

## Validation rules

Decoding is all-or-nothing. A frame either yields a complete value or an error;
there is no partial application. All MUD-derived state remains untrusted. Action
commands are operator input, but are still bounded before any network request
and checked again at each protocol boundary.

| Value                      | Rule                                                           |
| -------------------------- | -------------------------------------------------------------- |
| frame length               | <= 16384 UTF-16 code units, checked **before** parsing         |
| `type`                     | non-empty string, <= 32 chars                                  |
| `protocol`                 | must equal `2`                                                 |
| `at`, `seq`                | integers, `0 .. Number.MAX_SAFE_INTEGER`                       |
| context `session`          | 1..128 ASCII letters, digits, or underscores                   |
| `foreground`, `connection` | positive safe integers; null selection uses no context         |
| received `text`            | 1..1024 chars, no C0/C1 controls, DEL, or unpaired surrogates  |
| action `command`           | 1..512 printable ASCII characters (`0x20..0x7e`)               |
| dispatch `id`              | 1..64 ASCII letters, digits, or underscores                    |
| names                      | 1..64 chars, no C0/C1 controls, no DEL, no unpaired surrogates |
| `detail`                   | `null` or 1..256 chars, same character rules                   |
| `current`, `max`           | integers, `0 .. 1_000_000_000`                                 |
| `healthPercent`            | `null` or a finite number in `0 .. 100`                        |

Control characters are **rejected, not stripped**. A name containing an ANSI
escape is evidence of a normalization bug upstream, and stripping it would hide
that. Stripping happens once, deliberately, in the shared normalizer - before
the value ever becomes a protocol value.

Bounds live in `src/limits.ts` (`LIMITS`).

## Error codes

| Code                   | Meaning                            | Caller policy     |
| ---------------------- | ---------------------------------- | ----------------- |
| `frame_too_large`      | frame exceeded the pre-parse cap   | violation         |
| `invalid_json`         | not parseable JSON                 | violation         |
| `unsupported_protocol` | `protocol` is not `2`              | violation         |
| `unknown_type`         | a `type` this build does not model | consumer-specific |
| `invalid_field`        | a field failed its rule            | violation         |

Errors carry a dotted `path` such as `state.character.hp.max`. The rejected
input is never included in the error or in logs: it is attacker-controlled, and
keeping it out of logs and terminals is the point of rejecting it.

`RelayStateSource` ignores `unknown_type` server frames for forwards
compatibility. The relay rejects unknown client message types with close code
`1008`; `RelayActionSink` treats any non-`action-result` frame, including an
unknown type, as an `unknown` outcome and does not retry.

## Versioning

`protocol` is bumped **only** for breaking changes. Version 2 deliberately has
no compatibility shim for version 1.

Additive, ignorable changes keep version 2, because decoders drop unknown
object keys rather than rejecting them. Removing or retyping a field, or
changing the meaning of an existing one, requires a version bump.

## Changing the protocol

1. Update this document.
2. Update `src/` and the Python decoder in lockstep.
3. Add fixtures to `fixtures/accept/` and `fixtures/reject/` - a rejection
   fixture must pin the exact `code` and `path`.
4. Extend the shared normalizer and any affected client-adapter boundary if
   the change is state-bearing.
5. Extend the HUD.

`docs/architecture/objects/game-state.md` records this chain as change-impact
information.
