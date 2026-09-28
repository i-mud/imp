import { invoke } from '@tauri-apps/api/core';
import { isPermissionGranted, requestPermission, sendNotification } from '@tauri-apps/plugin-notification';

import alertSoundUrl from '../../assets/low-hp.wav?url';
import type { AlertEvent, DesktopAlertEffects } from './effects.ts';

let alertAudio: HTMLAudioElement | null = null;
let permissionRequested = false;

function isTauriRuntime(): boolean {
  return '__TAURI_INTERNALS__' in globalThis;
}

async function alertsMuted(): Promise<boolean> {
  if (!isTauriRuntime()) return false;

  try {
    return await invoke<boolean>('alerts_muted');
  } catch {
    return false;
  }
}

async function playAlertSound(): Promise<void> {
  if (typeof Audio === 'undefined') return;

  try {
    alertAudio ??= new Audio(alertSoundUrl);
    alertAudio.currentTime = 0;
    await alertAudio.play();
  } catch {
    // Alert delivery is best-effort and must never break the HUD.
  }
}

async function notificationPermissionGranted(): Promise<boolean> {
  if (!isTauriRuntime()) return false;

  try {
    if (await isPermissionGranted()) return true;
    if (permissionRequested) return false;

    permissionRequested = true;
    return (await requestPermission()) === 'granted';
  } catch {
    return false;
  }
}

async function showAlertNotification(event: AlertEvent): Promise<void> {
  try {
    if (!(await notificationPermissionGranted())) return;

    sendNotification({
      title: 'Imp',
      body: event.body,
    });
  } catch {
    // Alert delivery is best-effort and must never break the HUD.
  }
}

export const DESKTOP_ALERT_EFFECTS: DesktopAlertEffects = {
  alertsMuted,
  playAlertSound,
  showAlertNotification,
};
