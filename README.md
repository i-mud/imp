# Imp

**Interactive MUD Peripheral** is an alpha desktop HUD that runs alongside
Mudlet or TinyFugue, not instead of your MUD client. See your character's
vitals and target health, set alerts, and save commands to run on demand.
**AVATAR is the MUD whose GMCP mapping has been verified in live play.**

**[Download Imp for Windows x64](https://github.com/i-mud/imp/releases/latest)** — choose the `.exe` installer under **Assets**.

<img width="320" src="docs/images/imp-hud.png" alt="Expanded Imp HUD showing Aria’s health, mana, movement, and a frost giant’s remaining health">

_Preview rendered in Chromium from the frontend shipped in the published
v0.5.3 Windows installer, using synthetic game data. This is not a capture of
the installed Windows app or live gameplay._

## Quick start

The **Windows x64** installer (`Imp_<version>_x64-setup.exe`) is the prebuilt
desktop app. It's unsigned, so Windows may show a SmartScreen warning. Linux
and macOS desktops are source-build targets. The **Linux x86_64 server bundle**
is for the host running TinyFugue; it does not include TinyFugue. Use matching
release versions for remote setup. Neither route needs development tools.

For every connection mode, use Imp only on computers where you trust the other
users and programs. Imp's local service does not ask them to sign in. This applies
to your Windows PC and any Linux host running Imp, even with SSH or Direct WSS.

Choose the route based on where your MUD client runs:
[same-PC Mudlet](#local-mudlet-on-windows) or [remote TinyFugue](#remote-tinyfugue-on-linux).

### Local Mudlet on Windows

1. Download only the Windows installer. Install Imp and launch it once to create `%LOCALAPPDATA%\Imp\mudlet\Imp.mpackage`.
2. Open your AVATAR profile in Mudlet and install the file with **Package Manager**.
   Repeat once per profile you want to use with Imp.
3. In Imp, open **Settings → Connection → Manage** and select **Local** (a fresh
   install defaults to **External**). Select **Save**, quit Imp, and relaunch it.
4. Enable GMCP in Mudlet, log in to AVATAR, and bring that profile to the
   foreground. [Check that your data is live](#first-use).

No VPS, SSH, or pairing token is needed. See [Mudlet package setup](docs/install-windows.md#mudlet-integration).

### Remote TinyFugue on Linux

#### What you need

On the trusted Linux host running TinyFugue, follow the [server guide](deploy/README.md#install-from-a-release-bundle)
to verify and install the matching server bundle. Use the corrected, GMCP-capable
[TinyFugue build](integrations/tinyfugue/README.md#prerequisites). Choose **SSH**
on a fresh install for either mode below. On Windows, install Imp and open
**Settings → Connection → Manage**. Save your choice, quit and relaunch Imp,
then log in with GMCP enabled and select your TinyFugue world.

- **[Managed](docs/install-windows.md#managed-ssh)** (recommended for SSH): Imp
  starts and maintains the forward. Enter an OpenSSH host alias that passes
  the noninteractive preflight. Imp cannot answer password, passphrase, or
  host-key prompts.
- **[External](docs/install-windows.md#external-ssh)** (the default): You start
  and keep the forward running. If SSH needs a password or passphrase, enter
  it in your terminal, not in Imp.

**Direct** skips SSH: follow the [Direct WSS server setup](deploy/README.md#connection-selection)
and [Windows pairing guide](docs/install-windows.md#direct-wss) for a publicly
trusted WSS endpoint and private pairing token. Don't expose the relay itself.

## First use

Under **Settings → Display**, try **Compact** for a single row of vitals and
controls, and **Expanded** for full bars, target health when available, and an
actions footer. Use Expanded to check setup:

1. **Installed:** The bottom of Settings shows the running Imp version. It
   should match the version you installed.
2. **Connected:** **Connected to relay** / **Waiting for game data…** means Imp
   reached the relay but hasn't received character data.
3. **Live:** The HUD shows your intended character and updates as vitals change
   during play. An open SSH session or retained vitals is not proof of live data.

In either layout, hover over the status dot beside the character name to read
its label. **Connecting to relay** or **Reconnecting to relay** means Imp is
trying to connect. **Waiting for game data** means it is connected but waiting;
**No game feed** means no client is feeding the node. **Game data live** means
updates are fresh. **Game feed stalled** means no recent update: this can be
normal while idle, even though the last values remain visible.

- **Alert:** Open **Alerts** (bell) → **Manage**. Label it `Low health`,
  choose **Vitals**, set **Health** to a `50` percent threshold, choose
  **Sound** or **Notification**, and select **Add alert**. Mudlet supports
  vital alerts only; TinyFugue also supports received-text alerts.
- **Safe action:** Open **Actions** (crossed swords) → **Manage** and save label
  `Look` with command `look`. With the intended character live, choose **Look**
  once in **Actions** and check for the room description in your MUD client.
  **Forwarded** does not confirm delivery to the MUD. If the outcome is
  uncertain, check your client rather than retrying; Imp does not auto-retry.

## Help, bugs, and alpha feedback

- **Setup help:** See [Windows verification and troubleshooting](docs/install-windows.md#verify-the-installation)
  or [server diagnostics](deploy/README.md#common-failure-diagnostics). If
  connected but not live, check the selected profile or world and its GMCP.
- **Bug or feature request:** Use the [issue chooser](https://github.com/i-mud/imp/issues/new/choose).
- **Tried the alpha?** Use the [alpha feedback form](https://github.com/i-mud/imp/issues/new?template=alpha_feedback.yml),
  even if setup worked. Tell us your route, which checkpoint you reached,
  where you got stuck, or what helped during play.

Never post passwords, keys, pairing tokens, raw GMCP, or raw game logs. Report
vulnerabilities privately via the [security policy](SECURITY.md).
See the [trust boundary](docs/architecture/boundaries/trust-boundary.md) for details.

## Development

Start with [development prerequisites and commands](CONTRIBUTING.md#development-setup),
then follow the [development guide](docs/development.md) to run Imp from source.

## Contributing

See the [contribution guide](CONTRIBUTING.md) for setup, testing, and pull
requests, and follow the [code of conduct](CODE_OF_CONDUCT.md).

[Architecture and repository map](docs/architecture/CONTEXT.md) ·
[Wire protocol](packages/protocol/SPEC.md) · [Status](docs/status.md) ·
[Roadmap](docs/roadmap.md) · [Audits](docs/audits.md) · [MIT license](LICENSE)
