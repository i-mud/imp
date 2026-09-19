import { describe, expect, it, vi } from 'vitest';

import { dispatchLowHpAlert } from '../src/lib/alerts/effects.ts';
import { DEFAULT_ALERT_SETTINGS } from '../src/lib/alerts/settings.ts';

const event = { characterName: 'Ivrin', hpPercent: 24 } as const;

describe('dispatchLowHpAlert', () => {
  it('runs sound and notification once when both are enabled', async () => {
    const playLowHpSound = vi.fn();
    const showLowHpNotification = vi.fn();

    await dispatchLowHpAlert(event, DEFAULT_ALERT_SETTINGS, { playLowHpSound, showLowHpNotification });

    expect(playLowHpSound).toHaveBeenCalledOnce();
    expect(showLowHpNotification).toHaveBeenCalledOnce();
  });

  it('suppresses both effects when the low-HP alert is disabled', async () => {
    const playLowHpSound = vi.fn();
    const showLowHpNotification = vi.fn();

    await dispatchLowHpAlert(
      event,
      { ...DEFAULT_ALERT_SETTINGS, lowHpEnabled: false },
      { playLowHpSound, showLowHpNotification },
    );

    expect(playLowHpSound).not.toHaveBeenCalled();
    expect(showLowHpNotification).not.toHaveBeenCalled();
  });

  it('can disable sound without disabling the notification', async () => {
    const playLowHpSound = vi.fn();
    const showLowHpNotification = vi.fn();

    await dispatchLowHpAlert(
      event,
      { ...DEFAULT_ALERT_SETTINGS, soundEnabled: false },
      { playLowHpSound, showLowHpNotification },
    );

    expect(playLowHpSound).not.toHaveBeenCalled();
    expect(showLowHpNotification).toHaveBeenCalledOnce();
  });

  it('can disable the notification without disabling sound', async () => {
    const playLowHpSound = vi.fn();
    const showLowHpNotification = vi.fn();

    await dispatchLowHpAlert(
      event,
      { ...DEFAULT_ALERT_SETTINGS, notificationEnabled: false },
      { playLowHpSound, showLowHpNotification },
    );

    expect(playLowHpSound).toHaveBeenCalledOnce();
    expect(showLowHpNotification).not.toHaveBeenCalled();
  });

  it('does not let one failed effect block the other or reject state handling', async () => {
    const playLowHpSound = vi.fn(() => Promise.reject(new Error('audio failed')));
    const showLowHpNotification = vi.fn();

    await expect(
      dispatchLowHpAlert(event, DEFAULT_ALERT_SETTINGS, { playLowHpSound, showLowHpNotification }),
    ).resolves.toBeUndefined();
    expect(showLowHpNotification).toHaveBeenCalledOnce();
  });
});
