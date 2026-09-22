# Roadmap

Planned work only. Nothing on this page is implemented.

- What exists today, and the evidence behind it, lives in
  [`status.md`](status.md). If a claim appears in both files, `status.md` wins.
- This file tracks the next planned slice and the work queued behind it.
- Ordering after the explicitly planned next slice is provisional. Live
  evidence that exposes a more urgent defect reorders this list; a roadmap
  entry is not a commitment to build it next.

No dates. A slice is named here before it exists in code, so treat every
section below as intent, not as behavior.

## Slice 10 - Configurable notification triggers

Slug: `configurable-notification-triggers`. Planned; not started, and no branch
or implementation exists.

Today TinyScry has exactly one alert: the specialized low-HP evaluator
described in
[`architecture/objects/desktop-alerts.md`](architecture/objects/desktop-alerts.md).
Slice 10 replaces that single hard-coded rule with operator-defined
notification triggers, conceptually parallel to the configurable actions
delivered in Slice 8: the operator defines them, TinyScry persists them, and
the desktop evaluates them locally.

The current low-HP alert must become a specialization of the generalized
system rather than a second, parallel implementation left beside it.

### A. Vitals-triggered notifications

A vitals trigger watches one selected normalized vital:

- health;
- mana;
- moves.

The operator defines a threshold. When the watched vital drops below that
threshold, TinyScry produces the existing alert effects - sound, desktop
notification, or both - according to whichever trigger/settings model the
implementation settles on.

The generalized evaluator must preserve the anti-spam and transition semantics
the current low-HP evaluator already has: alerting on a downward crossing, not
on every identical state update, with re-baselining rather than synthesized
crossings when configuration, identity, or usability changes. Losing those
properties would be a regression even if every new trigger type worked.

The trigger schema is deliberately not designed here.

### B. Text-match-triggered notifications

A text trigger matches a pattern against text received from the MUD. On a
match, TinyScry produces sound, a desktop notification, or both.

This is the architecturally significant half of the slice. TinyScry currently
consumes normalized GMCP-derived state and nothing else, so Slice 10 must first
establish a safe, bounded input path for received MUD text before any matching
can be designed.

Deliberately undecided, and to be settled during implementation:

- the pattern syntax - literal, wildcard, regex, or something narrower;
- where matching finally occurs;
- the exact wire representation of a text event;
- the buffering strategy.

Required constraints, which are not open questions:

- matching is against received MUD text, never against `GameState`;
- text-trigger handling must never turn arbitrary MUD text into an executable
  command: no shell path, no `eval` path, no command synthesis;
- a notification never dispatches an action - alerts stay outbound-silent, as
  invariant 8 in
  [`architecture/CONTEXT.md`](architecture/CONTEXT.md) already requires;
- text events are bounded and transient; Slice 10 does not create a retained
  game-text history;
- the `StateSource` / `ActionSink` separation is not weakened, and state
  observation does not become bidirectional;
- the existing trust-boundary assumptions in
  [`architecture/boundaries/trust-boundary.md`](architecture/boundaries/trust-boundary.md)
  hold unchanged: MUD text is untrusted input.

The implementation must decide where the boundary sits across
TinyFugue text capture -> TinyScry transport -> desktop trigger evaluation,
without contaminating the normalized `GameState` model with arbitrary terminal
text unless there is a strong architectural reason to do so.

### Expected outcome

At a high level, Slice 10 is done when:

- the low-HP alert is one case of reusable notification-trigger definitions;
- vitals triggers cover health, mana, and moves thresholds;
- text-match triggers fire on incoming MUD text;
- trigger definitions are persisted operator configuration;
- sound and desktop-notification effects remain independently configurable;
- duplicate and transition behavior is bounded rather than continuous;
- a desktop management surface exists, analogous in spirit to Manage Actions;
- deterministic tests cover trigger evaluation and persistence;
- native acceptance covers notification, sound, and UI behavior;
- live acceptance exercises the text path before any claim that text-trigger
  delivery works end to end.

Detailed UI, protocol, schema, and pattern semantics are not decided yet and
must not be inferred from this list.

### What Slice 10 is not

- not automation: it never sends commands to the MUD;
- not a scripting engine;
- not a replacement for TinyFugue's own triggers and macros, which remain
  operator-owned;
- not a retained game-text log;
- not a general regex engine, unless implementation explicitly chooses one;
- not a weakening of the outbound-action trust boundary - the exact-context,
  no-queue/no-retry/no-replay action contract is untouched.

## After Slice 10

Unordered, and deliberately without slice numbers.

### MUD-agnostic normalization

Still important, no longer next. The objective is to keep TinyScry's own
normalized state while reducing AVATAR-specific assumptions in the integration
and normalization layer, so that GMCP- and MUD-specific concepts stay upstream
of the protocol, relay, and HUD boundary - ADR 0002 already requires that
direction.

This is incremental. No single slice promises universal MUD compatibility.

### Character identity reacquisition

Rapid AVATAR login and world transitions can produce later GMCP without another
authoritative `Char.Status.character_name`, leaving TinyScry without character
identity. Tracked as a current deferred defect in [`status.md`](status.md).

Constraints for any fix: identity is never inferred from ambiguous group or
player data, and TinyScry never invents an identity it was not told.

### Raw/ANSI GMCP robustness

Captured GMCP material containing raw ANSI or control bytes has produced
`invalid_raw_json` rejections. The work is to find the correct
capture/parsing/normalization boundary for that material. Sanitization
semantics are deliberately unchosen: fail-closed rejection is the current
behavior, and replacing it requires evidence about where the bytes are
introduced.

### TinyFugue runtime crash

An upstream/runtime failure printing `Internal error: socket.c, line 3717`
followed by `resize freed string`. Tracked separately from TinyScry feature
sequencing; it is upstream investigation and possibly upstream PR work, not a
TinyScry slice.

### Mudlet integration

Adapter work for a second client, sensible only after the normalization
boundary is generalized. The core HUD must not gain a direct Mudlet
dependency; whatever appears is another producer upstream of the protocol.

### Mobile feasibility

Later platform work, and exploratory. The current desktop arrangement - a Tauri
shell plus an OpenSSH-owned tunnel to a loopback relay - does not transfer
unchanged to a mobile platform, so this starts as a feasibility question rather
than a port.
