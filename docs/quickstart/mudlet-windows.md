# Quick start: Mudlet on Windows

For Mudlet and Imp on the same Windows x64 PC. No server is needed.
AVATAR is the only MUD whose GMCP mapping has been verified in live play.

Use Imp only on a PC where you trust the other users and programs: the local
Imp service does not require them to sign in.

1. Open the [official Imp release](https://github.com/i-mud/imp/releases/latest).
   Under **Assets**, download `Imp_<version>_x64-setup.exe`. Imp is alpha and the
   installer is unsigned; Windows may show a SmartScreen warning.
2. Install Imp and open it once. This creates `%LOCALAPPDATA%\Imp\mudlet\Imp.mpackage`.
3. In the Mudlet profile you want to use, open **Package Manager** and install
   that file. Import it once per profile you want to use with Imp.
4. In Imp, select **Set up connection**, or open **Settings → Connection →
   Manage** if you dismissed the prompt. Choose **On this computer**; no
   connection details are needed. In [v0.5.4](../install-windows.md#published-v054),
   open **Settings → Connection → Manage** and choose **Local** instead.
   If switching from Direct, read the
   [credential-removal warning](../../deploy/direct-wss.md#existing-direct-credentials-and-recovery)
   before saving. If you haven't saved Local yet, select **Save**. To apply a
   saved change, use **Quit Imp** in the tray and reopen Imp. If Local is
   already in use, skip the save and restart.
5. Enable GMCP in Mudlet and log into your MUD. Bring the intended profile to
   the foreground.
6. Check that Imp shows your character and that your vitals visibly update
   during ordinary play. A connected indicator or retained values aren't enough.

If no data arrives, see [troubleshooting](../troubleshooting.md). When the
package changes in a later Imp release, see [Mudlet package updates](../../integrations/mudlet/README.md#install-on-windows).

Once you see live vitals, see the [first-use guide](../first-use.md) for HUD
layouts and optional alerts and actions.
