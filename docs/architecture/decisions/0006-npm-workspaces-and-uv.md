# 0006 - npm workspaces plus uv, with no monorepo framework

Status: accepted
Date: 2026-09-15

Historical scope: the four-project count and lack of Python artifact builds
below describe bootstrap, not today's inventory. The repository now also has
shared-adapter and Mudlet Python projects and builds wheels/frozen runtimes for
distribution. npm workspaces, uv, and root npm scripts remain the chosen tools.

## Context

The repository holds two TypeScript packages and two Python projects. Task
runners like Turborepo or Nx, and alternative package managers like pnpm, were
the obvious candidates.

## Decision

- TypeScript: npm workspaces (`packages/*`, `apps/*`), built into the npm that
  ships with Node.
- Python: `uv` projects, one per service, invoked as
  `uv run --project <dir> <command>`.
- Cross-language entry point: npm scripts in the root `package.json`.

## Rationale

At four projects there is nothing to orchestrate: the dependency graph is
`desktop -> protocol`, and Python has no build step. A task runner would add a
configuration surface and a cache to debug in exchange for no measurable
benefit.

npm workspaces needs no extra install step on any platform, which matters
because the same checkout is built from WSL2 and from native Windows. `uv` is a
single static binary with identical invocation on all three platforms, so the
Python commands do not fork into `python3` vs. `python`.

## Consequences

- `npm run check` is the single gate; it chains lint, typecheck, and both
  language test suites.
- Adding a package means adding it to `workspaces` and re-running
  `npm install` - no generator, no project graph to register.
- Contributors need Node, `uv`, and (for native builds) the platform Rust
  toolchain. See `docs/architecture/boundaries/platform-and-build.md`.
