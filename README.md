# TinyScry

A small always-on-top companion HUD for MUDs.

TinyScry reads character vitals out of a running [TinyFugue](https://github.com/ingwarsw/tinyfugue)
session on a remote VPS and renders them in a compact, frameless, dark window
that sits over your MUD client: HP, mana, movement, and the current target's
health.

The MUD never talks to the HUD directly. A relay on the VPS normalizes state
into a protocol TinyScry owns, and the HUD reads it over an SSH tunnel.

- Architecture and change impact: [`docs/architecture/CONTEXT.md`](docs/architecture/CONTEXT.md)
- Wire protocol: [`packages/protocol/SPEC.md`](packages/protocol/SPEC.md)
- Implementation status and next milestone: [`docs/status.md`](docs/status.md)
- Platform/build strategy: [`docs/development.md`](docs/development.md)

## Data flow

```
MUD
 -> GMCP
 -> TinyFugue                       (VPS)
 -> TinyScry TF adapter             (VPS, integrations/tinyfugue)
 -> TinyScry relay, 127.0.0.1 only  (VPS, services/relay)
 -> SSH tunnel
 -> TinyScry desktop HUD            (your machine, apps/desktop)
```

The relay is **never** exposed publicly. Its security boundary is loopback plus
SSH, which is why it carries no authentication of its own - see
[ADR 0001](docs/architecture/decisions/0001-loopback-relay-and-ssh-boundary.md).

## Repository structure

```
tinyscry/
  apps/desktop/           Tauri 2 + Svelte 5 HUD window
  services/relay/         Python WebSocket relay (loopback only)
  integrations/tinyfugue/ GMCP capture, normalization, publisher
  packages/protocol/      Canonical wire protocol, validation, fixtures
  tests/e2e/              Cross-component end-to-end check
  docs/                   Architecture map, decisions, status
```

Unit tests live beside the code they test; `tests/` holds only checks that span
components.

## Prerequisites

Required for everything:

- **Node.js >= 24** and npm
- **[uv](https://docs.astral.sh/uv/)** (manages the Python environments; Python
  3.12+)

Required only to build the **native** desktop window:

| Target  | Toolchain                                                                                    |
| ------- | -------------------------------------------------------------------------------------------- |
| Windows | Rust (MSVC host), Microsoft C++ Build Tools, WebView2 runtime                                |
| Linux   | Rust, plus `webkit2gtk-4.1`, `libgtk-3`, `librsvg2`, `libayatana-appindicator3` dev packages |
| macOS   | Rust, Xcode command line tools                                                               |

You do **not** need Rust to develop the HUD, run the relay, or run any check.
`npm run dev` renders the real HUD in a browser against the mock source. See
[`docs/development.md`](docs/development.md) for the WSL2/Windows split.

## Install

```bash
npm install
uv sync --project services/relay
uv sync --project integrations/tinyfugue
```

`npm install` covers both TypeScript workspaces. The `uv sync` calls are
optional - every `npm run relay*` / `tf:*` script syncs on demand.

## Run it

### 1. HUD with mock data (no VPS, no TinyFugue, no Rust)

```bash
npm run dev
```

Opens on <http://localhost:1420> with `MockStateSource` driving changing
vitals. This is the fastest loop and exercises the real protocol decoder - the
mock builds frames and parses them like the relay path does.

### 2. Native HUD window

```bash
npm run tauri:dev
```

Always-on-top, frameless, transparent, draggable by its title row. Needs the
platform Rust toolchain from the table above.

Production bundle:

```bash
npm run tauri -- build
```

### 3. Relay

```bash
npm run relay                       # binds 127.0.0.1:8787
npm run relay -- --port 9000        # or pick a port
```

Check it:

```bash
curl http://127.0.0.1:8787/healthz
```

Endpoints: `/state` (subscribers), `/ingest` (producer), `/healthz`.

### 4. Feed the relay without a MUD

Replay a fixture session through the real normalizer:

```bash
npm run tf:replay -- fixtures/real-session.jsonl
npm run tf:replay -- fixtures/real-session.jsonl --dry-run
```

`--dry-run` prints normalized states and touches no network.

### 5. Point the HUD at the relay

```bash
VITE_TINYSCRY_SOURCE=relay VITE_TINYSCRY_RELAY_URL=ws://127.0.0.1:8787/state npm run dev
```

## SSH tunnel

With the relay running on the VPS bound to loopback, forward it:

```bash
ssh -N -L 8787:127.0.0.1:8787 <user>@<vps>
```

Then point the HUD at `ws://127.0.0.1:8787/state` exactly as in step 5 - it
cannot tell a tunnel from a local relay, which is what makes automated tunnel
management a later additive change.

The first slice uses this manual command deliberately. TinyScry stores no
passwords and handles no private keys; SSH stays your `ssh` client and your
agent.

## How TinyFugue feeds it

On the VPS, TinyFugue appends raw GMCP to a private capture file.
`tinyscry-capture` validates and converts each raw event, and
`tinyscry-bridge` publishes observed state changes:

```bash
tail -n +1 -F "$HOME/.local/state/tinyscry/gmcp.raw" |
  PYTHONUNBUFFERED=1 uv run --directory integrations/tinyfugue tinyscry-capture |
  PYTHONUNBUFFERED=1 uv run --directory integrations/tinyfugue tinyscry-bridge
```

TinyFugue is never asked to build a command line out of server content. The
verified hook, observed mappings, cold-start procedure, and record contract are
documented in
[`integrations/tinyfugue/README.md`](integrations/tinyfugue/README.md).

## Checks

```bash
npm run check          # everything below, in order
```

| Command              | Covers                                                                   |
| -------------------- | ------------------------------------------------------------------------ |
| `npm run lint`       | ESLint + Prettier, plus a check that every path named in the docs exists |
| `npm run typecheck`  | `tsc` and `svelte-check`, strict                                         |
| `npm run test`       | Vitest: protocol and HUD                                                 |
| `npm run relay:lint` | Ruff + mypy (strict) for the relay                                       |
| `npm run relay:test` | pytest: protocol corpus, state, loopback server                          |
| `npm run tf:lint`    | Ruff + mypy (strict) for the TF integration                              |
| `npm run tf:test`    | pytest: normalization, hostile records, replay                           |
| `npm run test:e2e`   | producer -> relay -> subscriber over loopback                            |
| `npm run build`      | production frontend build                                                |

`npm run lint:fix` applies ESLint and Prettier fixes.

## Status

Implemented, verified, and the next milestone: [`docs/status.md`](docs/status.md).

## Security

- All MUD/GMCP data is untrusted; the protocol layer fails closed on malformed
  input and never partially applies state.
- The relay binds `127.0.0.1` by default and has no public listening socket.
- No secrets, no persisted passwords, no private key handling.
- No server-provided value is ever interpolated into a shell command.

Details: [`docs/architecture/boundaries/trust-boundary.md`](docs/architecture/boundaries/trust-boundary.md).

## License

MIT
