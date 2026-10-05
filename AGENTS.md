# Imp - agent guide

Imp is a small always-on-top companion HUD for MUDs. Supported MUD-client
adapters attach to same-host Imp nodes, publish client-neutral normalized
state, and handle exact-context trusted actions. The desktop HUD can consume a
local node or reach a remote node through SSH or authenticated WSS.

## Start here

Source, tests and runtime are the authority on what exists and what executes.

| You need                               | Read                               |
| -------------------------------------- | ---------------------------------- |
| architecture, change impact, decisions | `docs/architecture/CONTEXT.md`     |
| the wire format                        | `packages/protocol/SPEC.md`        |
| commands, prerequisites, how to run it | `README.md`                        |
| what is built and verified             | `docs/status.md`                   |
| planned and deferred work              | `docs/roadmap.md`                  |
| platform/build constraints             | `docs/development.md`              |
| release/versioning rules and procedure | `docs/releases.md`                 |
| what still needs a real MUD session    | `integrations/tinyfugue/README.md` |
| historical repository audits           | `docs/audits.md`                   |

Start at `docs/architecture/CONTEXT.md` for anything structural. It is a
routing table, not a manual - read the one card that covers what you are
changing, then the source it cites.

## Rules

- Architectural cards carry a verification status. Do not assume a card is
  current; if it disagrees with the source, the source wins and the card has
  drifted. Fix the card.
- Update an architectural card only when a change invalidates what it claims.
  Not every code edit needs a documentation edit.
- All MUD/GMCP data is untrusted. Before touching validation, normalization or
  the relay's ingest path, read
  `docs/architecture/boundaries/trust-boundary.md`.
- Never interpolate a server-provided value into a shell command.
- Never commit secrets, private keys, or persisted passwords. Imp stores no
  SSH password or private key. Direct WSS deliberately persists one pairing
  token in native application configuration; follow
  `docs/architecture/boundaries/trust-boundary.md` for that credential boundary.
- A protocol change must update both decoders and the shared fixture corpus in
  the same commit, or the conformance tests will fail - that tripwire is
  intentional.
- `npm run check` is the platform-independent gate: lint/docs/version checks,
  TypeScript typechecks and tests, relay/shared-adapter/Mudlet/TinyFugue Python
  lint, type checks, and tests, the cross-component end-to-end check, and the
  frontend production build.
- Conventional Commits are release inputs, not just style. `fix:` is a patch,
  `feat:` is a minor increment, and an explicit breaking change is a major
  increment. While Imp is `0.x`, a normal `feat:` advances the minor version
  (for example `0.1.0` -> `0.2.0`); it does not imply `1.0.0`.
- Never invent or manually choose a release version. Release publication is
  automatic after an eligible change reaches `main`; `npm run release:next` is
  only an optional preview. Follow `docs/releases.md` for release behavior and
  recovery. Do not create release tags manually.

## Memory vs. documentation

If a persistent memory system is available, it holds experiential knowledge:
past investigations, failed approaches, preferences. It is advisory.

This repository is the source of truth for architecture. Never let a recalled
fact override repository evidence - verify against the source before relying on
it for a change.
