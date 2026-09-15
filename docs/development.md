# Development

Practical procedures. The commands themselves live in [`../README.md`](../README.md);
the reasoning behind the platform split lives in
[`architecture/boundaries/platform-and-build.md`](architecture/boundaries/platform-and-build.md).

## The two loops

**Frontend loop** - `npm run dev`. Browser at <http://localhost:1420>, mock
source, hot reload, no Rust, no relay. This is where nearly all HUD work
happens. The mock exercises the real protocol decoder, so behaviour you see
here is behaviour the relay path will produce.

**Native loop** - `npm run tauri:dev`. Only needed when changing window
behaviour: always-on-top, transparency, dragging, capabilities, or Rust code.
Requires the platform toolchain.

## Canonical checkout and WSL2

The canonical checkout is `~/src/tinyscry` inside WSL2. Everything except the
native Tauri build runs there.

For native Windows builds, **do not build through `\\wsl$`**. Cargo on the 9P
filesystem is dramatically slower and file watching is unreliable. Use a
separate checkout or a worktree on the Windows filesystem:

```powershell
# from Windows, against the same repository
git clone \\wsl$\Ubuntu\home\<user>\src\tinyscry C:\src\tinyscry
# or, if you prefer one history with two working trees:
cd C:\src\tinyscry
git worktree add ..\tinyscry-win
```

Then, in that Windows checkout:

```powershell
npm install
npm run tauri:dev
```

Windows prerequisites: Rust with the MSVC host triple, Microsoft C++ Build
Tools (the "Desktop development with C++" workload), and the WebView2 runtime
(preinstalled on current Windows 11).

`.editorconfig` pins LF line endings so the same tree is comfortable from both
sides. Do not enable `core.autocrlf`.

## Linux native builds

Requires root once:

```bash
sudo apt install libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev \
  libayatana-appindicator3-dev build-essential curl file
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

Under WSL2 the window renders through WSLg.

## Type-checking the Rust crate without a local toolchain

Useful when Rust is not installed, or to confirm the crate compiles on Linux
from a machine that cannot install system packages:

```bash
docker run --rm -v "$PWD":/w -w /w/apps/desktop/src-tauri rust:1-bookworm bash -lc '
  apt-get update -qq &&
  apt-get install -y -qq libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev \
    libayatana-appindicator3-dev &&
  cargo check'
```

This is a verification aid only. Shipping binaries are always built natively on
the target OS.

## Adding a package

1. Create the directory under `packages/` or `apps/` with a `package.json`.
2. `npm install` from the root - npm workspaces picks it up from the existing
   `workspaces` globs.
3. Give it `typecheck` and `test` scripts; the root `npm run typecheck` /
   `npm run test` fan out with `--if-present`.

For a Python project: add a `pyproject.toml` with the `hatchling` backend and a
`[dependency-groups] dev` list, then invoke it as
`uv run --project <dir> <command>`. Add root npm scripts so contributors have
one entry point.

## Working on the protocol

A protocol change touches both decoders and the fixture corpus in the same
commit. The chain is in
[`architecture/objects/game-state.md`](architecture/objects/game-state.md) and
the procedure is at the end of
[`../packages/protocol/SPEC.md`](../packages/protocol/SPEC.md).

Prefer adding a fixture over a language-specific unit test: a fixture
constrains both implementations at once.

## Working on the relay

`services/relay/src/tinyscry_relay/state.py` is transport-free and takes its
clock by injection. Test feed-status rules against it directly rather than
standing up a server - `test_server.py` is for wire behaviour only.

Run it on an ephemeral port while developing so a stale process does not
silently shadow your changes:

```bash
npm run relay -- --port 0
```

## Debugging the HUD over the tunnel

Point the browser build at the tunnelled relay - no native build needed:

```bash
ssh -N -L 8787:127.0.0.1:8787 <user>@<vps>
VITE_TINYSCRY_SOURCE=relay npm run dev
```

If the HUD shows "no data" while connected, the relay is reachable but the feed
is not: check `curl http://127.0.0.1:8787/healthz` and whether a producer is
attached. Socket liveness and feed liveness are separate signals by design -
see
[`architecture/processes/connection-lifecycle.md`](architecture/processes/connection-lifecycle.md).

## Before pushing

```bash
npm run check
```

Chains lint, typecheck, both test suites and the end-to-end check. CI should
run exactly this.
