# TinyScry System Map

Durable architectural navigation. Source code, tests and runtime are the
authority on what currently executes; this map exists so an agent can find the
right code quickly and know what a change is likely to break.

If this map contradicts the source, the source wins and the map is drifted -
fix the map, do not "fix" the code to match it.

## What TinyScry is

A small always-on-top companion HUD for MUDs. It reads character vitals out of
a running TinyFugue session on a remote VPS and renders them in a compact
frameless window on the operator's desktop.

```
MUD <-> GMCP <-> TinyFugue <-> TinyScry TF adapter <-> relay (loopback) <-> SSH tunnel <-> desktop
```

## Where things live

| Concern                      | Path                        |
| ---------------------------- | --------------------------- |
| Wire protocol + validation   | `packages/protocol/`        |
| Protocol reference           | `packages/protocol/SPEC.md` |
| Desktop HUD (Tauri + Svelte) | `apps/desktop/`             |
| Relay service (Python)       | `services/relay/`           |
| TinyFugue integration        | `integrations/tinyfugue/`   |
| VPS systemd units, install   | `deploy/`                   |
| Cross-component e2e check    | `tests/e2e/`                |
| Commands, prerequisites      | `README.md`                 |
| Platform/build strategy      | `docs/development.md`       |
| Implementation status, next  | `docs/status.md`            |

## Map entries

Read the card for the concept you are changing, then the source it cites.

### Objects

| Card                                                     | Covers                                           |
| -------------------------------------------------------- | ------------------------------------------------ |
| [`objects/game-state.md`](objects/game-state.md)         | the normalized state shape and who depends on it |
| [`objects/relay.md`](objects/relay.md)                   | the relay's state ownership and endpoints        |
| [`objects/state-source.md`](objects/state-source.md)     | HUD transport boundary and tunnel diagnostics    |
| [`objects/hud-model.md`](objects/hud-model.md)           | how the HUD turns events into rendered state     |
| [`objects/desktop-alerts.md`](objects/desktop-alerts.md) | desktop-local alert evaluation and effects       |

### Processes

| Card                                                                     | Covers                                  |
| ------------------------------------------------------------------------ | --------------------------------------- |
| [`processes/state-pipeline.md`](processes/state-pipeline.md)             | GMCP to pixels, hop by hop              |
| [`processes/connection-lifecycle.md`](processes/connection-lifecycle.md) | connect, reconnect, stale feed, no data |
| [`processes/managed-runtime.md`](processes/managed-runtime.md)           | VPS services, live feed, SSH ownership  |

### Boundaries

| Card                                                                   | Covers                                 |
| ---------------------------------------------------------------------- | -------------------------------------- |
| [`boundaries/trust-boundary.md`](boundaries/trust-boundary.md)         | what is untrusted, where it is checked |
| [`boundaries/platform-and-build.md`](boundaries/platform-and-build.md) | WSL2 / Windows / macOS build split     |

### Decisions

`decisions/` holds the reasoning behind the architecture. Read one when you are
about to contradict it.

| ADR                                                               | Decision                                            |
| ----------------------------------------------------------------- | --------------------------------------------------- |
| [0001](decisions/0001-loopback-relay-and-ssh-boundary.md)         | relay is loopback-only; SSH is the boundary         |
| [0002](decisions/0002-tinyscry-owned-protocol.md)                 | TinyScry owns its protocol; GMCP stops at normalize |
| [0003](decisions/0003-snapshot-only-state-transfer.md)            | whole snapshots, never partial updates              |
| [0004](decisions/0004-hand-written-validators-shared-fixtures.md) | hand-written decoders, shared fixture corpus        |
| [0005](decisions/0005-python-relay-with-websockets.md)            | small Python relay on `websockets`                  |
| [0006](decisions/0006-npm-workspaces-and-uv.md)                   | npm workspaces + uv, no monorepo framework          |
| [0007](decisions/0007-typescript-6-pin.md)                        | TypeScript pinned to 6.0.x                          |
| [0008](decisions/0008-wsl2-canonical-checkout.md)                 | WSL2 checkout, native per-platform builds           |
| [0009](decisions/0009-context-bound-trusted-actions.md)           | outbound actions require an exact TF context        |

## Invariants worth knowing before you edit

1. The HUD never imports a concrete state source. Only
   `apps/desktop/src/lib/config.ts` names them.
2. No GMCP concept exists downstream of
   `integrations/tinyfugue/src/tinyscry_tf/normalize.py`.
3. The relay binds loopback only, cannot be opted into a public bind, and has
   no authentication by design.
4. A rejected protocol frame never mutates state, in any component.
5. No server-provided value is ever concatenated into a shell command.
6. State and actions are bound to an exact TinyFugue session, foreground
   generation, and connection generation.
7. Desktop `StateSource` and `ActionSink` are separate boundaries; adding
   outbound control must not make state observation bidirectional.
8. Desktop alerts consume normalized `GameState` plus the existing desktop
   freshness model; they do not publish state or trigger outbound actions.

## Maintaining this map

Each card states a verification status and what it was verified against.
Statuses used here: `verified`, `inferred`, `unverified`, `stale`.

Update a card when a change invalidates what it claims - not on every edit. A
card that only restates the directory tree should be deleted instead.
