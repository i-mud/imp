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
mutate or disclose credentials. The connection-oriented installer leaves a fresh
gateway disabled for SSH/install-only, or delegates verified Direct setup after
preparing its narrow Caddy edge. Ordinary upgrades preserve prior enablement
and active state. Existing credentials are never implicitly rotated.

Optional `setup|rotate --public-url wss://HOST[:PORT]/state` verifies the real
TLS edge with the new in-memory token after local readiness and before handoff.
It requires trusted TLS, a protocol-v2 state hello, HTTP/WebSocket 404s for
privileged/unknown routes, and absent or minimal public health; it refuses
redirects and ignores environment proxies. Public failure uses the existing
configuration/service rollback. Existing-credential setup remains a no-op and
explicitly reports that public verification was not performed; public rotation
requires an already-active gateway. This command does not configure the TLS edge.
During credential mutation, temporary INT/TERM/HUP handlers enter the same
digest/service rollback; signals deferred inside a verification exception are
checked before token output. Original handlers are captured before installation,
which is protected by `finally`; teardown briefly blocks these signals until all
original handlers are restored, then restores the previous signal mask.
User-systemd subprocesses run in separate sessions so repeated terminal-group
TERM/HUP cannot independently kill rollback operations. Real subprocess
regressions cover the tested setup/rotation and repeated-group-signal boundaries,
not generic crash or system-service failure atomicity.
Public verification uses the existing asyncio WebSocket client/security floor
to avoid reproduced intermittent sync-client TLS handshake stalls.
`--verify-via-loopback` (NAT only) changes just the TCP destination to fixed
`127.0.0.1:443`; URL host, TLS SNI/certificate validation and Host stay public.

Automatic Direct edges are limited to Debian/Ubuntu, direct DNS matching assigned
global IPs (or explicit `--behind-nat`: one global IPv4 A, no AAAA, operator-owned
TCP 443 forward), and no operator-owned public deployment. `imp_relay.direct_edge`
owns the dedicated root-managed `imp-caddy.service`, exact generated config and
target-user marker, pending service-state rollback, and token-free trusted HTTPS
404 preflight. Operator edits revoke ownership. Explicit SSH stops/disables the
gateway while retaining its digest and disables only the unchanged owned edge.
No-option upgrades and install-only preserve both kinds of public edge. The
desktop's four connection modes are unchanged; server SSH is shared by Managed
and External SSH.

## Source

- `deploy/install.sh` - connection selection, upgrade preservation, direct terminal
  provisioning invocation, and runtime/user-service rollback.
- `services/relay/src/imp_relay/direct_edge.py` - DNS preflight, dedicated Caddy ownership and lifecycle,
  privilege/package policy, and token-free HTTPS readiness.
- `services/relay/src/imp_relay/public_verifier.py` - bounded manual public
  verification (`imp-server public-verify`); never sends an action frame.
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

The Imp-owned schema-3 Caddy edge allowlists the original raw request target,
not a normalized path: literal `/state`, `/action`, or `/healthz`, optionally
followed by `?` and a query. The gateway independently requires the exact
query-free endpoint, so queries and encoded, dot-segment, or slash variants
cannot acquire a route through proxy normalization. This restriction does not
change SSH's full-port forwarding trust boundary.

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

Provisioning tests exercise a disposable TLS edge with normal certificate and
hostname validation, authenticated hello, HTTP/WebSocket route policy,
optional public health, credential-safe DEBUG diagnostics, and restoration of
prior configuration bytes and service state on public failure. A local runtime
smoke used the real relay/gateway with a disposable trusted TLS listener; this
does not claim acceptance of a deployed public hostname or Caddy configuration.

Windows-native live verification over a publicly trusted TLS endpoint covered
valid state delivery, live updates, a real outbound action, automatic recovery
after a gateway interruption, and switching back to managed SSH without
changing relay/feed configuration. A separate live workstation WSS probe
verified wrong-token rejection with close code `1008` and no state exposure.
