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

The helper currently verifies only lifecycle/RPC semantics. Relay publishing and
trusted outbound actions are added in subsequent Slice 16 checkpoints.
