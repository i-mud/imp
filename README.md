# Imp

**Interactive MUD Peripheral**

Imp is a compact desktop companion for MUDs. It displays live character state
from your MUD client, provides configurable alerts, and lets you define trusted
action shortcuts.

Published release artifacts include:

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

Install Imp and launch it once to provision the Mudlet helper and package.
Install `Imp.mpackage` in each Mudlet profile that should publish to Imp.
The desktop defaults to **External**, so select **Local** in **Settings >
Connection > Manage**, save, and restart Imp. See [Mudlet profile setup](docs/install-windows.md#mudlet-integration).

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

- Linux x86_64 with glibc 2.36 or newer, the standard /lib64/ld-linux-x86-64.so.2
  loader, `libz.so.1`, and the system CA store (the bundle contains its own
  Python runtime, so no host Python is needed; musl is not supported);
- for the normal automatic installer: Debian 12+ or Ubuntu 24.04+ with
  `systemd`, `apt`, and `iproute2` (arbitrary Linux is not an installer-supported
  target); automatic Direct installs Caddy from the supported system apt
  repositories, so do not preinstall it;
- `systemd --user`;
- operator-installed, GMCP-capable TinyFugue **5.2.4 or later**, with the
  upstream fix for freezes after actions; `5.2.4` is the live-verified build;
- working GMCP negotiation and login. If your login scripts use `GMCP_LOGIN`
  to negotiate GMCP and send `Char.Login`, the build must expose that hook;
  Imp does not use the hook itself. A version string alone does not prove
  that a custom build includes the hook or the upstream fix.

On the desktop:

- Windows x64;
- for Managed SSH, Windows OpenSSH and a [host alias that passes the BatchMode
  preflight](docs/install-windows.md#managed-ssh).

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

A fresh interactive install asks which remote connection to configure. For
this walkthrough select **SSH**; it provides the same loopback relay/feed
setup for **Managed SSH** and **External SSH**, with no public edge. For
scripts, use `./install.sh --connection ssh`. For **Direct WSS**,
**Install only / configure later**, or an existing installation, see
[server connection selection](deploy/README.md#connection-selection).

The installer selects an existing `~/.tfrc` by default. If your actual launch
uses another startup file (for example, `tf -f"$HOME/path/to/.tfrc"`), pass
that existing file to `--tf-startup`. If insertion is unsafe or the hook
already exists, the installer leaves the file unchanged. In the startup file
your actual invocation reads, confirm `/load ~/.config/imp/capture.tf` runs
before any world connects or logs in. See [TinyFugue startup file setup](deploy/README.md#tinyfugue-startup-file)
for manual placement, including legacy world lists.

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

Make sure the actual running TinyFugue process uses the corrected executable;
upgrading a file on disk, reinstalling Imp, or reloading the hook does not
patch an old TF process. Check the running process's executable, not just the
`tf` found in a new shell. If TinyFugue is already running the corrected
binary, installing Imp's files still does not load the hook in that session:
run `/load ~/.config/imp/capture.tf`. A matching checkpoint can restore
retained identity, but verify fresh GMCP before calling the feed live.

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

On Windows, [configure your OpenSSH host alias and run the BatchMode
preflight](docs/install-windows.md#managed-ssh). Managed SSH cannot answer an
interactive password, passphrase, or host-key prompt.

### 4. Connect Imp

Open **Settings > Connection > Manage**. Select **Managed**, enter the verified
alias in **SSH target**, click **Save**, then quit and relaunch Imp. Imp starts
and supervises its own SSH forward to the VPS loopback node.

The installed desktop version confirms only installation. **Connected to
relay** without character data confirms only connectivity. Log in to the MUD
through TinyFugue, select the intended world, and confirm the HUD shows that
character and updates with live state. See [Windows verification](docs/install-windows.md#verify-the-installation).

To create action shortcuts in either display mode, open **Actions** (swords
icon) and choose **Manage**. The expanded HUD also has **Manage** in its footer.
To configure alerts, open **Alerts** (bell icon) in either display mode and
choose **Manage**. Both managers are available even when nothing is
saved. In both display modes, the top row keeps the same shortcut order:
**Alerts**, **Actions**, **Settings**, **Close Imp**. Select another panel
shortcut to switch directly. The open panel's icon gains a small ×; select
it again or press **Escape** to collapse the panel.

**Close Imp** shows an inline **Close Imp?** confirmation in the top row.
**Confirm** exits Imp. **Cancel** or **Escape** leaves the current panel open
and returns focus to **Close Imp**.

**Close** or **Escape** in a manager returns you to the HUD surface that
opened it and focuses its Manage control. Use the Alerts panel's quick
toggles to enable and disable individual alerts.

## Connection modes

Imp supports four desktop connection modes.

### Local

Local mode consumes the same-host Imp node on `127.0.0.1:8787`. Select it
explicitly for [same-host Mudlet](docs/install-windows.md#local).
Imp supervises its bundled node independently of the HUD's connection choice,
so a local adapter can stay attached even while the HUD consumes a remote node.

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

Imp does not store your SSH password or private key. It normally owns the
loopback forward on `127.0.0.1:8789`, but never takes ownership of an existing
forward. See [Managed SSH setup and ownership](docs/install-windows.md#managed-ssh).

### External SSH

External is the fresh-install default. Imp expects an endpoint at
`127.0.0.1:8789`; you start, maintain, and stop the SSH process.
See the [loopback-only forward example](docs/install-windows.md#external-ssh)
for custom SSH daemon ports, host-key verification, and the trust boundary.
Both SSH modes forward the entire remote relay TCP port, not just HUD routes.

### Direct WSS

Direct mode uses a publicly trusted TLS endpoint and an authenticated gateway
instead of SSH. See [Direct server installation](deploy/README.md#connection-selection)
for the supported automatic Caddy setup (including behind NAT) and
[manual deployment or credential recovery](deploy/README.md#direct-wss-gateway-advanced)
for existing public edges. To pair the desktop, follow
[Windows Direct setup](docs/install-windows.md#direct-wss).

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
Imp desktop -> 127.0.0.1:8789 -- SSH --> remote Imp node on 127.0.0.1:8787
```

MUD-client integrations own client-specific lifecycle, foreground selection,
GMCP capture, and the final trusted command write. TinyFugue uses its
versioned spool and `imp-feed`; Mudlet uses its Lua package and local helper.

Both feed the shared client-neutral record/normalization layer before publishing
canonical Imp state to the same-host node. The node owns selected context,
retained state, freshness, and the single-flight action broker.

Outbound actions travel in the opposite direction and remain bound to the exact
current client context. The final command write is client-specific.

The node remains loopback-only. Managed/External SSH forwards the entire
relay TCP port without route filtering; Direct WSS uses the separate
authenticated gateway.

## Installation and operation

For more detail:

- [Windows installation](docs/install-windows.md)
- [Server deployment and troubleshooting](deploy/README.md)
- [TinyFugue integration](integrations/tinyfugue/README.md)
- [Mudlet integration](integrations/mudlet/README.md)

## Release support boundary

Imp remains an alpha project.

Currently supported and verified:

- Windows x64 prebuilt desktop installer;
- Linux x86_64 prebuilt server bundle (binary floor and automatic-installer
  scope are separate; see [VPS deployment](deploy/README.md));
- `systemd --user` services for the server deployment;
- TinyFugue and Mudlet MUD-client integrations; and
- AVATAR GMCP normalization through the shared adapter.

Current limitations:

- the Windows installer is unsigned;
- Linux and macOS desktop applications remain source-build targets;
- TinyFugue itself remains operator-installed;
- broader MUD normalization remains future work;
- automatic Direct requires the supported host and an Imp-owned public edge;
  existing operator edges and unsupported environments require manual TLS and
  gateway provisioning; and
- automatic application updates are not included.

## Development

The requirements below are for developing Imp from source. They are **not**
required for a normal released installation.

Required:

- Node.js >= 24 and npm; and
- [uv](https://docs.astral.sh/uv/), which provides the exact Python
  interpreter pinned in `services/relay/.python-version`. Relay, build, release,
  and source workflows use the locked uv-managed environment, not ambient
  Python.

Clone the repository and install the development dependencies:

```bash
npm install
uv sync --locked --project services/relay --managed-python
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
