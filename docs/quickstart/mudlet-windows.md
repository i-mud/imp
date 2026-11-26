# Quick start: Mudlet on Windows

Use this route when Mudlet and Imp run on the same Windows x64 PC. You don't
need a server installation, SSH, WSS, a pairing credential, development tools,
or a separate Python installation. AVATAR is the only MUD whose GMCP mapping
has been verified in live play.

Use Imp only on a PC where you trust the other users and programs: the local
Imp service does not require them to sign in.

1. Open the [official Imp release](https://github.com/i-mud/imp/releases/latest).
   Under **Assets**, download `Imp_<version>_x64-setup.exe`. Imp is alpha and the
   installer is unsigned; Windows may show a SmartScreen warning.
2. Install Imp and open it once. This creates `%LOCALAPPDATA%\Imp\mudlet\Imp.mpackage`.
3. In the Mudlet profile you want to use, open **Package Manager** and install
   that file. Import it once per profile you want to use with Imp.
4. In Imp, open **Settings → Connection → Manage**. Choose **Local** and select
   **Save**. Use **Quit Imp** in the tray, then reopen Imp.
5. Enable GMCP in Mudlet and log into your MUD. Bring the intended profile to
   the foreground.
6. Check that Imp shows your character and that your vitals visibly update
   during ordinary play. A connected indicator or retained values aren't enough.

If no data arrives, confirm you reopened Imp after selecting **Local**, then
check the Mudlet profile, package and GMCP setting. See
[Windows troubleshooting](../install-windows.md#verify-the-installation) or
[Mudlet package updates](../../integrations/mudlet/README.md#install-on-windows).

Once you see live vitals, see the [first-use guide](../first-use.md) for HUD
layouts and optional alerts and actions.
