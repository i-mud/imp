# Mudlet integration

Mudlet is a local MUD-client adapter. It does not own remote transport.

```text
MUD <-> Mudlet <-> Lua adapter <-> local helper <-> local Imp node
```

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

Trusted outbound actions are added in a subsequent Slice 16 checkpoint.
