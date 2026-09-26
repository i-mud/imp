import { describe, expect, it } from 'vitest';

import type { Character } from '@tinyscry/protocol';

import type {
  AlertDefinition,
  TextAlertDefinition,
  VitalAlertDefinition,
} from '../src/lib/alerts/definitions.ts';
import { evaluateTextAlerts, evaluateVitalAlerts } from '../src/lib/alerts/runtime.ts';

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
        subjectLabel: 'Aria',
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
        subjectLabel: 'Aria',
      },
    ]);
  });

  it('evaluates target health and re-baselines when the target changes', () => {
    const targetHealth: VitalAlertDefinition = {
      id: 'target-health',
      kind: 'vital',
      label: 'Low target',
      enabled: true,
      vital: 'target-health',
      thresholdPercent: 25,
      soundEnabled: false,
      notificationEnabled: true,
    };

    const first = evaluateVitalAlerts(new Map(), [targetHealth], character(80, 80), true, {
      name: 'a frost giant',
      healthPercent: 80,
    });

    const crossing = evaluateVitalAlerts(first.states, [targetHealth], character(80, 80), true, {
      name: 'a frost giant',
      healthPercent: 20,
    });

    expect(crossing.triggered).toEqual([
      {
        definition: targetHealth,
        percent: 20,
        subjectLabel: 'a frost giant',
      },
    ]);

    const replacement = evaluateVitalAlerts(crossing.states, [targetHealth], character(80, 80), true, {
      name: 'a shadow hound',
      healthPercent: 10,
    });

    expect(replacement.triggered).toEqual([]);
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

describe('evaluateTextAlerts', () => {
  const tell: TextAlertDefinition = {
    id: 'tell',
    kind: 'text',
    label: 'Incoming tell',
    enabled: true,
    pattern: 'tells you',
    caseSensitive: false,
    soundEnabled: true,
    notificationEnabled: true,
  };

  const exact: TextAlertDefinition = {
    id: 'exact',
    kind: 'text',
    label: 'Exact marker',
    enabled: true,
    pattern: '[ALERT]',
    caseSensitive: true,
    soundEnabled: false,
    notificationEnabled: true,
  };

  it('matches literal substrings in definition order', () => {
    expect(
      evaluateTextAlerts([tell, exact], 'Aria TELLS YOU something [ALERT]').map((item) => item.definition.id),
    ).toEqual(['tell', 'exact']);
  });

  it('honors case sensitivity and ignores disabled definitions', () => {
    expect(
      evaluateTextAlerts([exact, { ...tell, enabled: false }], 'aria tells you something [alert]'),
    ).toEqual([]);
  });

  it('treats patterns literally rather than as regular expressions', () => {
    const literal: TextAlertDefinition = {
      ...tell,
      id: 'literal',
      pattern: '.*',
      caseSensitive: true,
    };

    expect(evaluateTextAlerts([literal], 'anything')).toEqual([]);
    expect(evaluateTextAlerts([literal], 'literal .* marker')).toEqual([{ definition: literal }]);
  });

  it('allows several definitions to match the same received line', () => {
    const second: TextAlertDefinition = {
      ...tell,
      id: 'second',
      pattern: 'Aria',
      caseSensitive: true,
    };

    expect(
      evaluateTextAlerts([tell, second], 'Aria tells you hello.').map((item) => item.definition.id),
    ).toEqual(['tell', 'second']);
  });

  it('has no crossing memory, so identical lines match repeatedly', () => {
    const line = 'Aria tells you hello.';

    expect(evaluateTextAlerts([tell], line)).toEqual([{ definition: tell }]);
    expect(evaluateTextAlerts([tell], line)).toEqual([{ definition: tell }]);
  });
});
