import { describe, expect, it, vi } from 'vitest';

import { MockStateSource } from '../src/lib/source/mock.ts';
import type { SourceEvent } from '../src/lib/source/types.ts';

describe('MockStateSource', () => {
  it('emits bounded decoded snapshots from a fixed seed', () => {
    vi.useFakeTimers();
    try {
      const events: SourceEvent[] = [];
      const source = new MockStateSource({ seed: 42, intervalMs: 600 });
      source.start((event) => events.push(event));
      vi.advanceTimersByTime(1_200);

      const snapshots = events.filter(
        (event): event is Extract<SourceEvent, { kind: 'snapshot' }> => event.kind === 'snapshot',
      );
      expect(events.some((event) => event.kind === 'protocol-error')).toBe(true);
      expect(snapshots.length).toBeGreaterThan(1);
      for (const snapshot of snapshots) {
        const character = snapshot.state.character;
        expect(character).not.toBeNull();
        expect(character?.hp?.current).toBeGreaterThanOrEqual(0);
        expect(character?.hp?.current).toBeLessThanOrEqual(1_200);
        expect(character?.mana?.current).toBeGreaterThanOrEqual(0);
        expect(character?.mana?.current).toBeLessThanOrEqual(800);
        expect(character?.moves?.current).toBeGreaterThanOrEqual(0);
        expect(character?.moves?.current).toBeLessThanOrEqual(500);
      }
      source.stop();
    } finally {
      vi.useRealTimers();
    }
  });

  it('halts emissions when stopped', () => {
    vi.useFakeTimers();
    try {
      const events: SourceEvent[] = [];
      const source = new MockStateSource({ seed: 7, intervalMs: 600 });
      source.start((event) => events.push(event));
      source.stop();
      const beforeAdvance = events.length;
      vi.advanceTimersByTime(1_800);

      expect(events).toHaveLength(beforeAdvance);
    } finally {
      vi.useRealTimers();
    }
  });
});
