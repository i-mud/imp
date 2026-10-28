# Server deployment

Installs Imp's relay and TinyFugue live feed as `systemd --user`
services on a VPS or home Linux host so the pipeline survives a reboot and an
administrator's SSH session ending without root-owned Imp services. Connection selection chooses
SSH, automatic Direct WSS, or install-only; the authenticated gateway remains
disabled on a fresh installation unless Direct is deliberately selected.

The relay, feed-facing endpoints, and gateway all remain loopback-only. SSH
forwards the **entire relay TCP port** to the workstation; it does not filter
relay routes or add application authentication. Direct WSS instead uses a
separate TLS reverse proxy in front of the authenticated gateway; the reverse
proxy never forwards the relay itself.

Everything below runs **on the Imp server** unless labelled otherwise.

Loopback prevents remote network access but is not same-user isolation. Any
local OS user or process in the VPS network namespace can reach the relay, and
Imp provides no per-user endpoint authentication. Deploy only on a
single-user server or where every host-local user/process is mutually trusted;
untrusted multi-user hosts are unsupported.

## Install from a release bundle

The normal server installation does **not** require an Imp source checkout,
Node.js, npm, Rust, `uv`, or any host Python on the VPS.

Binary compatibility and installer support are separate policies.

The frozen server binary requires:

- Linux x86_64 (little-endian ELF64) with glibc 2.36 or newer, the standard
  /lib64/ld-linux-x86-64.so.2 loader, `libz.so.1`, and the system CA store;
  musl and other loaders are unsupported. The release build enforces this with
  an ABI gate over every bundled ELF (loader, needed libraries, and required
  glibc/libstdc++/CXXABI/GCC symbol versions);
- the self-contained runtime inside the bundle (the published v0.4.0 release
  and installer need no host Python installation to install, run, or recover).
  A retained pre-frozen virtual-environment release kept as a rollback target
  may still need its original interpreter/runtime to execute that old release
  until migration succeeds and no legacy rollback is needed. The bundle neither
  installs nor manages that interpreter; migration of a real historical host
  remains live acceptance evidence.

The normal automatic installer is supported on Debian 12+ or Ubuntu 24.04+ with
`systemd --user`, `apt`, and `iproute2`. Automatic Direct installs Caddy from the
system apt repositories; do not preinstall Caddy for this path. It is not
supported on arbitrary Linux distributions. The installer adds no OS version
check; this is a support statement, not a runtime gate.

TinyFugue is a separate operator prerequisite: Imp ships only the client
adapter. TinyFugue must already be installed with the GMCP capabilities
described below.

Download both server assets from the matching Imp GitHub release:

```text
imp-server-<version>-linux-x86_64.tar.gz
imp-server-<version>-linux-x86_64.tar.gz.sha256
```

Verify the downloaded archive **before executing anything from it**:

```bash
sha256sum -c imp-server-<version>-linux-x86_64.tar.gz.sha256
```

Then extract it:

```bash
tar -xzf imp-server-<version>-linux-x86_64.tar.gz
cd imp-server-<version>
```

For a normal TinyFugue installation with the documented personal startup file,
run:

```bash
./install.sh
```

On a genuinely fresh interactive installation this asks:

```text
How will the Imp desktop connect?

1. SSH
2. Direct WSS
3. Install only / configure later
```

Direct selection also asks for a public hostname if one was not supplied.
An explicit `--connection` skips the choice prompt. Fresh non-interactive
installs must specify `--connection ssh|direct|none`; the installer never
guesses. A reinstall/upgrade without this option never prompts and preserves
the existing connection state. Existing Imp runtime/configuration paths,
credentials, helper links, and user-service state all count as prior
installation evidence; one missing file does not make an install fresh.

### TinyFugue startup file

The installer uses an existing `~/.tfrc` by default. When startup inspection is
safe, Imp backs it up, then prepends this line before all original commands:

```text
/load ~/.config/imp/capture.tf
```

The original bytes remain after the new line; ownership and permissions are
preserved. Normal installation rollback restores the original content. Duplicate
detection covers one level of literal absolute or `~/` `/load` paths. An existing
exact hook is retained without rewriting; its ordering is not established.

Recognized relative, deeper, or computed includes and `/eval` or `/require`
commands make inspection uncertain. BOM-prefixed files and legacy plain-world
lists are also not rewritten. In these cases the installer leaves the selected
file unchanged, reports no insertion, and requires manual early-loading setup.
Arbitrary startup macros cannot be fully inspected; this is not a TinyFugue
configuration parser.
Only column-zero `;` and `#` are treated as comments, matching TinyFugue's
startup reader. Indented comment markers are not valid comments; they leave the
file unchanged and require manual review, including in legacy world lists.

If `~/.tfrc` is absent and both stdin and stdout are interactive, the installer
asks for an explicit path to an existing startup file. It never creates one or
guesses a custom file. TinyFugue can fall back to working-directory startup files,
so creating `~/.tfrc` could change which configuration it loads. Without an
interactive terminal, select the existing file explicitly:

```bash
./install.sh --tf-startup "/path/to/active TinyFugue/startup-file"
```

`--tf-startup` is deterministic: an invalid or nonexistent file fails early,
without falling back to another file. For `tf -f FILE`, select that actual
`FILE`, not an assumed `~/.tfrc`.

Always confirm that your actual TinyFugue invocation loads the selected file
before any world connects or logs in. Installer success does not verify runtime
invocation or ordering of an existing hook. For manual setup, copy the supplied
hook to `~/.config/imp/capture.tf` and put the load line above first in a
macro-format startup file, ahead of connection/login commands. For a legacy
plain-world list, place the hook after all world definitions but before any
connection/login command, or convert those definitions to `/addworld` first.
Do not blindly prepend a command: it ends TF's legacy loader mode.

The installer:

