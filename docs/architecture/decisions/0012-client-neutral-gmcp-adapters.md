# 0012 - MUD-specific GMCP interpretation is client-neutral

Status: accepted
Date: 2026-10-01

## Context

Imp now has more than one MUD-client integration.

TinyFugue and Mudlet differ in how they expose connection lifecycle, foreground
selection, GMCP events, and outbound command execution. Those differences are
client concerns.

MUDs also differ in how they use GMCP. A character name, vitals, targets,
combat state, room state, and other information may use different packages,
field names, value shapes, or update semantics.

Those are separate dimensions.

If each MUD-client integration contains its own interpretation of each MUD's
GMCP, Imp acquires an M-by-N integration matrix:

    client x MUD

That would duplicate normalization behavior between TinyFugue, Mudlet, and
future clients and would make fixes to one MUD's interpretation client-specific.

Imp already has a client-neutral GMCP record boundary:

    Record(at, package, payload)

The architecture should preserve that boundary and make MUD-specific
interpretation reusable above every MUD client.

## Decision

MUD-client integrations and MUD-specific GMCP adapters are separate layers.

A **client adapter** owns only client-specific concerns:

- observing connection and disconnection;
- determining which client profile/world is authoritative;
- receiving GMCP and emitting client-neutral GMCP records;
- mapping client lifecycle onto Imp's exact context tuple;
- receiving trusted action dispatches; and
- performing the final client-specific command write.

A **GMCP adapter** owns MUD-specific interpretation:

- recognizing packages and fields used by a MUD;
- interpreting identity and character state;
- interpreting vitals, targets, combat, rooms, and other supported state;
- retaining any MUD-specific normalization state required across packets; and
- projecting the result into Imp's canonical state model.

Conceptually:

    MUD
     |
     | GMCP
     v
    MUD client
     |
     | client-specific capture
     v
    client adapter
     |
     | Record(at, package, payload)
     v
    GMCP adapter
     |
     | canonical Imp state
     v
    Imp node
     |
     +--> local Imp UI
     |
     +--> remote Imp UI through supported transport

The same GMCP adapter must be usable regardless of which supported MUD client
produced the records.

For example, an AVATAR GMCP adapter must work in both of these topologies:

    AVATAR -> TinyFugue -> AVATAR GMCP adapter -> Imp node

    AVATAR -> Mudlet    -> AVATAR GMCP adapter -> Imp node

Adding another MUD client must not require another AVATAR normalization
implementation.

Adding another MUD must not require modifying every client integration.

## Shared boundary

Client adapters emit the existing client-neutral record shape:

    Record(
        at=<epoch milliseconds>,
        package=<GMCP package>,
        payload=<validated JSON value>,
    )

A GMCP adapter consumes those records and produces Imp's canonical `GameState`.

Client-specific profile names, window identifiers, world identifiers, or APIs
do not become inputs to MUD-specific normalization unless they are first
represented as explicit client-neutral connection metadata.

The downstream Imp protocol remains client-neutral and MUD-neutral.

Protocol version 2 does not change as a consequence of this decision.

## Adapter selection

Selection of a GMCP adapter is a shared concern and does not belong to Mudlet,
TinyFugue, or another individual client integration.

A future selector may use, in priority order:

1. an explicit user-configured MUD adapter;
2. known connection metadata such as server hostname and port;
3. distinctive observed GMCP packages or fields;
4. a generic GMCP adapter; or
5. explicit user selection when the evidence is ambiguous.

Automatic detection must not silently switch the interpretation of an existing
authoritative context once state has been established unless a defined
lifecycle transition permits it.

The initial implementation does not need to implement all of these detection
mechanisms. This decision defines where that behavior belongs.

## Generic GMCP

Imp may provide a generic GMCP adapter for commonly used package shapes.

Generic interpretation must be conservative. A field that merely resembles a
known package is not sufficient justification to invent unsupported semantics.

MUD-specific adapters remain available where a game's GMCP differs from the
generic mapping.

## Implementation shape

The shared adapter package is the natural home for this layer.

A likely structure is:

    integrations/common/src/imp_adapter/
      records.py
      publisher.py
      gmcp/
        base.py
        generic.py
        avatar.py
        ...

The exact module layout is an implementation detail.

GMCP adapters may be declarative where simple field mappings are sufficient,
but the interface must also permit code-backed and stateful adapters for MUDs
whose GMCP semantics require them.

## Outbound actions

GMCP interpretation does not own command transport.

Trusted action delivery remains client-specific at the final hop because
TinyFugue, Mudlet, and future clients expose different command APIs.

MUD-specific higher-level action semantics may be introduced separately in the
future, but they must not collapse the client-adapter and GMCP-adapter
boundaries defined here.

## Consequences

- Imp needs one integration per MUD client and one GMCP adapter per supported
  MUD, rather than one implementation for every client/MUD pair.
- Mudlet packaging remains generic and must not contain AVATAR-specific
  normalization.
- TinyFugue and Mudlet can share fixes and improvements to a MUD's GMCP
  interpretation.
- Future clients can gain existing MUD support by producing the shared GMCP
  record boundary.
- Future MUD support can be added without changing the Mudlet or TinyFugue
  packages.
- A GMCP inspector or adapter-generation workflow can later operate on the same
  raw record boundary without depending on the MUD client.
