import { describe, expect, it } from 'vitest';

import {
  compactWindowSize,
  expandedWindowSize,
  EXPANDED_SETTINGS_WINDOW_SIZE,
  settingsWindowSize,
} from '../src/lib/window.ts';

describe('compactWindowSize', () => {
  it('rounds rendered content up to a stable logical window size', () => {
    const size = compactWindowSize(341.1, 42.1);

    expect(size).toEqual({ width: 342, height: 43 });
    expect(compactWindowSize(size.width, size.height)).toEqual(size);
  });

  it('keeps measured compact dimensions within the owned window bounds', () => {
    expect(compactWindowSize(1, 1)).toEqual({ width: 280, height: 40 });
    expect(compactWindowSize(1000, 1000)).toEqual({ width: 560, height: 160 });
  });
});

describe('expandedWindowSize', () => {
  it('grows for a target and shrinks after target removal', () => {
    const withoutTarget = expandedWindowSize(false);
    const withTarget = expandedWindowSize(true);
    const afterRemoval = expandedWindowSize(false);

    expect(withoutTarget.width).toBe(320);
    expect(withTarget.width).toBe(320);
    expect(withoutTarget.height).toBeLessThan(withTarget.height);
    expect(afterRemoval).toEqual(withoutTarget);
  });

  it('keeps expanded settings at the established size', () => {
    expect(EXPANDED_SETTINGS_WINDOW_SIZE).toEqual({ width: 320, height: 215 });
  });
});

describe('settingsWindowSize', () => {
  it('provides deliberate temporary space for compact-mode settings', () => {
    expect(settingsWindowSize(280, 180)).toEqual({ width: 320, height: 180 });
    expect(settingsWindowSize(440.2, 319.1)).toEqual({ width: 441, height: 320 });
    expect(settingsWindowSize(800, 800)).toEqual({ width: 560, height: 420 });
  });
});
