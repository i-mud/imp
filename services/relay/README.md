# Imp relay and remote gateway

The relay accepts producer publishes on `ws://127.0.0.1:8787/ingest` and serves
retained state to subscribers on `ws://127.0.0.1:8787/state`.

The relay is permanently loopback-only and implements no per-user
authentication.

Remote desktop access has two transport shapes:

- SSH forwards the entire relay TCP port to the local workstation, including
  producer and action-consumer routes; it adds no application authentication.
- Direct WSS uses the separate `imp-gateway`, which listens only on
  `127.0.0.1:8788`, authenticates the desktop before contacting the relay, and
  exposes only state/action and optionally health routes behind a TLS reverse
  proxy.

The gateway never exposes relay `/ingest` or `/action-consumer`.

Authentication is the first WebSocket text frame and is consumed by the
gateway before it opens the loopback relay connection. The gateway stores no
snapshot/text state and performs no action retry or replay.

`GET /healthz` on the relay returns feed/state health.
`GET /healthz` on the gateway returns only `{"status":"ok"}`.
