import { describe, expect, it } from 'vitest';

import {
  INITIAL_VITAL_ALERT_STATE,
  evaluateVitalAlert,
  vitalPercent,
  type VitalAlertInput,
  type VitalAlertState,
} from '../src/lib/alerts/vitals.ts';

function input(overrides: Partial<VitalAlertInput> = {}): VitalAlertInput {
  return {
    enabled: true,
    vital: 'health',
    thresholdPercent: 25,
    fresh: true,
    subjectKey: 'Aria',
    current: 100,
    max: 100,
    ...overrides,
  };
}

function step(state: VitalAlertState, overrides: Partial<VitalAlertInput>) {
  return evaluateVitalAlert(state, input(overrides));
}

describe('evaluateVitalAlert', () => {
  it('hydrates below threshold without triggering', () => {
    const result = step(INITIAL_VITAL_ALERT_STATE, { current: 20 });

    expect(result.triggered).toBe(false);
    expect(result.state.armed).toBe(false);
  });

  it('triggers once on a downward crossing', () => {
    const baseline = step(INITIAL_VITAL_ALERT_STATE, { current: 30 });
    const crossing = step(baseline.state, { current: 25 });
    const remainingLow = step(crossing.state, { current: 20 });

    expect(crossing.triggered).toBe(true);
    expect(remainingLow.triggered).toBe(false);
  });

  it('re-arms after recovery above the threshold', () => {
    const baseline = step(INITIAL_VITAL_ALERT_STATE, { current: 30 });
    const crossing = step(baseline.state, { current: 20 });
    const recovered = step(crossing.state, { current: 40 });
    const secondCrossing = step(recovered.state, { current: 20 });

    expect(recovered.state.armed).toBe(true);
    expect(secondCrossing.triggered).toBe(true);
  });

  it('re-baselines when the selected vital changes', () => {
    const health = step(INITIAL_VITAL_ALERT_STATE, {
      vital: 'health',
      current: 80,
    });
    const mana = step(health.state, {
      vital: 'mana',
      current: 20,
    });

    expect(mana.triggered).toBe(false);
    expect(mana.state.vital).toBe('mana');
  });

  it('re-baselines when threshold or subject changes', () => {
    const baseline = step(INITIAL_VITAL_ALERT_STATE, { current: 80 });

    expect(
      step(baseline.state, {
        thresholdPercent: 90,
        current: 20,
      }).triggered,
    ).toBe(false);

    expect(
      step(baseline.state, {
        subjectKey: 'Ivrin',
        current: 20,
      }).triggered,
    ).toBe(false);
  });

  it('clears the baseline while disabled or not fresh', () => {
    const baseline = step(INITIAL_VITAL_ALERT_STATE, { current: 80 });

    expect(step(baseline.state, { enabled: false }).state).toEqual(INITIAL_VITAL_ALERT_STATE);
    expect(step(baseline.state, { fresh: false }).state).toEqual(INITIAL_VITAL_ALERT_STATE);
  });

  it('clears the baseline for unusable vital samples', () => {
    const baseline = step(INITIAL_VITAL_ALERT_STATE, { current: 80 });

    expect(step(baseline.state, { current: null }).state).toEqual(INITIAL_VITAL_ALERT_STATE);
    expect(step(baseline.state, { max: 0 }).state).toEqual(INITIAL_VITAL_ALERT_STATE);
  });
});

describe('vitalPercent', () => {
  it('works for any normalized vital and preserves values over 100%', () => {
    expect(vitalPercent(24, 100)).toBe(24);
    expect(vitalPercent(125, 100)).toBe(125);
  });

  it('rejects unusable values', () => {
    expect(vitalPercent(null, 100)).toBeNull();
    expect(vitalPercent(10, null)).toBeNull();
    expect(vitalPercent(10, 0)).toBeNull();
    expect(vitalPercent(Number.NaN, 100)).toBeNull();
  });
});
