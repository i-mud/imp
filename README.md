# TinyScry

A small always-on-top companion HUD for MUDs.

TinyScry reads character vitals out of a running [TinyFugue](https://github.com/ingwarsw/tinyfugue)
session on a remote VPS and renders them in a compact, frameless, themeable
window that supports Dark, Light, and System themes. It sits over your MUD
client: HP, mana, movement, and the current target's health.

The MUD never talks to the HUD directly. TinyFugue-side Python normalizes state
into a protocol TinyScry owns, and the HUD reads it over an SSH tunnel.

- Architecture and change impact: [`docs/architecture/CONTEXT.md`](docs/architecture/CONTEXT.md)
- Wire protocol: [`packages/protocol/SPEC.md`](packages/protocol/SPEC.md)
- Current implementation and verification: [`docs/status.md`](docs/status.md)
- Planned and future work: [`docs/roadmap.md`](docs/roadmap.md)
- Platform/build strategy: [`docs/development.md`](docs/development.md)

## Data flow

```text
MUD <-> GMCP <-> TinyFugue             (VPS)
                  <-> TinyScry TF adapter
                  <-> TinyScry relay, loopback only
                  <-> SSH tunnel
                  <-> TinyScry desktop
```

The relay is **never** exposed publicly. Loopback plus SSH prevents remote
network access, but loopback does not isolate OS users and TinyScry adds no
per-user authentication. Both workstation and VPS must be single-user or trust
every host-local process - see
[ADR 0001](docs/architecture/decisions/0001-loopback-relay-and-ssh-boundary.md).

## Repository structure

```
tinyscry/
  apps/desktop/           Tauri 2 + Svelte 5 HUD window
  services/relay/         Python WebSocket relay (loopback only)
  integrations/tinyfugue/ GMCP capture, normalization, publisher
  packages/protocol/      Canonical wire protocol, validation, fixtures
  deploy/                 VPS systemd units and install documentation
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

Endpoints: `/state` (subscribers), `/ingest` (producer), `/action`
(one-shot requests), `/action-consumer` (TinyFugue helper), and `/healthz`.

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

TinyScry never talks SSH itself; it always uses the platform's system OpenSSH
client, your `~/.ssh/config`, `known_hosts` and agent. It stores no password
and handles no private key. Two modes, selected by
`tunnel.json` in the app's config directory
(`~/.config/dev.tinyscry.hud/tunnel.json` on Linux,
`%APPDATA%\dev.tinyscry.hud\tunnel.json` on Windows,
`~/Library/Application Support/dev.tinyscry.hud/tunnel.json` on macOS) -
created with a safe default the first time TinyScry runs:

```json
{ "mode": "external", "sshTarget": "" }
```

### External mode (default)

TinyScry owns no SSH process. Forward the relay yourself:

```bash
ssh -N -L 8787:127.0.0.1:8787 <user>@<vps>
```

Then point the HUD at `ws://127.0.0.1:8787/state` exactly as in step 5 - it
cannot tell a manual tunnel from a local relay. This is the mode to keep for
development or an unusual SSH setup.

### Managed mode

Set `mode` to `"managed"` and `sshTarget` to an existing `Host` alias from
your `~/.ssh/config` - the same alias `ssh <alias>` already connects with:

```json
{ "mode": "managed", "sshTarget": "avatar" }
```

On launch, TinyScry's Rust backend spawns and supervises exactly one child
equivalent to:

```text
ssh -N -T -o BatchMode=yes -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
    -L 127.0.0.1:8787:127.0.0.1:8787 -- <sshTarget>
```

directly by argv, never through a shell. Host-key verification is never
weakened - `StrictHostKeyChecking` and `UserKnownHostsFile` are never
overridden, so an unknown or changed host key is refused exactly as it would
be from a terminal. `BatchMode=yes` means a setup that would prompt (an agent
without the key loaded, a passphrase-only key) fails fast instead of hanging;
confirm `ssh <alias>` already connects non-interactively before switching to
managed mode.

If the child exits or the connection drops, TinyScry reconnects with bounded
backoff. If local port `8787` is already occupied, TinyScry never kills the
owning process: it verifies whether that port already answers with TinyScry's
relay health shape and, if so, uses it; otherwise it reports the conflict and
does not start a child. Closing TinyScry terminates only the child it spawned.

`ws://127.0.0.1:8787/state` remains the HUD's default relay URL in both modes.

## How TinyFugue feeds it

On the VPS, TinyFugue's hook writes versioned session/world/context events to a
private spool. `tinyscry-feed` drains them, keeps each world's normalized state
separate, and publishes only the selected exact context:

```bash
uv run --directory integrations/tinyfugue tinyscry-feed
```

The same fixed hook starts a context-bound action helper for the foreground
world. Action text crosses WebSocket and pipe boundaries as data; it is never
shell argv or evaluated TinyFugue source.

The verified hook, drained-file transport, per-world event contract, action
boundary, live verification procedure, and observed mappings are documented in
[`integrations/tinyfugue/README.md`](integrations/tinyfugue/README.md). For the
full VPS install - systemd units, lingering, and the hook install step - see
[`deploy/README.md`](deploy/README.md).

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

## Status and roadmap

Current implementation and verification: [`docs/status.md`](docs/status.md).
Planned and future work: [`docs/roadmap.md`](docs/roadmap.md).

## Security

- All MUD/GMCP data is untrusted; the protocol layer fails closed on malformed
  input and never partially applies state.
- The relay binds `127.0.0.1` by default and has no public listening socket.
- No secrets, no persisted passwords, no private key handling.
- Loopback TCP is not same-user isolation. Another local OS user or process can
  reach the listener; `Origin` checks are browser defense-in-depth, not
  authentication. Untrusted multi-user hosts are unsupported.
- No server-provided value is ever interpolated into a shell command.

Details: [`docs/architecture/boundaries/trust-boundary.md`](docs/architecture/boundaries/trust-boundary.md).

## License

MIT
