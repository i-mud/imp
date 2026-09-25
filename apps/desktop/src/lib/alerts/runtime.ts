import type { Character, Vital } from '@tinyscry/protocol';

import type { AlertDefinition, AlertVital, VitalAlertDefinition } from './definitions.ts';
import { INITIAL_VITAL_ALERT_STATE, evaluateVitalAlert, type VitalAlertState } from './vitals.ts';

export interface TriggeredVitalAlert {
  readonly definition: VitalAlertDefinition;
  readonly percent: number;
}

export interface VitalAlertsEvaluation {
  readonly states: Map<string, VitalAlertState>;
  readonly triggered: readonly TriggeredVitalAlert[];
}

function vitalOf(character: Character | null, vital: AlertVital): Vital | null {
  if (character === null) return null;

  switch (vital) {
    case 'health':
      return character.hp;
    case 'mana':
      return character.mana;
    case 'moves':
      return character.moves;
  }
}

export function evaluateVitalAlerts(
  previousStates: ReadonlyMap<string, VitalAlertState>,
  definitions: readonly AlertDefinition[],
  character: Character | null,
  fresh: boolean,
): VitalAlertsEvaluation {
  const states = new Map<string, VitalAlertState>();
  const triggered: TriggeredVitalAlert[] = [];

  for (const definition of definitions) {
    if (definition.kind !== 'vital') continue;

    const vital = vitalOf(character, definition.vital);
    const evaluation = evaluateVitalAlert(previousStates.get(definition.id) ?? INITIAL_VITAL_ALERT_STATE, {
      enabled: definition.enabled,
      vital: definition.vital,
      thresholdPercent: definition.thresholdPercent,
      fresh,
      subjectKey: character?.name ?? null,
      current: vital?.current ?? null,
      max: vital?.max ?? null,
    });

    states.set(definition.id, evaluation.state);

    if (evaluation.triggered && evaluation.percent !== null) {
      triggered.push({
        definition,
        percent: evaluation.percent,
      });
    }
  }

  return { states, triggered };
}
