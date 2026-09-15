# TinyScry relay

The relay accepts producer publishes on `ws://127.0.0.1:8787/ingest` and serves retained state to subscribers on `ws://127.0.0.1:8787/state`. It binds only to loopback by default; use an SSH tunnel for remote HUD access.

`GET /healthz` returns the relay's current feed status.
