import type { Character, Target, Vital } from '@tinyscry/protocol';

import type { AlertDefinition, AlertVital, VitalAlertDefinition } from './definitions.ts';
import { INITIAL_VITAL_ALERT_STATE, evaluateVitalAlert, type VitalAlertState } from './vitals.ts';

export interface TriggeredVitalAlert {
  readonly definition: VitalAlertDefinition;
  readonly percent: number;
  readonly subjectLabel: string;
}

export interface VitalAlertsEvaluation {
  readonly states: Map<string, VitalAlertState>;
  readonly triggered: readonly TriggeredVitalAlert[];
}

interface AlertSample {
  readonly subjectKey: string | null;
  readonly subjectLabel: string | null;
  readonly current: number | null;
  readonly max: number | null;
}

function characterSample(character: Character | null, vital: Vital | null): AlertSample {
  return {
    subjectKey: character?.name ?? null,
    subjectLabel: character?.name ?? null,
    current: vital?.current ?? null,
    max: vital?.max ?? null,
  };
}

function sampleOf(character: Character | null, target: Target | null, vital: AlertVital): AlertSample {
  switch (vital) {
    case 'health':
      return characterSample(character, character?.hp ?? null);
    case 'mana':
      return characterSample(character, character?.mana ?? null);
    case 'moves':
      return characterSample(character, character?.moves ?? null);
    case 'target-health':
      return {
        subjectKey: target?.name ?? null,
        subjectLabel: target?.name ?? null,
        current: target?.healthPercent ?? null,
        max: 100,
      };
  }
}

export function evaluateVitalAlerts(
  previousStates: ReadonlyMap<string, VitalAlertState>,
  definitions: readonly AlertDefinition[],
  character: Character | null,
  fresh: boolean,
  target: Target | null = null,
): VitalAlertsEvaluation {
  const states = new Map<string, VitalAlertState>();
  const triggered: TriggeredVitalAlert[] = [];

  for (const definition of definitions) {
    if (definition.kind !== 'vital') continue;

    const sample = sampleOf(character, target, definition.vital);
    const evaluation = evaluateVitalAlert(previousStates.get(definition.id) ?? INITIAL_VITAL_ALERT_STATE, {
      enabled: definition.enabled,
      vital: definition.vital,
      thresholdPercent: definition.thresholdPercent,
      fresh,
      subjectKey: sample.subjectKey,
      current: sample.current,
      max: sample.max,
    });

    states.set(definition.id, evaluation.state);

    if (evaluation.triggered && evaluation.percent !== null && sample.subjectLabel !== null) {
      triggered.push({
        definition,
        percent: evaluation.percent,
        subjectLabel: sample.subjectLabel,
      });
    }
  }

  return { states, triggered };
}
