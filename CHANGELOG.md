# Changelog

This file records notable user-facing changes to TinyScry.

Release versions use semantic versioning. Development slices and their
descriptive Git tags remain historical implementation milestones rather than
public release versions.

## [Unreleased]

Release-readiness work for the first distributable TinyScry build.

## [0.1.0] - Pending

First alpha release.

### Added

- Always-on-top Tauri desktop HUD with compact and expanded layouts, Dark,
  Light, and System themes, tray controls, and persisted window position.
- Normalized character HP, mana, movement, identity, and target-health display
  sourced from TinyFugue GMCP.
- External SSH, managed SSH, and authenticated Direct WSS desktop transports.
- Native Connection settings for configuring all three transport modes without
  manually editing application configuration.
- Managed SSH supervision with bounded reconnect, existing-relay adoption, and
  takeover when an adopted forward disappears.
- Authenticated Direct WSS gateway that leaves the TinyScry relay loopback-only.
- Configurable desktop alerts for vitals and received text, with sound and
  native notifications.
- Configurable outbound action shortcuts for operator-defined MUD commands.
- Context-bound outbound delivery tied to the active TinyFugue session, world,
  and connection generation.
- VPS user services for the relay, TinyFugue feed, and authenticated gateway.
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

### Initial release limitations

- The supported prebuilt desktop release target is Windows x64.
- The Windows alpha installer is unsigned and may trigger Windows SmartScreen
  warnings.
- Linux and macOS remain source-build targets without release acceptance for
  `v0.1.0`.
- TinyFugue is the supported MUD client integration for this release.
- The currently verified normalization mappings are based on AVATAR GMCP;
  broader MUD normalization remains future work.
- VPS setup, TLS reverse-proxy configuration, certificate management, and
  server-side pairing-token provisioning remain operator-managed.
- Application auto-update and pairing-token rotation UX are not included.

For the current implementation and verification evidence, see
[`docs/status.md`](docs/status.md). Planned work and release acceptance live in
[`docs/roadmap.md`](docs/roadmap.md).
