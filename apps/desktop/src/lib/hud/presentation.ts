import { freshnessOf, type HudModel } from './model.ts';

export type DisplayMode = 'expanded' | 'compact';
export type StatusIndicatorState = 'up' | 'stale' | 'down';

const DISPLAY_MODE_STORAGE_KEY = 'tinyscry.display-mode';

export function statusIndicatorOf(model: HudModel): StatusIndicatorState {
  const freshness = freshnessOf(model);

  if (freshness === 'fresh' && model.hasData) return 'up';
  if (freshness === 'feed-stalled') return 'stale';
  return 'down';
}

export function statusLabelOf(model: HudModel): string {
  const freshness = freshnessOf(model);

  if (freshness === 'fresh' && model.hasData) return 'Game data live';
  if (freshness === 'feed-stalled') return 'Game feed stalled';
  if (freshness === 'feed-down') return 'No game feed';
  if (freshness === 'reconnecting') {
    return model.phase === 'connecting' ? 'Connecting to relay' : 'Reconnecting to relay';
  }
  return model.phase === 'connected' ? 'Waiting for game data' : 'Relay unavailable';
}

export function displayModeFromPersisted(value: string | null): DisplayMode {
  return value === 'compact' ? 'compact' : 'expanded';
}

export function loadDisplayMode(): DisplayMode {
  try {
    return displayModeFromPersisted(globalThis.localStorage?.getItem(DISPLAY_MODE_STORAGE_KEY) ?? null);
  } catch {
    return 'expanded';
  }
}

export function saveDisplayMode(mode: DisplayMode): void {
  try {
    globalThis.localStorage?.setItem(DISPLAY_MODE_STORAGE_KEY, mode);
  } catch {
    // Local UI preferences must not interfere with HUD rendering.
  }
}
