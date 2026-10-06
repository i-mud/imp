# Changelog

This file records notable user-facing changes to Imp.

Release versions use semantic versioning. Development slices and their
descriptive Git tags remain historical implementation milestones rather than
public release versions.

## [Unreleased]

No changes yet.

## [0.3.1] - 2026-10-06

### Bug Fixes

- support private-group Direct WSS parents ([30cb46c](https://github.com/i-mud/imp/commit/30cb46ca1fb157ce8b28e155b92bdaacf0ebda87))

## [0.3.0] - 2026-10-06

### Features

- add Direct WSS provisioning ([21ac531](https://github.com/i-mud/imp/commit/21ac531f55398882089b071094b7db64ffb81764))

## [0.2.8] - 2026-10-05

### Bug Fixes

- validate action broker result timeout ([a98d1ca](https://github.com/i-mud/imp/commit/a98d1ca43028ed698ebf8a0373f0ab640a56aa8c))

## [0.2.7] - 2026-10-05

### Bug Fixes

- make shared UI client-neutral and accessible ([b513e7e](https://github.com/i-mud/imp/commit/b513e7edbb0d235db14936ddd017a9346e518e4e))

## [0.2.6] - 2026-10-05

### Bug Fixes

- contain desktop-owned native runtimes ([dd49555](https://github.com/i-mud/imp/commit/dd49555cec3986ce58f31ba7b8fd562e2c0a057e))

## [0.2.5] - 2026-10-04

### Bug Fixes

- harden relay diagnostics and version identity ([fcb1bdc](https://github.com/i-mud/imp/commit/fcb1bdcb4b3fcb2cdbb5bf56e8d2bac0084e4237))

## [0.2.4] - 2026-10-04

### Bug Fixes

- isolate slow relay subscribers ([edb486c](https://github.com/i-mud/imp/commit/edb486cbfb39faabfb11828ef67ede5ec0d038b4))

## [0.2.3] - 2026-10-03

### Bug Fixes

- keep selected feeds live on unchanged state observations ([6384e56](https://github.com/i-mud/imp/commit/6384e56b68413be3cccb152ce6e8d4c9f40a3bb1))

## [0.2.2] - 2026-10-03

### Bug Fixes

- enforce atomic bounded input rejection across adapters and relay ([41687db](https://github.com/i-mud/imp/commit/41687dbc824f8d3300ee27f987798c1943ffb6fc))

## [0.2.1] - 2026-10-02

### Bug Fixes

- version all Python release packages ([8f85a95](https://github.com/i-mud/imp/commit/8f85a9595b3778a558abeb7e89212d15eb84875c))

## [0.2.0] - 2026-10-02

### Bug Fixes

- bound Mudlet action acknowledgement ([69ced20](https://github.com/i-mud/imp/commit/69ced2038073af5fbc642a0aacf938232308f408))
- bundle shared adapter runtime ([7aca2b0](https://github.com/i-mud/imp/commit/7aca2b0b1d6943b6847928d0bcb0b69b21e68e4b))
- tolerate local node startup health race ([98159af](https://github.com/i-mud/imp/commit/98159af50915605609cda4720e01f1844ed288a6))

### Features

- add local connection mode ([4e94d60](https://github.com/i-mud/imp/commit/4e94d604bba2373bb761349ad8768b0af1a27dae))
- add Mudlet adapter lifecycle ([6767346](https://github.com/i-mud/imp/commit/6767346b58c4c21ba2f2de32ad2b09f37f45e9b4))
- add trusted Mudlet actions ([a00163c](https://github.com/i-mud/imp/commit/a00163c33a6ab279592846edf3c9e35621335b89))
- complete client-neutral local node transport ([a2a8318](https://github.com/i-mud/imp/commit/a2a8318cb9dd45d2848ff90af6ceeb8a635438dd))
- provision Mudlet helper from desktop ([7a96656](https://github.com/i-mud/imp/commit/7a96656f2576f3a0f8b3f98f5cdac1ad77d21bb5))
- publish Mudlet state to local node ([5d9e072](https://github.com/i-mud/imp/commit/5d9e0728a8357f0ace75791753b19edabca1dd35))
- supervise local Imp node ([21decf7](https://github.com/i-mud/imp/commit/21decf739453fba091776ca6df32162b6766c5dc))

## [0.1.0] - 2026-09-29

First alpha release of Imp — Interactive MUD Peripheral.

### Added

- Always-on-top Tauri desktop HUD with compact and expanded layouts, System,
  Dark, and Light themes, tray controls, and persisted window position.
- Compact layout and System theme as the fresh-install defaults.
- Normalized character HP, mana, movement, identity, and target-health display
  sourced from TinyFugue GMCP.
- External SSH, managed SSH, and authenticated Direct WSS desktop transports.
- Native Connection settings for configuring all three transport modes without
  manually editing application configuration.
- Managed SSH supervision with bounded reconnect, existing-relay adoption, and
  takeover when an adopted forward disappears.
- Authenticated Direct WSS gateway that leaves the Imp relay loopback-only.
- Configurable desktop alerts for vitals and received text, with sound and
  native notifications.
- Configurable outbound action shortcuts for operator-defined MUD commands.
- Context-bound outbound delivery tied to the active TinyFugue session, world,
  and connection generation.
- VPS user services for the relay, TinyFugue feed, and authenticated gateway.
- Versioned Linux x86_64 server bundle with an offline wheelhouse, internal and
  external checksum verification, idempotent per-user installation, stable
  runtime paths, and failed-activation rollback.
- Tag-driven release publishing of the server archive and checksum alongside
  the Windows desktop installer.
- Transient selected-world received-text delivery for alert evaluation.
- Deterministic protocol validation and shared cross-language fixtures.

### Reliability and safety

- Malformed protocol input fails closed without partially applying state.
- The relay remains restricted to loopback and cannot be configured for a
  public bind.
- Direct WSS authenticates before opening an upstream relay connection and
  exposes only the desktop-facing state/action boundary.
- Pairing credentials are kept out of URLs, WebView local storage, build-time
  configuration, and ordinary diagnostics.
- Outbound actions are never queued, retried, or replayed after rejection or
  connection loss.
- TinyFugue action delivery rechecks the exact live context immediately before
  sending and never retargets stale work to another world.
- Managed SSH never kills an unrelated process occupying its local port and
  owns only the child process it spawned.
- Windows release builds start the managed SSH child without opening a
  persistent console window.
- Exiting Imp terminates the managed SSH child it owns instead of leaving
  an orphaned `ssh.exe` process.

### Initial release limitations

- The supported prebuilt desktop release target is Windows x64.
- The Windows alpha installer is unsigned and may trigger Windows SmartScreen
  warnings.
- Linux and macOS remain source-build desktop targets without native desktop
  release acceptance for `v0.1.0`.
- The prebuilt server bundle target is Linux x86_64 with CPython 3.12 and
  `systemd --user`.
- TinyFugue is the supported MUD client integration and remains
  operator-installed.
- The currently verified normalization mappings are based on AVATAR GMCP;
  broader MUD normalization remains future work.
- Direct WSS TLS reverse-proxy configuration, certificate management, and
  server-side pairing-token provisioning remain operator-managed.
- Application auto-update and pairing-token rotation UX are not included.

For the current implementation and verification evidence, see
[`docs/status.md`](docs/status.md). Planned work and release acceptance live in
[`docs/roadmap.md`](docs/roadmap.md).
