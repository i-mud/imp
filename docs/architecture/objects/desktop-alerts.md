# Desktop alerts

## Purpose

TinyScry evaluates operator-configured alerts locally in the desktop HUD after
state has already crossed the protocol and freshness boundaries. Alerts are a
presentation-side behavior; they are not part of the relay, TinyFugue adapter,
protocol, SSH supervision, or GMCP normalization.

```text
normalized GameState + existing desktop freshness
                    |
                    v
             low-HP evaluator
                    |
              threshold crossing
               /           \
              v             v
        bundled sound   native notification
```

## Source

- `apps/desktop/src/lib/alerts/evaluator.ts` owns the pure low-HP crossing
  state machine.
- `apps/desktop/src/lib/alerts/settings.ts` owns validated local alert
  preferences.
- `apps/desktop/src/lib/alerts/effects.ts` owns best-effort dispatch to effect
  boundaries.
- `apps/desktop/src/lib/alerts/native.ts` owns the bundled audio and Tauri
  notification integrations.
- `apps/desktop/src/components/Hud.svelte` supplies already-normalized
  `GameState` plus `freshnessOf(model)` / `hasData` usability to the evaluator.

No alert code publishes state or sends data toward the MUD.

## Low-HP semantics

The default threshold is 25 percent. A valid current/max HP pair establishes a
percentage as `current / max * 100`.

A fresh first observation establishes a baseline and never alerts, even if it
is already at or below the threshold. After a baseline above the threshold,
TinyScry alerts only on a downward crossing from `> threshold` to
`<= threshold`. Remaining at or below the threshold does not repeat. Returning
above the threshold re-arms the evaluator.

Changing the threshold re-baselines rather than synthesizing a crossing. A
character identity change also establishes a new baseline, so relogging cannot
turn the first low-HP observation of a different character into a crossing.
Disabling and re-enabling the low-HP alert likewise re-baselines.

## Suppression boundary

The evaluator resets its crossing baseline whenever alert evaluation is not
usable. That includes:

- the low-HP alert being disabled;
- desktop state not being fresh/usable under the existing HUD model;
- missing current HP;
- missing maximum HP;
- non-finite/negative current HP; or
- non-finite/non-positive maximum HP.

Resetting means that the next fresh usable sample is hydration, not a crossing.
This suppresses false alerts from startup below threshold, stale state, and the
first recovered snapshot after reconnect. Replayed snapshots already rejected
by the HUD reducer do not create a new crossing either.

Slice 6 deliberately consumes the existing freshness semantics. It does not
try to solve the separate feed-liveness issue where an idle healthy session
can eventually be classified stale.

## Effects and failure isolation

Sound and native notifications are independently configurable. One evaluator
crossing dispatches each enabled effect once. Effect failures are best-effort:
a rejected audio play or notification call is contained and cannot interrupt
HUD state handling.

The sound is a bundled local WAV asset and has no network dependency. Native
notifications use Tauri's notification plugin. The capability grants only the
three commands used by TinyScry: permission check, permission request, and
notify.

Notification permission is checked lazily. If it has not been granted,
TinyScry requests it at most once during the current renderer lifetime before
silently giving up on that delivery path.

## Persisted preferences

Alert preferences use local storage, like display mode. The stored object
contains:

- low-HP alert enabled;
- low-HP threshold percentage;
- sound enabled; and
- desktop notification enabled.

Thresholds are integer percentages bounded to 1-100. Missing, malformed, or
out-of-range persisted values fall back field-by-field to defaults without
throwing during HUD startup.

## Verification

Deterministic coverage belongs in desktop unit tests and does not display a
real notification or require physical audio. Native Windows acceptance is
separate and must cover actual sound, notification delivery, permissions,
settings sizing/restoration, and non-maximizable drag-region behavior.

Status: verified
Verified against: the canonical `npm run check` gate and Windows 11 native
acceptance covering low-HP sound and notification delivery, notification
permission handling, settings presentation and restoration, non-maximizable
drag-region behavior, and target-driven expanded sizing.
