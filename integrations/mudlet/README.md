# Mudlet integration

Mudlet is a local MUD-client adapter. It does not own remote transport.

```text
MUD <-> Mudlet <-> Lua adapter <-> local helper <-> local Imp node
```

## Install on Windows

The Windows desktop installer carries both the shared Mudlet helper/runtime and
the small per-profile `Imp.mpackage`.

Launch Imp once after installation. The desktop provisions the package at the
stable path:

```text
%LOCALAPPDATA%\Imp\mudlet\Imp.mpackage
```

Use Mudlet's package manager to install that file once in each profile that
should publish to Imp. Imp does not modify Mudlet profiles automatically.

The package starts the desktop-provisioned helper from Imp's versioned local
runtime. The helper always talks to the same-host Imp node on
`127.0.0.1:8787`; the Mudlet package never owns SSH, TLS, pairing credentials,
or other remote transport.

When a later Imp version carries an updated `Imp.mpackage`, launching that
desktop version refreshes the stable package file above. Reinstall that file in
the relevant Mudlet profiles when the package itself changes.

The Lua side uses Mudlet's `spawn()` RPC facility to communicate with the helper
over newline-delimited JSON.

Lifecycle inputs are:

- `sysConnectionEvent` / `sysDisconnectionEvent` for connection generations;
- `sysProfileFocusChangeEvent` for foreground generations;
- `sysProtocolEnabled` / `sysProtocolDisabled` for GMCP readiness;
- `gmcp.Char.Status` and `gmcp.Char.Vitals` for the currently mapped state.

Receiving a GMCP message also establishes GMCP readiness, allowing a package
installed or reloaded after protocol negotiation to recover.

Only the foreground profile owns an Imp `/ingest` producer. Losing focus closes
that producer without sending a deselection; gaining focus selects the profile's
latest locally normalized state. This makes profile switching safe regardless
of the order in which the old and new profiles receive their focus events.

Trusted outbound actions use the same exact `(session, foreground, connection)`
context as state publication. The helper registers `/action-consumer` only for
the focused profile. A dispatch is forwarded to Lua with that exact context;
Lua independently checks its current connection and foreground generations
immediately before calling Mudlet `send()`. Only after that call returns without
a Lua error does it report `forwarded` to the node. A context mismatch reports
`rejected`; loss after dispatch remains `unknown` and is never retried.
