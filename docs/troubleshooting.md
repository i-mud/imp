# Troubleshooting Imp

Start with what you see below. In either HUD layout, hover over the status dot
beside the character name; **Settings → Display → Expanded** also shows the
connection message when there is no character. The last checkpoint is seeing
the **intended character's vitals visibly update during ordinary play**. A
working SSH login, a connected relay, or numbers left over from a previous
session don't establish that.

## Imp won't start

- **Meaning:** This is a desktop installation or launch problem, not a test of
  the server connection.
- **Check and recover:** Check the Windows notification area in case Imp is
  already running. Otherwise launch the installed Imp app, not the setup file.
  If setup didn't finish, check that you downloaded the official Windows x64
  `.exe` from [Imp releases](https://github.com/i-mud/imp/releases), not the
  Linux server bundle or a source archive. The installer is unsigned; see
  [Windows installation](install-windows.md#install-imp).
- **Stop:** If Windows or your organization's policy blocks it, don't disable
  protection. Ask your administrator. If the supported installer completed but
  Imp still won't open, [report](#report-a-problem-safely) whether it failed at
  install or launch and the sanitized error text, if any.

## Imp says the connection is not set up

- **Meaning:** In v0.6.0 and later, this message means Imp has no saved
  connection and is not trying to reach a relay.
  prompt doesn't pick a connection.
- **Check and recover:** Select **Set up connection** or go to
  **Settings → Connection → Manage**, then follow the
  [Windows connection guide](install-windows.md#configure-the-connection).
  This prompt isn't present in [v0.5.4](install-windows.md#published-v054).

## Connection settings are unavailable

- **Meaning:** If Imp couldn't read or validate an existing configuration
  at startup, it does not use a default route or overwrite the file.
- **Check and recover:** Select **Review connection settings**. If the
  existing file is unreadable or invalid, quit Imp from the tray.
  Restore a known-good configuration or move the existing file aside,
  keeping it private, then reopen Imp. See
  [configuration file locations](../deploy/operator-reference.md#desktop-configuration-files-for-diagnostics).
  Moving the file aside leaves Imp unconfigured; set up the connection again
  only when you have the required details. In particular, you'll need both
  the URL and token to set up Direct again.
- **Stop:** If you can't recover the file or required details safely,
  [report](#report-a-problem-safely) the sanitized error, not the file.

## Server login works but Imp won't connect

- **Meaning:** An interactive SSH login doesn't prove port forwarding, the Imp
  relay, or the separate Direct WSS path works.
- **Check and recover:** In **Settings → Connection → Manage**, check the
  connection **In use** (in v0.5.4, the selection shows what was last saved).
  Follow the matching route:
  - **Managed SSH (Use my server login):** In a current source build, select
    **Check connection** with the same server details or SSH alias. It checks
    the entered login and the relay's reported feed, not the running HUD
    route or desktop port. Use the
    [terminal preflight](../deploy/ssh.md#terminal-preflight) in v0.7.0 and
    earlier or to investigate the login. Handle prompts in your own SSH client.
    If the host key is unexpected or has changed, stop and verify it with
    the server operator.
  - **External SSH (Use my own SSH tunnel):** Make sure your
    [SSH forward](../deploy/ssh.md#external-ssh) is still running on
    `127.0.0.1:8789` and points to the right server's loopback relay.
    A login window without that forward isn't enough.
  - **Direct WSS (Use remote Imp credentials):** SSH is unrelated. Check
    the public `wss://` URL ending in `/state` and its trusted TLS certificate.
    Obtain the pairing token from your server operator through a trusted
    private channel; see [Direct WSS setup](../deploy/direct-wss.md).
- **Stop:** If the chosen path fails its checks, ask the server operator. Don't
  bypass host-key or TLS checks, replace an unknown process using a port, or
  expose the loopback relay to make the connection work. See [operator checks](#operator-checks).

If the Managed check reports forwarding unavailable or cannot read the relay,
ask the operator to check forwarding permission and the server's loopback relay.
A timeout may also mean the path or login was slow. A stale, down, or missing
feed is different from a login failure: see
[Imp is connected but shows no character](#imp-is-connected-but-shows-no-character).
If the check reports live but the HUD still isn't connected after Save and
restart, check the [gameplay forward and desktop port](../deploy/ssh.md#troubleshooting).

## Imp is connected but shows no character

- **Meaning:** **Connected to relay** / **Waiting for game data…** means Imp
  reached the relay but doesn't have a character to show. A relay can be
  available without a working game feed or a character identity.
- **Check and recover:** Log in to your GMCP-enabled MUD and bring the intended
  profile or world to the foreground. For [local Mudlet](quickstart/mudlet-windows.md),
  check that GMCP is enabled, the Imp package is installed in that profile,
  and the desktop uses **Local**. For [remote TinyFugue](quickstart/tinyfugue-remote.md),
  check that the world is connected and ask the operator to verify the capture
  hook and the existing GMCP login setup. Imp doesn't negotiate GMCP for
  TinyFugue; it needs character identity as well as vitals.
- **Stop:** If ordinary play still produces no character, ask the operator to
  check for a producer and snapshot, or [report](#report-a-problem-safely) the
  MUD and client. AVATAR is the currently live-verified GMCP mapping. See
  [operator checks](#operator-checks) for TinyFugue.

## Imp shows the wrong character

- **Meaning:** Imp may be displaying the previous character's retained data,
  following another foreground profile or world, or connected to the wrong node.
- **Check and recover:** Check the status dot and the desktop's connection
  mode. Bring the intended Mudlet profile to the foreground or select the
  intended world in TinyFugue. Watch for its name **and** new vitals as you
  play. If the wrong node is configured, correct the
  [connection settings](#connection-settings-dont-take-effect).
- **Stop:** Don't use Imp actions to test which character is active. If the
  wrong character's data keeps updating after you verify the route and switch
  profiles or worlds, [report](#report-a-problem-safely) what changed and when,
  without including character names.

## Vitals stopped updating

- **Meaning:** **Game feed stalled** means no recent update (which can be
  normal while idle). **No game feed** means the relay has no active feed.
  The last known values can remain visible in either case.
- **Check and recover:** In your MUD client, confirm the intended session is
  connected. Resume normal play and compare when a vital changes in the client.
  If the session had disconnected, reconnect that session normally and bring it
  back to the foreground. If the HUD says **Reconnecting to relay**, follow the
  [connection-loss steps](#the-connection-stopped) instead.
- **Stop:** If your client's vitals change but the HUD's do not, don't rely on
  those values or use Imp actions. Check the profile's package/GMCP or ask the
  TinyFugue operator to check the [feed](#operator-checks).

## The connection stopped

- **Meaning:** **Reconnecting to relay** or **Relay unavailable** concerns
  the path between Imp and the relay, not necessarily your MUD session.
  Imp retries the state connection. Old vitals can stay visible or reappear
  on reconnect, but Imp doesn't resend actions.
- **Check and recover:** Keep playing in your MUD client if it's still
  connected. Check the underlying path: for **Local**, look for a local node
  or port error; for **Managed**, check the network and follow the
  [Managed SSH diagnostics](#server-login-works-but-imp-wont-connect);
  for **External**, restore your own forward if it exited; for **Direct**,
  check public reachability and TLS. After it reconnects, confirm fresh
  updates before relying on the HUD again.
- **Stop:** If it keeps cycling or reports a port conflict, see the
  [Windows connection routes](install-windows.md#configure-the-connection)
  or ask the [server operator](#operator-checks). Don't kill an unknown
  listener or restart your MUD client to try to fix a transport outage.

## The pairing credential is rejected

- **Meaning:** A validation error on **Save** concerns the form. Repeated
  reconnects after a successful save are not by themselves proof of a bad
  token: a wrong endpoint, TLS problem or unavailable gateway can look similar.
- **Check and recover:** Confirm the `wss://…/state` URL and its current
  43-character token came from the same server. Obtain the token from the
  operator through a trusted private channel and enter it only in the
  **Pairing token** field, never in the URL or a support report. The saved
  token isn't displayed.
  Leave the field blank to keep the existing token only for the unchanged
  saved URL. When changing endpoints, enter the token for the new one, even
  if the installed version lets you save with a blank field. If the operator
  already rotated the token, enter the replacement. After saving a correction,
  [quit and reopen Imp](#connection-settings-dont-take-effect).
- **Stop:** If you can't obtain the correct token or the path still fails, ask
  the operator to use [Direct credential recovery](../deploy/operator-reference.md#existing-direct-credentials-and-recovery)
  and [bounded public verification](../deploy/operator-reference.md#bounded-public-verification).
  Don't rotate to diagnose a connection failure. Rotation is a deliberate
  server change that invalidates the old token for all clients using it.

## Connection settings don't take effect

- **Meaning:** The manager edits saved settings, not the running connection.
  **In use** is the running connection; **Saved for next launch** appears
  only when a saved change still needs a restart. The published v0.5.4
  release shows only the saved selection. Closing the dialog doesn't save
  or apply changes.
  **Check connection** doesn't save or apply changes either.
- **Check and recover:** If the intended route is already saved for next
  launch, use **Quit Imp** in the Windows notification area and reopen Imp.
  Otherwise, choose the intended route in **Settings → Connection → Manage**,
  select **Save**, then quit and reopen Imp. When switching away from Direct,
  **Remove credentials and save** deletes the locally stored Direct URL and
  token; **Cancel** preserves the saved configuration. Before confirming,
  make sure you can re-enter both if you intend to return to Direct. This
  doesn't revoke the token on the server.
- **Stop:** If a change doesn't save, address the displayed validation or
  write error first. If the settings are unavailable, follow
  [configuration recovery](#connection-settings-are-unavailable). If the correct
  route is in use after relaunch but isn't live, follow the matching connection
  or feed steps above.

## Imp says action delivery is uncertain

- **Meaning:** Imp didn't get a definite result. The command may already have
  run. Even **Forwarded** doesn't confirm final MUD delivery. **Action was not
  forwarded because the game context changed** is a different result: the
  selected session, foreground profile/world, or connection is no longer the
  one for that action.
- **Check and recover:** Look for the effect in the intended MUD client, or
  inspect the resulting game state there. Confirm the intended character is
  live before relying on Imp again. Imp doesn't retry or replay the action
  after reconnecting.
- **Stop:** If you can't tell whether it ran, leave it unresolved rather than
  resending it from Imp or from the MUD client. If this recurs,
  [report](#report-a-problem-safely) the result category and whether the context
  or connection had just changed, not the command or game output.

## Operator checks

Use these only if you administer the affected installation, or pass them to
its operator. Port and health checks show which part is reachable; they don't
prove fresh updates from the intended character.

- **Local Mudlet:** After launching Imp on the Windows PC, check the local
  node from a terminal with
  `curl.exe --fail --silent --show-error http://127.0.0.1:8787/healthz`.
  Once the foreground profile has received character data, look for a
  producer and snapshot. `stale` can be normal while idle.
- **SSH:** Follow the [Managed](../deploy/ssh.md#managed-ssh) or
  [External](../deploy/ssh.md#external-ssh) checks, then have the operator
  verify the server relay is reachable on loopback. Don't expose the relay.
- **Direct:** Have the operator check the loopback gateway and the
  [public TLS/WSS edge](../deploy/operator-reference.md#direct-wss-gateway-advanced).
  Don't loosen permissions on the private configuration or credential file.
- **TinyFugue:** Have the operator check `imp-feed.service` and whether the
  relay sees a producer and snapshot. An `fwrite` error points to a feed or
  spool problem even if TinyFugue keeps running. With a producer but no
  snapshot, verify [the hook loads early](../deploy/operator-reference.md#tinyfugue-startup-file)
  and [GMCP supplies character identity and vitals](../integrations/tinyfugue/README.md#gmcp-login-and-character-identity).

For server commands and remedies, see [common failure diagnostics](../deploy/operator-reference.md#common-failure-diagnostics)
and [deployment checks](../deploy/operator-reference.md#verifying-the-deployment).

## Report a problem safely

Use the [issue chooser](https://github.com/i-mud/imp/issues/new/choose). Include
Imp's running version (bottom of **Settings**, if it opens), the MUD client and
connection mode, the last checkpoint reached (installed, connected, or live
with the intended character), what you were doing when it stopped working, and
any short status or error text after redaction. If Imp doesn't open, give the
installer version instead. Describe what you expected and what happened.

Don't include raw logs, raw GMCP, captured frames, game text, action commands,
configuration dumps, passwords, SSH keys, pairing tokens or other credentials.
Remove usernames, hostnames, addresses, private paths and character names
from any quoted text or screenshot; when in doubt, summarize rather than
paste. Report suspected vulnerabilities privately via the
[security policy](../SECURITY.md).
