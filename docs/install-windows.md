# Windows installation

The supported prebuilt desktop target is Windows x64. Imp remains an alpha
project.

The Windows desktop application does not require Node.js, Rust, Python, WSL, or
an Imp source checkout. For a local Mudlet setup, the installer carries the Imp
node, Mudlet helper/runtime, and `Imp.mpackage`; no VPS installation is
required. For remote TinyFugue deployments, install the Imp runtime separately
from the self-contained Linux x86_64 server bundle. TinyFugue itself remains
operator-installed; see the [server prerequisites](../README.md#what-you-need).

## Install Imp

1. Open the [Imp GitHub release](https://github.com/i-mud/imp/releases) for the
   desired version.
2. Download the Windows x64 NSIS `.exe` installer attached to the release.
3. Run the installer.
4. Launch Imp.

The Windows installer is currently unsigned. Windows may display a reputation
or SmartScreen warning depending on the download path and system configuration.

The running native version is shown at the bottom of Imp's Settings panel.
That confirms the desktop build, not that it is connected or receiving state.

## Configure the connection

In either HUD layout, open **Settings** (gear icon), then select **Manage** in
the **Connection** section. Choose a mode below and fill in its fields. Click
**Save**, then quit and relaunch Imp when it says **Saved. Restart Imp to use
this connection.** Closing the manager does not save or restart Imp.

**External** is the fresh-install default. Select **Local** explicitly for
same-host Mudlet. For either remote SSH mode, choose **SSH**
(`--connection ssh`) on the server: Managed and External differ only in who
owns the SSH process on Windows, not in how the server is installed. See
[server connection selection](../deploy/README.md#connection-selection) for
all server choices.

## Local

Select **Local** for same-host Mudlet, then follow the [per-profile package
setup](#mudlet-integration). Local mode consumes the same-host Imp node on
`127.0.0.1:8787`; no VPS or SSH is required. Imp supervises its bundled node
independently of the HUD's connection mode, so Mudlet can stay attached to
that node even when the HUD consumes a different node remotely.

## Managed SSH

Managed SSH uses the Windows system OpenSSH client and your existing OpenSSH
configuration, keys, `known_hosts`, and agent. Imp does not store your SSH
password or private key.

Create or reuse a `Host` entry in `%USERPROFILE%\.ssh\config`, for example:

```sshconfig
Host imp-vps
    HostName vps.example.com
    User muduser
    Port 2222
```

Use your VPS hostname, SSH user, and SSH daemon port (omit `Port` for the
default 22). Set `IdentityFile` if this host needs a nondefault key. Verify the
server's host-key fingerprint through a trusted channel before accepting it in
OpenSSH. Make sure your key is usable without a prompt, for example by loading a
passphrase-protected key into your agent. Then run this from PowerShell:

```powershell
ssh -S none -o BatchMode=yes imp-vps exit
```

This must succeed without a password, passphrase, or host-key prompt. If it
fails, fix OpenSSH access before configuring Imp. Select **Managed** in the
[connection manager](#configure-the-connection), enter `imp-vps` in **SSH
target**, save, and restart Imp.

Imp starts, supervises, and reconnects its own SSH forward from
`127.0.0.1:8789` on Windows to `127.0.0.1:8787` on the server. It does not
reuse an OpenSSH master. If a usable forward already owns the local port,
Managed can use that endpoint without taking ownership; it starts its own
forward when the port becomes free. Quitting Imp stops only the SSH child it
started, not an existing forward or the server services.
Imp terminates only the SSH process it started.

## External SSH

External mode expects an existing endpoint at `127.0.0.1:8789`. For SSH, you
start, maintain, and stop the forward yourself. From PowerShell:

```powershell
ssh -N -T -o ExitOnForwardFailure=yes -p 2222 -L 127.0.0.1:8789:127.0.0.1:8787 muduser@vps.example.com
```

Replace `muduser@vps.example.com` with your SSH login. `2222` is the example SSH
daemon port; use the server's actual port or omit `-p 2222` for port 22 or an
alias whose `Port` is already configured. Keep the released desktop's local
forward port at `8789` and the server relay's destination port at `8787`.
Verify the server host key as for [Managed SSH](#managed-ssh), and keep this
SSH session running. `ExitOnForwardFailure` makes SSH fail when it cannot set up
the forward; it does not verify that the relay or game feed is live.

Use **External** in the [connection manager](#configure-the-connection) (no
change is needed on a fresh install). Imp does not start or stop this forward.

Both SSH modes forward the **entire remote relay TCP port** without filtering
routes or adding relay authentication. Loopback prevents network clients from
reaching the listeners directly, but every host-local user or process can
reach them. Use SSH forwarding only where local users and processes on both
machines are mutually trusted.

## Direct WSS

Direct mode does not use SSH. Follow the [server's Direct WSS setup](../deploy/README.md#connection-selection)
to obtain a publicly trusted WSS endpoint and a one-time pairing token. In the
automatic setup, Imp provisions its own restricted Caddy edge and gateway.
Never capture or redirect the provisioning output. An existing credential is
preserved, not rotated or redisplayed, and does not imply a freshly verified
pairing.

In the [connection manager](#configure-the-connection), select **Direct**,
enter the public endpoint ending in `/state` in **WSS state URL**, and paste the
43-character credential into **Pairing token**, not the URL. Save and restart
Imp.

The saved token is never displayed. Leave the token field blank when saving
Direct settings to keep it; after server-side rotation, paste the replacement
token, save, and restart Imp.

If Direct has a saved token, saving another mode first warns that the locally
stored Direct URL and token will be removed. **Cancel** keeps the saved
configuration and your pending edits unchanged; **Remove credentials and save**
saves the new mode and removes both. Returning to Direct requires re-entering
the URL and token. If you no longer have the token, deliberately rotate it on
the server to obtain a replacement; rotation invalidates the old token for
other paired desktops too. Closing the dialog or pressing Escape does not
confirm or save. Connection changes still take effect only after restarting Imp.

See [server connection selection](../deploy/README.md#connection-selection) for
automatic-path requirements. DNS/DDNS, router forwarding, and firewalls remain
operator-owned. Existing operator public edges and unsupported environments
require [advanced/manual gateway and TLS provisioning](../deploy/README.md#direct-wss-gateway-advanced);
that section also covers status, safe public verification, and deliberate rotation.

## Mudlet integration

Launch Imp once after installing or updating the Windows desktop application.
Imp provisions the Mudlet package at:

```text
%LOCALAPPDATA%\Imp\mudlet\Imp.mpackage
```

Use Mudlet's Package Manager to install `Imp.mpackage` once in **each profile**
that should publish to Imp. Imp deliberately does not modify Mudlet profiles
automatically. Connect and log in with GMCP enabled, and bring the intended
profile to the foreground.

Imp provisions the shared helper/runtime; the package starts that helper for
its profile. The helper talks only to the same-host Imp node on
`127.0.0.1:8787`. Mudlet and its helper do not own SSH, WSS, or pairing
credentials. See [Mudlet integration](../integrations/mudlet/README.md#install-on-windows)
for package upgrades.

The desktop transport ports have distinct roles:

```text
127.0.0.1:8787  same-host Imp node used by MUD-client adapters
127.0.0.1:8788  optional authenticated gateway
127.0.0.1:8789  SSH consumer endpoint used by Managed or External SSH
```

## Verify the installation

1. **Installed:** Settings shows the expected running Imp version. This does
   not establish connectivity.
2. **Connected:** In the expanded HUD, **Connected to relay** / **Waiting for
   game data…** means the desktop reached the relay but has no character data
   yet. A working SSH session or open forward alone does not prove this.
3. **Live:** Log in with your GMCP-enabled MUD client and select the intended
   Mudlet profile or TinyFugue world. Confirm the HUD shows that character and
   updates as game state changes. Retained vitals are not proof of live data.
   If verifying actions, send one harmless, operator-approved command and
   confirm the expected response in the MUD client.

For Managed SSH failures, repeat the [BatchMode preflight](#managed-ssh). For
External SSH, verify your forward is still running and owns `127.0.0.1:8789`.
For Direct WSS, check the public gateway URL, TLS certificate, and pairing
credential against the [deployment guide](../deploy/README.md#direct-wss-gateway-advanced).
If connected but not live, check the selected MUD client and its GMCP setup;
see [server verification](../deploy/README.md#verifying-the-deployment) for
TinyFugue.

## Release support boundary

- Windows x64 is the supported prebuilt desktop target.
- Imp remains alpha quality.
- The installer is unsigned.
- TinyFugue and Mudlet are supported MUD-client integrations.
- AVATAR is the currently live-verified GMCP mapping.
- Linux and macOS remain source-build desktop targets rather than accepted
  native desktop release binaries.
- Linux x86_64 has a prebuilt server bundle for the Imp runtime; TinyFugue
  itself remains operator-installed.
- Automatic Direct provisions an Imp-owned TLS edge and gateway on supported
  Linux hosts; existing operator public edges and unsupported environments
  require manual provisioning.
- Automatic application updates are not included.
