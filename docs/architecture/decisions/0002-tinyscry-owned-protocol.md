# 0002 - TinyScry owns its protocol; GMCP never reaches the HUD

Status: accepted
Date: 2026-09-15

## Context

The upstream data source is GMCP, whose package names, key spellings and value
types vary per MUD and are only partly documented. The HUD needs a stable shape
to render.

## Decision

`packages/protocol` defines a versioned, TinyScry-owned wire format. Nothing
downstream of normalization ever sees a GMCP package name, a GMCP key, or a
GMCP value type. MUD-specific mapping lives only in
`integrations/tinyfugue/src/tinyscry_tf/normalize.py`.

## Rationale

If the HUD read GMCP directly, every MUD quirk would become a UI change, and
switching or upgrading MUDs would ripple through the whole application. Owning
the protocol confines that churn to one file.

It also makes the untrusted-input boundary explicit and testable: the protocol
layer is where bounds and character rules are enforced, which is only possible
because the shape is ours.

## Consequences

- Adding a new vital is: extend `GameState`, extend both decoders, add a
  fixture, extend the normalizer, extend the HUD. `docs/architecture/objects/game-state.md`
  records that chain.
- Unknown keys are ignored rather than rejected, so compatible additions do
  not require a version bump. Removing, retyping, or changing the meaning of a
  field does; version 2 therefore has no version-1 compatibility shim.
- The normalizer carries the cost of MUD uncertainty, and it is the only place
  allowed to contain `UNVERIFIED:` markers.
