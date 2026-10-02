# Imp System Map

Durable architectural navigation. Source code, tests and runtime are the
authority on what currently executes; this map exists so an agent can find the
right code quickly and know what a change is likely to break.

If this map contradicts the source, the source wins and the map is drifted -
fix the map, do not "fix" the code to match it.

## What Imp is

A small always-on-top companion HUD for MUDs. A supported MUD-client adapter
attaches to an Imp node on the same host, emits client-neutral GMCP records, and
uses the shared normalization layer to publish canonical Imp state. The desktop
HUD may consume that node locally or reach another node through SSH or
authenticated WSS.

```text
MUD <-> GMCP <-> MUD client
                 |
                 v
           client adapter
                 |
                 v
       shared GMCP normalization
                 |
                 v
          Imp node :8787
            |       |
            |       +-> gateway :8788 -> TLS/WSS -> remote desktop
            |
            +-> local desktop

remote SSH desktop :8789 -- SSH --> remote Imp node :8787
```

TinyFugue and Mudlet are the current client adapters. MUD-specific GMCP
interpretation is shared rather than duplicated between them.

## Where things live

| Concern                                 | Path                        |
| --------------------------------------- | --------------------------- |
| Wire protocol + validation              | `packages/protocol/`        |
| Protocol reference                      | `packages/protocol/SPEC.md` |
| Desktop HUD + native supervisors        | `apps/desktop/`             |
| Imp node + remote gateway services      | `services/relay/`           |
| Shared client-neutral adapter core      | `integrations/common/`      |
| Mudlet integration                      | `integrations/mudlet/`      |
| TinyFugue integration                   | `integrations/tinyfugue/`   |
| VPS systemd units, install              | `deploy/`                   |
| Cross-component e2e check               | `tests/e2e/`                |
| Commands, prerequisites                 | `README.md`                 |
| Platform/build strategy                 | `docs/development.md`       |
| Current implementation and verification | `docs/status.md`            |
| Planned slices and deferred future work | `docs/roadmap.md`           |

## Map entries

Read the card for the concept you are changing, then the source it cites.

### Objects

| Card                                                       | Covers                                                    |
| ---------------------------------------------------------- | --------------------------------------------------------- |
| [`objects/game-state.md`](objects/game-state.md)           | the normalized state shape and who depends on it          |
| [`objects/relay.md`](objects/relay.md)                     | the relay's state ownership and local endpoints           |
| [`objects/gateway.md`](objects/gateway.md)                 | authenticated remote state/action transport boundary      |
| [`objects/state-source.md`](objects/state-source.md)       | HUD transport selection, diagnostics, and settings API    |
| [`objects/hud-model.md`](objects/hud-model.md)             | how the HUD turns events into rendered state              |
| [`objects/desktop-alerts.md`](objects/desktop-alerts.md)   | desktop-local alert evaluation and effects                |
| [`objects/desktop-actions.md`](objects/desktop-actions.md) | local action templates, UI invocation, and result meaning |

### Processes

| Card                                                                     | Covers                                         |
| ------------------------------------------------------------------------ | ---------------------------------------------- |
| [`processes/state-pipeline.md`](processes/state-pipeline.md)             | GMCP to pixels, hop by hop                     |
| [`processes/connection-lifecycle.md`](processes/connection-lifecycle.md) | connect, reconnect, stale feed, no data        |
| [`processes/managed-runtime.md`](processes/managed-runtime.md)           | VPS services, desktop transport, native config |

### Boundaries

| Card                                                                   | Covers                                 |
| ---------------------------------------------------------------------- | -------------------------------------- |
| [`boundaries/trust-boundary.md`](boundaries/trust-boundary.md)         | what is untrusted, where it is checked |
| [`boundaries/platform-and-build.md`](boundaries/platform-and-build.md) | WSL2 / Windows / macOS build split     |

### Decisions

`decisions/` holds the reasoning behind the architecture. Read one when you are
about to contradict it.

| ADR                                                                 | Decision                                           |
| ------------------------------------------------------------------- | -------------------------------------------------- |
| [0001](decisions/0001-loopback-relay-and-ssh-boundary.md)           | relay loopback boundary and SSH transport          |
| [0002](decisions/0002-imp-owned-protocol.md)                        | Imp owns its protocol; GMCP stops at normalize     |
| [0003](decisions/0003-snapshot-only-state-transfer.md)              | whole snapshots, never partial updates             |
| [0004](decisions/0004-hand-written-validators-shared-fixtures.md)   | hand-written decoders, shared fixture corpus       |
| [0005](decisions/0005-python-relay-with-websockets.md)              | small Python relay on `websockets`                 |
| [0006](decisions/0006-npm-workspaces-and-uv.md)                     | npm workspaces + uv, no monorepo framework         |
| [0007](decisions/0007-typescript-6-pin.md)                          | TypeScript pinned to 6.0.x                         |
| [0008](decisions/0008-wsl2-canonical-checkout.md)                   | WSL2 checkout, native per-platform builds          |
| [0009](decisions/0009-context-bound-trusted-actions.md)             | outbound actions require an exact context          |
| [0010](decisions/0010-authenticated-remote-gateway.md)              | public WSS uses a separate authenticated gateway   |
| [0011 (rename)](decisions/0011-rename-to-imp.md)                    | historical TinyScry-to-Imp identity decision       |
| [0011 (node)](decisions/0011-local-client-adapters-and-imp-node.md) | MUD-client adapters attach locally to an Imp node  |
| [0012](decisions/0012-client-neutral-gmcp-adapters.md)              | MUD-specific GMCP interpretation is client-neutral |

## Invariants worth knowing before you edit

1. The HUD never imports a concrete state source. Only
   `apps/desktop/src/lib/config.ts` names them.
2. No GMCP concept exists downstream of
   `integrations/common/src/imp_adapter/normalize.py`.
3. The relay binds loopback only, cannot be opted into a public bind, and has
   no authentication by design.
4. A rejected protocol frame never applies partial or invalid `GameState`.
5. No server-provided value is ever concatenated into a shell command.
6. State and actions are bound to an exact client-adapter session,
   foreground generation, and connection generation.
7. Desktop `StateSource` and `ActionSink` are separate boundaries; adding
   outbound control must not make state observation bidirectional.
8. Desktop alerts consume normalized `GameState` plus the existing desktop
   freshness model; they do not publish state or trigger outbound actions.
9. Saved desktop actions are local command templates, not queued or retained
   dispatches; the relay/client-adapter no-replay contract remains unchanged.
10. Public Direct WSS terminates at the authenticated loopback gateway, never
    at the relay. Authentication succeeds before the gateway opens the relay,
    and privileged producer/helper routes remain unreachable through the gateway.

## Maintaining this map

Each card states a verification status and what it was verified against.
Statuses used here: `verified`, `inferred`, `unverified`, `stale`.

Update a card when a change invalidates what it claims - not on every edit. A
card that only restates the directory tree should be deleted instead.
