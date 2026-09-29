# Contributing to Imp

Contributions are welcome.

Imp is still an alpha project, so keeping changes small, testable, and aligned
with the existing architecture is especially useful.

## Before starting

For bug fixes and small improvements, a pull request is fine.

For larger features, protocol changes, new integrations, or architectural
changes, open an issue first so the intended direction can be discussed before
substantial implementation work begins.

Security vulnerabilities should **not** be reported through public issues. See
[SECURITY.md](SECURITY.md).

## Development setup

Imp uses:

- Node.js 24 or newer;
- Python 3.12 or newer;
- [uv](https://docs.astral.sh/uv/); and
- Rust plus the relevant native toolchain for Tauri/native work.

Install the development dependencies:

```bash
npm install
uv sync --project services/relay
uv sync --project integrations/tinyfugue
```

See [docs/development.md](docs/development.md) for platform-specific setup.

## Understand the change boundary

Before changing architecture, protocol behavior, transport, normalization, or
security-sensitive code, start with
[docs/architecture/CONTEXT.md](docs/architecture/CONTEXT.md).

Important references include:

- [wire protocol](packages/protocol/SPEC.md);
- [trust boundary](docs/architecture/boundaries/trust-boundary.md);
- [current implementation status](docs/status.md); and
- [roadmap](docs/roadmap.md).

Source code and tests are authoritative if documentation and implementation
ever disagree.

## Testing

The main platform-independent validation gate is:

```bash
npm run check
```

It covers formatting and documentation checks, TypeScript checks and tests,
Python linting and tests, protocol integration checks, and the frontend build.

Native changes should also run the relevant native tests. For the Windows
Tauri application:

```bash
cargo test --locked --manifest-path apps/desktop/src-tauri/Cargo.toml
```

A documentation-only change normally needs only the checks relevant to the
files changed.

## Pull requests

Keep pull requests focused on one coherent change.

A useful pull request should explain:

- what changed;
- why it changed;
- how it was verified; and
- any known limitations or follow-up work.

Update documentation when the change alters behavior, installation,
architecture, protocol semantics, or a documented invariant.

Do not commit credentials, private keys, pairing tokens, passwords, generated
release artifacts, or machine-local configuration.

## Reporting bugs

Please include enough information to reproduce the problem:

- Imp version or commit;
- desktop operating system;
- connection mode;
- TinyFugue build and MUD when relevant;
- steps to reproduce;
- expected and actual behavior; and
- relevant sanitized logs or error output.

Never include credentials, pairing tokens, private keys, or other secrets in an
issue.
