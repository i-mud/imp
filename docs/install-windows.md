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

The published v0.7.0 release supports the Connection dialog and Managed SSH
server-login fields or an existing alias. Current source builds add a
Managed SSH **Check connection** button. For the older manager, see
[Published v0.5.4](#published-v054).

On a fresh launch, select **Set up connection**. **Not now** dismisses the
prompt without choosing or saving a connection. You can return to setup or
change an existing connection from **Settings → Connection → Manage** in
either HUD layout. Existing valid configurations don't need setup again.

A fresh installation opens **Connection** with no choice selected: choose
where your MUD client runs and follow the matching route below. If a
connection is already saved, the dialog opens on that route (including
**Advanced** for External), so you only change it if you want a different
one. Select **Save**, choose **Quit Imp** in the tray, then reopen Imp to
apply a change. Closing the dialog does not save or restart Imp.

**In use** shows which connection is active in this Imp session, not whether
the connection is healthy. **Saved for next launch** appears only when
the saved connection differs. If the intended connection is already saved
for next launch, restart without saving again. If it's already in use,
there's nothing to repeat.

If switching away from Direct, read
[what happens to the saved Direct credential](../deploy/direct-wss.md#existing-direct-credentials-and-recovery)
before saving.

### Published v0.5.4

This release doesn't show **Set up connection** on first launch. A fresh
installation uses **External** by default. Open **Settings → Connection →
Manage** and choose **Local**, **Managed**, **External**, or **Direct**
directly. This release shows the saved selection, not the running connection.
Save any change, then use **Quit Imp** in the tray and reopen it. A fresh
v0.5.4 installation already using External doesn't need a save or restart
to use your own SSH tunnel.

## Local

Choose **On this computer**. There are no connection fields to fill in.

Use [Local for Mudlet on this Windows PC](quickstart/mudlet-windows.md).

## Managed SSH

Choose **On another computer or server → Use my server login**.

Follow [Managed SSH](../deploy/ssh.md#managed-ssh) to establish host trust,
prepare your OpenSSH login, and check it. Use **Check connection** in a current
source build or the terminal preflight with the published v0.7.0 release.

## External SSH

Choose **On another computer or server → Advanced → Use my own SSH tunnel**.

Follow [External SSH](../deploy/ssh.md#external-ssh) to start the loopback
forward in a terminal and keep it running yourself.

## Direct WSS

Choose **On another computer or server → Use remote Imp credentials**.

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
