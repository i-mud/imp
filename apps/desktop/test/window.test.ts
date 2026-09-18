import { describe, expect, it } from 'vitest';

import { compactWindowSize, EXPANDED_WINDOW_SIZE } from '../src/lib/window.ts';

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

  it('keeps the canonical expanded size stable across mode changes', () => {
    expect(EXPANDED_WINDOW_SIZE).toEqual({ width: 320, height: 210 });
  });
});
