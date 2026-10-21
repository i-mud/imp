# Windows installation

The supported prebuilt desktop target is Windows x64. Imp remains an alpha
project.

The Windows desktop application does not require Node.js, Rust, Python, WSL, or
an Imp source checkout. For a local Mudlet setup, the installer carries the Imp
node, Mudlet helper/runtime, and `Imp.mpackage`; no VPS installation is
required. For remote TinyFugue deployments, install the Imp runtime separately
from the self-contained Linux x86_64 server bundle; v0.4.0 needs no host Python.
TinyFugue itself remains operator-installed.
See [`../deploy/README.md`](../deploy/README.md).

## Install Imp

1. Open the Imp GitHub release for the desired version.
2. Download the Windows x64 NSIS `.exe` installer attached to the release.
3. Run the installer.
4. Launch Imp.
5. Open **Settings -> Connection** and configure one of the supported
   transports below.
6. Save the connection settings and restart Imp.

The Windows installer is currently unsigned. Windows may display a reputation
or SmartScreen warning depending on the download path and system configuration.

The running native version is shown at the bottom of Imp's Settings panel.

## Local

Local mode consumes the desktop-owned same-host Imp node on
`127.0.0.1:8787`. It requires no SSH configuration and is the normal choice
when Mudlet and Imp run on the same Windows machine.

Select **Local**, save, and restart Imp. The local node itself is supervised in
every connection mode, so a local Mudlet adapter remains attached even if the
HUD is later switched to a remote node.

## Managed SSH

Managed SSH is the simplest remote mode when the VPS is already reachable
through a working OpenSSH host alias.

Imp uses the Windows system OpenSSH client and does not store an SSH
password or private key.

Before configuring Imp, verify from a terminal that the alias connects
without an interactive password or host-key prompt:

```text
ssh <alias>
```

Then:

1. Open **Settings -> Connection**.
2. Select **Managed**.
3. Enter that OpenSSH host alias.
4. Save.
5. Restart Imp.

Imp starts and supervises its own SSH local-forward child. Closing
Imp terminates only the SSH process it started.

## External SSH

External mode leaves SSH lifecycle entirely to the operator.

Start the forward yourself:

```text
ssh -N -L 8789:127.0.0.1:8787 <user>@<vps>
```

Then leave Imp in **External** connection mode. Imp connects to the
forwarded node at `127.0.0.1:8789`.

SSH forwards the entire remote relay TCP port, not just the routes used by the
HUD. The local listener is loopback-only, but local users and processes can
reach every forwarded relay route.

## Direct WSS

Direct mode does not use SSH. On a supported Debian/Ubuntu Linux server, select
**Direct WSS** during the release-bundle installation, or run:

```bash
./install.sh --connection direct --hostname imp.example.com
```

For a router/NAT deployment, use exactly one public IPv4 A record with no AAAA,
forward router TCP 443 to the server, and add `--behind-nat`. Imp installs and
owns a restricted Caddy edge, obtains publicly trusted TLS, and delegates
authenticated WSS verification and the one-time token handoff to
`imp-direct-wss`. Do not preinstall Caddy for this automatic workflow. Never
capture or redirect the provisioning output.

Fresh SSH and install-only installations leave Direct disabled; explicitly
choosing Direct intentionally provisions it. Existing credentials and connection
state are preserved on an ordinary reinstall/upgrade. An existing credential is
not implicitly rotated or redisplayed, and does not constitute a newly verified
authenticated handoff.

In **Settings -> Connection -> Direct**:

1. Select **Direct**.
2. Enter the public `wss:` state endpoint, ending in `/state`.
3. Enter the 43-character pairing token.
4. Save.
5. Restart Imp.

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

For each Mudlet profile that should publish to Imp, use Mudlet's package
manager to install that `Imp.mpackage` once. Imp deliberately does not modify
Mudlet profiles automatically.

The desktop owns the shared helper/runtime. Mudlet and its helper communicate
only with the same-host Imp node on `127.0.0.1:8787`; they do not own SSH, WSS,
pairing credentials, or other remote transport.

The desktop transport ports have distinct roles:

```text
127.0.0.1:8787  same-host Imp node used by MUD-client adapters
127.0.0.1:8788  optional authenticated gateway
127.0.0.1:8789  SSH consumer endpoint used by Managed or External SSH
```

This means a local Mudlet profile can remain connected to the local node while
the same Imp HUD consumes a different remote node through Managed SSH.

## Verify the installation

After restarting Imp:

- Settings should show the installed Imp version.
- The HUD should receive live character state.
- Saved actions should be able to send an operator-approved command through the
  active connection.

If Managed SSH does not connect, first verify that `ssh <alias>` works
non-interactively outside Imp.

If Direct WSS does not connect, verify the public gateway URL, TLS certificate,
and pairing token against the deployment documentation.

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
