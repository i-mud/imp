import { invoke } from '@tauri-apps/api/core';
import { isPermissionGranted, requestPermission, sendNotification } from '@tauri-apps/plugin-notification';

import lowHpSoundUrl from '../../assets/low-hp.wav?url';
import type { DesktopAlertEffects, LowHpAlertEvent } from './effects.ts';

let lowHpAudio: HTMLAudioElement | null = null;
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

async function playLowHpSound(): Promise<void> {
  if (typeof Audio === 'undefined') return;

  try {
    lowHpAudio ??= new Audio(lowHpSoundUrl);
    lowHpAudio.currentTime = 0;
    await lowHpAudio.play();
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

async function showLowHpNotification(event: LowHpAlertEvent): Promise<void> {
  try {
    if (!(await notificationPermissionGranted())) return;
    sendNotification({
      title: 'TinyScry',
      body: `Low HP — ${event.characterName} is at ${Math.round(event.hpPercent)}%`,
    });
  } catch {
    // Alert delivery is best-effort and must never break the HUD.
  }
}

export const DESKTOP_ALERT_EFFECTS: DesktopAlertEffects = {
  alertsMuted,
  playLowHpSound,
  showLowHpNotification,
};
