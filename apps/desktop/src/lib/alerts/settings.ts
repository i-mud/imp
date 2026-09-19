export interface AlertSettings {
  readonly lowHpEnabled: boolean;
  readonly lowHpThresholdPercent: number;
  readonly soundEnabled: boolean;
  readonly notificationEnabled: boolean;
}

export const MIN_HP_THRESHOLD_PERCENT = 1;
export const MAX_HP_THRESHOLD_PERCENT = 100;

export const DEFAULT_ALERT_SETTINGS: AlertSettings = {
  lowHpEnabled: true,
  lowHpThresholdPercent: 25,
  soundEnabled: true,
  notificationEnabled: true,
};

const ALERT_SETTINGS_STORAGE_KEY = 'tinyscry.alert-settings';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function persistedThreshold(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isInteger(value)) return null;
  if (value < MIN_HP_THRESHOLD_PERCENT || value > MAX_HP_THRESHOLD_PERCENT) return null;
  return value;
}

export function boundedThresholdPercent(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_ALERT_SETTINGS.lowHpThresholdPercent;
  return Math.min(MAX_HP_THRESHOLD_PERCENT, Math.max(MIN_HP_THRESHOLD_PERCENT, Math.round(value)));
}

export function alertSettingsFromPersisted(value: string | null): AlertSettings {
  if (value === null) return { ...DEFAULT_ALERT_SETTINGS };

  try {
    const parsed: unknown = JSON.parse(value);
    if (!isRecord(parsed)) return { ...DEFAULT_ALERT_SETTINGS };

    return {
      lowHpEnabled:
        typeof parsed.lowHpEnabled === 'boolean' ? parsed.lowHpEnabled : DEFAULT_ALERT_SETTINGS.lowHpEnabled,
      lowHpThresholdPercent:
        persistedThreshold(parsed.lowHpThresholdPercent) ?? DEFAULT_ALERT_SETTINGS.lowHpThresholdPercent,
      soundEnabled:
        typeof parsed.soundEnabled === 'boolean' ? parsed.soundEnabled : DEFAULT_ALERT_SETTINGS.soundEnabled,
      notificationEnabled:
        typeof parsed.notificationEnabled === 'boolean'
          ? parsed.notificationEnabled
          : DEFAULT_ALERT_SETTINGS.notificationEnabled,
    };
  } catch {
    return { ...DEFAULT_ALERT_SETTINGS };
  }
}

export function serializeAlertSettings(settings: AlertSettings): string {
  return JSON.stringify(settings);
}

export function loadAlertSettings(): AlertSettings {
  try {
    return alertSettingsFromPersisted(globalThis.localStorage?.getItem(ALERT_SETTINGS_STORAGE_KEY) ?? null);
  } catch {
    return { ...DEFAULT_ALERT_SETTINGS };
  }
}

export function saveAlertSettings(settings: AlertSettings): void {
  try {
    globalThis.localStorage?.setItem(ALERT_SETTINGS_STORAGE_KEY, serializeAlertSettings(settings));
  } catch {
    // Local UI preferences must not interfere with HUD rendering.
  }
}
