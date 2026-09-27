import { describe, expect, it, vi } from 'vitest';

import { HudStore } from '../src/lib/hud/store.svelte.ts';
import type { SourceEvent, StateSource } from '../src/lib/source/types.ts';

class FakeSource implements StateSource {
  readonly id = 'fake';
  readonly label = 'Fake';

  private listener: ((event: SourceEvent) => void) | null = null;

  start(listener: (event: SourceEvent) => void): void {
    this.listener = listener;
  }

  stop = vi.fn();

  emit(event: SourceEvent): void {
    this.listener?.(event);
  }
}

const textEvent = {
  kind: 'text',
  context: { session: 'session-1', foreground: 2, connection: 3 },
  at: 10,
  text: 'Aria tells you hello.',
} as const;

describe('HudStore transient text', () => {
  it('delivers text synchronously without changing or retaining the HUD model', () => {
    const store = new HudStore();
    const source = new FakeSource();
    const seen: string[] = [];

    store.subscribeText((event) => seen.push(event.text));
    store.attach(source);

    const before = store.model;

    source.emit(textEvent);

    expect(seen).toEqual(['Aria tells you hello.']);
    expect(store.model).toBe(before);

    const lateSeen: string[] = [];
    store.subscribeText((event) => lateSeen.push(event.text));

    expect(lateSeen).toEqual([]);
  });

  it('delivers repeated identical lines as separate events', () => {
    const store = new HudStore();
    const source = new FakeSource();
    const seen: string[] = [];

    store.subscribeText((event) => seen.push(event.text));
    store.attach(source);

    source.emit(textEvent);
    source.emit(textEvent);

    expect(seen).toEqual(['Aria tells you hello.', 'Aria tells you hello.']);
  });

  it('stops delivering after a transient subscriber unsubscribes', () => {
    const store = new HudStore();
    const source = new FakeSource();
    const seen: string[] = [];

    const unsubscribe = store.subscribeText((event) => seen.push(event.text));
    store.attach(source);

    source.emit(textEvent);
    unsubscribe();
    source.emit(textEvent);

    expect(seen).toEqual(['Aria tells you hello.']);
  });
});
