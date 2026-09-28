# TinyScry - agent guide

TinyScry is a small always-on-top companion HUD for MUDs. It reads character
vitals from a TinyFugue session on a remote VPS and renders them in a compact
frameless desktop window.

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
| what still needs a real MUD session    | `integrations/tinyfugue/README.md` |

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
- Never commit secrets, private keys, or persisted passwords. TinyScry stores no
  SSH password or private key. Direct WSS deliberately persists one pairing
  token in native application configuration; follow
  `docs/architecture/boundaries/trust-boundary.md` for that credential boundary.
- A protocol change must update both decoders and the shared fixture corpus in
  the same commit, or the conformance tests will fail - that tripwire is
  intentional.
- `npm run check` is the platform-independent gate: lint/docs/version checks,
  TypeScript typechecks and tests, relay and TinyFugue lint/tests, the
  cross-component end-to-end check, and the frontend production build.

## Memory vs. documentation

If a persistent memory system is available, it holds experiential knowledge:
past investigations, failed approaches, preferences. It is advisory.

This repository is the source of truth for architecture. Never let a recalled
fact override repository evidence - verify against the source before relying on
it for a change.
