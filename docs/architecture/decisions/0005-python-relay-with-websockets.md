# 0005 - The relay is a small Python service using `websockets`

Status: accepted
Date: 2026-09-15

Historical scope: the VPS/TinyFugue topology and state-only responsibilities
below describe the bootstrap rationale. The same Python relay now implements
the Imp node beside either supported client, including the packaged desktop
sidecar, and also brokers context-bound actions and transient text. The one
runtime-dependency/no-web-framework decision remains current.

## Context

The relay is a long-lived VPS process that accepts normalized state from a
local producer and fans it out to WebSocket subscribers. It shares a host with
TinyFugue and must be cheap to install and keep running there.

## Decision

Python with `asyncio` and the `websockets` library. One runtime dependency.
No web framework, no ASGI server, no message broker.

## Rationale

The relay does three things: hold one small object, fan it out, and answer a
health probe. `websockets` covers the transport including RFC 6455 framing,
ping/pong keepalive and a documented `process_request` hook that serves the
health endpoint on the same port - so no second listener and no HTTP framework
is needed.

Python also keeps the VPS side in one language: the TinyFugue adapter is
Python, so the relay and the adapter share the canonical Python protocol codec
instead of duplicating it. Writing the relay in Rust or Go would have made the
adapter a cross-language client for no benefit at this scale.

Hand-rolling WebSocket framing on the standard library was rejected: the
handshake, masking and close semantics are exactly the kind of thing where a
subtle bug is a security bug.

## Consequences

- The VPS needs Python 3.12+ and one dependency. `uv` manages the environment
  so the same command works on Linux, macOS and Windows.
- `websockets`' `max_size` gives a byte-level frame cap, complementing the
  character-level cap the decoder applies before `JSON.parse`/`json.loads`.
- If the relay ever needs to serve many subscribers or richer HTTP, that is a
  new decision; the current shape is sized for one operator.
