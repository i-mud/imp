# Imp

**Interactive MUD Peripheral**

Imp is a compact desktop companion for MUDs. It displays live character state
from your MUD client, provides configurable alerts, and lets you define trusted
action shortcuts.

Current published release artifacts include:

- a Windows x64 desktop installer, including the desktop-owned local Imp node
  and Mudlet helper/package;
- a Linux x86_64 server bundle for TinyFugue deployments; and
- TinyFugue and Mudlet MUD-client integrations for GMCP state and trusted
  outbound actions.

AVATAR is the currently live-verified GMCP mapping.

## Quick start

Imp supports both local and remote MUD-client topologies.

For Mudlet running on the same Windows machine as Imp, the simplest path is:

```text
MUD <-> Mudlet <-> Imp local node <-> Imp desktop
```

Install Imp, launch it once so the Mudlet helper/package is provisioned, install
`Imp.mpackage` in the Mudlet profile, and select **Local** connection mode. See
[Windows installation](docs/install-windows.md#mudlet-integration) for the
profile setup.

For TinyFugue running on a VPS, the recommended remote setup is **Managed SSH**:

```text
MUD <-> TinyFugue on VPS <-> Imp node
                              |
                              | SSH
                              v
                         Imp desktop
```

The remote Imp node stays bound to the VPS loopback interface. The desktop uses
your existing OpenSSH configuration to reach it.

The numbered walkthrough below describes this remote TinyFugue topology.

### What you need

On the VPS:

- Linux x86_64;
- Python 3.12, available as `python3.12`, with `venv` support;
- `systemd --user`;
- TinyFugue already installed and configured for GMCP; and
- a TinyFugue build whose GMCP support includes the `GMCP_LOGIN` hook.

On the desktop:

- Windows x64;
- Windows OpenSSH; and
- an SSH host alias that can connect to the VPS non-interactively.

Released installations do **not** require an Imp source checkout, Node.js,
npm, Rust, or `uv`.

### 1. Install the Imp server on the VPS

From the matching
[GitHub release](https://github.com/i-mud/imp/releases), download:

```text
imp-server-<version>-linux-x86_64.tar.gz
imp-server-<version>-linux-x86_64.tar.gz.sha256
```

Then, on the VPS:

```bash
sha256sum -c imp-server-<version>-linux-x86_64.tar.gz.sha256

tar -xzf imp-server-<version>-linux-x86_64.tar.gz
cd imp-server-<version>

./install.sh
```

The installer uses an existing `~/.tfrc` by default and adds the Imp capture
hook to it.

If TinyFugue uses a different startup file:

```bash
./install.sh --tf-startup /path/to/your/tinyfugue/startup-file
```

The installer does not create a missing TinyFugue startup file.

Check the required services:

```bash
systemctl --user is-active \
  imp-relay.service \
  imp-feed.service
```

Both should report `active`.

For the services to survive logout and start without an interactive SSH
session:

```bash
loginctl show-user "$USER" -p Linger
```

The expected result is:

```text
Linger=yes
```

If it is disabled and your account is allowed to enable it:

```bash
loginctl enable-linger "$USER"
```

Otherwise an administrator can enable lingering for your account.

After installing the hook, restart TinyFugue and connect to the MUD so Imp sees
a fresh login and world-selection context.

For custom TinyFugue layouts, detailed verification, diagnostics, upgrades, or
Direct WSS deployment, see [VPS deployment](deploy/README.md).

### 2. Install Imp on Windows

From the same
[GitHub release](https://github.com/i-mud/imp/releases), download and run the
Windows x64 `.exe` installer.

The Windows installer is currently unsigned, so Windows may display a
SmartScreen or reputation warning.

Launch Imp after installation.

### 3. Verify SSH access

Before enabling Managed SSH, verify from PowerShell or another Windows terminal
that your existing OpenSSH alias connects without an interactive password or
host-key prompt:

```text
ssh <alias>
```

For example, if this works:

```text
ssh avatar
```

then `avatar` is the alias you enter in Imp.

### 4. Connect Imp

In Imp:

1. Open **Settings -> Connection**.
2. Select **Managed**.
3. Enter your OpenSSH host alias.
4. Save.
5. Restart Imp.

Imp starts and supervises its own SSH forward to the loopback-only Imp node on
the VPS.

After TinyFugue is connected to the MUD, the HUD should begin showing live
character state.

You can then configure alerts and create action shortcuts from Imp's settings.

## Connection modes

Imp supports four desktop connection modes.

### Local

Local mode consumes the desktop-owned same-host Imp node directly on
`127.0.0.1:8787`. This is the normal mode for a local Mudlet integration.

The local node is supervised independently of the HUD's selected connection
mode, so local adapters may remain attached even while the HUD consumes another
Imp node remotely.

### Managed SSH — recommended for remote SSH

Imp starts and supervises the SSH forwarding process itself. It uses the
platform OpenSSH client and your existing SSH configuration, keys,
`known_hosts`, and agent.
The desktop-owned client stays foreground and does not create or reuse a
persistent shared master (`-S none`, `ForkAfterAuthentication=no`). Your SSH
configuration and any external masters are left unchanged.
For Unix source builds, jump/proxy clients must also remain foreground; the
target's options do not propagate to OpenSSH's generated ProxyJump client. See
the [foreground proxy configuration requirement](docs/architecture/processes/managed-runtime.md#desktop-tunnel-boundary).

Imp does not store your SSH password or private key. The SSH local listener is
`127.0.0.1:8789` and forwards the **entire remote relay TCP port** on `8787`;
Imp uses its state/action routes, but SSH does not filter other relay routes.

### External SSH

Use this when you want to manage the SSH process yourself:

```bash
ssh -N -L 8789:127.0.0.1:8787 <user>@<vps>
```

Leave Imp in **External** connection mode. It connects to the forwarded node at
`127.0.0.1:8789`.

### Direct WSS — advanced

Direct mode does not use SSH. It requires Imp's authenticated gateway behind a
publicly trusted TLS reverse proxy plus a pairing token.

Direct WSS is deliberately not enabled by the normal server installation.

See [VPS deployment](deploy/README.md#direct-wss-gateway-advancedmanual) for
gateway, TLS, and pairing setup.

## What Imp provides

- live HP, mana, and movement;
- current character identity;
- current target health;
- compact and expanded HUD layouts;
- Dark, Light, and System themes;
- configurable vital and received-text alerts;
- sound and native notifications;
- configurable outbound action shortcuts; and
- automatic Managed SSH reconnection with bounded backoff.

## How it works

```text
MUD <-> GMCP <-> Mudlet / TinyFugue
                    |
                    | client-specific capture/lifecycle
                    v
             shared GMCP adapter
                    |
                    | normalized state + exact context
                    v
             Imp node :8787
               |          |
               |          +-> gateway :8788 -> TLS/WSS -> remote HUD
               |
               +-> local HUD

Managed/External remote consumption:
Imp desktop :8789 -- SSH --> remote Imp node :8787
```

MUD-client integrations own client-specific lifecycle, foreground selection,
GMCP capture, and the final trusted command write. TinyFugue uses its
versioned spool and `imp-feed`; Mudlet uses its Lua package and local helper.

Both feed the shared client-neutral record/normalization layer before publishing
canonical Imp state to the same-host node. The node owns selected context,
retained state, freshness, and the single-flight action broker.

Outbound actions travel in the opposite direction and remain bound to the exact
current client context. The final command write is client-specific.

The node remains loopback-only. Remote UI access is a separate transport choice:
Managed/External SSH forwards a consumer endpoint, while Direct WSS uses the
separate authenticated gateway.

## Installation and operation

For more detail:

- [Windows installation](docs/install-windows.md)
- [VPS deployment and troubleshooting](deploy/README.md)
- [TinyFugue integration](integrations/tinyfugue/README.md)
- [Mudlet integration](integrations/mudlet/README.md)

## Release support boundary

Imp remains an alpha project.

Currently supported and verified:

- Windows x64 prebuilt desktop installer;
- Linux x86_64 prebuilt server bundle;
- CPython 3.12 server runtime;
- `systemd --user` services for the server deployment;
- TinyFugue and Mudlet MUD-client integrations; and
- AVATAR GMCP normalization through the shared adapter.

Current limitations:

- the Windows installer is unsigned;
- Linux and macOS desktop applications remain source-build targets;
- TinyFugue itself remains operator-installed;
- broader MUD normalization remains future work;
- Direct WSS TLS termination and pairing-token provisioning are
  operator-managed; and
- automatic application updates are not included.

## Development

The requirements below are for developing Imp from source. They are **not**
required for a normal released installation.

Required:

- Node.js >= 24 and npm;
- [uv](https://docs.astral.sh/uv/); and
- Python 3.12+.

Clone the repository and install the development dependencies:

```bash
npm install
uv sync --project services/relay
uv sync --project integrations/common
uv sync --project integrations/mudlet
uv sync --project integrations/tinyfugue
```

Run the HUD in a browser with mock data:

```bash
npm run dev
```

Run the native Tauri application:

```bash
npm run tauri:dev
```

Native builds additionally require the platform Rust/Tauri toolchain. See
[development documentation](docs/development.md) for Windows, Linux, macOS,
and WSL2 details.

Run the complete platform-independent validation gate:

```bash
npm run check
```

Repository areas:

```text
apps/desktop/           Tauri + Svelte desktop HUD and native supervisors
services/relay/         Imp node relay and authenticated gateway
integrations/common/    Client-neutral GMCP normalization and publisher
integrations/mudlet/    Mudlet lifecycle, helper, state, and actions
integrations/tinyfugue/ TinyFugue capture, feed, state, and actions
packages/protocol/      Canonical Imp wire protocol
deploy/                 Server installer and systemd units
docs/                   Architecture, decisions, status, and roadmap
```

## Security

Imp treats MUD and GMCP input as untrusted.

The normal node binds only to `127.0.0.1`. SSH credentials remain owned by
OpenSSH, outbound actions are tied to the exact current MUD-client context, and
malformed protocol input fails closed.

Imp's loopback services are intended for a single-user host, or a host where
all local users and processes are mutually trusted.

For the complete trust model, see
[trust boundary](docs/architecture/boundaries/trust-boundary.md).

## Project documentation

- [Architecture context](docs/architecture/CONTEXT.md)
- [Wire protocol](packages/protocol/SPEC.md)
- [Current implementation status](docs/status.md)
- [Roadmap](docs/roadmap.md)
- [Development](docs/development.md)
- [Repository audits (historical)](docs/audits.md)

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for
development workflow, testing, and pull request guidance.

Please follow the [code of conduct](CODE_OF_CONDUCT.md). Security
vulnerabilities should be reported privately according to
[SECURITY.md](SECURITY.md), not through public issues.

## License

MIT
