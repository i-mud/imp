# Implementation status

Canonical status for TinyScry. Platform-independent checks run in WSL2; the
native shell is also runtime-verified on Windows 11 x64.

## Working end to end

The complete production-shaped path runs today:

```text
MUD GMCP -> TinyFugue hook -> private spool -> checked adapter JSONL
         -> normalize -> publisher -> loopback relay
         -> SSH tunnel (external or TinyScry-managed) -> native Windows Tauri HUD
```

The source data came from a real target-MUD session. A selected, redacted
fixture was replayed from the VPS through an external SSH tunnel into the
native Windows HUD. Rendered identity, HP, mana, movement, target acquisition,
target damage, and target clearing matched the normalized stream. Producer
stall, producer exit, relay restart, and SSH-tunnel interruption all kept last
known values visibly non-live.

Managed mode is implemented in the Tauri backend: it starts the system OpenSSH
client directly by argv, supervises one owned child, retries with bounded
backoff, and leaves credentials and host verification to OpenSSH. External
tunnel mode remains the default and remains supported.

| Component                 | State                                                                                                                     |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `packages/protocol`       | Complete for version 1. Types, bounds, fail-closed decoder, 32-case corpus.                                               |
| `services/relay`          | Complete for the first slice. Loopback bind, `/state`, `/ingest`, `/healthz`, retained snapshot, feed tracking.           |
| `apps/desktop` (frontend) | Complete. HUD, mock source, relay source with backoff, pure reducer.                                                      |
| `apps/desktop` (Tauri)    | Native shell runtime-verified on Windows; managed SSH lifecycle implemented and covered by deterministic Rust checks.     |
| `integrations/tinyfugue`  | Real-session hook, checked converter, observed-only normalizer, publisher, bridge, replay, and redacted fixture verified. |

## Verification

### Automated CI coverage

`.github/workflows/ci.yml` runs on pull requests targeting `main`, pushes to
`main`, and manual dispatch. It installs the committed npm and uv dependency
state with Node 24 and Python 3.12, then runs the canonical project gate once:
`npm run check`.

That gate covers:

- protocol validation and the shared fixture corpus;
- frontend formatting, lint, type checks, tests, and production build;
- relay lint, strict type checks, and tests;
- TinyFugue adapter lint, strict type checks, and tests;
- deterministic fixture and trust-boundary behavior; and
- the producer-to-relay-to-subscriber loopback end-to-end test.

CI requires no TinyScry secrets or external infrastructure. It does not compile
the Rust crate and does not prove native or live runtime behavior.

### Manual, native, and live evidence

These checks require a native platform, real processes, or operator-controlled
infrastructure and remain separate from CI:

| Evidence                  | Status                                                                                                                                               |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Windows Tauri runtime     | Verified on Windows 11: launch, close, topmost, drag, resize, transparency, live WebView updates, and clean runtime console.                         |
| Actual OpenSSH child      | Not live-verified in managed mode; deterministic Rust checks cover argv, ownership, port conflict, retry, and shutdown.                              |
| Real VPS/systemd behavior | Unit files and lifecycle boundaries are implemented; installation, lingering, restart, and reboot behavior need VPS proof.                           |
| Interactive TinyFugue     | Verified with TinyFugue 5.1.6; the fixed-path `fwrite()` hook did not block the interactive client.                                                  |
| Real MUD/GMCP session     | Verified from live play and a redacted capture; observed normalization covered `Char.Status` and `Char.Vitals`.                                      |
| External SSH runtime      | Verified with a manual local forward, including interruption and recovery.                                                                           |
| Relay bind                | VPS listener observed at `127.0.0.1:8787` only.                                                                                                      |
| Non-loopback guard        | `--host 0.0.0.0` refused without `--allow-non-loopback`; loud warnings when opted in.                                                                |
| `GET /healthz`            | Tunneled HTTP 200; observed `down`, `live`, and `stale` feed states.                                                                                 |
| Last-known safety         | Retained values were labelled `STALE`, `DOWN`, or `RECONNECTING`; they were never presented as live.                                                 |
| Shell-safety boundary     | No MUD value is evaluated as TinyFugue or shell code; the hook uses a fixed path and SSH is spawned directly by argv.                                |
| Tauri Rust crate          | Slice 3 Windows-native mirror: all three Rust gates clean; previously container-verified with `webkit2gtk`.                                          |

CI does not replace any row in this table and must not be cited as evidence for
Windows Tauri behavior, actual OpenSSH supervision, VPS/systemd behavior,
interactive TinyFugue, or a real MUD/GMCP session.

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

## Current milestone

Slice 3 establishes the GitHub Actions baseline around the existing
deterministic gate. Native Rust checks and operator-controlled runtime evidence
remain separate pre-commit and manual responsibilities.
