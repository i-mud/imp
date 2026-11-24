import { getCurrentWindow, LogicalSize } from '@tauri-apps/api/window';

export interface HudWindowSize {
  readonly width: number;
  readonly height: number;
}

export const EXPANDED_WINDOW_WIDTH = 320;

const COMPACT_MIN_WIDTH = 280;
const COMPACT_MAX_WIDTH = 560;
const COMPACT_MIN_HEIGHT = 30;
const COMPACT_MAX_HEIGHT = 160;
const COMPACT_PANEL_MIN_HEIGHT = COMPACT_MIN_HEIGHT;
const COMPACT_PANEL_MAX_HEIGHT = 420;
export function actionDialogWindowSize(width: number, contentHeight: number): HudWindowSize {
  return {
    width: Math.min(Math.max(Math.ceil(width), COMPACT_MIN_WIDTH), COMPACT_MAX_WIDTH),
    height: Math.max(Math.ceil(contentHeight), 1),
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
