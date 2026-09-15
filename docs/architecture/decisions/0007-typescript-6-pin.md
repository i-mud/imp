# 0007 - TypeScript is pinned to 6.0.x, not the npm `latest`

Status: accepted
Date: 2026-09-15

## Context

`npm view typescript version` resolved to `7.0.2` during bootstrap. The
surrounding toolchain does not support it yet, as declared by the packages
themselves:

- `typescript-eslint@8.70.0` peer range: `typescript >=4.8.4 <6.1.0`
- `svelte-check@4.7.6` peer range: `typescript ^5.0.0 || ^6.0.0`

## Decision

Pin `typescript` to `~6.0.3` in every manifest. The intersection of the two
supported ranges is `6.0.x`.

## Rationale

"Current stable" means current stable _for this toolchain_. Taking TypeScript 7
would silence type-aware linting and `svelte-check`, which are two of the four
quality gates - a worse outcome than being one major behind on the compiler.

## Consequences

- Revisit when `typescript-eslint` widens its peer range past `6.1.0` and
  `svelte-check` declares TypeScript 7 support. Both are the gating packages;
  check them first.
- The pin is `~6.0.3` (patch-only) rather than `^6` so a 6.1 release cannot
  silently break `typescript-eslint`'s upper bound.
- This is the kind of fact that goes stale. The peer ranges above are quoted
  from the installed versions so a future reader can re-check them directly.
