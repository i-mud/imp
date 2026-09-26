import type { AlertDefinition, TextAlertDefinition, VitalAlertDefinition } from './definitions.ts';

export interface AlertEvent {
  readonly alertId: string;
  readonly body: string;
}

export interface DesktopAlertEffects {
  alertsMuted?(): Promise<boolean> | boolean;
  playAlertSound(event: AlertEvent): Promise<void> | void;
  showAlertNotification(event: AlertEvent): Promise<void> | void;
}

export function vitalAlertEvent(
  definition: VitalAlertDefinition,
  characterName: string,
  percent: number,
): AlertEvent {
  return {
    alertId: definition.id,
    body: `${definition.label} — ${characterName} is at ${Math.round(percent)}%`,
  };
}

export function textAlertEvent(definition: TextAlertDefinition): AlertEvent {
  return {
    alertId: definition.id,
    body: definition.label,
  };
}

export async function dispatchAlert(
  event: AlertEvent,
  definition: AlertDefinition,
  effects: DesktopAlertEffects,
): Promise<void> {
  if (!definition.enabled) return;
  if (await effects.alertsMuted?.()) return;

  const pending: Promise<unknown>[] = [];

  if (definition.soundEnabled) {
    pending.push(Promise.resolve().then(() => effects.playAlertSound(event)));
  }

  if (definition.notificationEnabled) {
    pending.push(Promise.resolve().then(() => effects.showAlertNotification(event)));
  }

  await Promise.allSettled(pending);
}
