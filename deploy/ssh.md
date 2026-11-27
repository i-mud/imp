# Connect Imp over SSH

Choose **Managed SSH** to let Imp run and reconnect the tunnel. Use **External
SSH** if you prefer to keep your own tunnel open or your SSH login needs an
interactive prompt. Both modes use the same server installation.

## Before you start

- Follow the Linux [release-bundle installation](README.md#install-from-a-release-bundle)
  as the user who runs TinyFugue and choose **SSH**. Install the
  [Windows desktop](../docs/install-windows.md) if you haven't yet.
  For the full setup through first game data, see the
  [TinyFugue quick start](../docs/quickstart/tinyfugue-remote.md).
- Use the Windows OpenSSH **Client** from PowerShell on the same computer and
  Windows account that run Imp, not from WSL or from the server. Ask the server
  operator for the SSH hostname, login name, port, and host-key fingerprint.

Both modes forward the entire relay TCP port without filtering routes or adding
relay authentication. Use SSH forwarding only if you trust the local users and
processes on **both** computers: a loopback listener isn't reachable directly
from the network, but other processes on that computer can reach it. Never bind
the relay or the SSH forward to a public interface.

## Managed SSH

In a source build with separate login fields, follow [Server and
username](#server-and-username). The published
[v0.6.0 installer](https://github.com/i-mud/imp/releases/tag/v0.6.0) uses the
[existing alias](#existing-ssh-alias) steps below instead. Neither route
handles interactive SSH prompts in Imp.

### Server and username

1. Get the server address, SSH username, any nondefault SSH port, and
   host-key fingerprint from the operator. If your key needs a passphrase,
   load it into your Windows OpenSSH agent before opening Imp. Use only
   trusted OpenSSH configuration on the Windows account that runs Imp.
2. If this is your first connection to the host, verify the key in a
   foreground PowerShell login:

   ```powershell
   ssh -S none -o ForkAfterAuthentication=no -o BatchMode=no -o StrictHostKeyChecking=ask -o VerifyHostKeyDNS=no -o NoHostAuthenticationForLocalhost=no -o UpdateHostKeys=no -l muduser -p 2222 -- vps.example.com
   ```

   Replace the example username, port and host with your own values; omit
   `-p 2222` if you will leave the port blank in Imp. Compare the displayed
   fingerprint with the one obtained through a separate trusted channel
   **before** accepting an unknown key. If `CheckHostIP` is enabled in your
   OpenSSH configuration, verify the host and its address in this preflight.
   If the key is unexpected or changed, stop and confirm it with the operator.
   Complete the login in PowerShell and type `exit`. Don't bypass verification.

3. With the same host, username and port (or no `-p`), test a fresh,
   noninteractive login from PowerShell:

   ```powershell
   ssh -S none -o ForkAfterAuthentication=no -o BatchMode=yes -o StrictHostKeyChecking=yes -o VerifyHostKeyDNS=no -o NoHostAuthenticationForLocalhost=no -o UpdateHostKeys=no -l muduser -p 2222 -- vps.example.com exit
   ```

   This must succeed without prompting; `exit` tests remote command access
   too. It doesn't check SSH forwarding permission, the desktop port, or
   relay health. Fix SSH access first, or use [External SSH](#external-ssh)
   if the login needs a prompt every time.

4. Select **Set up connection** or go to **Settings → Connection → Manage**.
   Choose **On another computer or server → Use my server login**. Enter
   the same **Server**, **Username**, and, if specified, **Port (optional)**.
   Select **Save**, choose **Quit Imp** in the tray, then reopen Imp.

Enter a server name, not a URL or a full SSH command. **Server** accepts an
ASCII DNS name (letters, digits and hyphens; labels must start and end with
a letter or digit, up to 63 characters per label and 253 for the name, not
counting an optional final dot), canonical dotted-decimal IPv4 (e.g.
`192.0.2.12`), or bare IPv6 (e.g. `2001:db8::12`, no brackets or `%zone`).
Noncanonical IPv4 forms such as `127.1`, `0177.0.0.1`, `0x7f000001`, or
`127.0.0.1.` are rejected. **Username** is 1–64 ASCII characters: start with
a letter, digit, or `_`; after that, `.` and `-` are also allowed. Don't
include whitespace or control characters in either field. The optional port
must be an integer from `1` to `65535` and refers to SSH, not the relay.

The hostname is an OpenSSH configuration selector, not a guarantee of the
final network address. OpenSSH honors trusted configuration such as `HostName`,
`Match` (including `Match exec`), `IdentityFile`, `ProxyCommand`, and
`ProxyJump`. Your username and explicit port override configuration; a blank
port uses OpenSSH's configured port (usually `22`). No alias entry is required.
Imp invokes OpenSSH with strict target host verification, no DNS-based
automatic trust, no localhost trust exception, and no automatic host-key
updates. Imp itself doesn't edit `known_hosts`; OpenSSH can still record a
new address for an already trusted key when `CheckHostIP` is enabled.
Proxy and jump clients can have their own trust and foreground requirements;
see [proxy ownership limits](../docs/architecture/processes/managed-runtime.md#desktop-tunnel-boundary).

### Existing SSH alias

#### Set up and verify your SSH alias

Create or reuse a host entry in `%USERPROFILE%\.ssh\config`.

```sshconfig
Host imp-vps
    HostName vps.example.com
    User muduser
    Port 2222
```

Replace the example hostname and user. Use your server's SSH port, or omit
`Port` for the default (`22`). This port is for the SSH login, not the relay or
its forward. Set `IdentityFile` if the host needs a nondefault key. Keep your
normal host-key verification and use only trusted OpenSSH configuration.

On first use, make a foreground login from PowerShell so you can handle prompts:

```powershell
ssh -S none -o BatchMode=no -o ForkAfterAuthentication=no imp-vps
```

Compare the presented fingerprint to the one you obtained through a trusted
channel **before** accepting an unknown key. If the key is unexpected or has
changed, stop and have the server operator verify it; don't bypass the warning.
Handle authentication prompts in PowerShell, not in Imp. Type `exit` once you
have verified the login.

1. Make sure the alias can log in without prompts as the Windows account that
   runs Imp. If your key has a passphrase, load it into the Windows OpenSSH
   agent before opening Imp.
2. In PowerShell, test a fresh login with that alias:

   ```powershell
   ssh -S none -o BatchMode=yes imp-vps exit
   ```

   If it fails, fix SSH access first. If your login always needs a prompt, use
   [External SSH](#external-ssh).

3. Select **On another computer or server → Use my server login** in the
   Connection dialog. In a build with server fields, select **Use an existing
   SSH alias** to enter the **SSH target**. With v0.6.0,
   enter your alias in **SSH target** directly. In
   [v0.5.4](../docs/install-windows.md#published-v054), open the manager via
   Settings and select **Managed** instead. Select **Save**, choose **Quit Imp**
   in the tray, then reopen Imp.

A saved SSH target stays in alias mode and is not parsed or migrated to
the separate fields. Legacy Managed SSH retains its existing host-key
settings; switching to separate fields is optional.

## External SSH

Use the Windows OpenSSH **Client** from PowerShell on the same Windows account
that runs Imp. Follow the [trusted-host requirements](#before-you-start), then
start the forward:

```powershell
ssh -N -T -S none -o ForkAfterAuthentication=no -o BatchMode=no -o ExitOnForwardFailure=yes -L 127.0.0.1:8789:127.0.0.1:8787 muduser@vps.example.com
```

Replace `muduser@vps.example.com` with your SSH login or an existing verified
alias. For a nondefault SSH port, add `-p 2222` before the target, using your
server's port; omit `-p` for port `22` or a port already set in the alias.

On first connection, compare the presented host-key fingerprint with one
obtained from the server operator through a separate trusted channel before
accepting it. Stop if the key is unexpected or has changed. Answer any
authentication prompts in that terminal and keep it open. Press **Ctrl+C**
there to stop your forward.

In Imp, select **Set up connection** or open **Settings → Connection →
Manage**. Choose **On another computer or server → Advanced → Use my own SSH
tunnel** and select **Save**. Use **Quit Imp** in the tray and reopen Imp to
apply the change. If you already saved External SSH, restart only if you
haven't reopened Imp since saving.

In [v0.5.4](../docs/install-windows.md#published-v054), an unchanged fresh
install already uses **External**. If switching from another mode in that
release, choose **External** in **Settings → Connection → Manage**, then
save, quit and reopen Imp.

## Tunnel ownership and ports

Managed uses the system OpenSSH client, configuration, keys, `known_hosts`, and
agent. Imp doesn't store an SSH password or private key. Managed and its
preflight use `-S none` to avoid reusing an OpenSSH master. The preflight
checks a new noninteractive login, not forwarding permission, port
availability, or relay health.

Both forms of Managed SSH supervise their own forward. If a listener on the
desktop port already answers like an Imp relay, Imp can use it without owning
it. The relay health check cannot tell which server the listener reaches,
and a successful SSH login is not proof that the desktop's state or action
connection uses that login. Check that an existing forward goes to the intended
server before use. Managed can start its own forward when the port becomes
free. Quitting Imp stops only its own SSH child, not an existing forward or
the server services. Loopback limits network exposure; it does not exclude
other local processes.

With External, Imp doesn't start, reconnect, or stop the forward.
`ExitOnForwardFailure` makes SSH exit if it can't set up the forward; it
doesn't check that the remote relay is reachable or that the game feed is live.

For both modes, the Windows desktop consumes `127.0.0.1:8789` and the forward
connects to the relay at `127.0.0.1:8787` **on the Linux server**. Keep these two
ports as shown for the released desktop and server. Port `8787` on Windows is
for the desktop's same-host node, not the SSH listener.

## Troubleshooting

- **SSH login fails:** Fix it in PowerShell first. Check the server address or
  alias, username, SSH port, key and agent. If the preflight prompts or fails,
  don't work around verification. If a host key is unexpected or changed, stop
  and confirm it with the server operator.
- **Local port occupied:** Use `Get-NetTCPConnection -LocalPort 8789 -State Listen`
  in PowerShell to identify the listener by `OwningProcess`. Stop only a forward
  you started and meant to replace. If the listener is unknown or belongs to
  another application, identify it or ask its owner to free the port; don't kill
  an unknown process or expose the listener to work around the conflict.
- **SSH connects but there's no relay:** From PowerShell, check
  `curl.exe --fail --silent --show-error http://127.0.0.1:8789/healthz` while
  Imp (Managed) or your own forward (External) is running. If it fails, check
  whether the relay responds at `http://127.0.0.1:8787/healthz` from the server
  itself. If the server check works, confirm the forward is running and ask the
  operator to check whether SSH allows forwarding to the loopback relay. If
  the server check fails, see the [operator reference](operator-reference.md)
  for relay service and log checks.
- **Connected but no fresh vitals:** The relay's `/healthz` can show whether it
  has a producer and snapshot, but neither proves live game data. Log in to your
  GMCP-enabled MUD in the intended TinyFugue world and watch for that character's
  vitals to change as you play. See [Troubleshooting](../docs/troubleshooting.md)
  if you only see **Waiting for game data…**, retained vitals, or the wrong
  character.
