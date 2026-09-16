# Platform and build boundary

## The split

TinyScry has two build surfaces with very different requirements.

| Surface                                                  | Needs                                    | Runs in WSL2       |
| -------------------------------------------------------- | ---------------------------------------- | ------------------ |
| Protocol, relay, TF adapter, HUD frontend, all checks    | Node + uv                                | yes                |
| Tauri shell (native window, always-on-top, transparency) | platform Rust toolchain + system webview | no, as provisioned |

Everything in the first row is the daily loop. Reasoning in
`docs/architecture/decisions/0008-wsl2-canonical-checkout.md`.

## Measured environment

Observed on this machine:

- WSL2: Node 24.12.0, npm 11.6.2, Python 3.14.4, uv 0.12.5, Docker 29.8.0.
  **No** Rust toolchain, **no** `pkg-config`, **no** `webkit2gtk`, and **no**
  passwordless sudo.
- Windows 11 x64: Node 26.2.0, npm 12.0.2, Rust 1.98.1 stable with the
  `x86_64-pc-windows-msvc` host, Visual Studio Professional 2022 with the
  native desktop C++ workload, Windows SDK 10.0.22621.0, and WebView2 runtime
  152.0.4191.66.

The native shell was built and launched from a disposable Windows-filesystem
mirror of the canonical WSL2 tree. `npm run dev` remains the fast frontend
loop; `npm run tauri:dev` is the native window verification loop.

## Per-platform prerequisites

| Target  | Toolchain                                                                                |
| ------- | ---------------------------------------------------------------------------------------- |
| Windows | Rust (MSVC host) + Microsoft C++ Build Tools + WebView2 runtime                          |
| Linux   | Rust + `webkit2gtk-4.1`, `libgtk-3`, `librsvg2`, `libayatana-appindicator3` dev packages |
| macOS   | Rust + Xcode command line tools                                                          |

No cross-compilation. A webview app needs the target OS's webview and linker;
faking that is a large amount of fragile machinery to avoid one checkout.

## Working across WSL2 and Windows

The canonical checkout is `~/src/tinyscry` in WSL2. For native Windows builds,
use a checkout or `git worktree` on the Windows filesystem rather than building
through `\\wsl$` - the 9P filesystem makes Cargo builds dramatically slower and
file watching unreliable.

`.editorconfig` pins LF endings so the same tree is usable from both sides.

## Deliberate portability constraints

These exist so the HUD does not become Windows-only by accident:

- Always-on-top is configured declaratively in `tauri.conf.json`, not through
  per-platform native calls.
- Transparency and `backdrop-filter` are treated as enhancements. The panel
  must stay fully legible without them, which is why its base colour is opaque
  enough on its own. Never make essential information depend on a compositor
  feature.
- `app-region: drag` is present alongside `data-tauri-drag-region` because
  Windows needs it for touch and pen input.
- `macOSPrivateApi: true` is required for transparent backgrounds on macOS;
  it has no effect elsewhere.

## Verifying the Rust crate without a full toolchain

The Rust side can be type-checked in a container that has `webkit2gtk`, which
is how it was verified at bootstrap - see `docs/status.md`. This is a
verification aid, not a build path: shipping binaries still happens natively
per platform.

## Change impact

- Adding a Tauri plugin adds a Rust dependency and usually a capability entry
  in `apps/desktop/src-tauri/capabilities/`. It also adds per-platform system
  requirements - check all three before merging.
- Adding a native command means the browser dev path loses that feature;
  guard it the way `apps/desktop/src/lib/window.ts` does, so `npm run dev`
  keeps working.

## Verification

Status: verified
Verified against: Windows 11 native launch and direct window interaction at the
versions above; Linux Rust compilation remains container-verified.
Version numbers here go stale quickly - re-measure rather than trusting them.
