import { describe, expect, it, vi } from 'vitest';

import { RelayStateSource, type WebSocketLike } from '../src/lib/source/relay.ts';
import type { SourceEvent } from '../src/lib/source/types.ts';

class FakeWebSocket implements WebSocketLike {
  private readonly listeners = new Map<string, Array<(event: Event) => void>>();
  readonly url: string;
  closed = false;

  constructor(url: string) {
    this.url = url;
  }

  addEventListener(type: string, listener: (event: Event) => void): void {
    const listeners = this.listeners.get(type) ?? [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  close(): void {
    this.closed = true;
  }

  emit(type: string, event: Event): void {
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

function createSource(sockets: FakeWebSocket[]): RelayStateSource {
  return new RelayStateSource({
    url: 'ws://127.0.0.1:8787/state',
    reconnect: { initialDelayMs: 100, maxDelayMs: 1_000, factor: 2 },
    webSocketFactory: (url) => {
      const socket = new FakeWebSocket(url);
      sockets.push(socket);
      return socket;
    },
  });
}

const textFrame = JSON.stringify({
  type: 'text',
  protocol: 2,
  context: { session: 'session1', foreground: 1, connection: 1 },
  at: 150,
  text: 'The ancient troll snarls.',
});

const snapshotFrame = JSON.stringify({
  type: 'snapshot',
  protocol: 2,
  seq: 1,
  at: 100,
  context: { session: 'session1', foreground: 1, connection: 1 },
  state: {
    character: { name: 'Aria', hp: { current: 90, max: 100 }, mana: null, moves: null },
    target: null,
  },
});

describe('RelayStateSource', () => {
  it('turns valid relay frames into source events', () => {
    const sockets: FakeWebSocket[] = [];
    const events: SourceEvent[] = [];
    const source = createSource(sockets);
    source.start((event) => events.push(event));
    const socket = sockets[0];
    if (socket === undefined) throw new Error('expected relay socket');
    socket.emit('open', new Event('open'));
    socket.emit('message', new MessageEvent('message', { data: snapshotFrame }));

    expect(events).toContainEqual({ kind: 'connection', phase: 'connected', detail: null });
    expect(events).toContainEqual({
      kind: 'snapshot',
      seq: 1,
      at: 100,
      context: { session: 'session1', foreground: 1, connection: 1 },
      state: {
        character: { name: 'Aria', hp: { current: 90, max: 100 }, mana: null, moves: null },
        target: null,
      },
    });
    source.stop();
  });

  it('emits transient received text without turning it into state', () => {
    const sockets: FakeWebSocket[] = [];
    const events: SourceEvent[] = [];
    const source = createSource(sockets);
    source.start((event) => events.push(event));
    const socket = sockets[0];
    if (socket === undefined) throw new Error('expected relay socket');

    socket.emit('message', new MessageEvent('message', { data: textFrame }));

    expect(events).toContainEqual({
      kind: 'text',
      context: { session: 'session1', foreground: 1, connection: 1 },
      at: 150,
      text: 'The ancient troll snarls.',
    });

    source.stop();
  });

  it('emits decode failures without emitting snapshots', () => {
    const sockets: FakeWebSocket[] = [];
    const events: SourceEvent[] = [];
    const source = createSource(sockets);
    source.start((event) => events.push(event));
    const socket = sockets[0];
    if (socket === undefined) throw new Error('expected relay socket');
    socket.emit('message', new MessageEvent('message', { data: '{"type":"snapshot"}' }));

    expect(events.some((event) => event.kind === 'protocol-error')).toBe(true);
    expect(events.some((event) => event.kind === 'snapshot')).toBe(false);
    source.stop();
  });

  it('closes a socket after a protocol violation', () => {
    const sockets: FakeWebSocket[] = [];
    const source = createSource(sockets);
    source.start(() => undefined);
    const socket = sockets[0];
    if (socket === undefined) throw new Error('expected relay socket');

    socket.emit('message', new MessageEvent('message', { data: '{"type":"snapshot"}' }));

    expect(socket.closed).toBe(true);
    source.stop();
  });

  it('closes and reconnects after a binary frame without applying state', () => {
    vi.useFakeTimers();
    const random = vi.spyOn(Math, 'random').mockReturnValue(0.5);
    try {
      const sockets: FakeWebSocket[] = [];
      const events: SourceEvent[] = [];
      const source = createSource(sockets);
      source.start((event) => events.push(event));
      const socket = sockets[0];
      if (socket === undefined) throw new Error('expected relay socket');

      socket.emit('message', new MessageEvent('message', { data: new ArrayBuffer(1) }));

      expect(socket.closed).toBe(true);
      expect(events).toContainEqual({
        kind: 'protocol-error',
        error: {
          code: 'invalid_field',
          path: '<frame>',
          message: 'expected a text WebSocket frame',
        },
      });
      expect(events.some((event) => event.kind === 'snapshot')).toBe(false);

      socket.emit('close', new Event('close'));
      vi.advanceTimersByTime(100);
      expect(sockets).toHaveLength(2);
      source.stop();
    } finally {
      random.mockRestore();
      vi.useRealTimers();
    }
  });

  it('silently ignores unknown message types', () => {
    const sockets: FakeWebSocket[] = [];
    const events: SourceEvent[] = [];
    const source = createSource(sockets);
    source.start((event) => events.push(event));
    const socket = sockets[0];
    if (socket === undefined) throw new Error('expected relay socket');
    const beforeFrame = events.length;
    socket.emit('message', new MessageEvent('message', { data: '{"type":"future","protocol":2,"at":1}' }));

    expect(events).toHaveLength(beforeFrame);
    expect(socket.closed).toBe(false);
    source.stop();
  });

  it('reconnects with backoff after close and stops retrying when stopped', () => {
    vi.useFakeTimers();
    const random = vi.spyOn(Math, 'random').mockReturnValue(0.5);
    try {
      const sockets: FakeWebSocket[] = [];
      const events: SourceEvent[] = [];
      const source = createSource(sockets);
      source.start((event) => events.push(event));
      const socket = sockets[0];
      if (socket === undefined) throw new Error('expected relay socket');
      socket.emit('close', new Event('close'));

      expect(events).toContainEqual({
        kind: 'connection',
        phase: 'reconnecting',
        detail: 'Relay connection closed.',
      });
      vi.advanceTimersByTime(100);
      expect(sockets).toHaveLength(2);
      source.stop();
      vi.advanceTimersByTime(1_000);
      expect(sockets).toHaveLength(2);
    } finally {
      random.mockRestore();
      vi.useRealTimers();
    }
  });

  it('prefers a supplied diagnostic detail over the generic reconnect message', () => {
    const sockets: FakeWebSocket[] = [];
    const events: SourceEvent[] = [];
    const source = new RelayStateSource({
      url: 'ws://127.0.0.1:8787/state',
      reconnect: { initialDelayMs: 100, maxDelayMs: 1_000, factor: 2 },
      webSocketFactory: (url) => {
        const socket = new FakeWebSocket(url);
        sockets.push(socket);
        return socket;
      },
      diagnosticDetail: () => 'SSH tunnel unavailable.',
    });
    source.start((event) => events.push(event));
    const socket = sockets[0];
    if (socket === undefined) throw new Error('expected relay socket');
    socket.emit('close', new Event('close'));

    expect(events).toContainEqual({
      kind: 'connection',
      phase: 'reconnecting',
      detail: 'SSH tunnel unavailable.',
    });
    source.stop();
  });
});
