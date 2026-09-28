import { isSafeText } from '@imp/protocol';

export type AlertVital = 'health' | 'mana' | 'moves' | 'target-health';
export type TextMatchMode = 'contains' | 'wildcard';

interface AlertDefinitionBase {
  readonly id: string;
  readonly label: string;
  readonly enabled: boolean;
  readonly soundEnabled: boolean;
  readonly notificationEnabled: boolean;
}

export interface VitalAlertDefinition extends AlertDefinitionBase {
  readonly kind: 'vital';
  readonly vital: AlertVital;
  readonly thresholdPercent: number;
}

export interface TextAlertDefinition extends AlertDefinitionBase {
  readonly kind: 'text';
  readonly matchMode: TextMatchMode;
  readonly pattern: string;
  readonly caseSensitive: boolean;
}

export type AlertDefinition = VitalAlertDefinition | TextAlertDefinition;

export const MIN_ALERT_THRESHOLD_PERCENT = 1;
export const MAX_ALERT_THRESHOLD_PERCENT = 100;
export const MAX_ALERT_DEFINITIONS = 64;
export const MAX_ALERT_LABEL_CHARACTERS = 64;
export const MAX_TEXT_PATTERN_CHARACTERS = 256;

const MAX_ALERT_ID_CHARACTERS = 128;
const ALERT_DEFINITIONS_STORAGE_KEY = 'imp.alerts';
const LEGACY_ALERT_SETTINGS_STORAGE_KEY = 'imp.alert-settings';

export const DEFAULT_LOW_HEALTH_ALERT: VitalAlertDefinition = {
  id: 'low-health',
  kind: 'vital',
  label: 'Low HP',
  enabled: true,
  vital: 'health',
  thresholdPercent: 25,
  soundEnabled: true,
  notificationEnabled: true,
};

export const DEFAULT_ALERT_DEFINITIONS: readonly AlertDefinition[] = [DEFAULT_LOW_HEALTH_ALERT];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isValidId(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    value.length > 0 &&
    value.length <= MAX_ALERT_ID_CHARACTERS &&
    isSafeText(value)
  );
}

function persistedThreshold(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isInteger(value)) return null;
  if (value < MIN_ALERT_THRESHOLD_PERCENT || value > MAX_ALERT_THRESHOLD_PERCENT) return null;
  return value;
}

