import { getCurrentWindow, LogicalSize } from '@tauri-apps/api/window';

export interface HudWindowSize {
  readonly width: number;
  readonly height: number;
}

export const ACTION_STRIP_HEIGHT = 50;
const EXPANDED_NO_TARGET_WINDOW_SIZE: HudWindowSize = { width: 320, height: 142 };
const EXPANDED_WITH_TARGET_WINDOW_SIZE: HudWindowSize = { width: 320, height: 190 };
export const EXPANDED_SETTINGS_WINDOW_SIZE: HudWindowSize = { width: 320, height: 260 };
const ACTION_DIALOG_HEIGHT = 400;

export function expandedWindowSize(hasTarget: boolean, hasActions: boolean): HudWindowSize {
  const base = hasTarget ? EXPANDED_WITH_TARGET_WINDOW_SIZE : EXPANDED_NO_TARGET_WINDOW_SIZE;
  return hasActions ? { width: base.width, height: base.height + ACTION_STRIP_HEIGHT } : base;
}

const COMPACT_MIN_WIDTH = 280;
const COMPACT_MAX_WIDTH = 560;
const COMPACT_MIN_HEIGHT = 40;
const COMPACT_MAX_HEIGHT = 160;
const COMPACT_PANEL_MIN_HEIGHT = COMPACT_MIN_HEIGHT;
const COMPACT_PANEL_MAX_HEIGHT = 420;
export function actionDialogWindowSize(width: number): HudWindowSize {
  return {
    width: Math.min(Math.max(Math.ceil(width), COMPACT_MIN_WIDTH), COMPACT_MAX_WIDTH),
    height: ACTION_DIALOG_HEIGHT,
  };
}

export function compactWindowSize(contentWidth: number, contentHeight: number): HudWindowSize {
  return {
    width: Math.min(Math.max(Math.ceil(contentWidth), COMPACT_MIN_WIDTH), COMPACT_MAX_WIDTH),
    height: Math.min(Math.max(Math.ceil(contentHeight), COMPACT_MIN_HEIGHT), COMPACT_MAX_HEIGHT),
  };
}

export function compactPanelWindowSize(contentWidth: number, contentHeight: number): HudWindowSize {
  return {
    width: Math.min(Math.max(Math.ceil(contentWidth), COMPACT_MIN_WIDTH), COMPACT_MAX_WIDTH),
    height: Math.min(Math.max(Math.ceil(contentHeight), COMPACT_PANEL_MIN_HEIGHT), COMPACT_PANEL_MAX_HEIGHT),
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
