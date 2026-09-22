# 0008 - WSL2 holds the canonical checkout; native builds happen per platform

Status: accepted
Date: 2026-09-15

## Context

The primary development machine is Windows 11 with WSL2, and the canonical
checkout lives at `~/src/tinyscry` inside WSL2. Tauri has no supported
Linux-to-Windows cross-compilation path: a Windows binary needs the MSVC
toolchain and WebView2 on Windows, and a Linux binary needs `webkit2gtk` on
Linux.

Measured state of this machine during bootstrap:

- WSL2: Node 24.12.0, uv 0.12.5, Python 3.14.4. No Rust toolchain, no
  `pkg-config`, no `webkit2gtk`, and no passwordless sudo.
- Windows: WebView2 runtime 152.0.4191.66 present. No Rust, no MSVC Build
  Tools.

## Decision

- Everything platform-independent - protocol, relay, TF adapter, HUD frontend,
  lint, typecheck, tests, frontend build - runs in WSL2 and is the daily loop.
- The Tauri shell is built natively per target OS against the same checkout:
  from Windows for `.exe`/`.msi`, from WSL2/WSLg (or a container) for Linux,
  from macOS for `.app`.
- No cross-compilation is attempted, and no build step is contorted to pretend
  one platform can produce another's binary.

## Rationale

Cross-compiling a webview application means reproducing another OS's system
webview and linker, which is a large amount of fragile machinery in exchange
for skipping a checkout on the target OS. Keeping the frontend fully testable
in WSL2 gets most of the iteration speed without any of that.

## Consequences

- `npm run dev` (browser, mock source) is the fast loop and needs no Rust.
  `npm run tauri:dev` needs the native toolchain and therefore does not run in
  this WSL2 environment as provisioned.
- To build natively on Windows: install Rust (MSVC host) and Microsoft C++
  Build Tools, then run the Tauri commands from the disposable Windows-native
  mirror at `C:\src\tinyscry-native`. Refresh it from the canonical WSL2 tree
  with `npm run native:sync` or `npm run native:watch`; building across
  `\\wsl$` is slow.
- To build on Linux: `webkit2gtk-4.1`, `libgtk-3`, `librsvg2` and
  `libayatana-appindicator3` development packages are required, which needs
  root on this machine.
- CI can cover all three targets without any developer installing three
  toolchains.
