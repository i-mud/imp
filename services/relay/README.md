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

## Installed gateway provisioning

The bundled Linux target-user CLI `imp-direct-wss setup|rotate|status` manages
only the supported user-systemd gateway installation and fixed user paths.
Invoke the stable installed link `~/.local/bin/imp-direct-wss`; from a source
checkout invoke `uv run --directory services/relay imp-direct-wss OPERATION`,
replacing `OPERATION` with one of `setup`, `rotate`, or `status` (for example,
`uv run --directory services/relay imp-direct-wss setup`). Setup and rotation
require interactive stdin/stdout before mutation and show a newly issued token
only for intentional terminal handoff. Status does not write or reveal
credentials.

The normal installer leaves the gateway disabled; upgrades preserve
enablement and restart it only if already active. The operator remains
responsible for public hostname, DNS, TLS, and the reverse-proxy route allowlist.
See the [deployment guide](../../deploy/README.md#direct-wss-gateway-advanced).
