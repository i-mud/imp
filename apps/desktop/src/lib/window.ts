import { getCurrentWindow, LogicalSize } from '@tauri-apps/api/window';

export interface HudWindowSize {
  readonly width: number;
  readonly height: number;
}

export const EXPANDED_WINDOW_SIZE: HudWindowSize = { width: 320, height: 210 };

const COMPACT_MIN_WIDTH = 280;
const COMPACT_MAX_WIDTH = 560;
const COMPACT_MIN_HEIGHT = 40;
const COMPACT_MAX_HEIGHT = 160;

export function compactWindowSize(contentWidth: number, contentHeight: number): HudWindowSize {
  return {
    width: Math.min(Math.max(Math.ceil(contentWidth), COMPACT_MIN_WIDTH), COMPACT_MAX_WIDTH),
    height: Math.min(Math.max(Math.ceil(contentHeight), COMPACT_MIN_HEIGHT), COMPACT_MAX_HEIGHT),
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
