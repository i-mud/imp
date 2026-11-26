# First use

In **Settings → Display**, choose **Compact** for a single row of vitals and
controls, or **Expanded** for full bars, target health when available, and an
actions footer. Use **Expanded** to check your setup.

## Check that Imp is working

1. **Installed:** The bottom of **Settings** shows the running Imp version.
   Check that it matches the version you installed.
   In source builds with the new Connection dialog, **Connection not set up**
   means no route has been saved. Select **Set up connection**, or choose
   **Not now** and return via **Settings → Connection → Manage**. See the
   [Windows connection guide](install-windows.md#configure-the-connection)
   for the routes and the v0.5.4 instructions.
2. **Connected:** **Connected to relay** / **Waiting for game data…** means Imp
   reached the relay but is still waiting for character data. An open SSH
   session alone does not prove Imp reached the relay.
3. **Live:** Imp shows your intended character, and the vitals visibly update
   during ordinary play. Old values alone don't count. This is the final setup
   checkpoint; you don't need to create an alert or action.

In either layout, hover over the status dot beside the character name:

- **Connection saved** with a restart prompt: Quit Imp from the tray and
  reopen it to use the saved connection.
- **Connection settings unavailable:** Select **Review connection settings**.
  If Imp cannot read the configuration, see
  [configuration recovery](troubleshooting.md#connection-settings-are-unavailable).
- **Connecting to relay** / **Reconnecting to relay:** Imp is trying to connect.
- **Relay unavailable:** Imp isn't connected.
- **Waiting for game data** / **No game feed:** Imp is connected but doesn't
  have live data. Check the selected Mudlet profile or TinyFugue world and GMCP.
- **Game data live:** Game data is fresh. Check that it is the character you expect.
- **Game feed stalled:** No recent update. This can happen while idle, even if
  the last values remain visible.

If you get stuck, see [connection and game-feed troubleshooting](troubleshooting.md).

## Optional: Add a low-health alert

1. Open **Alerts** (bell) → **Manage**.
2. Set **Label** to `Low health`, choose **Vitals**, and select **Health**.
3. Set **Threshold** to `50` percent, choose **Sound** or **Notification**, and
   select **Add alert**.

Mudlet supports vital alerts only. TinyFugue also supports alerts that match
received game text.

## Optional: Try a `look` action

1. Open **Actions** (crossed swords) → **Manage**.
2. Enter `Look` as the **Label** and `look` as the **Command**, then select **Save**.
3. With the intended character live, choose **Look** once in **Actions**. Check
   your MUD client for the room description.

**Forwarded** means Imp passed the action toward the game client; it does not
confirm delivery to the MUD. If delivery is uncertain, check your MUD client
rather than repeating the action. Imp does not retry it automatically.
