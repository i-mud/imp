import { describe, expect, it } from 'vitest';

import {
  DEFAULT_ALERT_SETTINGS,
  alertSettingsFromPersisted,
  boundedThresholdPercent,
  serializeAlertSettings,
} from '../src/lib/alerts/settings.ts';

describe('alert settings persistence', () => {
  it('uses defaults when nothing has been stored', () => {
    expect(alertSettingsFromPersisted(null)).toEqual(DEFAULT_ALERT_SETTINGS);
  });

  it('round-trips persisted settings', () => {
    const settings = {
      lowHpEnabled: false,
      lowHpThresholdPercent: 31,
      soundEnabled: false,
      notificationEnabled: true,
    } as const;

    expect(alertSettingsFromPersisted(serializeAlertSettings(settings))).toEqual(settings);
  });

  it('falls back safely for malformed JSON and malformed fields', () => {
    expect(alertSettingsFromPersisted('{')).toEqual(DEFAULT_ALERT_SETTINGS);
    expect(
      alertSettingsFromPersisted(
        JSON.stringify({
          lowHpEnabled: 'yes',
          lowHpThresholdPercent: 0,
          soundEnabled: 1,
          notificationEnabled: null,
        }),
      ),
    ).toEqual(DEFAULT_ALERT_SETTINGS);
  });

  it('keeps valid fields when another persisted field is malformed', () => {
    expect(
      alertSettingsFromPersisted(
        JSON.stringify({
          lowHpEnabled: false,
          lowHpThresholdPercent: 101,
          soundEnabled: false,
          notificationEnabled: true,
        }),
      ),
    ).toEqual({
      lowHpEnabled: false,
      lowHpThresholdPercent: 25,
      soundEnabled: false,
      notificationEnabled: true,
    });
  });

  it('bounds UI threshold input to 1-100 percent', () => {
    expect(boundedThresholdPercent(0)).toBe(1);
    expect(boundedThresholdPercent(1)).toBe(1);
    expect(boundedThresholdPercent(24.6)).toBe(25);
    expect(boundedThresholdPercent(100)).toBe(100);
    expect(boundedThresholdPercent(101)).toBe(100);
    expect(boundedThresholdPercent(Number.NaN)).toBe(25);
  });
});
