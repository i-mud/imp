# Desktop alerts

## Purpose

Imp evaluates operator-configured alerts locally in the desktop HUD after
state has already crossed the protocol and freshness boundaries. Alerts are a
presentation-side behavior; they do not publish state, dispatch actions, or
send commands toward the MUD.

Slice 10 generalizes the original single low-HP rule into configurable
vital-threshold and received-text alerts. Vital alerts operate on normalized
state. Text alerts operate on transient, context-bound received-text events
that are never retained in `GameState` or `HudModel`.

Data flow:

    normalized GameState                 transient received text
            |                                      |
            v                                      v
    configured vital definitions          configured text definitions
            |                                      |
            v                                      v
    per-trigger crossing state            bounded Contains/Wildcard match
            |                                      |
            +------------------+-------------------+
                               |
                         alert event
                         /         \
                        v           v
                  bundled sound  native notification

## Source

- `apps/desktop/src/lib/alerts/definitions.ts` owns validated persisted alert
  definitions, legacy low-HP preference migration, and definition bounds.
- `apps/desktop/src/lib/alerts/vitals.ts` owns the pure reusable
  vital-threshold crossing state machine.
- `apps/desktop/src/lib/alerts/runtime.ts` maps configured vital definitions to
  normalized character vitals, keeps independent evaluator state per vital
  definition, and performs stateless bounded matching for text definitions.
- `apps/desktop/src/lib/alerts/effects.ts` owns generalized best-effort effect
  dispatch.
- `apps/desktop/src/lib/alerts/native.ts` owns the bundled audio and Tauri
  notification integrations.
- `apps/desktop/src/lib/hud/store.svelte.ts` fans transient text events out
  synchronously without storing a last line or adding text to `HudModel`.
- `apps/desktop/src/components/Hud.svelte` supplies normalized character state
  plus the existing `freshnessOf(model)` / `hasData` usability boundary and
  dispatches matching transient text alerts.
- `apps/desktop/src/components/AlertDialog.svelte` creates and edits both vital
  and received-text definitions.

No alert code publishes state or invokes the desktop `ActionSink`.

## Alert definitions

Definitions are ordered operator configuration stored locally under
`imp.alerts`. At most 64 definitions are retained.

A definition has:

- an opaque local id;
- a bounded safe-text label;
- an enabled flag;
- independent sound and desktop-notification flags; and
- a trigger-specific body.

Threshold definitions select one normalized metric:

- `health`;
- `mana`;
- `moves`;
- `target-health`.

Health, mana, and moves use normalized current/max character vitals. Target
health uses the already-normalized target health percentage against a maximum
of 100.

Every threshold is an integer percentage in the inclusive range 1-100.

Text definitions contain a bounded pattern, a match mode (`contains` or
`wildcard`), and an independent case-sensitivity flag. Existing stored text
definitions that predate `matchMode` migrate to `contains`; unknown match modes
are rejected. Text alerts consume only the transient received-text stream; the
incoming line is not added to persisted alert configuration or retained
desktop state.

If the generalized store does not yet exist, Imp reads the previous
`imp.alert-settings` low-HP preferences and materializes them in memory as
the default `low-health` vital definition. Once generalized definitions are
saved, that store takes precedence, including an intentionally empty list.

The settings surface exposes a dedicated alert manager. Operators can create,
edit, delete, enable, and disable both threshold and received-text alerts and
independently select sound and desktop-notification effects. Enabled state is a
persisted quick-toggle rather than part of the edit form. The compact and
expanded Bell surfaces expose the same enabled state through a shared quick
alert list.

## Vital threshold semantics

Each configured vital definition owns independent crossing state.

A valid current/max pair establishes a percentage as `current / max * 100`.
The first fresh usable observation establishes a baseline and never alerts,
even if already at or below the threshold.

After a baseline above the threshold, Imp alerts only on a downward
crossing from `> threshold` to `<= threshold`. Remaining at or below the
threshold does not repeat. Returning above the threshold re-arms that
definition.

Changing the threshold, selected metric, or subject identity re-baselines
rather than synthesizing a crossing. Disabling a definition also clears its
baseline. Removing a definition removes its evaluator memory.

