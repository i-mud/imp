# HudModel

## Purpose

The HUD's rendered state, and the pure function that derives it from
`SourceEvent`s. This is where "connected but no data", "reconnecting with stale
values" and "out-of-order snapshot" are decided.

## Source

- `apps/desktop/src/lib/hud/model.ts` - `HudModel`, `INITIAL_MODEL`,
  `applyEvent`. Framework-free and immutable.
- `apps/desktop/src/lib/hud/store.svelte.ts` - Svelte 5 runes wrapper; holds
  the model in `$state` and delegates every transition to `applyEvent`.
- `apps/desktop/test/model.test.ts` - the reduction rules.

## Why the split

Reactivity and decision-making are separated so the interesting logic is
testable in plain Node with no compiler and no DOM. The store is deliberately
thin: if a rule can be stated without Svelte, it belongs in `model.ts`.

## Relationships

Fed by:

- any `StateSource` - see `docs/architecture/objects/state-source.md`

Read by:

- `apps/desktop/src/components/Hud.svelte` and its children

## Change impact

- Adding a rendered field: `HudModel`, `applyEvent`, the reducer tests, the
  components.
- Changing the reconnect display rule changes what the operator sees during an
  outage; `docs/architecture/processes/connection-lifecycle.md` describes the intended behaviour
  and must be updated with it.
- `applyEvent` switches exhaustively over `SourceEvent['kind']`, so a new event
  kind surfaces here as a type error rather than as silent inaction.

## Invariants

- Pure and immutable: `applyEvent` returns a new model and never mutates its
  input.
- A `snapshot` whose `seq` is not newer than the current high-water mark is
  dropped.
- The high-water mark resets on `hello` and on connection transitions, because
  the relay's `seq` restarts when its process does.
- A `protocol-error` records the error and leaves `state` untouched.
- The store contains no business logic.

## Verification

Status: verified
Verified against: `apps/desktop` at bootstrap; reducer unit tests passing.
