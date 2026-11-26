# Quick start: TinyFugue on a Linux server

Use this route when you play in TinyFugue on Linux and want the HUD on your
Windows x64 PC. Use Imp only where you trust the other users and programs on
**both** computers. SSH does not make Imp safe on an untrusted shared host.
AVATAR is the only MUD whose GMCP mapping has been verified in live play.

## On the Linux server

1. Check that your usual launcher runs a corrected, GMCP-capable **TinyFugue
   5.2.4 or later** and identify the startup file it actually uses. Follow the
   [TinyFugue prerequisites](../../integrations/tinyfugue/README.md#prerequisites),
   especially if you use a wrapper, a custom build or `-fFILE`.
2. Use the [release bundle instructions](../../deploy/README.md#install-from-a-release-bundle)
   to check Linux support, download the official Linux x86_64 archive and
   matching checksum, and verify it **before** extraction or execution. Use the
   same release for Windows and Linux. The bundle does not include TinyFugue.
3. Run the installer as the user who runs TinyFugue. Choose **SSH** for a fresh
   install, whichever Windows method you use below. Confirm your actual startup
   file loads Imp capture **before** connecting or logging in. If the installer
   cannot establish load order, use the [startup-file instructions](../../deploy/README.md#tinyfugue-startup-file)
   before continuing.

## On the Windows computer

Download `Imp_<version>_x64-setup.exe` under **Assets** in the
[official Imp release](https://github.com/i-mud/imp/releases/latest). Install and
open Imp. This is an unsigned alpha installer; Windows may show a SmartScreen warning.

Verify the SSH host-key fingerprint through a trusted channel before accepting
it. Stop and investigate an unexpected key change. Then choose how to connect:

- **Let Imp maintain the connection:** Use **Managed** if your verified login
  works without prompts. Follow the [OpenSSH alias and noninteractive preflight](../install-windows.md#managed-ssh).
  In **Settings → Connection → Manage**, select **Managed**, enter the alias in
  **SSH target**, and select **Save**. Use **Quit Imp** in the tray, then reopen Imp.
- **Keep the connection open in a terminal:** Use **External** if you need to
  answer authentication prompts or prefer manual control. Start the
  [loopback-only SSH forward](../install-windows.md#external-ssh) and leave that
  terminal open. Answer prompts there, not in Imp. A fresh Imp install already
  uses **External**; don't save or restart just to select it again. If changing
  from another mode, select **External**, save and reopen Imp.

An open SSH session alone doesn't prove Imp can reach the server's relay.

## In TinyFugue

Start TinyFugue using the verified executable and startup file, log in with
GMCP working, and select the intended world. If TinyFugue is already running,
see [how to load capture safely](../../integrations/tinyfugue/README.md#running-normal-production-path);
the installer doesn't reload it for you.

Check that Imp shows your character and the vitals visibly update as you play.
**Waiting for game data** means Imp connected, not that data is live. If no fresh
vitals appear, check the intended world, GMCP and the capture hook, then see
[Windows checks](../install-windows.md#verify-the-installation) or
[server troubleshooting](../../deploy/README.md#common-failure-diagnostics).

If your server operator has already configured Direct WSS, see
[Direct desktop setup](../install-windows.md#direct-wss). For hosting it yourself,
see [public and NAT requirements](../../deploy/README.md#automatic-direct-public-edge)
and [credential recovery](../../deploy/README.md#existing-direct-credentials-and-recovery).
Direct WSS is outside initial alpha recruitment.

Once you see live vitals, see the [first-use guide](../first-use.md) for HUD
layouts and optional alerts and actions.
