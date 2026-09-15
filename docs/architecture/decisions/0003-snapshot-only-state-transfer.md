# 0003 - The relay sends whole snapshots, never partial updates

Status: accepted
Date: 2026-09-15

## Context

The security requirement is that malformed input must fail safely rather than
mutate state partially. A patch/delta protocol makes that hard: a delta that
validates in isolation can still be applied to the wrong base, and a delta
rejected halfway through has already touched state.

## Decision

Every state-bearing frame carries the complete `GameState`. There is no delta,
patch or merge message. A new subscriber receives the current snapshot
immediately after `hello`.

## Rationale

The entire state is a character name, three current/max pairs and an optional
target - a few hundred bytes. There is no bandwidth problem to solve, so the
only thing a delta protocol would buy is complexity and a partial-mutation
failure mode.

With whole snapshots, "fail closed" is structurally guaranteed: the decoder
either returns a complete valid `GameState` or returns an error, and the caller
has nothing partial to apply. See `decodeServerMessage` in
`packages/protocol/src/decode.ts`.

## Consequences

- Snapshots carry a monotonic `seq` so a late or duplicated frame can be
  dropped. `seq` is per relay process and resets on restart, so consumers reset
  their high-water mark on `hello` and on any reconnect.
- The relay must retain the last snapshot to serve it to new subscribers, which
  is also what lets a reconnecting HUD show last-known values instead of
  blanking.
- Retained state can be stale, so freshness is carried separately by the
  `status` message's `feed` field rather than inferred from the socket.
