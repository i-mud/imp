# Windows installation

TinyScry `v0.1.0` is an alpha release. The supported prebuilt desktop target is
Windows x64.

The Windows desktop application does not require Node.js, Rust, Python, WSL, or
a TinyScry source checkout. The TinyScry VPS services and TinyFugue integration
must already be deployed separately; see [`../deploy/README.md`](../deploy/README.md).

## Install TinyScry

1. Open the TinyScry GitHub release for the desired version.
2. Download the Windows x64 NSIS `.exe` installer attached to the release.
3. Run the installer.
4. Launch TinyScry.
5. Open **Settings -> Connection** and configure one of the supported
   transports below.
6. Save the connection settings and restart TinyScry.

The `v0.1.0` installer is unsigned. Windows may display a reputation or
SmartScreen warning depending on the download path and system configuration.

The running native version is shown at the bottom of TinyScry's Settings panel.

## Managed SSH

Managed SSH is the simplest mode when the VPS is already reachable through a
working OpenSSH host alias.

TinyScry uses the Windows system OpenSSH client and does not store an SSH
password or private key.

Before configuring TinyScry, verify from a terminal that the alias connects
without an interactive password or host-key prompt:

```text
ssh <alias>
```

Then:

1. Open **Settings -> Connection**.
2. Select **Managed**.
3. Enter that OpenSSH host alias.
4. Save.
5. Restart TinyScry.

TinyScry starts and supervises its own SSH local-forward child. Closing
TinyScry terminates only the SSH process it started.

## External SSH

External mode leaves SSH lifecycle entirely to the operator.

Start the forward yourself:

```text
ssh -N -L 8787:127.0.0.1:8787 <user>@<vps>
```

Then leave TinyScry in **External** connection mode. TinyScry connects to the
forwarded relay at `127.0.0.1:8787`.

## Direct WSS

Direct mode does not use SSH. It requires a TinyScry authenticated gateway
behind a publicly trusted TLS reverse proxy.

In **Settings -> Connection**:

1. Select **Direct**.
2. Enter the public `wss:` state endpoint, ending in `/state`.
3. Enter the 43-character pairing token.
4. Save.
5. Restart TinyScry.

Gateway deployment and pairing-token setup are documented in
[`../deploy/README.md`](../deploy/README.md).

## Verify the installation

After restarting TinyScry:

- Settings should show the installed TinyScry version.
- The HUD should receive live character state.
- Saved actions should be able to send an operator-approved command through the
  active connection.

If Managed SSH does not connect, first verify that `ssh <alias>` works
non-interactively outside TinyScry.

If Direct WSS does not connect, verify the public gateway URL, TLS certificate,
and pairing token against the deployment documentation.

## Release support boundary

For `v0.1.0`:

- Windows x64 is the supported prebuilt desktop target.
- The release is alpha quality.
- The installer is unsigned.
- TinyFugue is the supported MUD client integration.
- AVATAR is the currently live-verified GMCP mapping.
- Linux and macOS remain source-build targets rather than accepted release
  binaries.
- VPS deployment, TLS termination, and pairing-token provisioning remain
  operator-managed.
- Automatic application updates are not included.
