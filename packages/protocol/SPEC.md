# TinyScry protocol, version 1

Canonical reference for the TinyScry wire format. This document is authoritative
for the _shape and rules_; the implementations are authoritative for behaviour:

| Role                      | Implementation                                        |
| ------------------------- | ----------------------------------------------------- |
| TypeScript decoder (HUD)  | `src/decode.ts`                                       |
| Python decoder (relay/TF) | `../../services/relay/src/tinyscry_relay/protocol.py` |
| Conformance corpus        | `fixtures/`                                           |

The format is owned by TinyScry and is deliberately independent of the MUD and
of TinyFugue - see `docs/architecture/decisions/0002-tinyscry-owned-protocol.md`.

## Transport

JSON text frames over WebSocket. One message per frame. Binary frames are
ignored.

| Endpoint                        | Direction           | Messages                      |
| ------------------------------- | ------------------- | ----------------------------- |
| `ws://127.0.0.1:8787/state`     | relay -> subscriber | `hello`, `snapshot`, `status` |
| `ws://127.0.0.1:8787/ingest`    | producer -> relay   | `publish`                     |
| `http://127.0.0.1:8787/healthz` | operator -> relay   | plain HTTP JSON               |

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

Every message carries `"protocol": 1` and, except `publish`, an `at` timestamp
in epoch milliseconds.

| `type`     | Direction | Fields                                              |
| ---------- | --------- | --------------------------------------------------- |
| `hello`    | -> client | `at`, `relay: { name, version }`                    |
| `snapshot` | -> client | `seq`, `at`, `state`                                |
| `status`   | -> client | `at`, `feed: "live" \| "stale" \| "down"`, `detail` |
| `publish`  | -> relay  | `state`                                             |

`seq` is a monotonic counter **per relay process**. It restarts when the relay
restarts, so consumers reset their high-water mark on `hello` and on reconnect.

`feed` describes the upstream game feed, not the socket:

| Value   | Meaning                                                       |
| ------- | ------------------------------------------------------------- |
| `live`  | a producer is connected and published within the stale window |
| `stale` | a producer is connected but has not published recently        |
| `down`  | no producer is connected                                      |

The relay retains its last snapshot across producer disconnects, so a
reconnecting HUD gets state immediately; `feed` is what tells it whether that
state is fresh.

## Validation rules

Decoding is all-or-nothing. A frame either yields a complete value or an error;
there is no partial application. All input is treated as untrusted because it
originates transitively from a MUD server.

| Value            | Rule                                                           |
| ---------------- | -------------------------------------------------------------- |
| frame length     | <= 16384 UTF-16 code units, checked **before** parsing         |
| `type`           | non-empty string, <= 32 chars                                  |
| `protocol`       | must equal `1`                                                 |
| `at`, `seq`      | integers, `0 .. Number.MAX_SAFE_INTEGER`                       |
| names            | 1..64 chars, no C0/C1 controls, no DEL, no unpaired surrogates |
| `detail`         | `null` or 1..256 chars, same character rules                   |
| `current`, `max` | integers, `0 .. 1_000_000_000`                                 |
| `healthPercent`  | `null` or a finite number in `0 .. 100`                        |

Control characters are **rejected, not stripped**. A name containing an ANSI
escape is evidence of a normalization bug upstream, and stripping it would hide
that. Stripping happens once, deliberately, in the TF normalizer - before the
value ever becomes a protocol value.

Bounds live in `src/limits.ts` (`LIMITS`).

## Error codes

| Code                   | Meaning                            | Caller policy        |
| ---------------------- | ---------------------------------- | -------------------- |
| `frame_too_large`      | frame exceeded the pre-parse cap   | violation            |
| `invalid_json`         | not parseable JSON                 | violation            |
| `unsupported_protocol` | `protocol` is not `1`              | violation            |
| `unknown_type`         | a `type` this build does not model | **ignore the frame** |
| `invalid_field`        | a field failed its rule            | violation            |

Errors carry a dotted `path` such as `state.character.hp.max`. The rejected
input is never included in the error or in logs: it is attacker-controlled, and
keeping it out of logs and terminals is the point of rejecting it.

`unknown_type` is the forwards-compatibility escape hatch and is the one code a
caller must not treat as a fault.

## Versioning

`protocol` is bumped **only** for breaking changes.

Additive, ignorable changes keep version 1, because decoders drop unknown
object keys rather than rejecting them. A newer relay may therefore send extra
fields to an older HUD. Removing or retyping a field, or changing the meaning of
an existing one, requires a version bump.

## Changing the protocol

1. Update this document.
2. Update `src/` and the Python decoder in lockstep.
3. Add fixtures to `fixtures/accept/` and `fixtures/reject/` - a rejection
   fixture must pin the exact `code` and `path`.
4. Extend the TF normalizer if the change is state-bearing.
5. Extend the HUD.

`docs/architecture/objects/game-state.md` records this chain as change-impact
information.
