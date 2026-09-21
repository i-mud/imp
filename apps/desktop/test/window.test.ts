import { describe, expect, it } from 'vitest';

import {
  actionDialogWindowSize,
  compactPanelWindowSize,
  compactWindowSize,
  expandedWindowSize,
  EXPANDED_SETTINGS_WINDOW_SIZE,
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
  it('preserves the established no-action dimensions', () => {
    expect(expandedWindowSize(false, false)).toEqual({ width: 320, height: 142 });
    expect(expandedWindowSize(true, false)).toEqual({ width: 320, height: 190 });
  });

  it('adds one bounded action strip independent of action count', () => {
    expect(expandedWindowSize(false, true)).toEqual({ width: 320, height: 192 });
    expect(expandedWindowSize(true, true)).toEqual({ width: 320, height: 240 });
  });

  it('pins the expanded settings size', () => {
    expect(EXPANDED_SETTINGS_WINDOW_SIZE).toEqual({ width: 320, height: 260 });
  });
});

describe('actionDialogWindowSize', () => {
  it('uses the expanded HUD width and fixed management height', () => {
    expect(actionDialogWindowSize(320)).toEqual({ width: 320, height: 400 });
  });

  it('preserves compact baseline widths including both supported bounds', () => {
    expect(actionDialogWindowSize(280)).toEqual({ width: 280, height: 400 });
    expect(actionDialogWindowSize(380)).toEqual({ width: 380, height: 400 });
    expect(actionDialogWindowSize(560)).toEqual({ width: 560, height: 400 });
  });

  it('clamps management width to the compact window bounds', () => {
    expect(actionDialogWindowSize(1)).toEqual({ width: 280, height: 400 });
    expect(actionDialogWindowSize(800)).toEqual({ width: 560, height: 400 });
  });
});

describe('compactPanelWindowSize', () => {
  it('preserves a fitting closed compact width', () => {
    const closed = compactWindowSize(347.2, 40);

    expect(compactPanelWindowSize(closed.width, 180)).toEqual({ width: 348, height: 180 });
  });

  it('uses intrinsic panel width within the global compact bounds', () => {
    expect(compactPanelWindowSize(280, 180)).toEqual({ width: 280, height: 180 });
    expect(compactPanelWindowSize(440.2, 319.1)).toEqual({ width: 441, height: 320 });
    expect(compactPanelWindowSize(800, 800)).toEqual({ width: 560, height: 420 });
  });
});
