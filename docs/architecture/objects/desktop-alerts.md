# Desktop alerts

## Purpose

TinyScry evaluates operator-configured alerts locally in the desktop HUD after
state has already crossed the protocol and freshness boundaries. Alerts are a
presentation-side behavior; they do not publish state, dispatch actions, or
send commands toward the MUD.

The Slice 10 implementation is currently being generalized from the original
single low-HP rule. The active runtime in this checkpoint supports configurable
vital-threshold definitions for health, mana, and moves. Text-trigger
definitions have a validated persisted shape, but received-MUD-text transport
and text matching are not implemented yet.

Data flow:

    normalized GameState + existing desktop freshness
                        |
                        v
           configured vital definitions
                        |
                        v
              per-trigger evaluator state
                        |
                 threshold crossing
                   /           \
                  v             v
            bundled sound   native notification

## Source

- `apps/desktop/src/lib/alerts/definitions.ts` owns validated persisted alert
  definitions, legacy low-HP preference migration, and definition bounds.
- `apps/desktop/src/lib/alerts/vitals.ts` owns the pure reusable
  vital-threshold crossing state machine.
- `apps/desktop/src/lib/alerts/runtime.ts` maps configured vital definitions to
  normalized character vitals and keeps independent evaluator state per
  definition.
- `apps/desktop/src/lib/alerts/effects.ts` owns generalized best-effort effect
  dispatch.
- `apps/desktop/src/lib/alerts/native.ts` owns the bundled audio and Tauri
  notification integrations.
- `apps/desktop/src/components/Hud.svelte` supplies normalized character state
  plus the existing `freshnessOf(model)` / `hasData` usability boundary.

No alert code publishes state or invokes the desktop `ActionSink`.

## Alert definitions

Definitions are ordered operator configuration stored locally under
`tinyscry.alerts`. At most 64 definitions are retained.

A definition has:

- an opaque local id;
- a bounded safe-text label;
- an enabled flag;
- independent sound and desktop-notification flags; and
- a trigger-specific body.

Vital definitions select one normalized vital:

- `health`;
- `mana`;
- `moves`;

and an integer threshold percentage in the inclusive range 1-100.

The persisted model also defines bounded literal text-trigger configuration for
Slice 10's later text path. Persisting such a definition does not currently
make it executable: no received-text transport or text evaluator exists in
this checkpoint.

If the generalized store does not yet exist, TinyScry reads the previous
`tinyscry.alert-settings` low-HP preferences and materializes them in memory as
the default `low-health` vital definition. Once generalized definitions are
saved, that store takes precedence, including an intentionally empty list.

The current settings surface temporarily continues to expose the migrated
`low-health` definition directly. The dedicated alert-management UI is a later
Slice 10 checkpoint.

## Vital threshold semantics

Each configured vital definition owns independent crossing state.

A valid current/max pair establishes a percentage as `current / max * 100`.
The first fresh usable observation establishes a baseline and never alerts,
even if already at or below the threshold.

After a baseline above the threshold, TinyScry alerts only on a downward
crossing from `> threshold` to `<= threshold`. Remaining at or below the
threshold does not repeat. Returning above the threshold re-arms that
definition.

Changing the threshold, selected vital, or character identity re-baselines
rather than synthesizing a crossing. Disabling a definition also clears its
baseline. Removing a definition removes its evaluator memory.

Multiple definitions are independent and may therefore fire on the same state
update.

## Suppression boundary

Vital evaluation resets its crossing baseline whenever the observation is not
usable. That includes:

- the definition being disabled;
- desktop state not being fresh/usable under the existing HUD model;
- missing character identity;
- missing current or maximum vital values;
- non-finite or negative current values; or
- non-finite or non-positive maximum values.

The next fresh usable sample after such a reset is hydration, not a crossing.
This preserves the original low-HP anti-spam behavior across startup,
reconnect, stale state, identity changes, and configuration changes.

## Effects and failure isolation

Each definition independently selects sound, desktop notification, or both.

The tray-level `Mute alerts` state suppresses all configured alert effects for
the current application run without mutating saved definitions.

Effect delivery is best-effort. A rejected audio play or notification call is
contained and cannot interrupt HUD state handling, and failure of one effect
does not block the other.

The sound remains the bundled local WAV asset and has no network dependency.
Native notifications use Tauri's notification plugin.

Notification permission is checked lazily. If it has not been granted,
TinyScry requests it at most once during the current renderer lifetime before
silently giving up on that delivery path.

## Trust and outbound behavior

Alert definitions are local operator configuration. MUD-derived values used by
the evaluator are already-normalized `GameState` fields.

Alerts remain outbound-silent:

- they never dispatch an action;
- they never synthesize a command;
- they never execute shell or TinyFugue source; and
- they do not weaken the `StateSource` / `ActionSink` separation.

The later text-trigger path must preserve the same rule while treating received
MUD text as bounded untrusted input.

## Verification

Deterministic desktop tests cover:

- persisted-definition validation and bounds;
- legacy low-HP preference migration;
- independent vital crossing state;
- health, mana, and moves selection;
- stale/reconnect and identity re-baselining;
- removed-definition state cleanup;
- per-definition sound/notification selection;
- tray mute suppression; and
- effect failure isolation.

Real notification/audio behavior remains a native acceptance concern.

The received-text path, text matching, alert-management UI, and live text-path
acceptance are still pending Slice 10 work.

Status: verified for the generalized vitals checkpoint
Verified against: desktop unit tests and strict Svelte/TypeScript checking.
