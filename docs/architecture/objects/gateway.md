# Authenticated remote gateway

## Purpose

`imp-gateway` is Imp's public-network authentication boundary for
Direct WSS desktop transport. The process itself remains loopback-only. A
separate TLS reverse proxy owns the public listener and forwards only the
gateway's desktop-facing routes.

The gateway does not replace the relay and does not make the relay public. It
authenticates a remote desktop before opening any connection to the existing
loopback relay.

The installed Linux bundle's `imp-direct-wss setup|rotate|status` command
manages only the supported target-user service and fixed configuration paths.
Setup and rotation require terminal stdin/stdout before mutation; plaintext
token output is reserved for deliberate one-time handoff. Status does not
mutate or disclose credentials. The normal installer leaves the gateway
disabled, and upgrades preserve its prior enablement, restarting it only when
it was active. The operator owns hostname, DNS, TLS, and public proxy setup.

## Source

- `services/relay/src/imp_relay/gateway.py` - listener, authentication,
  endpoint policy, and relay bridging.
- `services/relay/src/imp_relay/gateway_config.py` - validated loopback
  listener/upstream configuration and pairing-token digest input.
- `services/relay/src/imp_relay/gateway_main.py` - process entry point and
  bounded lifecycle logging.
- `services/relay/src/imp_relay/direct_wss.py` - installed-bundle setup,
  rotation, and read-only status for the target user's gateway.
- `deploy/systemd/imp-gateway.service` - VPS user-service shape.
- `apps/desktop/src-tauri/src/tunnel_config.rs` - native Direct-WSS endpoint
  and pairing-token configuration.
- `apps/desktop/src/lib/config.ts` - renderer runtime transport selection.

## Endpoints

The gateway has a deliberately smaller surface than the relay:

| Endpoint   | Role     | Behaviour                                                             |
| ---------- | -------- | --------------------------------------------------------------------- |
| `/state`   | desktop  | authenticate, then bridge the relay state stream                      |
| `/action`  | desktop  | authenticate, receive one action, then bridge one relay action result |
| `/healthz` | operator | process-only JSON health: `{"status":"ok"}`                           |

The gateway does not expose `/ingest` or `/action-consumer`. Unknown and
privileged relay routes are rejected before the relay is contacted.

A TLS reverse proxy may publish the three gateway routes above, but must never
proxy the relay listener itself.

## Authentication

Every `/state` or `/action` WebSocket begins with one gateway-owned text frame:

```json
{ "type": "auth", "token": "<pairing token>" }
```

The frame is transport authentication, not protocol-v2 traffic. It is consumed
by the gateway and never forwarded to the relay.

The token must be canonical unpadded base64url for exactly 32 random bytes,
which gives a 43-character textual form. The server stores only the SHA-256
digest of that textual token. After syntax validation, the presented token is
hashed and compared to the configured digest in constant time.

Malformed JSON, binary authentication, the wrong token, unexpected fields, or
authentication timeout closes the client without opening an upstream relay
connection.

The native desktop persists the plaintext token because the WebView must send
the authentication frame. The token is not placed in the URL, WebSocket
subprotocol, build-time `VITE_*` configuration, WebView `localStorage`, or
logs, including DEBUG frame diagnostics.

## State bridge

After successful authentication, `/state` opens exactly one upstream
`/state` connection to the relay.

The gateway forwards the relay's normal protocol-v2 stream without changing
snapshot, sequence, context, feed-status, or transient-text semantics. It
retains none of those values itself.

The authenticated client is observation-only on this socket. A further client
frame is a policy violation and closes the remote connection.

If the relay connection ends, the remote connection ends. The desktop's
existing reconnect policy owns recovery; the gateway does not synthesize or
retain state.

## Action bridge

After successful authentication, `/action` waits for one action frame before
opening the relay `/action` connection. An authenticated but idle remote client
therefore cannot reserve relay action capacity.

The gateway sends the one request upstream and returns the one relay result. It
does not retry, queue, replay, reinterpret `unknown`, or mutate the exact
TinyFugue context carried by the action.

## Network and origin boundary

Both the gateway listener and its configured relay upstream are restricted to
literal loopback addresses. The relay upstream must use `ws:` with an explicit
port and no credentials, path, query, or fragment.

Gateway-to-relay WebSocket connections explicitly disable environment proxy
selection, so a process-level proxy setting cannot redirect the trusted
loopback hop.

Browser `Origin` checking remains defense in depth. The accepted browser
origins are Imp's Vite development origin and Tauri origin; clients
without an `Origin` header are also permitted. Origin is not authentication.

TLS termination belongs to the reverse proxy. Direct desktop configuration
requires `wss:` and rejects insecure remote `ws:`.

## Failure semantics

- Authentication policy failures close with WebSocket code `1008`.
- An unavailable loopback relay closes the remote connection with `1011`.
- Gateway or reverse-proxy loss is observed by `RelayStateSource` as a normal
  socket interruption and enters bounded reconnect.
- Actions are one-shot and are never automatically retried after an ambiguous
  failure.

The gateway has no durable state and no application-level acknowledgement of
authentication.

## Change impact

Changes to gateway authentication, accepted origins, endpoint exposure,
listener binding, relay-upstream validation, or token handling are trust-boundary
changes. Read
`docs/architecture/decisions/0010-authenticated-remote-gateway.md` and
`docs/architecture/boundaries/trust-boundary.md` first.

Adding a remotely exposed relay capability must be an explicit authorization
decision. Do not proxy a new relay endpoint merely because the relay already
implements it.

Changing `/state` or `/action` also affects desktop URL derivation and the
reverse-proxy route set.

## Invariants

- The gateway binds loopback only.
- The gateway connects only to a literal-loopback relay.
- Authentication succeeds before any relay connection is opened.
- The authentication frame is never forwarded to the relay.
- `/ingest` and `/action-consumer` are never remotely exposed.
- The gateway retains no snapshot or received text.
- The gateway never retries an action.
- The health endpoint contains no operator, relay-state, or credential data.
- The pairing token never appears in a URL or diagnostic output, including DEBUG.

## Verification

Status: verified.

Deterministic gateway tests cover token validation, pre-auth relay isolation,
authentication timeout, binary/malformed/wrong-token rejection, Origin and
route rejection, loopback restrictions, minimal health output, state/text
bridging, action round trips, relay loss, and no-proxy/no-retry behavior.

Windows-native live verification over a publicly trusted TLS endpoint covered
valid state delivery, live updates, a real outbound action, automatic recovery
after a gateway interruption, and switching back to managed SSH without
changing relay/feed configuration. A separate live workstation WSS probe
verified wrong-token rejection with close code `1008` and no state exposure.
