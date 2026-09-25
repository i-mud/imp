import { describe, expect, it, vi } from 'vitest';

import { dispatchAlert, vitalAlertEvent } from '../src/lib/alerts/effects.ts';
import type { VitalAlertDefinition } from '../src/lib/alerts/definitions.ts';

const definition: VitalAlertDefinition = {
  id: 'low-mana',
  kind: 'vital',
  label: 'Low mana',
  enabled: true,
  vital: 'mana',
  thresholdPercent: 25,
  soundEnabled: true,
  notificationEnabled: true,
};

const event = { body: 'Low mana — Ivrin is at 24%' };

describe('dispatchAlert', () => {
  it('runs sound and notification once when both are enabled', async () => {
    const playAlertSound = vi.fn();
    const showAlertNotification = vi.fn();

    await dispatchAlert(event, definition, {
      playAlertSound,
      showAlertNotification,
    });

    expect(playAlertSound).toHaveBeenCalledOnce();
    expect(showAlertNotification).toHaveBeenCalledOnce();
  });

  it('suppresses all effects while alerts are temporarily muted', async () => {
    const alertsMuted = vi.fn(() => true);
    const playAlertSound = vi.fn();
    const showAlertNotification = vi.fn();

    await dispatchAlert(event, definition, {
      alertsMuted,
      playAlertSound,
      showAlertNotification,
    });

    expect(alertsMuted).toHaveBeenCalledOnce();
    expect(playAlertSound).not.toHaveBeenCalled();
    expect(showAlertNotification).not.toHaveBeenCalled();
  });

  it('suppresses effects for a disabled definition', async () => {
    const playAlertSound = vi.fn();
    const showAlertNotification = vi.fn();

    await dispatchAlert(event, { ...definition, enabled: false }, { playAlertSound, showAlertNotification });

    expect(playAlertSound).not.toHaveBeenCalled();
    expect(showAlertNotification).not.toHaveBeenCalled();
  });

  it('configures sound and notification independently per definition', async () => {
    const playAlertSound = vi.fn();
    const showAlertNotification = vi.fn();

    await dispatchAlert(
      event,
      { ...definition, soundEnabled: false },
      { playAlertSound, showAlertNotification },
    );

    expect(playAlertSound).not.toHaveBeenCalled();
    expect(showAlertNotification).toHaveBeenCalledOnce();
  });

  it('does not let one failed effect block the other or reject state handling', async () => {
    const playAlertSound = vi.fn(() => Promise.reject(new Error('audio failed')));
    const showAlertNotification = vi.fn();

    await expect(
      dispatchAlert(event, definition, {
        playAlertSound,
        showAlertNotification,
      }),
    ).resolves.toBeUndefined();

    expect(showAlertNotification).toHaveBeenCalledOnce();
  });
});

describe('vitalAlertEvent', () => {
  it('formats a generalized vital notification from the configured label', () => {
    expect(vitalAlertEvent(definition, 'Ivrin', 24.4)).toEqual({
      body: 'Low mana — Ivrin is at 24%',
    });
  });
});