- verifies the bundle's internal checksums;
- installs the self-contained PyInstaller runtime `<bundle>/bin/imp-server`
  (with its `<bundle>/bin/imp-server-runtime/` directory and hardlinked role
  aliases such as `<bundle>/bin/imp-relay`) with no Python virtual environment, wheel installation, or
  network package resolution;
- installs releases under
  `~/.local/share/imp/releases/<version>/`;
- treats an installed version as immutable: reinstalling the exact same bundle
  reuses it, while a different bundle claiming the same version is rejected;
- atomically selects the installed runtime through
  `~/.local/share/imp/current`;
- installs the fixed `~/.local/bin/imp-action-consumer`,
  `~/.local/bin/imp-feed-watch`, and `~/.local/bin/imp-direct-wss` links to
  `current/bin/<name>`, migrating legitimate legacy helper links;
- installs `~/.config/imp/capture.tf`;
- installs the relay, feed, and optional gateway user units;
- enables and starts `imp-relay.service` and `imp-feed.service`;
- preserves and rolls back the previous installation if activation fails; and
- preserves existing gateway credentials and enabled/active state on ordinary
  reinstall/upgrade, restarting an already-active gateway on the selected runtime.
  Explicit connection changes follow the rules below.

Units run `%h/.local/share/imp/current/bin/imp-server ROLE ...` directly (no
shell or `PATH`). Upgrading from a legacy virtual-environment release leaves
that old release directory in place; a failed activation restores the prior
units, links, and `current` selection, so rollback returns to the old release
with its historical runtime prerequisites.

For any already-running TinyFugue, run `/load ~/.config/imp/capture.tf` once
after installing this update, before testing live restart recovery. Installing
the hook file does not reload TF.
The updated hook owns one reload-safe `imp-feed-watch` quote job, using the
stable CLI link above (packaged role `imp-server feed-watch`); it checks the
private readiness marker once per second with bounded reads and exits when
TF closes its stdout pipe.

After feed restart, readiness is not authority: the live TF hook reasserts its
actual foreground context or null selection, never blindly trusting a saved
selection. Matching normalized checkpoint values return as stale until fresh
authoritative GMCP arrives. No MUD reconnect is required while matching cached
identity survives. This cannot recover identity that was never captured because
the hook loaded too late. The checkpoint is ephemeral: reboot or checkpoint loss
still requires fresh identity-bearing login GMCP under the documented TinyFugue
prerequisites, with the hook loaded before login.

Configuration and state directories are kept private to the user.

### Connection selection

```bash
./install.sh --connection ssh
./install.sh --connection direct --hostname imp.example.com
./install.sh --connection direct --hostname imp.example.com --behind-nat
./install.sh --connection none
```

- **ssh** installs the loopback relay/feed configuration shared by desktop
  **Managed SSH** and **External SSH**. It creates no public edge. When services
  are being started, explicit SSH stops/disables the gateway but retains
  `gateway.env`; it disables only an unchanged Imp-owned Caddy listener.
  Operator-owned web configuration is never disabled or modified.
- **direct** requires live services, a public DNS hostname, and interactive
  stdin/stdout for a new one-time token handoff. `--hostname` and `--behind-nat`
  are valid only with Direct; `--connection direct --no-start` is rejected.
- **none** installs normal components without configuring fresh Direct access.
  On an existing installation it preserves credentials, gateway state, and
  public-edge identity; it does not mean delete or disable Direct.
- **no option on an existing install** preserves connection state and
  operator-owned public edges. No implicit credential rotation occurs.
  Explicit Direct upgrades may migrate exact owned legacy edge policy even when
  inactive; no-option and `none` only attempt migration if the incoming gateway
  was enabled or active. `--no-start` uses that original snapshot while preserving
  existing connection services rather than applying an SSH transition.

Managed/External SSH and Direct/Local remain desktop choices, not four server
installation modes.

### Automatic Direct public edge

