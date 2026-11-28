# Direct WSS

Direct connects the Imp desktop to a remote TinyFugue server over public TLS,
without an SSH tunnel. Use it only where you trust the users and programs on
both computers. **Already have a WSS URL and pairing token?**
[Skip to desktop setup](#direct-desktop-configuration).

If you're setting up the server, review the route below before following the
[verified release bundle instructions](README.md#install-from-a-release-bundle).
Automatic Direct setup requires Debian or Ubuntu with systemd, `ip` and `ss`
from iproute2, Caddy available from the system apt repositories, and `sudo`
access. Let Imp install Caddy; don't preinstall it. The installer refuses
existing Caddy, nginx or Apache configuration and unowned TCP 80/443 or UDP 443
listeners. For an existing public proxy, unsupported platform, or a refused
edge, see the [operator reference](operator-reference.md#automatic-direct-public-edge)
or use [SSH](ssh.md) instead.

For either route, launch the installer as the TinyFugue user, not root, without
`--no-start`. New provisioning requires private interactive input and output;
don't record, pipe, or redirect the session. Supply an ASCII DNS hostname, not
an IP address, URL, port, wildcard, or path. Only Caddy is public; never open
the loopback relay (`8787`) or gateway (`8788`) ports to the internet.

## Publicly addressed Linux server

1. Point an A and/or AAAA record for a DNS hostname you control at the server.
   Every address returned must be public and assigned directly to the server.
2. Allow inbound TCP **80 and 443** through the host and provider firewalls so
   Caddy can obtain and renew a publicly trusted certificate.
3. From the verified, extracted bundle, run `./install.sh --connection direct --hostname imp.example.com`
   with your hostname. Alternatively, on a fresh interactive install run
   `./install.sh`, choose **Direct WSS**, and enter the hostname.

## Linux server behind residential NAT

1. Point **exactly one** A record at the router's public IPv4 address. Do not
   publish an AAAA record for this hostname or point it at a CDN or proxy.
   Carrier-grade NAT without a way to forward a public inbound port is not
   supported. If your public IP changes, keep DNS current yourself.
2. Forward **TCP 443** on the router to **TCP 443** on the Linux server and allow
   it through the host firewall. Keep the forward in place for certificate
   renewal. No inbound TCP 80, UDP 443 forward, or router hairpin/loopback support
   is needed. Imp does not configure routers, firewalls, or DNS for you.
3. From the verified, extracted bundle, run
   `./install.sh --connection direct --hostname imp.example.com --behind-nat`
   with your hostname. Alternatively, on a fresh interactive install, choose
   **Direct WSS** and enter your hostname. If the single public IPv4 isn't
   assigned to the server, the installer can offer a NAT choice. **Stop/no is
   the default**. Confirm only after setting up the forward; the prompt does
   not prove reachability. Fix DNS or interface inspection errors rather than
   assuming NAT.

## What the installer verifies

On either route, with no existing gateway credential, **Imp Direct WSS is
ready** means setup verified authentication and handed off a new token.
Directly addressed servers are checked via public DNS. On NAT, the check uses
the local Caddy listener with the public hostname and normal TLS validation.
Certificate issuance over TCP 443 shows that the CA reached Caddy, but the
loopback check can't prove desktop reachability. For NAT, pair and check from a
desktop outside the router's network.

## Direct desktop configuration

On Windows x64, install and open `Imp_<version>_x64-setup.exe` from the same
[official release](https://github.com/i-mud/imp/releases) as the server bundle.
This is an unsigned alpha installer; Windows may show a SmartScreen warning.

The pairing token is a **reusable secret**, not a one-time login code. The server
stores only a digest; the plaintext is displayed once after successful setup or
rotation and can't be retrieved from the server. Keep it private (for example,
in an approved password manager), and paste it only into Imp for the intended
server. Never put the token in a URL, shell argument, environment variable,
server file, proxy configuration, log, screenshot, or support message.

1. On the workstation, select **Set up connection** or open **Settings →
   Connection → Manage**. Choose **On another computer or server → Use
   remote Imp credentials**. In [v0.5.4](../docs/install-windows.md#published-v054),
   open the manager via Settings and select **Direct** instead.
2. Enter `wss://imp.example.com/state` (with your hostname) in **WSS state URL**.
   The URL must use `wss://`, end in `/state`, and have no user info, query, or
   fragment. Paste the 43-character token separately into **Pairing token**.
3. Select **Save**, then use **Quit Imp** in the tray and reopen Imp. Save
   applies the settings on next start; it does not test the connection or
   rotate the server credential. The native desktop stores the token, but the
   settings form does not display it.
4. With Imp capture loaded before login and GMCP working in TinyFugue, play
   in the intended world. You're done when Imp shows the right character and
   their vitals visibly update in ordinary play. **Waiting for game data** or
   old vitals don't count. See [troubleshooting](../docs/troubleshooting.md) if
   you're stuck, or the [first-use guide](../docs/first-use.md#check-that-imp-is-working)
   for more on the HUD indicators.

## Connection codes (source only)

> **Not in a published release.** v0.8.0 and earlier installers and server
> bundles neither print nor accept codes: with a release, use the manual steps
> above. With a source build of both the server and the desktop, the code is the
> preferred route and the manual URL and token entry stays as the fallback.
> Mixed pairs (release on either side) need the manual route. No acceptance of
> codes on an installed artifact, live server, NAT deployment or by an
> independent player is recorded yet.

With a code-capable server, `imp-direct-wss setup` or `rotate` run with
`--public-url` prints, after public verification and the URL lines:

```text
Imp Direct connection code (shown once; contains the pairing token; not stored):
imp1.<endpoint>.<token>
Manual fallback with the same credential (enter the URL separately):
Direct WSS pairing token (shown once; not stored):
<43-character token>
```

Setup without `--public-url` prints no code. If the verified URL can't be
represented, the server says `A connection code is unavailable for this URL;
enter the URL and token separately.` and prints only the token.

**A connection code contains the reusable pairing token.** The terminal shows
the same credential twice for compatibility with older desktops. Anyone
holding either form can observe state and request actions until rotation. Paste it
only into Imp, from a private terminal or approved password manager; never into
a URL, argument, environment variable, file, log, screenshot, chat or support
message. The server stores only the token digest and can't reprint the code.
Terminal delivery isn't atomic or recoverable: if the terminal fails or the
session is interrupted mid-handoff, treat the credential as exposed and rotate.

To pair in a source build: copy the code and paste it into the masked
**Connection code** field of **Use remote Imp credentials**. The form is
code-first unless a Direct connection is already saved. It then shows
`Imp will connect to` and the decoded `wss://…/state` endpoint. **Review** it
(stop if it isn't your server), then **Save** and quit and reopen Imp. A
rejected code gets a generic message that doesn't echo it, and nothing is
saved. **Enter URL and token manually** switches to the usual two fields and
clears the code. Only in manual entry can a blank token keep the saved credential.
Saving does not test the connection or rotate the server credential.

Format. The desktop enforces all of this on paste, with no repair or
normalisation; the server issues a code only for an endpoint that meets it:

- `imp1.` + the endpoint's unpadded base64url + `.` + the 43-character
  base64url encoding of the 32-byte token, all canonical (no padding, no extra
  characters, or nonzero unused bits). Input is ASCII and at most 512 bytes
  before trimming; only outer ASCII space, tab, CR and LF are trimmed.
- The endpoint is exactly lowercase `wss://HOST[:PORT]/state`: no user info,
  query, fragment or other path. `HOST` is a lowercase ASCII DNS name (labels
  of 1–63 of `a-z0-9-`, starting and ending alphanumeric, whose final label
  starts with a letter; a single label is allowed; at most 253 characters in
  all) or canonical dotted-decimal IPv4 (four octets 0–255, no leading zeros).
  Any label beginning `xn--` is rejected rather than interpreted as IDNA. A
  port is 1–65535, written canonically, and an explicit `:443` is kept.
- IPv6 hosts, uppercase, other schemes and anything else are not
  representable; use manual entry. The endpoint binding rules for a saved
  credential still apply.

Use manual entry for an older server or desktop, an endpoint a code can't
represent, a server run without `--public-url`, or a credential retained from
an earlier handoff. Re-running setup with a retained credential prints no token
or code, and a code can't be rebuilt from the server; rotate deliberately (see
below) to obtain a new one. Rotation invalidates every previously issued
code and token for all desktops, and a replacement code goes through the same
paste, review, save and restart. A code that failed to save can still be used
later; a lost one needs rotation.

Clearing the desktop's saved Direct credential works as for a manual token and
neither revokes a code nor stops the server. Hold the printed code only as
long as needed to pair desktops.

**Trust and NAT.** The code's endpoint comes from the URL the server verified
just before printing. On the NAT route that check reaches only the local Caddy
listener with normal TLS validation (see above), so it doesn't prove outside
reachability: pair from a desktop outside the router and confirm vitals update.
A code doesn't pin or replace TLS: Imp still verifies the endpoint's
certificate, and a code for the wrong or untrusted host stays wrong. If the
code reached you over an untrusted channel, treat the token as exposed and rotate.

## Existing Direct credentials and recovery

- Re-running the installer with a retained credential doesn't print the token,
  rotate it, enable Direct, or verify the requested hostname. Keep using the
  existing URL and token. Running `setup` again does not recover the token.
  Don't delete `gateway.env` to force a new handoff or replace the digest by hand.
- If connection fails, run `~/.local/bin/imp-direct-wss status` as the installed
  Linux user. It reports local configuration, service state and minimal health
  without revealing the token. Local health doesn't prove the public edge works.
  Check the desktop URL, DNS and TCP reachability, and fix certificate errors
  rather than bypassing validation. For an inactive or changed edge, a missing
  digest, or a failed install, use the [operator recovery procedure](operator-reference.md#existing-direct-credentials-and-recovery).
- If you lose the token or it is compromised, deliberately rotate it. After
  checking the public edge and ensuring the existing gateway is active, run
  this as the installed Linux user in a private terminal:

  ```bash
  ~/.local/bin/imp-direct-wss rotate --public-url wss://imp.example.com/state
  ```

  For the NAT configuration, append `--verify-via-loopback` to the command.
  Successful rotation replaces the credential for **every** paired desktop;
  paste the new token into each approved desktop, save and restart. Failed
  rotation can restore the prior credential. If a compromised token might
  still work, stop the gateway with `systemctl --user stop imp-gateway.service`
  and treat it as a containment issue before retrying. See the
  [operator rotation and verification details](operator-reference.md#direct-wss-gateway-advanced).

- To remove Direct credentials **from this desktop**, choose a different route
  in the Connection dialog and select **Save**, then **Remove credentials and
  save**. **Cancel** at that confirmation keeps the saved Direct configuration.
  The switch takes effect when you quit and reopen Imp. This removes the saved
  URL and token on this desktop only; it does not revoke the token or stop the
  server. Returning to Direct requires entering both fields again. If no
  authorized copy of the token remains, rotate it on the server as above.

Use the Connection dialog rather than editing native settings by hand.
The [native configuration paths](operator-reference.md#desktop-configuration-files-for-diagnostics)
are for diagnostics only.

### Changing a saved endpoint

Leave **Pairing token** blank only when keeping the saved endpoint and its
unchanged token. Paste the correct token for a different endpoint, or the
replacement after rotation even if the URL hasn't changed.

Upgrade to [v0.5.4](https://github.com/i-mud/imp/releases/tag/v0.5.4) or later.
In v0.5.3 and affected older versions, saving a different Direct URL with a
blank token can reuse the previous credential. v0.5.4 is the first published
release that rejects this:
the native check compares the validated host, effective port and full path,
accepting exact saved text and equivalent simple ASCII spellings such as
adding the default `:443`. The form offers the keep-token hint only when the
URL text matches exactly. Rejection leaves the saved settings and running
connection unchanged.
