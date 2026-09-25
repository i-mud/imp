import { describe, expect, it } from 'vitest';

import type { Character } from '@tinyscry/protocol';

import type { AlertDefinition, VitalAlertDefinition } from '../src/lib/alerts/definitions.ts';
import { evaluateVitalAlerts } from '../src/lib/alerts/runtime.ts';

const character = (hp: number, mana: number, moves = 100): Character => ({
  name: 'Aria',
  hp: { current: hp, max: 100 },
  mana: { current: mana, max: 100 },
  moves: { current: moves, max: 100 },
});

const health: VitalAlertDefinition = {
  id: 'health',
  kind: 'vital',
  label: 'Low health',
  enabled: true,
  vital: 'health',
  thresholdPercent: 25,
  soundEnabled: true,
  notificationEnabled: true,
};

const mana: VitalAlertDefinition = {
  id: 'mana',
  kind: 'vital',
  label: 'Low mana',
  enabled: true,
  vital: 'mana',
  thresholdPercent: 30,
  soundEnabled: false,
  notificationEnabled: true,
};

describe('evaluateVitalAlerts', () => {
  it('evaluates independent vital triggers with independent crossing state', () => {
    const first = evaluateVitalAlerts(new Map(), [health, mana], character(80, 80), true);

    const second = evaluateVitalAlerts(first.states, [health, mana], character(70, 20), true);

    expect(second.triggered).toEqual([
      {
        definition: mana,
        percent: 20,
      },
    ]);
  });

  it('can trigger more than one configured vital on the same state update', () => {
    const first = evaluateVitalAlerts(new Map(), [health, mana], character(80, 80), true);

    const second = evaluateVitalAlerts(first.states, [health, mana], character(20, 20), true);

    expect(second.triggered.map((item) => item.definition.id)).toEqual(['health', 'mana']);
  });

  it('evaluates movement triggers against normalized moves', () => {
    const moves: VitalAlertDefinition = {
      id: 'moves',
      kind: 'vital',
      label: 'Low moves',
      enabled: true,
      vital: 'moves',
      thresholdPercent: 20,
      soundEnabled: true,
      notificationEnabled: false,
    };

    const first = evaluateVitalAlerts(new Map(), [moves], character(80, 80, 60), true);

    const second = evaluateVitalAlerts(first.states, [moves], character(80, 80, 15), true);

    expect(second.triggered).toEqual([
      {
        definition: moves,
        percent: 15,
      },
    ]);
  });

  it('drops evaluator memory for removed definitions', () => {
    const first = evaluateVitalAlerts(new Map(), [health, mana], character(80, 80), true);

    const second = evaluateVitalAlerts(first.states, [health], character(80, 80), true);

    expect([...second.states.keys()]).toEqual(['health']);
  });

  it('ignores text definitions in the vitals evaluator', () => {
    const text: AlertDefinition = {
      id: 'tell',
      kind: 'text',
      label: 'Tell',
      enabled: true,
      pattern: 'tells you',
      caseSensitive: false,
      soundEnabled: true,
      notificationEnabled: true,
    };

    const result = evaluateVitalAlerts(new Map(), [text], character(20, 20), true);

    expect(result.states.size).toBe(0);
    expect(result.triggered).toEqual([]);
  });

  it('re-baselines all definitions when the state is not fresh', () => {
    const first = evaluateVitalAlerts(new Map(), [health, mana], character(80, 80), true);

    const stale = evaluateVitalAlerts(first.states, [health, mana], character(20, 20), false);

    expect(stale.triggered).toEqual([]);
    expect([...stale.states.values()].every((state) => !state.hasBaseline)).toBe(true);
  });
});