The automatic path is intentionally limited to Debian/Ubuntu with systemd,
the system apt repositories providing a Caddy package,
and `ip`/`ss` from iproute2. It does not add DNS-provider integrations, package
repositories, or a second TLS implementation. Other environments and existing
operator public edges require [manual provisioning](#direct-wss-gateway-advanced).

Supply an ASCII DNS domain name, not an IP address, URL, port, wildcard, or path;
hostname case is normalized. DNS preflight occurs before runtime mutation or
credential generation. Two normal Direct deployment shapes are supported:

| Topology                 | DNS and reachability                                                                                                | Installer option |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------- | ---------------- |
| VPS / directly addressed | Every A/AAAA answer is global and assigned directly to this server. A subset of its public addresses is sufficient. | No topology flag |
| Home / NAT               | Exactly one global IPv4 A points to the router; router TCP 443 forwards to this server TCP 443. No AAAA records.    | `--behind-nat`   |

The default directly-addressed policy is unchanged. A fresh interactive Direct
install offers NAT confirmation only after successfully inspecting the server's
interfaces and finding that its single global IPv4 A is not assigned among the
public addresses, including when no public interface address exists. This can
indicate NAT or another externally routed topology; it does not prove that a
router or working port forward exists. **Stop/no is the default**. Confirm that
TCP 443 is forwarded here before accepting; the installer revalidates NAT DNS
after acceptance. It asks nothing extra when directly-addressed DNS passes.
Missing or failing `ip`, malformed interface data, and DNS failures stop with
their actual diagnostics, not a NAT suggestion. Fix the reported inspection or
DNS problem before retrying.
Non-interactive mismatches require explicit `--behind-nat`; NAT is never inferred.
Explicit `--behind-nat` keeps its existing DNS validation and does not require a
public address on the server. NAT rejects multiple A answers and any AAAA,
including a locally valid IPv6 address: remove AAAA or use the directly-addressed
path. Private, CGNAT, documentation, reserved, multicast, and other non-global
DNS addresses are rejected.
DNS must point directly to the router, not a CDN/reverse proxy.

Configure the router's TCP 443 forward yourself before installation. Imp does
not manage routers, UPnP/NAT-PMP, firewalls, or DNS providers. CGNAT without an
inbound public port forward is unsupported. A dynamic public IPv4 is supported
when DNS is current; manage DDNS externally. NAT hairpin/loopback, inbound TCP 80,
and UDP 443 forwarding are not required for NAT Direct.

Before any public mutation, the helper refuses existing Caddy/nginx/Apache
configuration and any unowned TCP 80/443 or UDP 443 listener. It does not adopt
an existing Caddy installation. For a new edge, it masks stock Caddy listeners
before apt installation so package startup cannot publish the default site.
The package may remain installed and masked after a failed setup or later SSH
transition; only the dedicated Imp listener is enabled.

Interactive public changes deliberately invoke `sudo`. Non-interactive changes
use non-prompting privilege and fail when it is unavailable. No credentials
enter the public-edge helper. Under `sudo` the bundled edge executes as the
authorized host administrator; this is not a limited-sudoers security boundary
and Imp makes no such claim.

Ownership consists of root-owned, non-symlink, exact generated files:

```text
/etc/imp/Caddyfile
/etc/imp/direct-edge.json
/etc/systemd/system/imp-caddy.service
```

The marker records the target user, hostname, topology, and a pending transaction's
previous enabled/active state. Schema 3 records the current raw-target policy for
both topologies. Only exact version-1 directly-addressed or version-2 NAT state
is eligible for migration; those legacy templates remain for ownership and recovery.
Exact config/unit bytes, safe ownership/modes, and loaded-unit checks distinguish
generated state from operator edits. Pending transactions, wrong UID, config/unit
drift, and hostname/topology changes are refused rather than adopted.
Caddy's unused admin listener is disabled.

The Caddyfile's CEL allowlist examines the full original request target,
`{http.request.orig_uri}`, not `.path` or a normalized path matcher. Only literal
`/state`, `/action`, and `/healthz`, optionally followed by a literal `?` and any
query (including empty), reach `127.0.0.1:8788`; everything else returns Caddy
`404`. The gateway independently rejects noncanonical targets and queries under
its existing policy. The observed normalized-edge defect did not bypass authentication.
The relay's `8787` port is never proxied. Caddy owns normal publicly trusted
certificate acquisition and renewal. Directly-addressed deployments retain normal
Caddy ACME behavior; permit inbound TCP 80/443 and account for host/provider firewalls yourself.
NAT config uses this supported Caddy ACME issuer policy:

```text
tls {
    issuer acme {
        disable_http_challenge
    }
}
```

Only HTTP-01 is disabled; TLS-ALPN-01 remains enabled, with no DNS challenge or
provider credentials. Successful public-CA issuance through TLS-ALPN-01 is
external evidence that public TCP 443 reaches this Caddy instance. Keep that
port forward available for renewal. This syntax is supported by Caddy 2.6.2
and later; see [Caddy TLS issuer configuration](https://caddyserver.com/docs/caddyfile/directives/tls)
and [TLS-ALPN reachability requirements](https://caddyserver.com/docs/automatic-https#tls-alpn-challenge).

After activation, trusted HTTPS GETs to `/unknown`, `/ingest`, and
`/action-consumer` must each return `404`. NAT readiness dials `127.0.0.1:443`
while keeping the public hostname for TLS SNI, certificate hostname validation,
and HTTP Host; the locally presented certificate must validate against normal
public trust. It does not require the server to reach its own router public IP.
Directly-addressed verification continues dialing normal public DNS.
Certificate errors, redirects, proxy errors, and other HTTP statuses are not
success; environment proxies are ignored and readiness is bounded.
Only then does the installer invoke `~/.local/bin/imp-direct-wss setup
--public-url wss://HOST/state` directly with inherited terminal stdin/stdout;
NAT adds `--verify-via-loopback`. It never pipes, captures, parses, or persists
the provisioning output. The provisioning command owns token generation,
digest persistence, local/public authenticated verification, rollback, and
one-time token display.

Failure does not print Direct-ready instructions. The helper restores the
previous owned listener state, leaving a newly created listener stopped and
disabled; package installation is not transactionally undone. Existing runtime,
TinyFugue, and user-service rollback remains in place before credential creation.
Once Direct provisioning starts, a newly present `gateway.env` is conservatively
treated as a possible token handoff even if a parent signal arrives before the
child's success is recorded. The local runtime/credential is retained for
recovery, while the public edge is restored/disabled and no Direct success is
claimed. Real INT/TERM/HUP regressions cover the tested mutation phases. A
closed-controlling-PTY regression covers an SSH transition with dead terminal
output: cleanup disables shell errexit, treats diagnostics as best-effort, and
continues independent restoration attempts while preserving HUP exit status.
Independent service restoration continues even if a filesystem copy fails; its
rollback backup is retained rather than deleted. Restoration is still best-effort
if filesystem or system-service operations fail. SIGKILL, power loss, and
root/operator edits can require manual recovery.

An explicit SSH transition commits the gateway shutdown with the durable edge
commit. If the commit child fails, credential-free helper inspection must prove
the same target UID/hostname, exact owned configuration/unit, no pending journal,
and disabled/inactive dedicated service with no running PID. This proof retains
the completed SSH connection state even if the child was interrupted after
publication; the installer still exits failure. Without proof, ordinary
connection restoration applies. Ordinary runtime rollback remains in place.
After an ambiguous SSH commit failure, the installer parent temporarily ignores
terminal INT/TERM/HUP while performing the bounded, read-only durable proof.
Ignored dispositions are inherited from process creation by the reconciliation
helper and its service-query child, including the fork/startup window.
The original recorded signal/failure remains the eventual exit reason; normal
parent traps are restored after recording the proof result. The query retains
its 120-second subprocess timeout. Genuine proof or timeout failure still fails
closed; it is not an assumed commit. These guarantees do not cover arbitrary
crashes or power loss.

Provisioner rollback's user-systemd subprocesses run in separate sessions.
Real process-group TERM followed by another TERM and HUP during a blocked
rollback subprocess restores the previous credential and active/enabled state
in the regression harness. Signal-guard entry and teardown restoration are
also tested. These checks do not establish recovery from SIGKILL, power loss,
or unavailable systemd/filesystem operations.

Before any generated edge file is published, the installer helper durably writes
the pending preparation journal identifying stock masks absent at entry.
TERM immediately after the journal, Caddyfile, or unit-file publication is covered
by cleanup/retry regressions. Failed/partial masking removes only the attempted
masks and Imp-generated files, returning to an absent, directly retryable baseline.
Pre-existing null-device masks remain untouched. Once package work may have
begun, protective masks and the disabled owned edge remain available for retry;
package installation is not transactionally reversed.

### Existing Direct credentials and recovery

If `gateway.env` already exists, explicit Direct installation preserves it and
the public-edge identity while migrating eligible owned legacy policy, does not
call no-op setup as verification, and prints no Direct-ready or new-token claim.
Keep using the existing URL/token. For a new
verified handoff, deliberately configure/enable the public edge, inspect
`imp-direct-wss status`, ensure the gateway is active, then use
`imp-direct-wss rotate --public-url wss://HOST/state` interactively.
On a NAT host add `--verify-via-loopback` to that deliberate rotation command.
Explicit Direct upgrades migrate eligible exact owned version-1 direct/version-2
NAT policy to schema 3 even when the incoming gateway is disabled and inactive.
No-option upgrades and `none` preserve connection selection and only attempt
implicit migration when the original gateway was enabled or active, including
under `--no-start`. With both flags false, a formerly Direct, now SSH installation
does not query or change the retained legacy edge and needs no root/sudo for it.
`none` does not persist a transport transition or disable Direct: Direct-to-`none`
is preservation, with the same enabled-or-active predicate, not a disabling transition.
Existing credentials do not implicitly provision, rotate, or enable Direct.
Hostname, NAT choice, UID, gateway credentials, certificate data, unit, stock
masks, and enabled/active state are preserved; operator edges are untouched.
Migration uses the existing pending journal and commit/rollback transaction:
snapshots restore the exact old Caddyfile and marker plus prior service state on
handled failure before edge commit. An incomplete restoration retains the journal for retry.
This does not renew public reachability evidence or request a new token, and
does not promise arbitrary crash/power-loss atomicity.

For an unchanged current-policy Imp-owned edge disabled by an SSH transition, an operator can
reactivate `imp-caddy.service` with `sudo systemctl enable --now imp-caddy.service`.
Enable and start the existing gateway with `systemctl --user enable --now imp-gateway.service`
before deliberate rotation. For operator-owned public edges, use that edge's
own lifecycle. Do not assume the hostname requested during an existing-credential
install was publicly authenticated.

If `gateway.env` is already missing, explicit Direct installation can reuse an
exact-owned schema-1 Direct or schema-2 NAT edge only with the original UID,
hostname and topology, no pending transaction, intact stock masks, and an exact
loaded public-edge unit that is disabled, inactive and has PID 0. It validates
the current policy before activation and keeps one enable journal through the
existing setup/token handoff and commit. Each new Direct installer attempt
assigns that journal a fresh transaction ID; its automatic commit and cleanup
rollback require that same ID. Refusing an older or replaced journal cannot
authorize cleanup to change it. Setup generates a new token; it does
not recover the lost token or digest. Previously paired desktops need the new
token. The user gateway must be inactive for setup; its active-gateway refusal
is unchanged. Do not delete a retained credential to force this flow.

Before handoff, rollback of an owned recovery transaction restores the exact
legacy files and inactive edge. After possible handoff, the installer retains
the new credential/runtime and closes/restores only its own public-edge
transaction. A pre-existing or replaced journal is left untouched and reported
for manual recovery. Rollback proves the public listener stopped before
restoring legacy policy. Unrelated operator state and changed identity/topology
are never adopted. No-option and `none` installs do not implicitly create a
replacement identity.
An interrupted enable may carry a transaction ID or temporarily pair a legacy
marker with exact current policy; use the updated bundle for rollback/commit.
Older helpers do not recognize an identified journal: they change nothing but
may report success without completing recovery. Manual operator commit/rollback
without an ID remains available in the updated helper after deliberately
inspecting the owned state; the ID is not a pairing credential.

The root-owned marker retains pending state if edge restoration is incomplete.
Manual recovery commands must run from the **extracted bundle directory**.
After inspecting the unchanged owned files and service, obtain sudo credentials
first; the helper's privileged rollback/commit uses non-interactive `sudo -n`:

```bash
sudo -v
./bin/imp-server direct-edge rollback "$(id -u)"
```

Rollback resolves failed preparation, retries prior listener-state restoration,
or restores a pending migration's old configuration/marker snapshots without
credential access. Use `./bin/imp-server direct-edge commit "$(id -u)"`
only to deliberately accept a completed, verified pending change; unfinished
pre-package preparation cannot be committed. If operator edits revoked
ownership, resolve that configuration manually; the installer does not overwrite
it to force recovery. Normal non-interactive installer paths never prompt for
sudo credentials.

### User lingering

For the user services to survive logout and start without an interactive SSH
session, systemd user lingering must be enabled:

```bash
loginctl show-user "$USER" -p Linger
```

The expected result is:

```text
Linger=yes
```

If it is disabled, enable it:

```bash
loginctl enable-linger "$USER"
```

If policy prevents the user from doing that, an administrator can run:

```bash
sudo loginctl enable-linger "$USER"
```

The installer reports a warning when lingering is not enabled; it does not
silently perform privileged system configuration.

## Direct WSS gateway (advanced)

These lower-level commands support recovery, deliberate rotation, existing
deployments, and operator-owned/manual TLS edges. For normal supported installs,
use [connection selection](#connection-selection) instead.

Direct WSS uses one 256-bit pairing token. The gateway stores only its
SHA-256 digest. The plaintext token is shown once by the bundle's provisioning
command for deliberate terminal handoff to the desktop. On the VPS, never put
the plaintext token in arguments, environment variables, files, URLs, service
units, reverse-proxy configuration, or logs.

The bundled `imp-direct-wss` command is installed at the stable user link
`~/.local/bin/imp-direct-wss`. Invoke that link from the installed Linux
target user with a working user systemd manager; it operates only on that
user's installed bundle and fixed HOME paths. From a source checkout, invoke
`uv run --directory services/relay imp-direct-wss OPERATION`, replacing
`OPERATION` with exactly one of `setup`, `rotate`, or `status` (for example,
`uv run --directory services/relay imp-direct-wss setup`). Both forms retain
the installed-target-user safety boundary; this is not a general systemd manager.

Run setup from an interactive terminal:

```bash
~/.local/bin/imp-direct-wss setup
```

Setup requires stdin and stdout terminals before mutation. With no existing
configuration it securely generates a token, writes only its digest to
`~/.config/imp/gateway.env` (private `0700` config directory and `0600` file),
enables/starts the supported gateway user service, checks exact minimal HTTP
health `{"status":"ok"}`, and authenticates `/state` requiring the first
protocol-v2 `hello`. The token is printed once to the terminal only after
success; do not capture or redirect command output. If a secure, syntactically
valid single-digest configuration already exists, setup is idempotent: it
generates no token, makes no service/network calls, and changes nothing.
If configuration is missing while the gateway is already active, setup refuses
before mutation; use `rotate` only when rotation is deliberate. Unsafe existing
files or service arrangements are rejected without modification. Paths must not
be symlinks; unsupported custom gateway unit files, systemd drop-ins, linked or
masked units, and manager state requiring daemon reload are rejected rather
than adopted. The supported unit must match the installed shipped service;
systemd's resolved fragment, executable, environment-file, and restart policy
must agree with that unit.

Rotate a compromised or otherwise replaced token using:

```bash
~/.local/bin/imp-direct-wss rotate
```

Rotate has the same terminal requirement and handoff discipline. If the
gateway was active, it is restarted and its exact minimal HTTP health plus
authenticated `/state` protocol-v2 hello are verified. If it was inactive, it
remains inactive (and retains its previous enabled/disabled state); no
network preflight or activation occurs.

To require public-edge verification **before** the one-time token handoff:

```bash
~/.local/bin/imp-direct-wss setup --public-url wss://imp.example.com/state
~/.local/bin/imp-direct-wss rotate --public-url wss://imp.example.com/state
```

For an explicitly authorized NAT/port-forwarded deployment without hairpin,
append `--verify-via-loopback` after the `--public-url URL` argument. The public
URL must use the default secure port (no explicit port) or explicit `443`.
Loopback verification dials only `127.0.0.1:443` and retains the public URL's
hostname for TLS and Host. It accepts no arbitrary verification destination and
never bypasses certificate verification. External diagnostics should omit this
flag.

`--public-url URL` follows `setup` or `rotate`; `status` takes no options.
Without loopback verification, other valid public ports remain supported.
The URL must use `wss`, contain a hostname, and use exactly `/state` (no prefix,
trailing slash, userinfo, query, or fragment).
Invalid URL diagnostics never echo the supplied value.

Configure DNS and the TLS edge before invoking this option. After the usual
relay, local gateway, and service-state checks, provisioning authenticates the
public `/state` with the newly generated in-memory token and requires a decoded
protocol-v2 hello. It requires both HTTPS GET and WebSocket handshake HTTP
`404` responses for `/ingest`, `/action-consumer`, and `/unknown`. Public
`/healthz` must return either `404` or `200` with exactly `{"status":"ok"}`.
Normal trust-store TLS validation is mandatory; redirects are refused and
environment proxies are ignored. HTTP probes use a five-second elapsed deadline
for headers/body as well as socket timeouts, with the same Unix/main-thread,
unarmed `ITIMER_REAL` constraint as the standalone verifier below.
No action is sent and no general endpoint scan is performed.

Every requested check must pass before the token is printed. Failure uses the
same configuration/service rollback as local readiness failure, with generic
credential-safe network diagnostics. The token is never passed to a subprocess,
exported, persisted, or embedded in a URL or proxy configuration.

Already-configured `setup --public-url ...` is still a non-destructive no-op:
it reports that public verification was **not performed**, without requesting
the existing plaintext token. Use deliberate rotation to verify with a new
token. `rotate --public-url ...` requires an already-active gateway and refuses
before credential generation/replacement when it is stopped; plain `rotate`
continues to support stopped gateways without starting them.

Handled failures attempt to restore the previous configuration bytes and
enabled/active service state. Rollback is best effort: if recovery is incomplete,
the command reports recovery-required and attempts to stop the gateway, rather
than claiming success. These protections cover handled operation failures, not
power loss or cross-filesystem/systemd atomicity.

If the prior digest is restored after a failed rotation, the old token
authenticates again. Treat that as immediate containment work: stop the gateway
and address the failure before retrying rotation. Do not assume failed rotation
invalidated a compromised credential.

Check without changing files, locks, credentials, or service state:

```bash
~/.local/bin/imp-direct-wss status
```

Status separately reports configuration path safety, syntax validity, file and
directory modes, gateway unit policy, systemd enabled/active state, and minimal
health without disclosing credential material. An unsafe path names the
offending directory and reason. The fixed paths are `~/.config/imp/gateway.env`,
`~/.config/systemd/user/imp-gateway.service`, and
`~/.local/share/imp/current/bin/imp-server` (invoked as `gateway`). The config's only accepted
content is exactly one `IMP_GATEWAY_TOKEN_SHA256=<64 hex characters>`
assignment, with full-line comments and surrounding whitespace allowed; NUL
bytes are rejected anywhere, including comments. It is not a general environment
file. The config directory and file must be owned by the target user with modes
exactly `0700` and `0600`, respectively.

The ancestors `~/.config`, `~/.config/systemd`, and `~/.config/systemd/user`
must be owned by the target user and never world-writable. They may be
group-writable only when their group is the target user's primary group and the
system account database enumeration (`getent passwd`/`getent group`) lists the
target user, no other account with that primary group, and no other
supplementary member in any group record with that GID. A group-writable
ancestor that also carries an extended access ACL is rejected. That covers the
ordinary Ubuntu local user-private-group layout (`0775` with umask `002`), so
those directories need no permission change. A group-writable ancestor whose
group is shared, or cannot be resolved or enumerated, is rejected.

This check sees only enumerable accounts and groups. On hosts whose account
sources do not fully enumerate users and groups, such as some SSSD or LDAP
configurations, it is not proof that no other principal shares the group. If any
other account can write such an ancestor through directory-service membership
or another mechanism, remove group write from that ancestor.

Explicit fresh Direct installation delegates pairing to this command after
preparing the supported public edge. Ordinary upgrades preserve the digest and
gateway state; explicit Direct with an existing credential reports preservation,
not successful authenticated public verification.

The gateway listens only on `127.0.0.1:8788` and connects only to the relay at
`ws://127.0.0.1:8787`. Never proxy port 8787 or the relay's `/ingest` or
`/action-consumer` endpoints to the Internet.

### Public TLS/WSS edge for Direct mode

For manual public edges, the operator owns hostname, DNS, publicly trusted TLS
certificate acquisition/renewal, and reverse-proxy lifecycle. Imp's Python
gateway does not terminate TLS. The reverse proxy must forward only literal
`/state`, `/action`, and optionally `/healthz`, with an optional literal `?`
query suffix, to `127.0.0.1:8788`; reject every other original request target.
The gateway still rejects queries and noncanonical targets independently.

A minimal route shape supported by Caddy 2.6.2 uses the full original URI
(not `.path` or a normalizing path matcher):

```text
https://imp.example {
    @gateway expression `
        {http.request.orig_uri} == "/state" || {http.request.orig_uri}.startsWith("/state?") ||
        {http.request.orig_uri} == "/action" || {http.request.orig_uri}.startsWith("/action?") ||
        {http.request.orig_uri} == "/healthz" || {http.request.orig_uri}.startsWith("/healthz?")
    `

    handle @gateway {
        reverse_proxy 127.0.0.1:8788
    }

    handle {
        respond 404
    }
}
```

If certificates are provisioned outside the reverse proxy, renewal must also
reload the proxy after replacing its readable certificate/key copies.

The older manual Slice 11 live acceptance used Caddy and a publicly trusted
certificate and verified the public `/healthz`, `/state`, and `/action` path while
`/ingest` and `/action-consumer` remained unreachable. This is historical
advanced/manual-edge evidence, not the default automatic installation workflow
or acceptance of current provisioning. See [implementation status](../docs/status.md)
for the recorded NAT, published-artifact, and clean directly-addressed acceptance.

### Bounded public verification

Run this from a machine with network reachability to the public hostname. On
the Imp server, or any machine with an extracted/installed server bundle, the
frozen runtime needs no host Python installation. It probes authenticated
state, auth rejection, and HTTP plus WebSocket 404s for privileged/unknown
routes without an `Origin` header, and optionally minimal health. It sends no
action frame. The token is read from /dev/tty using `getpass`, never argv or
environment. Both secret prompts fail closed if terminal echo control is
unavailable, before reading fallback input or starting network activity. Errors
are intentionally generic so exception text cannot expose credentials or
response payloads.

```bash
~/.local/share/imp/current/bin/imp-server public-verify 'wss://imp.example/state'
~/.local/share/imp/current/bin/imp-server public-verify 'wss://imp.example/state' --verify-via-loopback
```

From an extracted bundle that is not installed, use
`./bin/imp-server public-verify ...`. The `<bundle>/bin/imp-public-verify` alias
dispatches to the same role. Source checkouts run the same module,
`imp_relay.public_verifier`, with the developer toolchain (Python 3.12 and
`uv`), for example `uv run --project services/relay python -m
imp_relay.public_verifier URL`.

The first argument is the approved WSS state endpoint (optionally followed by
`--verify-via-loopback`). It rejects credentials, query/fragment, non-WSS URLs,
and endpoints not ending in `/state`; derives sibling routes while retaining
any path prefix. The optional previous-token prompt proves revocation after
rotation; its value is read only from /dev/tty and never appears in output. TLS
verification stays enabled, redirects are refused, proxies are bypassed, and no
insecure TLS options are used; WebSocket and HTTPS probes use the same shared
public connection policy as `imp-direct-wss --public-url`
(`imp_relay.direct_wss`). WebSocket probes run with `asyncio.run`, bounded by
their open, receive and close timeouts. Each HTTPS probe has a five-second
monotonic elapsed deadline covering headers and body, in addition to the socket
timeout. It uses `SIGALRM` to interrupt even a drip-fed response, so it
requires Unix, the main thread, and an unarmed `ITIMER_REAL`; it refuses to
probe if a real-time timer is already armed.

For a NAT host that cannot reach its own public address, append
`--verify-via-loopback` after the URL. Every probe then dials only
`127.0.0.1:443` locally; the public URL's authority (so TLS server name and
certificate validation) and `Host` header are unchanged, and the URL must use
the default port. This proves local edge TLS/routing only, not external
reachability or router port forwarding. It is not available for arbitrary
targets.

Do not enable websocket protocol/frame logging. The Imp `websockets` logger's
INFO security floor, applied by the shared public connection policy, is part of
the credential confidentiality boundary.

### Separate live acceptance authorization

This is an operator-run acceptance plan only; no live change is authorized by
this document, and none is being performed now. Before any mutation, obtain
explicit approval naming the existing VPS, desktop, public hostname,
maintenance window, and one harmless MUD action. Approval must specifically
cover securely parking the exact existing `gateway.env` bytes in a mode-`0600`
backup containing only the digest assignment plus permitted comments/whitespace,
stopping/disabling the gateway to establish the clean initial state,
setup/rotation/reinstall and desktop changes, and restoring the prior approved
configuration and service state. Any old plaintext token
may be held only in the operator's approved secure terminal/native credential
store, never in a VPS file or log. If approval or the existing Caddy
environment is absent, stop before mutation. Reuse the existing service and
port `8788`; do not provision infrastructure or change Caddy, DNS, or TLS.

1. Establish and record the installed-bundle baseline with `~/.local/bin/imp-direct-wss status`: no gateway digest, service disabled/inactive, and Direct WSS not enabled; preserve the prior digest-only configuration and enabled/active state as authorized.
2. Run the supported setup command `~/.local/bin/imp-direct-wss setup` interactively.
3. Receive the newly generated token once through the intentional terminal handoff; never redirect, capture, or log output.
4. Confirm `~/.config/imp/gateway.env` contains only the SHA-256 digest assignment, with the supported private ownership/modes and no plaintext token.
5. Confirm `imp-gateway.service` is enabled and active on the existing user manager.
6. Confirm setup completed its local minimal-health check and authenticated `/state` protocol-v2 hello; record `~/.local/bin/imp-direct-wss status`.
7. Run the bounded verifier against the existing public `wss:` `/state` URL and confirm trusted TLS plus authenticated state access.
8. Confirm the verifier receives 404 for privileged/unknown HTTP and WebSocket routes without an `Origin` header.
9. Configure the existing desktop's Direct pairing through Settings with the public `/state` URL and the new token; save and restart.
10. Confirm live MUD state arrives over Direct WSS.
11. Send only the separately approved action through the desktop UI and independently observe its execution; do not synthesize an action in a probe.
12. While the gateway is active, run `~/.local/bin/imp-direct-wss rotate`; receive the replacement token only by terminal handoff and confirm the service remains active.
13. Rerun the bounded verifier, entering the replacement token and old token at its secure prompts; confirm the old credential and missing/wrong credentials close with `1008`, while the replacement succeeds.
14. Save the replacement token in the existing desktop Direct settings, restart, and confirm Direct state succeeds.
15. Stop and restart the gateway once; confirm the desktop's existing reconnect behavior recovers state without action replay.
16. Reinstall/upgrade from the already-approved extracted bundle on the same VPS by running `./install.sh`; confirm the digest, enabled state, and active state are preserved and the active gateway is restarted as expected.
17. Confirm `ss -ltnp` shows relay `8787` and gateway `8788` listening only on loopback; restore the parked digest-only configuration, prior service enablement/active state, and approved desktop credential, then record only observed results and restore outcome.

### Direct desktop configuration

On the workstation, open Imp and use **Settings -> Connection -> Direct**.

Enter the public `wss:` state endpoint (for example `wss://imp.example/state`)
and the 43-character pairing token displayed by `imp-direct-wss setup` or
`rotate`. Save and restart Imp. The URL must use `wss:`, contain no
credentials, query, or fragment, and end in `/state`.

The pairing token is persisted by the native application. Imp's settings-read
path does not return the stored plaintext token merely to populate the form;
an existing Direct token can therefore remain unchanged when editing other
Direct settings.

Do not put the token in a URL, reverse-proxy configuration, `VITE_*`
environment value, or WebView `localStorage`.

For troubleshooting only, the native configuration is persisted at:

- Linux: `~/.config/dev.imud.imp/tunnel.json`
- Windows: `%APPDATA%\dev.imud.imp\tunnel.json`
- macOS: `~/Library/Application Support/dev.imud.imp/tunnel.json`

Normal setup should use the Connection UI rather than editing this file by
hand.

## TinyFugue integration details

The installer copies the shipped hook to:

```text
~/.config/imp/capture.tf
```

See [TinyFugue startup file](#tinyfugue-startup-file) for existing-file selection,
safe automatic insertion, and cases requiring manual early-loading setup.

Load the hook before anything in that startup path can connect or log in to
the MUD. AVATAR sends the full identity-bearing `Char.Status` only once, during
initial character login, and provides no supported way to request another full
snapshot; later `Char.Status` messages are deltas that may omit
`character_name`. Loading is additive: Imp does not own or replace the
operator's GMCP negotiation or connection macros. Repeated loads replace the
named Imp definitions rather than duplicating them.
The Imp `GMCP`, `CONNECT`, and `WORLD` hooks are defined at priority 2
with fall-through (`-Fp2`) so they observe without consuming operator events.
`GMCP_LOGIN` remains an operator login-script prerequisite but is not a
Imp capture hook.
Imp runs ahead of default priority-1 handlers such as `received-gmcp`; the
`-F` flag lets those handlers run afterward. Two same-priority
non-fall-through GMCP hooks previously lost whole events intermittently. Do
not drop `-F` and do not change the operator's own hooks. A higher-priority
non-fall-through hook can still prevent Imp from running, so re-verify
coexistence when operator priorities differ.

Confirm the GMCP definitions inside TinyFugue: the operator's handler should
remain at its existing priority and `imp_capture_gmcp` must appear as
`-Fp2`.

The hook writes to the fixed path `~/.local/state/imp/spool`.
`imp-feed` owns that path as a symlink into its private runtime
directory and replaces it on every (re)start; nothing about the hook file
changes when the feed restarts. See
[`integrations/tinyfugue/README.md`](../integrations/tinyfugue/README.md) for
why this is a plain drained file rather than a FIFO.

## TinyFugue GMCP login prerequisite

Because that identity message is sent once, capture depends on the operator's
TinyFugue performing GMCP login sequencing at the right negotiation point. The
invariant is a **build whose GMCP support includes the `GMCP_LOGIN` hook**,
driving operator login scripts that use it to run their GMCP capability
negotiation and send `Char.Login`. Without `GMCP_LOGIN` the operator login path
cannot be relied on to sequence this correctly, and Imp then keeps
consuming `Char.Vitals` and `Char.Status` deltas without ever observing
`character_name`, so the HUD stays down.

This is stated as a capability, not a version. The build verified live was
TinyFugue `5.2.2-3-g4f0ff34`; an upstream or distribution version number does
not by itself prove `GMCP_LOGIN` is compiled into the binary in use, so confirm
the capability. The conclusive signal is on the first login after installing
the hook: login produces a full `Char.Status` carrying `character_name`, and
Imp obtains a snapshot and writes its checkpoint - visible as
`has_snapshot: true` with a non-null `seq` on `/healthz`. In the verified run,
`Char.StatusVars` was observed immediately followed by that full identity-bearing
`Char.Status`; AVATAR does not document that ordering as a guarantee, so treat
it as an observation rather than a requirement. When the signal is missing,
[bounded diagnostic capture](#diagnostic-raw-capture-opt-in-vps) shows which
packages did arrive.

The build, the upgrade and the login scripts are operator-owned; Imp
never modifies them and sends no GMCP itself.

## Verifying the deployment

For the normal Managed/External SSH installation:

```bash
# Required services are active.
systemctl --user is-active \
  imp-relay.service \
  imp-feed.service

# Required services are enabled for future user-systemd starts.
systemctl --user is-enabled \
  imp-relay.service \
  imp-feed.service

# The unauthenticated relay remains loopback-only.
ss -ltnp | grep -E ':8787[[:space:]]'
# expect 127.0.0.1:8787, never 0.0.0.0:8787

# The selected runtime is the versioned release installation.
readlink ~/.local/share/imp/current

# TinyFugue actions use the stable helper path.
readlink ~/.local/bin/imp-action-consumer

# Relay process/state health.
curl -s http://127.0.0.1:8787/healthz

# The feed created the runtime spool and private context marker.
ls -l ~/.local/state/imp/spool
stat -c '%a %n' ~/.local/state/imp/context  # 600 after a world selection

# Recent lifecycle logs; raw GMCP and action text are not logged.
journalctl --user \
  -u imp-relay.service \
  -u imp-feed.service \
  -n 50
```

When Direct WSS is deliberately configured, additionally verify:

```bash
systemctl --user is-active imp-gateway.service

ss -ltnp | grep -E ':8788[[:space:]]'
# expect 127.0.0.1:8788, never a public bind

curl -s http://127.0.0.1:8788/healthz
# expect: {"status":"ok"}

# Verify the public TLS edge independently:
# curl -s https://<public-host>/healthz
# curl -s -o /dev/null -w '%{http_code}\n' https://<public-host>/ingest
# the second command must report 404
```

## Common failure diagnostics

| Symptom                                                        | Check                                                                                                                                                                                                                                                                   |
| -------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `imp-feed` exits immediately with "another Imp feed holds ..." | A duplicate instance is running - manual invocation while the service is active, or a second service instance. `systemctl --user status imp-feed.service`, then stop the extra process.                                                                                 |
| Relay reachable but no HUD data                                | In SSH mode, check `curl http://127.0.0.1:8787/healthz` through the forward. In Direct mode, check both local gateway health and the public TLS `/healthz`; then confirm a relay producer is attached and inspect `imp-feed.service`.                                   |
| Direct WSS repeatedly reconnects                               | Confirm the public certificate is trusted/current, the reverse proxy forwards `/state` and `/action` to `127.0.0.1:8788`, and the desktop pairing token matches the digest in `gateway.env`. Never move the token into the URL while debugging.                         |
| Direct WSS path is unsafe or unit policy unsupported           | `imp-direct-wss status` names the directory and reason. For a shared group-writable or world-writable ancestor, remove that write access or give the directory the user's private group; do not loosen `~/.config/imp` or `gateway.env`.                                |
| TinyFugue shows an `fwrite` error line                         | The feed is down or the hook symlink target directory is missing. TinyFugue is not blocked by this - it is the intended fail-open behaviour - but no HUD update reaches the relay until the feed is running again.                                                      |
| Services do not survive a reboot                               | Confirm `loginctl show-user "$USER" -p Linger` reports `Linger=yes`; without it, user units never start without an interactive login.                                                                                                                                   |
| Relay bound to more than loopback                              | The current relay refuses every non-loopback host and has no override. Restore the shipped unit and executable if this occurs.                                                                                                                                          |
| Feed consuming GMCP but relay reports `has_snapshot: false`    | No `Char.Status.character_name` has been observed since the feed started. Confirm the hook was loaded by the active startup file before login, and that the TinyFugue build provides `GMCP_LOGIN` (the TinyFugue integration section); then log the character in again. |

## Diagnostic raw capture (opt-in, VPS)

Normal operation keeps raw GMCP only in the bounded private runtime spool
under `$XDG_RUNTIME_DIR`; it retains no raw diagnostic history. To capture a
bounded, private diagnostic trail temporarily:

```bash
systemctl --user edit imp-feed.service
```

Add an override:

```ini
[Service]
ExecStart=
ExecStart=%h/.local/share/imp/current/bin/imp-server feed --diagnostic-capture
```

Then `systemctl --user daemon-reload && systemctl --user restart
imp-feed.service`. Files land privately under
`~/.local/state/imp/diagnostics/`, rotate at 1 MiB, and keep at most 5
files. Remove the override (`systemctl --user revert imp-feed.service`)
when done; this is a debugging aid, not a default.

## Development checkout deployment

The source-tree VPS workflow remains available for contributors and local
development, but it is not the release installation path.

From the canonical development checkout:

```bash
npm run vps:sync
```

By default this synchronizes the repository to the configured development VPS
checkout. Developers may then use the project-local `uv` environments for
iteration and testing.

Released installations should use the versioned server archive and
`install.sh` instead. Normal Imp users do not need a VPS Git checkout, Node.js,
npm, Rust, `uv`, or Python. Source and build workflows need `uv` with the exact
interpreter pinned in `services/relay/.python-version`.