For character metrics, the subject identity is the normalized character name.
For target health, the subject identity is the normalized target name. Losing
the target resets the baseline, and a differently named replacement
re-baselines without alerting. The current normalized target model has no
stable per-creature identifier, so a direct replacement by another target with
the identical name cannot be distinguished as a new subject.

Multiple definitions are independent and may therefore fire on the same state
update.

## Text trigger semantics

Text alerts support two bounded matching modes.

`contains` performs literal substring matching. No character has special
meaning in this mode, including `*`.

`wildcard` matches the whole received line. Only `*` is special, and it means
zero or more characters. Every other character is literal, including regular
expression punctuation such as `.`, `?`, `[`, `]`, `+`, `^`, `$`, and `\`.
A wildcard pattern with no `*` therefore requires exact whole-line equality.
Leading or trailing `*` explicitly permit unmatched text at that edge.

Wildcard matching is implemented as ordered literal-segment matching rather
than regular-expression evaluation or per-character dynamic programming. The
grammar remains intentionally small and bounded: there are no regular
expressions, captures, command synthesis, shell semantics, or executable
expressions.

Both modes can be independently case-sensitive or case-insensitive.
Case-insensitive matching uses deterministic string lowercasing rather than
locale-specific comparison.

Every enabled matching definition fires independently in definition order, so
one received line may trigger several alerts. Text matching has no threshold
crossing or debounce memory: repeated identical received lines are distinct
events and may alert repeatedly.

The alert event contains the stable configured alert id and configured label.
Raw received MUD text is never copied into the native notification body.

The received-text path remains transient end to end. A text event is delivered
only to subscribers connected at that moment, is not retained or replayed by
the relay, is not incorporated into `GameState`, and is not retained by
`HudStore`.

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
Imp requests it at most once during the current renderer lifetime before
silently giving up on that delivery path.

## Trust and outbound behavior

Alert definitions are local operator configuration. MUD-derived values used by
the evaluator are already-normalized `GameState` fields.

Alerts remain outbound-silent:

- they never dispatch an action;
- they never synthesize a command;
- they never execute shell or TinyFugue source; and
- they do not weaken the `StateSource` / `ActionSink` separation.

Received MUD text is bounded untrusted input. It is used only for bounded
Contains/Wildcard matching and is never interpreted as a command, expression,
regular expression, or executable source.

## Verification

Deterministic desktop tests cover:

- persisted-definition validation and bounds;
- legacy low-HP preference migration;
- independent vital crossing state;
- health, mana, moves, and target-health selection;
- target-health crossing and target-change re-baselining;
- alert-management threshold validation;
- stale/reconnect and identity re-baselining;
- removed-definition state cleanup;
- per-definition sound/notification selection;
- tray mute suppression; and
- effect failure isolation;
- synchronous transient-text fan-out without `HudModel` retention;
- Contains and whole-line Wildcard matching semantics;
- literal `*` behavior in Contains mode and zero-or-more behavior in Wildcard mode;
- exact whole-line Wildcard matching when no `*` is present;
- case-sensitive and case-insensitive matching;
- legacy text-definition migration to Contains and rejection of unknown modes;
- repeated identical line handling and multiple-definition matching;
- persisted quick enable/disable behavior; and
- stable alert ids with configured-label-only text notification bodies.

Native/live acceptance additionally verified creation and editing of a
received-text alert, persistence after reopening the manager, repeated
identical matches, sound and notification delivery, and that the notification
body contains only the configured label rather than raw received MUD text.

The final Windows-native Slice 10 smoke also verified the optimized wildcard
matcher: `*` consumed both zero and multiple characters, whole-line anchoring
held at both ends, leading/trailing `*` relaxed those edges, wildcard mode with
no star required exact whole-line equality, Contains kept `*` literal, and
case-insensitive wildcard matching worked. Bell quick toggles immediately
disabled and re-enabled the same live text-alert definition set. Compact and
expanded Bell panels, intrinsic expanded Settings/Alerts sizing, and compact
zero-definition Actions/Alerts affordances were also exercised successfully.

Status: verified for configurable vital and received-text alerts
Verified against: desktop unit tests, strict Svelte/TypeScript checking, and
Windows-native live text-alert acceptance.
