# Server deployment

Install Imp's relay and TinyFugue feed on the Linux host where TinyFugue runs.
Imp runs as your normal user under `systemd --user`. The relay and optional
Direct WSS gateway listen only on loopback. Loopback is not same-user
isolation: other local users and processes can reach the unauthenticated relay.
Use a single-user host or one where all host-local users and processes are
mutually trusted. Untrusted multi-user hosts are unsupported.

## Install from a release bundle

Run these steps **on the Linux host**, as the user who runs TinyFugue. Do not
run `install.sh` as root.

- **Supported installer:** Debian 12+ or Ubuntu 24.04+ with `systemd --user`,
  `apt`, and `iproute2`.
- **Server binary:** Linux x86_64 with glibc 2.36+; musl is unsupported.
  For nonstandard hosts, check the [loader and runtime requirements](operator-reference.md#release-runtime-and-user-services).
- **TinyFugue:** Use a GMCP-capable build of TinyFugue 5.2.4 or later.
  A GMCP-capable build from the upstream
  [5.2.4 release](https://github.com/ingwarsw/tinyfugue/releases/tag/5.2.4)
  is the tested baseline and includes the quote-readiness fix. Imp ships the
  adapter, not TinyFugue, and does not negotiate GMCP for you.

No source checkout, Node.js, npm, Rust, `uv`, or host Python is needed for a
normal release installation.

Download both assets from the same [official Imp release](https://github.com/i-mud/imp/releases/latest):

- `imp-server-<version>-linux-x86_64.tar.gz`
- `imp-server-<version>-linux-x86_64.tar.gz.sha256`

In the download directory, replace `<version>` with the actual release version.
Verify the archive **before extracting or executing anything from it**:

```bash
sha256sum -c imp-server-<version>-linux-x86_64.tar.gz.sha256
```

Proceed only if the archive reports `OK`:

```bash
tar -xzf imp-server-<version>-linux-x86_64.tar.gz
cd imp-server-<version>
```

### TinyFugue startup file

Before installing, check the startup banner in your normal TinyFugue session
for the version. If you launch `tf` from your shell, `type -a tf` shows which
executable or alias that command resolves to. Check the build's feature
information for GMCP support, or confirm that your existing GMCP handlers
receive events during normal play. For a custom build, an unclear launcher
(such as a wrapper), or scripts that depend on `GMCP_LOGIN`, use the
[detailed TinyFugue prerequisites](../integrations/tinyfugue/README.md#prerequisites).

Identify the existing startup file your launcher uses. If you pass `-fFILE`,
use that file. Without `-f`, TinyFugue looks for `.tfrc`, then `tfrc`, in your
home directory before trying those names in the working directory. The
installer assumes an existing `~/.tfrc`. If your launcher uses a different
file, add the [documented `--tf-startup` option](operator-reference.md#tinyfugue-startup-file)
to the installation command you choose below. The installer never creates or
guesses an active startup file.

When inspection is safe, the installer backs up the file and prepends
`/load ~/.config/imp/capture.tf`. If it leaves the file unchanged or finds an
existing hook, verify the hook loads in your actual TinyFugue invocation before
any connection or login. Follow the [TinyFugue integration instructions](../integrations/tinyfugue/README.md#running-normal-production-path)
for manual loading; do not blindly prepend a command to a legacy world list.
The [startup file reference](operator-reference.md#tinyfugue-startup-file)
covers inspection limits and rollback.

### Connection selection

Choose how the desktop will connect before running the installer:

- **SSH:** Choose **SSH** when prompted. Imp can manage the tunnel or use one
  you start. SSH creates no public edge. Bind the forward only to workstation
  loopback: it carries the whole relay port and adds no relay authentication.
- **Direct WSS:** Follow the [Direct WSS guide](direct-wss.md) **instead of**
  the SSH instructions below. It covers public DNS, firewalls, and what to
  select when running the installer. Don't preinstall Caddy or expose the relay.
- **Install only / configure later:** Choose this to install without setting
  up fresh remote access.

Fresh non-interactive installs must explicitly select `--connection ssh`,
`--connection direct`, or `--connection none`; the installer does not guess.
Direct needs a public hostname and, for a new pairing token, an interactive
terminal. On an existing installation, `--connection none` preserves existing
connection state; it does not disable Direct. See the
[connection state reference](operator-reference.md#connection-selection)
for explicit changes on an existing installation.

For a fresh interactive **SSH** or **Install only** setup with the default
startup file, run this once from the extracted bundle and choose your method:

```bash
./install.sh
```

### User lingering

To keep Imp running after logout and start it on reboot without an interactive
login, check that user lingering is enabled:

```bash
loginctl show-user "$USER" -p Linger
```

If it does not return `Linger=yes`, run `loginctl enable-linger "$USER"`.
If policy prevents that, see the [administrator instructions](operator-reference.md#user-lingering).
The installer warns about missing lingering; it does not enable it for you.

## Verifying the deployment

On the server, check that both units report `active` and the local relay answers:

```bash
systemctl --user is-active imp-relay.service imp-feed.service
curl --fail --silent --show-error http://127.0.0.1:8787/healthz
```

Continue with your chosen desktop route: [Managed SSH](ssh.md#managed-ssh),
[External SSH](ssh.md#external-ssh), or [Direct WSS](direct-wss.md#direct-desktop-configuration).
If you haven't installed the desktop yet, see [Install Imp on Windows](../docs/install-windows.md#install-imp).
Start TinyFugue with its verified startup file, sign in to the intended character, and play
normally. **Setup is complete when that character's vitals visibly update in
Imp.** A working tunnel, successful authentication, or retained snapshot alone
is not enough.

See [operator verification](operator-reference.md#verifying-the-deployment)
for fuller checks and [troubleshooting](../docs/troubleshooting.md) if the live
HUD does not update.

## Normal upgrade

Download, verify, and extract the new release as above, then run `./install.sh`
without `--connection`. If you used `--tf-startup`, supply the same active file
again. An ordinary upgrade does not ask for a connection choice, change or
rotate credentials, or change which connection is enabled. If activation fails,
the installer attempts to restore the previous installation. If it reports
incomplete restoration, stop and keep the rollback backups it names.
If the corrected TinyFugue executable is already running, run
`/load ~/.config/imp/capture.tf` in it once to load the new hook; copying the
hook does not reload the running process.

## Further reference

- <a name="automatic-direct-public-edge"></a>[Automatic Direct public edge](direct-wss.md): public server and residential NAT setup.
- <a name="existing-direct-credentials-and-recovery"></a>[Existing Direct credentials and recovery](direct-wss.md#existing-direct-credentials-and-recovery).
- <a name="direct-wss-gateway-advanced"></a>[Direct WSS gateway (advanced)](operator-reference.md#direct-wss-gateway-advanced).
- <a name="public-tlswss-edge-for-direct-mode"></a>[Public TLS/WSS edge for Direct mode](operator-reference.md#public-tlswss-edge-for-direct-mode).
- <a name="bounded-public-verification"></a>[Bounded public verification](operator-reference.md#bounded-public-verification).
- <a name="separate-live-acceptance-authorization"></a>[Separate live acceptance authorization](operator-reference.md#separate-live-acceptance-authorization).
- <a name="direct-desktop-configuration"></a>[Direct desktop configuration](direct-wss.md#direct-desktop-configuration).
- <a name="tinyfugue-integration-details"></a>[TinyFugue integration details](operator-reference.md#tinyfugue-integration-details).
- <a name="tinyfugue-gmcp-login-and-identity"></a>[TinyFugue GMCP login and identity](../integrations/tinyfugue/README.md#gmcp-login-and-character-identity).
- <a name="common-failure-diagnostics"></a>[Common failure diagnostics](../docs/troubleshooting.md).
- <a name="diagnostic-raw-capture-opt-in-vps"></a>[Diagnostic raw capture (opt-in, VPS)](operator-reference.md#diagnostic-raw-capture-opt-in-vps).
- <a name="development-checkout-deployment"></a>[Development checkout deployment](operator-reference.md#development-checkout-deployment).
