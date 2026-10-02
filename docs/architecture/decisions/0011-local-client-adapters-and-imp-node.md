# 0011 - MUD-client adapters attach locally to an Imp node

Status: accepted
Date: 2026-09-30

## Context

Imp's first production topology placed TinyFugue, the feed, relay, and action
consumer on a VPS. The desktop therefore reached the relay through either an
SSH local forward or the authenticated WSS gateway.

That deployment history must not become an architectural requirement.

TinyFugue can also run on the same workstation as the Imp desktop. Mudlet is
normally a desktop application and therefore naturally runs beside a local Imp
installation. At the same time, an operator may want the MUD client on one
machine and the Imp UI on another.

Future Imp clients, including mobile clients, may also need to communicate with
an Imp instance on another machine.

MUD-client choice and network topology are therefore independent dimensions.

## Decision

A MUD-client adapter always attaches to an **Imp node on the same host**.

The Imp node owns the client-neutral runtime semantics already represented by
the relay:

- selected normalized state;
- feed freshness;
- transient received text;
- exact-current context;
- state subscribers;
- trusted action requests;
- one eligible action consumer;
- one action in flight; and
- no action queue, retry, fan-out, or replay.

Adapters use only the node's host-local producer and action-consumer
boundaries. They do not own remote transport.

Remote access occurs only on the consumer side of the node:

- SSH may make a remote node's loopback state/action endpoints appear locally;
- the authenticated gateway may expose only state/action capabilities through
  WSS; and
- privileged producer and action-consumer endpoints remain host-local.

Conceptually:

    MUD <-> client <-> local adapter <-> local Imp node
                                         |
                                         +-> local Imp UI
                                         |
                                         +-> SSH / authenticated WSS
                                                   |
                                                   v
                                              remote Imp UI

The same rule applies to every supported MUD client.

For TinyFugue, the existing feed and action helper remain local to the relay
beside TinyFugue whether that host is a VPS or the operator's workstation.

For Mudlet, the Mudlet integration communicates only with the Imp node on the
Mudlet machine. It does not implement SSH, TLS, pairing-token authentication,
or public listening.

## Desktop node ownership

A normal desktop installation must be able to provide a local Imp node for
local MUD-client adapters without requiring an Imp source checkout.

How that runtime is packaged is an implementation decision for Slice 16. The
architecture does not require the node to be implemented inside the Tauri
process.

The existing relay semantics should be reused rather than independently
reimplemented inside each MUD-client adapter or UI. A second implementation of
the node state/action rules requires explicit justification because duplicated
relay semantics would create two authorities for context, freshness, and
no-replay behavior.

The desktop UI remains a consumer of an Imp node. It must not learn how Mudlet
or TinyFugue produces state.

## Context

Protocol-v2 context remains the exact-current fence used for state and actions:

    (session, foreground generation, connection generation)

The tuple remains opaque downstream and exact equality remains its only
protocol-level interpretation.

The existing TinyFugue adapter keeps its current generation behavior.

A second adapter must map its own lifecycle onto the same three properties:

- one adapter/runtime session identity;
- a generation that changes when the adapter changes which game connection is
  authoritative for the node; and
- a generation that changes when that authoritative game connection is
  replaced or reconnected.

Adapter-specific profile, world, window, or connection identifiers do not
become downstream protocol fields.

This generalizes which adapter may produce the tuple; it does not change the
wire shape, validation, equality rule, or TinyFugue interpretation. Protocol
version 2 therefore remains the wire version.

## Local trust boundary

The Imp node remains loopback-only and unauthenticated.

Loopback is a network boundary, not same-user authentication. A workstation
running a local node has the same existing requirement as the VPS deployment:
host-local users and processes must be mutually trusted.

A local Mudlet or TinyFugue adapter gains no authority beyond what the existing
host-local producer/action-consumer endpoints already grant.

Remote clients never receive access to those privileged endpoints.

## Consequences

- TinyFugue is no longer architecturally associated with "remote" operation.
  Local TinyFugue and VPS TinyFugue use the same adapter-to-node boundary.
- Mudlet is no longer architecturally associated with "local-only" operation.
  Its adapter is local to its node, while an Imp UI may consume that node from
  another machine.
- The desktop `StateSource` and `ActionSink` boundaries remain valid. They
  consume a node and do not gain Mudlet-specific behavior.
- SSH and authenticated WSS remain node-to-UI transport choices, not MUD-client
  integration choices.
- The authenticated gateway continues to expose only state, action, and
  optional health capabilities. It never exposes producer or action-consumer
  routes.
- Initial Mudlet support may retain AVATAR-specific normalization. Supporting a
  second MUD client does not imply universal MUD normalization.
- A future Imp instance may consume one node and publish or proxy that state to
  another Imp peer, including a mobile client. That Imp-to-Imp chaining is not
  implemented by this decision or by the initial Slice 16 scope.
