# Windows installation

The supported prebuilt desktop target is Windows x64. Imp remains an alpha
project.

The Windows desktop application does not require Node.js, Rust, Python, WSL, or
an Imp source checkout. If you play in Mudlet on this PC, start with the
[local Mudlet quick start](quickstart/mudlet-windows.md). If TinyFugue runs
on a Linux server, use the [remote TinyFugue quick start](quickstart/tinyfugue-remote.md)
for SSH or the [Direct WSS guide](../deploy/direct-wss.md) for a public WSS endpoint.
Use Imp only on computers where you trust the other users and programs.

## Install Imp

1. Open the [official Imp release](https://github.com/i-mud/imp/releases/latest).
2. Under **Assets**, download `Imp_<version>_x64-setup.exe`. For a remote
   setup, use the same release as the Linux server bundle.
3. Run the installer and launch Imp.

The Windows installer is currently unsigned. Windows may display a reputation
or SmartScreen warning depending on the download path and system configuration.

The running native version is shown at the bottom of Imp's Settings panel.
That confirms the desktop build, not that it is connected or receiving state.

## Configure the connection

In either HUD layout, open **Settings** (gear icon), then select **Manage** in
the **Connection** section. Select your mode below. When **Save** shows
**Saved. Restart Imp to use this connection.**, choose **Quit Imp** in the
tray, then reopen Imp. Closing the manager does not save or restart Imp.

The manager shows **saved settings**, not necessarily the running connection.
If you saved changes but haven't reopened Imp, restart without saving again.
If you already applied the intended settings, there's nothing to repeat.
An unchanged fresh install uses **External**. If you are
switching away from Direct, read [what happens to the saved Direct credential](../deploy/direct-wss.md#existing-direct-credentials-and-recovery)
before saving.

## Local

Use [Local for Mudlet on this Windows PC](quickstart/mudlet-windows.md).

## Managed SSH

Follow [Managed SSH](../deploy/ssh.md#managed-ssh) to verify your OpenSSH login,
run the noninteractive preflight, and let Imp maintain the connection.

## External SSH

Follow [External SSH](../deploy/ssh.md#external-ssh) to start the loopback
forward in a terminal and keep it running yourself.

## Direct WSS

Use [Direct desktop configuration](../deploy/direct-wss.md#direct-desktop-configuration)
to pair for the first time. If you're [changing a saved endpoint](../deploy/direct-wss.md#changing-a-saved-endpoint),
check which token to enter **before** saving the new URL.

## Mudlet integration

Follow the [local Mudlet quick start](quickstart/mudlet-windows.md) to install
the package in your profile. See the [Mudlet adapter guide](../integrations/mudlet/README.md#install-on-windows)
for package updates.

## Verify the installation

Use the [first-use checks](first-use.md#check-that-imp-is-working) to distinguish
installed, connected, and live. If you cannot see the intended character's
vitals update during ordinary play, start with [troubleshooting](troubleshooting.md).

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
