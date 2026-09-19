import type { AlertSettings } from './settings.ts';

export interface LowHpAlertEvent {
  readonly characterName: string;
  readonly hpPercent: number;
}

export interface DesktopAlertEffects {
  playLowHpSound(event: LowHpAlertEvent): Promise<void> | void;
  showLowHpNotification(event: LowHpAlertEvent): Promise<void> | void;
}

export async function dispatchLowHpAlert(
  event: LowHpAlertEvent,
  settings: AlertSettings,
  effects: DesktopAlertEffects,
): Promise<void> {
  if (!settings.lowHpEnabled) return;

  const pending: Promise<unknown>[] = [];

  if (settings.soundEnabled) {
    pending.push(Promise.resolve().then(() => effects.playLowHpSound(event)));
  }
  if (settings.notificationEnabled) {
    pending.push(Promise.resolve().then(() => effects.showLowHpNotification(event)));
  }

  await Promise.allSettled(pending);
}
