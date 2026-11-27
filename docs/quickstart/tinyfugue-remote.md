# Quick start: TinyFugue on a Linux server

Use this route when you play in TinyFugue on Linux and want the HUD on your
Windows x64 PC. Use Imp only where you trust the other users and programs on
**both** computers. SSH does not make Imp safe on an untrusted shared host.
AVATAR is the only MUD whose GMCP mapping has been verified in live play.

This quick start uses SSH. If you have a public WSS endpoint or plan to host
one, use the [Direct WSS guide](../../deploy/direct-wss.md) instead of installing
for SSH. Direct WSS is outside initial alpha recruitment.

## On the Linux server

Follow [Install from a release bundle](../../deploy/README.md#install-from-a-release-bundle)
to check prerequisites and download and verify the Linux x86_64 archive before
extracting it. Run the installer as the user who runs TinyFugue and choose
**SSH** on a fresh install. Before logging in, confirm that the startup file
your launcher actually uses loads Imp capture before connection or login
commands. If the installer cannot establish load order, follow the
[startup-file instructions](../../deploy/README.md#tinyfugue-startup-file).

## On the Windows computer

From the same [official release](https://github.com/i-mud/imp/releases) as the
Linux bundle, download `Imp_<version>_x64-setup.exe` under **Assets**. Run it
and open Imp. The alpha installer is unsigned; Windows may show a SmartScreen
warning. Select **Set up connection**, or open **Settings → Connection →
Manage** if you dismissed the prompt. Choose **On another computer or server**.
Then choose who runs the SSH connection:

- **Use my server login:** Follow [Managed SSH](../../deploy/ssh.md#managed-ssh)
  if your verified Windows OpenSSH login works without prompts. The published
  v0.6.0 installer uses an existing SSH alias; newer source builds can also
  accept a server, username, and optional SSH port directly.
- **Advanced → Use my own SSH tunnel:** Follow
  [External SSH](../../deploy/ssh.md#external-ssh) if you need authentication
  prompts or prefer manual control.

If you installed [v0.5.4](../install-windows.md#published-v054), follow the
release-specific steps in the matching SSH section instead.

An open SSH session alone doesn't prove Imp can reach the intended server's
relay. Save any connection change and quit and reopen Imp to apply it.

## In TinyFugue

Start TinyFugue using the verified executable and startup file, log in with
GMCP working, and select the intended world. If TinyFugue is already running,
see [how to load capture safely](../../integrations/tinyfugue/README.md#running-normal-production-path);
the installer doesn't reload it for you.

Check that Imp shows your character and the vitals visibly update as you play.
**Waiting for game data** means Imp connected, not that data is live. If no
fresh vitals appear, use [troubleshooting](../troubleshooting.md).

Once you see live vitals, see the [first-use guide](../first-use.md) for HUD
layouts and optional alerts and actions.