function persistedBoolean(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

function persistedLabel(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const label = value.trim();
  if (alertLabelError(label) !== null) return null;
  return label;
}

function persistedBase(entry: Record<string, unknown>): Omit<AlertDefinitionBase, never> | null {
  if (!isValidId(entry.id)) return null;

  const label = persistedLabel(entry.label);
  const enabled = persistedBoolean(entry.enabled);
  const soundEnabled = persistedBoolean(entry.soundEnabled);
  const notificationEnabled = persistedBoolean(entry.notificationEnabled);

  if (label === null || enabled === null || soundEnabled === null || notificationEnabled === null) {
    return null;
  }

  return {
    id: entry.id,
    label,
    enabled,
    soundEnabled,
    notificationEnabled,
  };
}

function persistedDefinition(value: unknown): AlertDefinition | null {
  if (!isRecord(value)) return null;

  const base = persistedBase(value);
  if (base === null) return null;

  if (value.kind === 'vital') {
    if (
      value.vital !== 'health' &&
      value.vital !== 'mana' &&
      value.vital !== 'moves' &&
      value.vital !== 'target-health'
    ) {
      return null;
    }
    const thresholdPercent = persistedThreshold(value.thresholdPercent);
    if (thresholdPercent === null) return null;

    return {
      ...base,
      kind: 'vital',
      vital: value.vital,
      thresholdPercent,
    };
  }

  if (value.kind === 'text') {
    if (typeof value.pattern !== 'string' || textPatternError(value.pattern) !== null) return null;

    const matchMode =
      value.matchMode === undefined
        ? 'contains'
        : value.matchMode === 'contains' || value.matchMode === 'wildcard'
          ? value.matchMode
          : null;
    const caseSensitive = persistedBoolean(value.caseSensitive);

    if (matchMode === null || caseSensitive === null) return null;

    return {
      ...base,
      kind: 'text',
      matchMode,
      pattern: value.pattern,
      caseSensitive,
    };
  }

  return null;
}

function cloneDefaults(): AlertDefinition[] {
  return DEFAULT_ALERT_DEFINITIONS.map((definition) => ({ ...definition }));
}

export function alertThresholdError(value: number): string | null {
  if (
    !Number.isInteger(value) ||
    value < MIN_ALERT_THRESHOLD_PERCENT ||
    value > MAX_ALERT_THRESHOLD_PERCENT
  ) {
    return `Threshold must be a whole number from ${MIN_ALERT_THRESHOLD_PERCENT} to ${MAX_ALERT_THRESHOLD_PERCENT}.`;
  }
  return null;
}

export function boundedThresholdPercent(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_LOW_HEALTH_ALERT.thresholdPercent;
  return Math.min(MAX_ALERT_THRESHOLD_PERCENT, Math.max(MIN_ALERT_THRESHOLD_PERCENT, Math.round(value)));
}

export function alertLabelError(value: string): string | null {
  const label = value.trim();
  if (label.length === 0) return 'Enter a label.';
  if (!isSafeText(label)) return 'Label cannot contain control or malformed characters.';
  if ([...label].length > MAX_ALERT_LABEL_CHARACTERS) {
    return `Label must be at most ${MAX_ALERT_LABEL_CHARACTERS} characters.`;
  }
  return null;
}

export function textPatternError(value: string): string | null {
  if (value.trim().length === 0) return 'Enter text to match.';
  if (!isSafeText(value)) return 'Pattern cannot contain control or malformed characters.';
  if ([...value].length > MAX_TEXT_PATTERN_CHARACTERS) {
    return `Pattern must be at most ${MAX_TEXT_PATTERN_CHARACTERS} characters.`;
  }
  return null;
}

export function alertDefinitionsFromPersisted(value: string | null): AlertDefinition[] {
  if (value === null) return cloneDefaults();

  try {
    const parsed: unknown = JSON.parse(value);
    if (!Array.isArray(parsed)) return cloneDefaults();

    const definitions: AlertDefinition[] = [];
    const ids = new Set<string>();

    for (const entry of parsed) {
      const definition = persistedDefinition(entry);
      if (definition === null || ids.has(definition.id)) continue;

      ids.add(definition.id);
      definitions.push(definition);

      if (definitions.length === MAX_ALERT_DEFINITIONS) break;
    }

    return definitions;
  } catch {
    return cloneDefaults();
  }
}

export function legacyAlertDefinitionsFromPersisted(value: string | null): AlertDefinition[] {
  if (value === null) return cloneDefaults();

  try {
    const parsed: unknown = JSON.parse(value);
    if (!isRecord(parsed)) return cloneDefaults();

    const fallback = DEFAULT_LOW_HEALTH_ALERT;

    return [
      {
        ...fallback,
        enabled: typeof parsed.lowHpEnabled === 'boolean' ? parsed.lowHpEnabled : fallback.enabled,
        thresholdPercent: persistedThreshold(parsed.lowHpThresholdPercent) ?? fallback.thresholdPercent,
        soundEnabled: typeof parsed.soundEnabled === 'boolean' ? parsed.soundEnabled : fallback.soundEnabled,
        notificationEnabled:
          typeof parsed.notificationEnabled === 'boolean'
            ? parsed.notificationEnabled
            : fallback.notificationEnabled,
      },
    ];
  } catch {
    return cloneDefaults();
  }
}

export function serializeAlertDefinitions(definitions: readonly AlertDefinition[]): string {
  return JSON.stringify(definitions);
}

export function loadAlertDefinitions(storage?: Pick<Storage, 'getItem'> | null): AlertDefinition[] {
  try {
    const target = storage === undefined ? globalThis.localStorage : storage;
    if (target === null || target === undefined) return cloneDefaults();

    const persisted = target.getItem(ALERT_DEFINITIONS_STORAGE_KEY);
    if (persisted !== null) return alertDefinitionsFromPersisted(persisted);

    return legacyAlertDefinitionsFromPersisted(target.getItem(LEGACY_ALERT_SETTINGS_STORAGE_KEY));
  } catch {
    return cloneDefaults();
  }
}

export function saveAlertDefinitions(
  definitions: readonly AlertDefinition[],
  storage?: Pick<Storage, 'setItem'> | null,
): boolean {
  if (definitions.length > MAX_ALERT_DEFINITIONS) return false;

  try {
    const target = storage === undefined ? globalThis.localStorage : storage;
    if (target === null || target === undefined) return false;
    target.setItem(ALERT_DEFINITIONS_STORAGE_KEY, serializeAlertDefinitions(definitions));
    return true;
  } catch {
    return false;
  }
}

export function commitAlertDefinitions(
  definitions: AlertDefinition[],
  commit: (definitions: AlertDefinition[]) => void,
  storage?: Pick<Storage, 'setItem'> | null,
): boolean {
  if (!saveAlertDefinitions(definitions, storage)) return false;
  commit(definitions);
  return true;
}
