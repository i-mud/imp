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

## Canonical checkout and Windows-native mirror

The canonical checkout is `~/src/imp` inside WSL2. Everything except the
native Tauri build runs there.

For native Windows builds, **do not build through `\\wsl$`**. Cargo on the 9P
filesystem is dramatically slower and file watching is unreliable. The native
Windows tree at `C:\src\imp-native` is a disposable execution mirror, not
a Git checkout or source of truth. Make all source edits in WSL2; never edit
the mirror.

Use `npm run native:sync` in WSL2 for a one-shot mirror refresh. It copies the
canonical tree to the Windows tree while excluding Git metadata, dependency and
build directories, local environment files, and private-key material.

For active native development, run `npm run native:watch` in WSL2. It performs
an initial synchronization, then refreshes the mirror after source changes.
Run `npm ci` and `npm run tauri:dev` from the Windows mirror, where Cargo,
WebView2, and file watching stay on the native filesystem. The mirror needs no
Git or GitHub authentication.

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

`services/relay/src/imp_relay/state.py` is transport-free and takes its
clock by injection. Test feed-status rules against it directly rather than
standing up a server - `test_server.py` is for wire behaviour only.

Run it on an ephemeral port while developing so a stale process does not
silently shadow your changes:

```bash
npm run relay -- --port 0
```

## Debugging the HUD over the tunnel

Point the browser build at a separate local port forwarded to the VPS node:

```bash
ssh -N -L 8790:127.0.0.1:8787 <user>@<vps>
VITE_IMP_SOURCE=relay VITE_IMP_RELAY_URL=ws://127.0.0.1:8790/state npm run dev
```

Use a free local port such as `8790`, not `8787`, which the desktop-owned local
node uses. `VITE_IMP_RELAY_URL` sets the browser relay state URL; the matching
action URL is derived from it.

If the HUD shows "no data" while connected, inspect the forwarded node with
`curl http://127.0.0.1:8790/healthz`: check whether a producer is attached and a
snapshot has arrived. Socket liveness and feed liveness are separate signals -
see
[`architecture/processes/connection-lifecycle.md`](architecture/processes/connection-lifecycle.md).

## Native runtime ownership checks

On a host with the native prerequisites and bundled resources:

```bash
cargo test --locked --manifest-path apps/desktop/src-tauri/Cargo.toml --features runtime-acceptance
cargo fmt --manifest-path apps/desktop/src-tauri/Cargo.toml --check
cargo clippy --locked --manifest-path apps/desktop/src-tauri/Cargo.toml --all-targets --features runtime-acceptance
```

The feature builds an isolated subprocess fixture, not another shipped runtime.
Windows CI runs it without terminating the runner's controlling process tree.
The tests exercise the production ownership implementation, abrupt parent
death, descendants, multiple children, external safety, failed startup, and
normal exit/restart. Windows additionally checks atomic job assignment and
nested-job behavior.
Fixture guards retain exact Windows handles, Linux pidfds, or a connected
fixture self-exit channel on other Unix hosts. Windows/Linux identities enter
cleanup ownership before remaining metadata reads or handshake validation;
connected Unix channels are registered before their response is checked.
Guards observe survival/death before cleanup and clean through assertion
unwinding without replacing the original failure. An uncontained launcher and
a deliberately withheld handshake prove the failure/cleanup paths on Windows
and Linux; the Linux `setsid()` negative control records the group boundary.
Windows executable-resolution tests compare production spawns with Rust
`Command`, including cwd shadowing, PATH precedence, explicit paths, and
Unicode/space names. Recheck this parity when updating the Rust toolchain.

For real Windows desktop acceptance, sync the mirror and build its sidecars
using the normal Windows Build workflow prerequisites. Build the production
desktop with a disposable application identifier, then run the acceptance
script from the mirror:

```powershell
$config = Join-Path $env:TEMP ('imp-ownership-' + [guid]::NewGuid() + '.json')
try {
  '{"identifier":"dev.imud.imp.ownership-acceptance"}' | Set-Content -Encoding ascii $config
  npm run tauri --workspace @imp/desktop -- build --bundles nsis --config $config
  if ($LASTEXITCODE) { throw 'Native build failed' }
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-native-ownership.ps1
  if ($LASTEXITCODE) { throw 'Native ownership acceptance failed' }
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-native-ownership.ps1 -InjectFailureAfterSpawn
  if ($LASTEXITCODE -eq 0) { throw 'Injected acceptance failure was not reported' }
} finally {
  Remove-Item $config
}
```

Do not run this against the normal application identifier. The script refuses
occupied runtime ports and an existing acceptance configuration, creates only
disposable settings, and retains exact spawned process handles. It checks real
node/gateway health, hard desktop termination, normal window close, child
crash/restart, relaunch, and survival of an external copy of the same sidecar.
Its Managed SSH case uses a local banner-stalling TCP peer: it exercises the
real system SSH spawn and partial startup, not an authenticated VPS forward.
No installer is installed and no MUD, credential, or remote service is used.

`-InjectFailureAfterSpawn` intentionally exits nonzero after discovering Managed
SSH replacements. Require `Injected failure cleanup passed: all exact fixture
handles exited.` before accepting that negative run. The original failure is
reported only after all retained desktop/runtime/external fixture handles have
been stopped, waited, and checked.

For authenticated SSH acceptance on Linux, build the frozen desktop node and
the feature harness, then run:

```bash
cargo build --locked --manifest-path apps/desktop/src-tauri/Cargo.toml --features runtime-acceptance --bin runtime-ownership-harness
uv run --project services/relay python scripts/test-managed-ssh.py apps/desktop/src-tauri/target/debug/runtime-ownership-harness
```

This requires the installed OpenSSH server (`sshd`), usable server prerequisites,
and free loopback port `8787`. It launches its own local server with disposable
keys/config/known-hosts and the actual frozen node; no system service or operator
SSH configuration is changed. It exercises synchronous 1 MiB LocalCommand
completion, effective config under persistent defaults, authenticated
ProxyCommand/ProxyJump with explicitly foreground jump aliases, normal/abrupt
cleanup, relaunch, and an independently persistent external master/forward.
Its observer retains pidfds only for its own descendant tree, including
adopted daemonized fixtures, and cleans/waits them before propagating failure.

## Before pushing

```bash
npm run check
```

Chains lint/docs/version checks, TypeScript typechecks and tests, relay,
shared-adapter, Mudlet, and TinyFugue lint/tests, the cross-component
end-to-end check, and the frontend production build. The Linux CI job runs
exactly this. Native Rust tests and Windows NSIS builds are separate native
gates.
