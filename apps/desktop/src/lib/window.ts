import { getCurrentWindow, LogicalSize } from '@tauri-apps/api/window';

export interface HudWindowSize {
  readonly width: number;
  readonly height: number;
}

// Heights fit the titlebar, content padding, three-vital stack, and the existing target block when present.
const EXPANDED_NO_TARGET_WINDOW_SIZE: HudWindowSize = { width: 320, height: 142 };
const EXPANDED_WITH_TARGET_WINDOW_SIZE: HudWindowSize = { width: 320, height: 190 };
export const EXPANDED_SETTINGS_WINDOW_SIZE: HudWindowSize = { width: 320, height: 215 };

export function expandedWindowSize(hasTarget: boolean): HudWindowSize {
  return hasTarget ? EXPANDED_WITH_TARGET_WINDOW_SIZE : EXPANDED_NO_TARGET_WINDOW_SIZE;
}

const COMPACT_MIN_WIDTH = 280;
const COMPACT_MAX_WIDTH = 560;
const COMPACT_MIN_HEIGHT = 40;
const COMPACT_MAX_HEIGHT = 160;
const SETTINGS_MIN_WIDTH = 320;
const SETTINGS_MAX_WIDTH = COMPACT_MAX_WIDTH;
const SETTINGS_MIN_HEIGHT = COMPACT_MIN_HEIGHT;
const SETTINGS_MAX_HEIGHT = 420;

export function compactWindowSize(contentWidth: number, contentHeight: number): HudWindowSize {
  return {
    width: Math.min(Math.max(Math.ceil(contentWidth), COMPACT_MIN_WIDTH), COMPACT_MAX_WIDTH),
    height: Math.min(Math.max(Math.ceil(contentHeight), COMPACT_MIN_HEIGHT), COMPACT_MAX_HEIGHT),
  };
}

export function settingsWindowSize(contentWidth: number, contentHeight: number): HudWindowSize {
  return {
    width: Math.min(Math.max(Math.ceil(contentWidth), SETTINGS_MIN_WIDTH), SETTINGS_MAX_WIDTH),
    height: Math.min(Math.max(Math.ceil(contentHeight), SETTINGS_MIN_HEIGHT), SETTINGS_MAX_HEIGHT),
  };
}

export function closeWindow(): void {
  if ('__TAURI_INTERNALS__' in globalThis) void getCurrentWindow().close();
}

export function resizeHudWindow(size: HudWindowSize): void {
  if ('__TAURI_INTERNALS__' in globalThis) void getCurrentWindow().setSize(new LogicalSize(size));
}

export function setAlwaysOnTop(alwaysOnTop: boolean): void {
  if ('__TAURI_INTERNALS__' in globalThis) void getCurrentWindow().setAlwaysOnTop(alwaysOnTop);
}
