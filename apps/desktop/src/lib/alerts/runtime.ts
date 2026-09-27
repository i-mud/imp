import type { Character, Target, Vital } from '@tinyscry/protocol';

import type {
  AlertDefinition,
  AlertVital,
  TextAlertDefinition,
  VitalAlertDefinition,
} from './definitions.ts';
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

export interface TriggeredTextAlert {
  readonly definition: TextAlertDefinition;
}

function wildcardMatches(text: string, pattern: string): boolean {
  if (!pattern.includes('*')) return text === pattern;

  const parts = pattern.split('*');
  const startsWithWildcard = pattern.startsWith('*');
  const endsWithWildcard = pattern.endsWith('*');
  const lastIndex = parts.length - 1;

  let offset = 0;
  let partIndex = 0;

  if (!startsWithWildcard) {
    const first = parts[0]!;
    if (!text.startsWith(first)) return false;
    offset = first.length;
    partIndex = 1;
  }

  const middleEnd = endsWithWildcard ? parts.length : lastIndex;

  for (; partIndex < middleEnd; partIndex += 1) {
    const part = parts[partIndex]!;
    if (part.length === 0) continue;

    const foundAt = text.indexOf(part, offset);
    if (foundAt === -1) return false;

    offset = foundAt + part.length;
  }

  if (!endsWithWildcard) {
    const last = parts[lastIndex]!;
    const lastStart = text.length - last.length;

    return lastStart >= offset && text.endsWith(last);
  }

  return true;
}

export function evaluateTextAlerts(
  definitions: readonly AlertDefinition[],
  text: string,
): readonly TriggeredTextAlert[] {
  const triggered: TriggeredTextAlert[] = [];
  let lowerText: string | null = null;

  for (const definition of definitions) {
    if (definition.kind !== 'text' || !definition.enabled) continue;

    const candidateText = definition.caseSensitive ? text : (lowerText ??= text.toLowerCase());
    const candidatePattern = definition.caseSensitive ? definition.pattern : definition.pattern.toLowerCase();

    const matches =
      definition.matchMode === 'contains'
        ? candidateText.includes(candidatePattern)
        : wildcardMatches(candidateText, candidatePattern);

    if (matches) triggered.push({ definition });
  }

  return triggered;
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
