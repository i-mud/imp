import { describe, expect, it, vi } from 'vitest';

import { MockActionSink } from '../src/lib/action/mock.ts';
import { RelayActionSink } from '../src/lib/action/relay.ts';

const context = { session: 'session1', foreground: 2, connection: 3 } as const;

class FakeWebSocket {
  private readonly listeners = new Map<string, Array<(event: Event) => void>>();
  readonly sent: string[] = [];
  closed = false;

  addEventListener(type: string, listener: (event: Event) => void): void {
    const listeners = this.listeners.get(type) ?? [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  close(): void {
    this.closed = true;
  }

  send(data: string): void {
    this.sent.push(data);
  }

  emit(type: string, event: Event): void {
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

function createSink(sockets: FakeWebSocket[], timeoutMs = 5_000): RelayActionSink {
  return new RelayActionSink({
    url: 'ws://127.0.0.1:8787/action',
    timeoutMs,
    webSocketFactory: () => {
      const socket = new FakeWebSocket();
      sockets.push(socket);
      return socket;
    },
  });
}

describe('RelayActionSink', () => {
  it('validates before opening a socket', async () => {
    const sockets: FakeWebSocket[] = [];
    const sink = createSink(sockets);

    await expect(sink.send(context, 'look\nnorth')).resolves.toEqual({
      status: 'rejected',
      detail: 'Command must be 1..512 printable ASCII characters.',
    });
    expect(sockets).toHaveLength(0);
  });

  it('uses one ephemeral socket and returns the relay result', async () => {
    const sockets: FakeWebSocket[] = [];
    const sink = createSink(sockets);
    const result = sink.send(context, 'say hello');
    const socket = sockets[0];
    if (socket === undefined) throw new Error('expected action socket');

    socket.emit('open', new Event('open'));
    expect(JSON.parse(socket.sent[0] ?? '')).toEqual({
      type: 'action',
      protocol: 2,
      context,
      command: 'say hello',
    });
    socket.emit(
      'message',
      new MessageEvent('message', {
        data: JSON.stringify({ type: 'action-result', protocol: 2, status: 'forwarded', detail: null }),
      }),
    );

    await expect(result).resolves.toEqual({ status: 'forwarded', detail: null });
    expect(socket.closed).toBe(true);
  });

  it('sends authentication before the action when configured', async () => {
    const sockets: FakeWebSocket[] = [];
    const sink = new RelayActionSink({
      url: 'wss://example.test/action',
      authenticationToken: 'pairing-token',
      webSocketFactory: () => {
        const socket = new FakeWebSocket();
        sockets.push(socket);
        return socket;
      },
    });

    const result = sink.send(context, 'look');
    const socket = sockets[0];
    if (socket === undefined) throw new Error('expected action socket');

    socket.emit('open', new Event('open'));

    expect(socket.sent.map((frame) => JSON.parse(frame))).toEqual([
      {
        type: 'auth',
        token: 'pairing-token',
      },
      {
        type: 'action',
        protocol: 2,
        context,
        command: 'look',
      },
    ]);

    socket.emit(
      'message',
      new MessageEvent('message', {
        data: JSON.stringify({
          type: 'action-result',
          protocol: 2,
          status: 'forwarded',
          detail: null,
        }),
      }),
    );

    await expect(result).resolves.toEqual({
      status: 'forwarded',
      detail: null,
    });
  });

  it('returns unknown on timeout without retrying', async () => {
    vi.useFakeTimers();
    try {
      const sockets: FakeWebSocket[] = [];
      const result = createSink(sockets, 50).send(context, 'north');
      vi.advanceTimersByTime(50);

      await expect(result).resolves.toEqual({ status: 'unknown', detail: 'Relay action timed out.' });
      expect(sockets).toHaveLength(1);
      expect(sockets[0]?.closed).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('MockActionSink', () => {
  it('records valid commands and returns its deterministic result', async () => {
    const sink = new MockActionSink({ status: 'rejected', detail: 'demo rejection' });

    await expect(sink.send(context, 'look')).resolves.toEqual({
      status: 'rejected',
      detail: 'demo rejection',
    });
    expect(sink.sent).toEqual([{ context, command: 'look' }]);
  });
});
