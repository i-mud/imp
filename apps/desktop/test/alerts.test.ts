import { describe, expect, it } from 'vitest';

import {
  INITIAL_LOW_HP_ALERT_STATE,
  evaluateLowHpAlert,
  hpPercent,
  type LowHpAlertInput,
  type LowHpAlertState,
} from '../src/lib/alerts/evaluator.ts';

function input(overrides: Partial<LowHpAlertInput> = {}): LowHpAlertInput {
  return {
    enabled: true,
    thresholdPercent: 25,
    fresh: true,
    subjectKey: 'Aria',
    currentHp: 100,
    maxHp: 100,
    ...overrides,
  };
}

function step(state: LowHpAlertState, overrides: Partial<LowHpAlertInput>) {
  return evaluateLowHpAlert(state, input(overrides));
}

describe('evaluateLowHpAlert', () => {
  it('does not alert on initial hydration below the threshold', () => {
    const result = step(INITIAL_LOW_HP_ALERT_STATE, { currentHp: 20 });

    expect(result.triggered).toBe(false);
    expect(result.state.armed).toBe(false);
  });

  it('does not alert while remaining above the threshold', () => {
    const first = step(INITIAL_LOW_HP_ALERT_STATE, { currentHp: 31 });
    const second = step(first.state, { currentHp: 27 });

    expect(second.triggered).toBe(false);
  });

  it('alerts once on a downward crossing and not again while remaining below', () => {
    const first = step(INITIAL_LOW_HP_ALERT_STATE, { currentHp: 27 });
    const crossing = step(first.state, { currentHp: 24 });
    const lower = step(crossing.state, { currentHp: 20 });

    expect(crossing.triggered).toBe(true);
    expect(lower.triggered).toBe(false);
  });

  it('re-arms above the threshold and alerts on the next crossing', () => {
    const first = step(INITIAL_LOW_HP_ALERT_STATE, { currentHp: 27 });
    const crossing = step(first.state, { currentHp: 24 });
    const healed = step(crossing.state, { currentHp: 28 });
    const secondCrossing = step(healed.state, { currentHp: 24 });

    expect(healed.state.armed).toBe(true);
    expect(secondCrossing.triggered).toBe(true);
  });

  it('treats the exact threshold as below for crossing purposes', () => {
    const first = step(INITIAL_LOW_HP_ALERT_STATE, { currentHp: 26 });
    const crossing = step(first.state, { currentHp: 25 });

    expect(crossing.triggered).toBe(true);
  });

  it('suppresses unknown or invalid vitals', () => {
    expect(step(INITIAL_LOW_HP_ALERT_STATE, { currentHp: null }).triggered).toBe(false);
    expect(step(INITIAL_LOW_HP_ALERT_STATE, { maxHp: null }).triggered).toBe(false);
    expect(step(INITIAL_LOW_HP_ALERT_STATE, { maxHp: 0 }).triggered).toBe(false);
    expect(step(INITIAL_LOW_HP_ALERT_STATE, { maxHp: -1 }).triggered).toBe(false);
  });

  it('suppresses non-fresh state and clears the previous crossing baseline', () => {
    const first = step(INITIAL_LOW_HP_ALERT_STATE, { currentHp: 40 });
    const stale = step(first.state, { fresh: false, currentHp: 24 });

    expect(stale.triggered).toBe(false);
    expect(stale.state).toEqual(INITIAL_LOW_HP_ALERT_STATE);
  });

  it('does not alert on the first recovered snapshot after reconnect hydration', () => {
    const first = step(INITIAL_LOW_HP_ALERT_STATE, { currentHp: 40 });
    const reconnecting = step(first.state, { fresh: false, currentHp: 40 });
    const restored = step(reconnecting.state, { fresh: true, currentHp: 24 });

    expect(restored.triggered).toBe(false);
    expect(restored.state.armed).toBe(false);
  });

  it('does not carry a crossing baseline across character identity changes', () => {
    const first = step(INITIAL_LOW_HP_ALERT_STATE, { subjectKey: 'Aria', currentHp: 40 });
    const relogged = step(first.state, { subjectKey: 'Ivrin', currentHp: 20 });

    expect(relogged.triggered).toBe(false);
    expect(relogged.state.subjectKey).toBe('Ivrin');
    expect(relogged.state.armed).toBe(false);
  });

  it('resets while disabled and does not synthesize a crossing when re-enabled', () => {
    const first = step(INITIAL_LOW_HP_ALERT_STATE, { currentHp: 40 });
    const disabled = step(first.state, { enabled: false, currentHp: 20 });
    const enabled = step(disabled.state, { enabled: true, currentHp: 20 });

    expect(enabled.triggered).toBe(false);
  });

  it('re-baselines without alerting when the configured threshold changes', () => {
    const first = step(INITIAL_LOW_HP_ALERT_STATE, { currentHp: 40, thresholdPercent: 25 });
    const changed = step(first.state, { currentHp: 40, thresholdPercent: 50 });

    expect(changed.triggered).toBe(false);
    expect(changed.state.armed).toBe(false);
  });
});

describe('hpPercent', () => {
  it('uses current divided by maximum and preserves overheal percentages', () => {
    expect(hpPercent(24, 100)).toBe(24);
    expect(hpPercent(125, 100)).toBe(125);
  });

  it('rejects unusable input', () => {
    expect(hpPercent(null, 100)).toBeNull();
    expect(hpPercent(10, null)).toBeNull();
    expect(hpPercent(10, 0)).toBeNull();
    expect(hpPercent(Number.NaN, 100)).toBeNull();
  });
});
