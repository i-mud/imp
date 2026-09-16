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
filesystem is dramatically slower and file watching is unreliable. Once the
repository has committed history, prefer a separate clone or worktree on the
Windows filesystem.

For the initial native verification, the canonical tree contained uncommitted
bootstrap work, so a disposable, secret-excluding mirror was used instead:

```powershell
$source = "\\wsl.localhost\<distro>\home\<user>\src\tinyscry"
$destination = "C:\src\tinyscry-native"
New-Item -ItemType Directory -Force $destination | Out-Null
robocopy $source $destination /E /COPY:DAT /DCOPY:DAT /R:2 /W:1 `
  /XD .git node_modules dist target .venv venv __pycache__ .pytest_cache `
      .mypy_cache .ruff_cache .vite .idea `
  /XF .env .env.* *.pem *.key id_rsa* id_ed25519* known_hosts .DS_Store Thumbs.db
if ($LASTEXITCODE -gt 7) { exit $LASTEXITCODE }
Set-Location $destination
npm ci
npm run tauri:dev
```

The WSL tree remains authoritative. Make source edits there and rerun the copy;
do not edit the disposable mirror.

### Windows prerequisites

Tauri 2 requires Rust with the MSVC host, the Microsoft C++ toolchain and
Windows SDK, and the WebView2 runtime. Windows 11 normally includes WebView2.

Install the missing Rust toolchain from PowerShell:

```powershell
winget install --id Rustlang.Rustup --exact --source winget `
  --accept-source-agreements --accept-package-agreements --silent
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
rustup default stable
rustup target add x86_64-pc-windows-msvc
```

If the C++ workload or WebView2 is absent, install it from an Administrator
PowerShell:

```powershell
winget install --id Microsoft.VisualStudio.2022.BuildTools --exact `
  --override "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
winget install --id Microsoft.EdgeWebView2Runtime --exact
```

Do not enable `core.autocrlf`; `.editorconfig` pins LF endings.

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
