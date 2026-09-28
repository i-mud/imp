import { describe, expect, it, vi } from 'vitest';

import {
  DEFAULT_ALERT_DEFINITIONS,
  MAX_ALERT_DEFINITIONS,
  alertDefinitionsFromPersisted,
  alertThresholdError,
  commitAlertDefinitions,
  legacyAlertDefinitionsFromPersisted,
  loadAlertDefinitions,
  saveAlertDefinitions,
  serializeAlertDefinitions,
  textPatternError,
  type AlertDefinition,
} from '../src/lib/alerts/definitions.ts';

const healthAlert: AlertDefinition = {
  id: 'health-30',
  kind: 'vital',
  label: 'Health warning',
  enabled: true,
  vital: 'health',
  thresholdPercent: 30,
  soundEnabled: true,
  notificationEnabled: false,
};

const textAlert: AlertDefinition = {
  id: 'tell',
  kind: 'text',
  matchMode: 'contains',
  label: 'Incoming tell',
  enabled: true,
  pattern: 'tells you',
  caseSensitive: false,
  soundEnabled: false,
  notificationEnabled: true,
};

describe('alert definition persistence', () => {
  it('uses the default low-health trigger when nothing has been stored', () => {
    expect(alertDefinitionsFromPersisted(null)).toEqual(DEFAULT_ALERT_DEFINITIONS);
  });

  it('round-trips vital and text definitions', () => {
    const definitions = [healthAlert, textAlert];

    expect(alertDefinitionsFromPersisted(serializeAlertDefinitions(definitions))).toEqual(definitions);
  });

  it('migrates text definitions without a match mode to contains', () => {
    const persisted = JSON.parse(JSON.stringify(textAlert)) as Record<string, unknown>;
    delete persisted.matchMode;

    expect(alertDefinitionsFromPersisted(JSON.stringify([persisted]))).toEqual([textAlert]);
  });

  it('rejects unknown text match modes', () => {
    expect(
      alertDefinitionsFromPersisted(
        JSON.stringify([
          {
            ...textAlert,
            matchMode: 'regex',
          },
        ]),
      ),
    ).toEqual([]);
  });

  it('preserves an intentionally empty definition list', () => {
    expect(alertDefinitionsFromPersisted('[]')).toEqual([]);
  });

  it('skips malformed definitions and duplicate ids while preserving order', () => {
    expect(
      alertDefinitionsFromPersisted(
        JSON.stringify([
          healthAlert,
          { ...healthAlert, label: 'Duplicate' },
          { ...textAlert, pattern: '\n' },
          textAlert,
        ]),
      ),
    ).toEqual([healthAlert, textAlert]);
  });

  it('bounds the number of retained definitions', () => {
    const definitions = Array.from({ length: MAX_ALERT_DEFINITIONS + 4 }, (_, index) => ({
      ...healthAlert,
      id: `alert-${index}`,
    }));

    expect(alertDefinitionsFromPersisted(JSON.stringify(definitions))).toHaveLength(MAX_ALERT_DEFINITIONS);
  });

  it('migrates the legacy low-HP settings field by field', () => {
    expect(
      legacyAlertDefinitionsFromPersisted(
        JSON.stringify({
          lowHpEnabled: false,
          lowHpThresholdPercent: 31,
          soundEnabled: false,
          notificationEnabled: true,
        }),
      ),
    ).toEqual([
      {
        ...DEFAULT_ALERT_DEFINITIONS[0],
        enabled: false,
        thresholdPercent: 31,
        soundEnabled: false,
        notificationEnabled: true,
      },
    ]);
  });

  it('falls back field by field when legacy settings are malformed', () => {
    expect(
      legacyAlertDefinitionsFromPersisted(
        JSON.stringify({
          lowHpEnabled: 'yes',
          lowHpThresholdPercent: 101,
          soundEnabled: false,
          notificationEnabled: null,
        }),
      ),
    ).toEqual([
      {
        ...DEFAULT_ALERT_DEFINITIONS[0],
        soundEnabled: false,
      },
    ]);
  });

  it('prefers the generalized store once it exists, including an empty list', () => {
    const getItem = vi.fn((key: string) => {
      if (key === 'imp.alerts') return '[]';
      if (key === 'imp.alert-settings') {
        return JSON.stringify({ lowHpEnabled: true });
      }
      return null;
    });

    expect(loadAlertDefinitions({ getItem })).toEqual([]);
    expect(getItem).toHaveBeenCalledTimes(1);
  });

  it('uses legacy settings only while the generalized store is absent', () => {
    const getItem = vi.fn((key: string) => {
      if (key === 'imp.alerts') return null;
      if (key === 'imp.alert-settings') {
        return JSON.stringify({
          lowHpEnabled: false,
          lowHpThresholdPercent: 40,
          soundEnabled: true,
          notificationEnabled: false,
        });
      }
      return null;
    });

    expect(loadAlertDefinitions({ getItem })).toEqual([
      {
        ...DEFAULT_ALERT_DEFINITIONS[0],
        enabled: false,
        thresholdPercent: 40,
        notificationEnabled: false,
      },
    ]);
  });

  it('does not commit state when persistence fails', () => {
    const commit = vi.fn();

    expect(commitAlertDefinitions([healthAlert], commit, null)).toBe(false);
    expect(commit).not.toHaveBeenCalled();
  });

  it('rejects saving more than the definition limit', () => {
    const setItem = vi.fn();
    const definitions = Array.from({ length: MAX_ALERT_DEFINITIONS + 1 }, (_, index) => ({
      ...healthAlert,
      id: `alert-${index}`,
    }));

    expect(saveAlertDefinitions(definitions, { setItem })).toBe(false);
    expect(setItem).not.toHaveBeenCalled();
  });

  it('round-trips target-health threshold definitions', () => {
    const targetHealth: AlertDefinition = {
      id: 'target-health-20',
      kind: 'vital',
      label: 'Finish target',
      enabled: true,
      vital: 'target-health',
      thresholdPercent: 20,
      soundEnabled: false,
      notificationEnabled: true,
    };

    expect(alertDefinitionsFromPersisted(serializeAlertDefinitions([targetHealth]))).toEqual([targetHealth]);
  });

  it('validates vital thresholds for the management UI', () => {
    expect(alertThresholdError(1)).toBeNull();
    expect(alertThresholdError(25)).toBeNull();
    expect(alertThresholdError(100)).toBeNull();
    expect(alertThresholdError(0)).not.toBeNull();
    expect(alertThresholdError(101)).not.toBeNull();
    expect(alertThresholdError(12.5)).not.toBeNull();
    expect(alertThresholdError(Number.NaN)).not.toBeNull();
  });

  it('validates bounded safe literal text patterns', () => {
    expect(textPatternError('tells you')).toBeNull();
    expect(textPatternError('   ')).not.toBeNull();
    expect(textPatternError('hello\nworld')).not.toBeNull();
  });
});
