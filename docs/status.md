# Implementation status

Canonical status for TinyScry. Verified at bootstrap on Linux (WSL2), Node
24.12.0, Python 3.14.4, uv 0.12.5.

## Working end to end

The whole VPS-side chain and the HUD run today:

```
TF fixture records -> normalize -> publisher -> relay /ingest
                   -> relay broadcast -> HUD over WebSocket -> rendered vitals
```

This was exercised for real, not just unit-tested: replaying
`integrations/tinyfugue/fixtures/session.jsonl` into a live loopback relay
produced the exact values in the HUD, and killing the producer moved the HUD to
its stale presentation while the socket stayed up.

| Component                 | State                                                                                                           |
| ------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `packages/protocol`       | Complete for version 1. Types, bounds, fail-closed decoder, 32-case corpus.                                     |
| `services/relay`          | Complete for the first slice. Loopback bind, `/state`, `/ingest`, `/healthz`, retained snapshot, feed tracking. |
| `apps/desktop` (frontend) | Complete. HUD, mock source, relay source with backoff, pure reducer.                                            |
| `apps/desktop` (Tauri)    | Configured and type-checks. Not launched natively on this machine - no Rust toolchain here.                     |
| `integrations/tinyfugue`  | Adapter, normalizer, publisher, bridge, replay and fixtures complete. **GMCP capture unverified.**              |

## Verification performed

| Check                   | Result                                                                                                                         |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `npm run lint`          | clean (ESLint + Prettier + `scripts/check-docs.mjs`: 211 documented path references all resolve)                               |
| `npm run typecheck`     | clean - `tsc` and `svelte-check`, 250 files, 0 warnings                                                                        |
| `npm run test`          | 60 passed (44 protocol, 16 HUD)                                                                                                |
| `npm run relay:lint`    | ruff + `mypy --strict` clean                                                                                                   |
| `npm run relay:test`    | 47 passed                                                                                                                      |
| `npm run tf:lint`       | ruff + `mypy --strict` clean                                                                                                   |
| `npm run tf:test`       | 12 passed                                                                                                                      |
| `npm run test:e2e`      | 2 PASS - publish delivered, retained snapshot on reconnect                                                                     |
| `npm run build`         | 61 kB JS / 3.7 kB CSS                                                                                                          |
| Relay bind              | `127.0.0.1:8787` only; connection to the host's routable address refused                                                       |
| Non-loopback guard      | `--host 0.0.0.0` refused without `--allow-non-loopback`; loud warnings when opted in                                           |
| `GET /healthz`          | HTTP 200 with parseable JSON                                                                                                   |
| Shell-safety audit      | no `shell=True`/`os.system`/`subprocess`/`child_process`/Rust `Command` anywhere in the source tree                            |
| HUD visual, mock source | changing HP/mana/MV and target percentage confirmed                                                                            |
| HUD visual, live relay  | fixture session rendered; values matched the replay output                                                                     |
| HUD states              | fresh, no-data, stale-feed and reconnecting all confirmed                                                                      |
| Tauri Rust crate        | `cargo check`, `cargo clippy -D warnings`, `cargo fmt --check` clean (rustc 1.98.1, Tauri 2.11.5, in a `webkit2gtk` container) |

## Not done, and why

- **Native window never launched.** WSL2 here has no Rust toolchain, no
  `pkg-config`/`webkit2gtk`, and no passwordless sudo; Windows has WebView2 but
  no Rust or MSVC. The crate compiles and the window config is schema-valid,
  but `alwaysOnTop`, transparency and drag behaviour are unverified _at
  runtime_. See `docs/architecture/boundaries/platform-and-build.md`.
- **Real GMCP capture.** Deliberately not guessed. See the next section.
- **Automated SSH tunnelling.** Out of scope for the first slice by decision
  (`docs/architecture/decisions/0001-loopback-relay-and-ssh-boundary.md`); the
  manual `ssh -N -L` command is documented in the README.
- **No CI.** `npm run check` is the intended CI command; no workflow file yet.

## Bugs found and fixed during bootstrap

Recorded because both were caught by verification rather than by review, and
both are the kind that come back.

1. **Stale vitals rendered as live.** The HUD derived freshness from the socket
   alone, so a relay that outlived its producer kept showing minutes-old vitals
   at full confidence - the one failure a companion HUD must not have.
   Freshness now lives in `freshnessOf()` in `apps/desktop/src/lib/hud/model.ts`
   and requires both a live socket and a live feed. Pinned by a regression test
   in `apps/desktop/test/model.test.ts`.
2. **Python 3.14-only syntax under a 3.12 floor.** The TF adapter used
   unparenthesised `except A, B:` (PEP 758) while declaring
   `requires-python = ">=3.12"`, which is a `SyntaxError` on any 3.12/3.13 VPS.
   It passed lint because the project pinned ruff's `target-version` and mypy's
   `python_version` to the local interpreter. Both overrides are gone; ruff now
   derives its floor from `requires-python`, so lint catches a recurrence.

## Next milestone

**Connect the real TinyFugue session.** Everything downstream of normalization
is fixture-verified, so this needs no change to the protocol, the relay, or the
HUD.

Smallest useful step: capture a real GMCP transcript from the actual MUD and
turn it into a fixture.

1. On the VPS, enable GMCP in TinyFugue and log raw GMCP for a session that
   includes login, taking damage, healing, acquiring a target, damaging it, and
   losing it.
2. Convert that log into `integrations/tinyfugue/fixtures/real-session.jsonl`
   using the record contract in `integrations/tinyfugue/README.md`.
3. Replace the `UNVERIFIED:` mapping entries in
   `integrations/tinyfugue/src/tinyscry_tf/normalize.py` with the observed
   package names, key spellings and value types.
4. Run `npm run tf:replay -- fixtures/real-session.jsonl --dry-run` and check
   the normalized states.
5. Then run it against a live relay and confirm the HUD.

The specific unknowns are enumerated in
`integrations/tinyfugue/README.md` under "Required real-session verification".

After that, in rough priority order: run the native window on a machine with a
Rust toolchain and verify always-on-top behaviour; add a CI workflow running
`npm run check`; then consider automated SSH tunnel management.
