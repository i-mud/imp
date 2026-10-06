# 0010 - Public remote access uses an authenticated loopback gateway

Status: accepted
Date: 2026-09-27

Topology note: the TinyFugue/VPS diagram below records the original deployment.
The gateway now serves an Imp node beside either supported MUD client, including
a desktop-owned node; authentication and route restrictions are unchanged.
Privileged routes are unreachable through this gateway, not through an
authorized SSH forward of the full relay port. Such a forward retains the
relay's mutually trusted host-local process boundary.

## Context

ADR 0001 deliberately keeps the Imp relay on loopback and uses SSH for
remote access. That remains a strong transport for an operator who already has
SSH access, but requiring an SSH forward is a significant installation and
usability barrier for distributing Imp to other TinyFugue users.

Making the existing relay public would collapse an intentional trust boundary.
The relay accepts local producer and TinyFugue helper connections as well as
desktop state and action connections. Its lack of per-user authentication is
safe only because its listener is restricted to loopback.

Imp therefore needs a second remote transport without changing the relay
into an Internet-facing service.

## Decision

The existing relay remains permanently loopback-only, unauthenticated, and
unchanged in role. It continues to own all protocol-v2 state, context,
freshness, text-event, and action semantics.

A separate `imp-gateway` process provides the remote boundary.

The intended topology is:

    TinyFugue
        |
        v
    feed / action consumer
        |
        v
    127.0.0.1:8787
    Imp relay
        |
        v
    127.0.0.1:8788
    Imp authenticated gateway
        |
        v
    TLS reverse proxy :443
        |
        v
    wss://public-host/...
        |
        v
    Imp desktop

The gateway itself also binds loopback only. TLS termination and the public TCP
listener belong to an ordinary reverse proxy such as Caddy or nginx. Direct
Imp desktop connections require `wss:`; insecure remote `ws:` is not a
supported transport.

The gateway exposes only the desktop-facing state and action capabilities. It
must never provide a route to the relay's `/ingest` or `/action-consumer`
endpoints.

SSH remains a supported alternative transport. External/manual and managed SSH
modes continue to connect to the loopback relay exactly as they do today.

## Authentication

Authentication is a gateway transport prelude, not an Imp protocol-v2
message and does not require a protocol version change.

Immediately after the WebSocket opens, the direct client sends one text frame
with this shape:

    {"type":"auth","token":"<pairing token>"}

The gateway authenticates that frame before opening any connection to the
loopback relay. The authentication frame is consumed by the gateway and is
never forwarded upstream.

After authentication:

- a state connection receives the normal protocol-v2 relay stream;
- an action connection sends the normal protocol-v2 `action` frame and receives
  the normal `action-result`;
- the gateway adds no application-level acknowledgement, retention, queue,
  retry, replay, or context semantics.

A pairing token is 32 cryptographically random bytes encoded as unpadded
base64url. The textual form is exactly 43 characters.

The gateway is configured with the SHA-256 digest of that textual token, not
the plaintext token. It validates the presented token's syntax, hashes it, and
uses a constant-time digest comparison.

There is one active pairing token per Imp gateway installation in this
slice. User accounts, roles, multiple independent credentials, refresh tokens,
and delegated access are out of scope.

Authentication failure, a malformed authentication frame, a binary first
frame, or failure to authenticate within a short bounded timeout closes the
connection without contacting the relay.

Authentication failures must never log the supplied token or raw
attacker-controlled frame.

## Desktop credential boundary

The direct-transport pairing token is persistent local application
configuration and must not be:

- embedded in a build-time `VITE_*` value;
- stored in WebView `localStorage`;
- placed in a URL, path, query string, or fragment;
- sent as a WebSocket subprotocol value; or
- written to diagnostics or ordinary logs.

The native application configuration owns the persisted token. The renderer
may receive it at runtime only as needed to authenticate its WebSocket
connection.

This does not make renderer memory a secret enclave; it prevents unnecessary
persistent and handshake-level exposure of the credential.

## Origin and endpoint policy

The gateway applies browser Origin checks as defense in depth in addition to
token authentication. No Origin and the Imp development/Tauri origins may
be accepted; unrelated browser origins are rejected.

Only the remote state and action routes are WebSocket-upgrade targets. Unknown
routes and privileged relay route names are rejected locally by the gateway
without contacting the relay.

A local health endpoint may exist for process supervision, but it must expose no
relay snapshot, character, target, received text, action, pairing-token, or
other operator data.

## Relay connection behavior

The gateway connects only to a loopback Imp relay.

It opens that upstream connection only after successful client authentication.
This prevents an unauthenticated remote connection from causing retained relay
state to be read or buffered.

The gateway is a transport bridge, not another state owner:

- it retains no snapshot;
- it retains no received-text event;
- it performs no action retry;
- it performs no reconnect resend;
- it does not alter context tuples;
- it does not convert `unknown` action results into retries; and
- it does not weaken the relay's one-consumer/one-in-flight action rules.

If the local relay is unavailable, the remote state connection fails or closes
and the existing desktop reconnect policy may try a new connection later. An
action whose outcome cannot be established remains `unknown`; the gateway
never retries it.

## Rationale

Separating the public boundary from the relay preserves the strongest property
of the existing architecture: producer and action-consumer endpoints are not
reachable through the public WSS gateway.

A first WebSocket frame is used instead of a bearer token in the URL because
URLs are routinely copied, retained, and logged. The browser/WebView WebSocket
API also does not provide a portable way to attach an arbitrary
`Authorization` header.

Storing only a digest on the server limits the value of copied gateway
configuration. Because the token has 256 bits of random entropy, offline
guessing of the digest is not practical.

TLS is delegated to a mature reverse proxy rather than reimplemented inside
Imp. The gateway still owns application authentication and authorization;
TLS alone is not authentication to Imp.

## Consequences

- ADR 0001 remains authoritative for the relay itself: the relay still rejects
  non-loopback binding and gains no authentication.
- This ADR adds a second supported remote transport rather than replacing SSH.
- Possession of the pairing token grants the remote desktop capabilities of one
  Imp installation: state observation and context-bound action requests.
- Compromise of that token therefore requires rotation.
- The reverse proxy never needs the Imp pairing token; it forwards the
  WebSocket traffic without interpreting Imp authentication.
- Protocol-v2 remains unchanged.
- The installed Linux bundle provides target-user gateway setup, rotation, and
  status commands; hostname, DNS, public TLS, and reverse-proxy provisioning
  remain operator-owned.
- Multi-user hosting and mutually untrusted local users remain outside the
  current supported trust boundary.
