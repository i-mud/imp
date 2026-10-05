# Platform and build boundary

## The split

Imp has two build surfaces with very different requirements.

| Surface                                     | Needs                                    | Execution                                     |
| ------------------------------------------- | ---------------------------------------- | --------------------------------------------- |
| Platform-independent `npm run check` gate   | Node + uv                                | WSL2 and Linux CI                             |
| Tauri Rust tests                            | Rust + target native dependencies        | any supported host when dependencies exist    |
| Native desktop build and runtime acceptance | platform Rust toolchain + system webview | target OS; Windows release builds use Windows |

The platform-independent gate is the daily loop. Native tests and packaging are
separate because Tauri ultimately depends on the target OS's native webview and
toolchain. Reasoning lives in
`docs/architecture/decisions/0008-wsl2-canonical-checkout.md`.

## Toolchain contract

This boundary records durable requirements rather than a live workstation
inventory:

- the root package requires Node.js 24 or newer;
- all Python projects require Python 3.12 or newer and use `uv`;
- Linux CI runs `npm run check` with Node 24, Python 3.12, and the repository's
  pinned `uv` setup;
- Windows CI runs the native Rust tests and builds the x64 NSIS installer with
  Node 24 and stable Rust; and
- native Windows development requires the MSVC Rust host, Microsoft C++ build
  tools, and WebView2.

The dated bootstrap machine snapshot remains in ADR 0008 as historical context.
Local Node, npm, Python, Rust, SDK, and WebView versions are operational
evidence, not architectural constants; re-measure them when they matter.

The native shell is built and launched from a disposable Windows-filesystem
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

The canonical checkout is `~/src/imp` in WSL2. The native Windows tree at
`C:\src\imp-native` is a disposable execution mirror, not a second Git
checkout and never a source of truth.

Use `npm run native:sync` for a one-shot mirror refresh or
`npm run native:watch` while editing in WSL2. Run `npm run tauri:dev` from the
Windows mirror so Cargo, WebView2, and file watching stay on the native
filesystem. Git and GitHub authentication are not required in the mirror.

`.editorconfig` pins LF endings so the same source remains usable from both
sides.

## Deliberate portability constraints

These exist so the HUD does not become Windows-only by accident:

- Always-on-top and non-maximizable behavior are configured declaratively in
  `tauri.conf.json`, not through per-platform native calls. Imp-owned size
  changes still use the normal window API while manual resizing remains off.
- Transparency and `backdrop-filter` are treated as enhancements. The panel
  must stay fully legible without them, which is why its base colour is opaque
  enough on its own. Never make essential information depend on a compositor
  feature.
- `app-region: drag` is present alongside `data-tauri-drag-region` because
  Windows needs it for touch and pen input.
- `macOSPrivateApi: true` is required for transparent backgrounds on macOS;
  it has no effect elsewhere.
- Alert sound is a bundled frontend asset. Native notifications use the
  supported Tauri notification plugin rather than shell commands or a custom
  platform bridge.

## Runtime lifetime containment

Windows release runtime children use a private kill-on-close Job Object, with
atomic job membership at process creation (Windows 10+). Ordinary descendants
are contained; an incompatible enclosing job fails startup rather than
enabling breakaway or running uncontained. Only node, gateway, and owned
Managed SSH spawns enter the job, not Tauri dev/build tools or WebView.

Linux/macOS source builds use an independent guardian and private process
group for supported foreground runtime trees. Kernel pipe EOF triggers cleanup
after desktop death. Linux additionally gives the direct child `PDEATHSIG` with
a parent recheck and enables guardian subreaping; macOS has neither Linux
facility. Managed SSH disables target-client sharing/backgrounding, and Unix
jump/proxy clients require their own foreground configuration. Independently
daemonizing or session/group-changing descendants and guardian failure are
outside that tree guarantee. This is not equivalent to Windows kernel Job
containment. Native macOS runtime acceptance remains unavailable; no macOS
release claim is made.

The precise lifecycle contract is in
[`../processes/managed-runtime.md`](../processes/managed-runtime.md), and the
isolated native checks are in [`../../development.md`](../../development.md).

## Verifying the Rust crate without a full toolchain

The Rust side can be type-checked in a container that has `webkit2gtk`, which
is how it was verified at bootstrap - see `docs/status.md`. This is a
verification aid, not a build path: shipping binaries still happens natively
per platform.

## Change impact

- Adding a Tauri plugin adds a Rust dependency and usually a capability entry
  in `apps/desktop/src-tauri/capabilities/`. The notification plugin is limited
  to permission check, permission request, and notify capabilities. It also
  adds per-platform system requirements - check all three before merging.
- Adding a native command means the browser dev path loses that feature;
  guard it the way `apps/desktop/src/lib/window.ts` does, so `npm run dev`
  keeps working.
- Window behavior that depends on the host window manager, including
  drag-region double-click maximization, requires native acceptance and must
  not be claimed from deterministic CI alone.

## Verification

Status: verified

Verified against the current Linux `npm run check` CI gate, the Windows native
Rust-test/NSIS workflow, and Windows-native runtime acceptance through the
`v0.1.0` release. Native evidence includes non-maximizable window behavior,
notifications, connection management, managed-SSH process lifecycle, live
state/actions, and clean installer/reinstall acceptance.

Linux and macOS remain source-build targets without release acceptance.
Machine-specific version numbers are intentionally not treated as durable
architecture; re-measure them when they matter.
