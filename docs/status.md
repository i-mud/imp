# Implementation status

Canonical status for TinyScry. Platform-independent checks run in WSL2; the
native shell is also runtime-verified on Windows 11 x64.

## Working end to end

The complete production-shaped path runs today:

```text
MUD GMCP -> TinyFugue hook -> raw capture -> checked adapter JSONL
         -> normalize -> publisher -> loopback relay
         -> manual SSH tunnel -> native Windows Tauri HUD
```

The source data came from a real target-MUD session. A selected, redacted
fixture was replayed from the VPS through the manual SSH tunnel into the native
Windows HUD. Rendered identity, HP, mana, movement, target acquisition,
target damage, and target clearing matched the normalized stream. Producer
stall, producer exit, relay restart, and SSH-tunnel interruption all kept last
known values visibly non-live.

| Component                 | State                                                                                                                     |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `packages/protocol`       | Complete for version 1. Types, bounds, fail-closed decoder, 32-case corpus.                                               |
| `services/relay`          | Complete for the first slice. Loopback bind, `/state`, `/ingest`, `/healthz`, retained snapshot, feed tracking.           |
| `apps/desktop` (frontend) | Complete. HUD, mock source, relay source with backoff, pure reducer.                                                      |
| `apps/desktop` (Tauri)    | Launched and directly exercised on native Windows 11. See runtime evidence below.                                         |
| `integrations/tinyfugue`  | Real-session hook, checked converter, observed-only normalizer, publisher, bridge, replay, and redacted fixture verified. |

## Verification performed

| Check                       | Result                                                                                                                         |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `npm run lint`              | clean (ESLint + Prettier + `scripts/check-docs.mjs`: documented path references all resolve)                                   |
| `npm run typecheck`         | clean - `tsc` and `svelte-check`, 0 errors and 0 warnings                                                                      |
| `npm run test`              | 60 passed (44 protocol, 16 HUD)                                                                                                |
| `npm run relay:lint`        | ruff + `mypy --strict` clean                                                                                                   |
| `npm run relay:test`        | 47 passed                                                                                                                      |
| `npm run tf:lint`           | ruff + `mypy --strict` clean                                                                                                   |
| `npm run tf:test`           | 18 passed                                                                                                                      |
| `npm run test:e2e`          | 2 PASS - publish delivered, retained snapshot on reconnect                                                                     |
| `npm run build`             | 61 kB JS / 3.7 kB CSS                                                                                                          |
| Real GMCP capture           | 249 hook lines over 214.662 s; 200 valid JSON records; 49 invalid inventory payloads safely rejected                           |
| Fixture provenance          | 14 selected records; 68 retained fields and 2 redacted name fields compared to the private capture with 0 mismatches           |
| Observed normalization      | `Char.Status` and `Char.Vitals` only; unknown `Char.Group.List` preserved the exact prior HUD state                            |
| Live MUD session            | live play at 08:36-08:52 UTC: `feed=live`, `seq` 1 to 11, one producer socket, native HUD rendered the live character          |
| Relay bind                  | VPS listener observed at `127.0.0.1:8787` only; manual SSH local forward used                                                  |
| Non-loopback guard          | `--host 0.0.0.0` refused without `--allow-non-loopback`; loud warnings when opted in                                           |
| `GET /healthz`              | tunneled HTTP 200; observed `down`, `live`, and `stale` feed states                                                            |
| Native real-session HUD     | rendered captured identity/resources and target `81% -> 29% -> 0% -> cleared`                                                  |
| Native connection lifecycle | producer stall `STALE`; producer exit `DOWN`; relay/SSH interruption `RECONNECTING`; both recovered to `LIVE`                  |
| Last-known safety           | retained values were labelled `STALE`, `DOWN`, or `RECONNECTING`; they were never presented as live                            |
| Shell-safety audit          | no MUD value is evaluated as TF or shell code; capture uses fixed-path `fwrite`, checked parsing, and direct pipes             |
| HUD visual, mock source     | changing HP/mana/MV and target percentage confirmed                                                                            |
| Tauri Rust crate            | `cargo check`, `cargo clippy -D warnings`, `cargo fmt --check` clean (rustc 1.98.1, Tauri 2.11.5, in a `webkit2gtk` container) |
| Windows native environment  | Rust 1.98.1 stable MSVC host; Visual Studio 2022 native desktop workload; Windows SDK 10.0.22621.0; WebView2 152.0.4191.66     |
| Windows native launch       | `target\debug\tinyscry-desktop.exe` launched and responded; the in-HUD close control ended the full dev process with exit 0    |
| Always on top               | `WS_EX_TOPMOST` present on the live native window (`extendedStyle=0x00040118`)                                                 |
| Drag region                 | pointer drag on the blank title region moved the window exactly `(+90, +60)`                                                   |
| Resize                      | pointer resize changed `320x210` to `422x282`; shrinking stopped at configured `280x180`                                       |
| Native HUD                  | two WebView snapshots 1.3 seconds apart changed HP `806→748`, mana `533→523`, movement `276→241`, target `52%→46%`             |
| Transparency and chrome     | native screen captures show the desktop through rounded corners and translucent panel; no native title bar or frame is visible |
| Native runtime console      | WebView console warnings `[]`, page errors `[]`; final Tauri process log contains no material error                            |

## Not done, and why

- **Automated SSH tunnelling.** Out of scope by decision
  (`docs/architecture/decisions/0001-loopback-relay-and-ssh-boundary.md`); the
  verified manual `ssh -N -L` command remains the supported boundary.
- **No CI.** `npm run check` is the intended CI command; no workflow file yet.

## Bugs found and fixed during bootstrap

Recorded because each was caught by verification rather than by review, and
each is the kind that comes back.

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
3. **Vite watched locked Cargo executables on Windows.** The first native
   launch compiled the Rust crate but then failed with `EBUSY` while Vite tried
   to watch a generated Cargo executable. `vite.config.ts` now ignores the
   native source tree; the next native launch succeeded.
4. **The panel did not fill a resized native window.** At the configured
   `320x210` viewport the panel measured 226 pixels high, clipping its bottom;
   after expansion it left a transparent dead region below the content. The HUD
   now fills `100vh` and uses compact spacing. Runtime measurement is exactly
   `320x210`, with the target row ending at pixel 205.
5. **Blocking input starved the producer's event loop.** `tinyscry-bridge` read
   its input with a synchronous `for` loop, so its WebSocket client never
   processed the relay's close frame: after a relay restart the producer socket
   stayed in `CLOSE-WAIT` and no later state was ever published. `process_lines`
   now awaits `asyncio.to_thread(stream.readline)`, leaving the event loop free
   for close, ping and timeout handling. Pinned by
   `integrations/tinyfugue/tests/test_bridge.py`.

## Next milestone

Add a CI workflow that runs `npm run check`. The native and remote runtime
paths require platform credentials and interactive infrastructure, so CI
should keep exercising their deterministic protocol, fixture, relay, and
frontend boundaries rather than pretending to reproduce the manual evidence
recorded above.
