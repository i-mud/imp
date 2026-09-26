import { describe, expect, it } from 'vitest';

import {
  actionDialogWindowSize,
  compactPanelWindowSize,
  compactWindowSize,
  EXPANDED_SETTINGS_WINDOW_SIZE,
  EXPANDED_WINDOW_SIZE,
  EXPANDED_WITH_TARGET_WINDOW_SIZE,
} from '../src/lib/window.ts';

describe('compactWindowSize', () => {
  it('rounds rendered content up to a stable logical window size', () => {
    const size = compactWindowSize(341.1, 42.1);

    expect(size).toEqual({ width: 342, height: 43 });
    expect(compactWindowSize(size.width, size.height)).toEqual(size);
  });

  it('keeps measured compact dimensions within the owned window bounds', () => {
    expect(compactWindowSize(1, 1)).toEqual({ width: 280, height: 30 });
    expect(compactWindowSize(1000, 1000)).toEqual({ width: 560, height: 160 });
  });
});

describe('expanded window sizes', () => {
  it('preserves the established dimensions', () => {
    expect(EXPANDED_WINDOW_SIZE).toEqual({ width: 320, height: 142 });
    expect(EXPANDED_WITH_TARGET_WINDOW_SIZE).toEqual({ width: 320, height: 190 });
  });

  it('pins the expanded settings size', () => {
    expect(EXPANDED_SETTINGS_WINDOW_SIZE).toEqual({ width: 320, height: 227 });
  });
});

describe('actionDialogWindowSize', () => {
  it('uses the measured management content height', () => {
    expect(actionDialogWindowSize(320, 301.2)).toEqual({
      width: 320,
      height: 302,
    });
  });

  it('preserves compact baseline widths including both supported bounds', () => {
    expect(actionDialogWindowSize(280, 250)).toEqual({ width: 280, height: 250 });
    expect(actionDialogWindowSize(380, 250)).toEqual({ width: 380, height: 250 });
    expect(actionDialogWindowSize(560, 250)).toEqual({ width: 560, height: 250 });
  });

  it('clamps management width while preserving measured height', () => {
    expect(actionDialogWindowSize(1, 275.1)).toEqual({ width: 280, height: 276 });
    expect(actionDialogWindowSize(800, 275.1)).toEqual({ width: 560, height: 276 });
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
