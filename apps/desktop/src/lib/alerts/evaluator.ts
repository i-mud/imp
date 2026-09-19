export interface LowHpAlertInput {
  readonly enabled: boolean;
  readonly thresholdPercent: number;
  readonly fresh: boolean;
  readonly subjectKey: string | null;
  readonly currentHp: number | null;
  readonly maxHp: number | null;
}

export interface LowHpAlertState {
  readonly hasBaseline: boolean;
  readonly subjectKey: string | null;
  readonly previousPercent: number | null;
  readonly armed: boolean;
  readonly thresholdPercent: number | null;
}

export interface LowHpAlertEvaluation {
  readonly state: LowHpAlertState;
  readonly triggered: boolean;
  readonly hpPercent: number | null;
}

export const INITIAL_LOW_HP_ALERT_STATE: LowHpAlertState = {
  hasBaseline: false,
  subjectKey: null,
  previousPercent: null,
  armed: false,
  thresholdPercent: null,
};

function validThreshold(value: number): boolean {
  return Number.isFinite(value) && value >= 1 && value <= 100;
}

export function hpPercent(currentHp: number | null, maxHp: number | null): number | null {
  if (currentHp === null || maxHp === null) return null;
  if (!Number.isFinite(currentHp) || currentHp < 0) return null;
  if (!Number.isFinite(maxHp) || maxHp <= 0) return null;
  return (currentHp / maxHp) * 100;
}

function reset(): LowHpAlertEvaluation {
  return { state: INITIAL_LOW_HP_ALERT_STATE, triggered: false, hpPercent: null };
}

export function evaluateLowHpAlert(state: LowHpAlertState, input: LowHpAlertInput): LowHpAlertEvaluation {
  if (
    !input.enabled ||
    !input.fresh ||
    input.subjectKey === null ||
    !validThreshold(input.thresholdPercent)
  ) {
    return reset();
  }

  const percent = hpPercent(input.currentHp, input.maxHp);
  if (percent === null) return reset();

  if (
    !state.hasBaseline ||
    state.subjectKey !== input.subjectKey ||
    state.thresholdPercent !== input.thresholdPercent
  ) {
    return {
      state: {
        hasBaseline: true,
        subjectKey: input.subjectKey,
        previousPercent: percent,
        armed: percent > input.thresholdPercent,
        thresholdPercent: input.thresholdPercent,
      },
      triggered: false,
      hpPercent: percent,
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
      previousPercent: percent,
      armed: percent > input.thresholdPercent,
      thresholdPercent: input.thresholdPercent,
    },
    triggered: crossedDown,
    hpPercent: percent,
  };
}
