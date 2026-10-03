# ADR 0011: Rename TinyScry to Imp

## Status

Accepted.

Historical identity-cutover record (Slice 14), not current installation or
release instructions. The public Imp release followed Slice 15; current runtime
paths and automatic release procedures are documented in
[`../../releases.md`](../../releases.md) and
[`../processes/managed-runtime.md`](../processes/managed-runtime.md).

The `0011` number was also used for the later
[`local-client/node decision`](0011-local-client-adapters-and-imp-node.md);
cite the filename or title to disambiguate them.

## Context

The original TinyScry name coupled the product to TinyFugue and described only
its observational role. The application has grown into a broader MUD peripheral:
it receives live state, produces alerts, and can perform explicitly configured,
context-bound actions.

The rename occurs before the first release is treated as externally consumed.
There are no external installations or users whose persisted TinyScry identity
must remain compatible.

## Decision

The product is named **Imp**, short for **Interactive MUD Peripheral**.

Its concise product description is:

> A cross-platform MUD companion for live state, alerts, and trusted actions.

This is a clean identity break rather than a compatibility migration.

Current identities are renamed consistently:

- npm workspace and package names use `imp` and `@imp/*`;
- the Rust desktop crate and executable use `imp-desktop`;
- Python distributions, import packages, and executables use `imp-*`, `imp_relay`,
  and `imp_tf`;
- the Tauri product name is `Imp` and its application identifier is
  `dev.imud.imp`;
- environment variables use `IMP_*` and `VITE_IMP_*`;
- local storage keys use `imp.*`;
- user configuration, state, and runtime paths use `imp`;
- systemd user units use `imp-*.service`;
- TinyFugue integration macros and files use the Imp name;
- the TinyFugue spool event marker is `IMP2`, retaining the existing format
  generation number while changing the product namespace.
- the private selected-context marker is `IMPCTX 2`, retaining context format
  version 2 while changing the product namespace.

No TinyScry compatibility aliases, fallback storage keys, legacy executable
names, or migration paths are retained.

The fresh-install desktop defaults are also established as:

- compact display mode;
- System theme.

The canonical source checkout is `~/src/imp`; the Windows native execution
mirror is `C:\src\imp-native`; the default VPS deployment path is `~/imp`.

The existing `v0.1.0` tag and release will be moved to the validated final Imp
release commit rather than preserving a separate public TinyScry release.

## Consequences

Development machines and the VPS must be switched from the old TinyScry paths,
services, and configuration to their Imp equivalents.

Because there are no external users or installations, avoiding permanent
compatibility machinery is preferable to carrying obsolete product identities.

Future changes should use Imp terminology throughout the current source tree,
documentation, release artifacts, runtime state, and deployment configuration.
