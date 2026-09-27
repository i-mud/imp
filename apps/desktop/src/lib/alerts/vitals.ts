import type { AlertVital } from './definitions.ts';

export interface VitalAlertInput {
  readonly enabled: boolean;
  readonly vital: AlertVital;
  readonly thresholdPercent: number;
  readonly fresh: boolean;
  readonly subjectKey: string | null;
  readonly current: number | null;
  readonly max: number | null;
}

export interface VitalAlertState {
  readonly hasBaseline: boolean;
  readonly subjectKey: string | null;
  readonly vital: AlertVital | null;
  readonly previousPercent: number | null;
  readonly armed: boolean;
  readonly thresholdPercent: number | null;
}

export interface VitalAlertEvaluation {
  readonly state: VitalAlertState;
  readonly triggered: boolean;
  readonly percent: number | null;
}

export const INITIAL_VITAL_ALERT_STATE: VitalAlertState = {
  hasBaseline: false,
  subjectKey: null,
  vital: null,
  previousPercent: null,
  armed: false,
  thresholdPercent: null,
};

function validThreshold(value: number): boolean {
  return Number.isFinite(value) && value >= 1 && value <= 100;
}

export function vitalPercent(current: number | null, max: number | null): number | null {
  if (current === null || max === null) return null;
  if (!Number.isFinite(current) || current < 0) return null;
  if (!Number.isFinite(max) || max <= 0) return null;
  return (current / max) * 100;
}

function reset(): VitalAlertEvaluation {
  return {
    state: INITIAL_VITAL_ALERT_STATE,
    triggered: false,
    percent: null,
  };
}

export function evaluateVitalAlert(state: VitalAlertState, input: VitalAlertInput): VitalAlertEvaluation {
  if (
    !input.enabled ||
    !input.fresh ||
    input.subjectKey === null ||
    !validThreshold(input.thresholdPercent)
  ) {
    return reset();
  }

  const percent = vitalPercent(input.current, input.max);
  if (percent === null) return reset();

  if (
    !state.hasBaseline ||
    state.subjectKey !== input.subjectKey ||
    state.vital !== input.vital ||
    state.thresholdPercent !== input.thresholdPercent
  ) {
    return {
      state: {
        hasBaseline: true,
        subjectKey: input.subjectKey,
        vital: input.vital,
        previousPercent: percent,
        armed: percent > input.thresholdPercent,
        thresholdPercent: input.thresholdPercent,
      },
      triggered: false,
      percent,
    };
  }

  const crossedDown =
    state.armed &&
    state.previousPercent !== null &&
    state.previousPercent > input.thresholdPercent &&
    percent <= input.thresholdPercent;

  return {
    state: {
      hasBaseline: true,
      subjectKey: input.subjectKey,
      vital: input.vital,
      previousPercent: percent,
      armed: percent > input.thresholdPercent,
      thresholdPercent: input.thresholdPercent,
    },
    triggered: crossedDown,
    percent,
  };
}
