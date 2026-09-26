import { decodeServerMessage } from '@tinyscry/protocol';

import type { SourceEvent, StateSource } from './types.ts';

export interface WebSocketLike {
  addEventListener(type: string, listener: (event: Event) => void): void;
  close(): void;
}

export type WebSocketFactory = (url: string) => WebSocketLike;

export interface RelaySourceOptions {
  readonly url: string;
  readonly reconnect: {
    readonly initialDelayMs: number;
    readonly maxDelayMs: number;
    readonly factor: number;
  };
  readonly webSocketFactory?: WebSocketFactory;
  /**
   * Best-known transport-level reason for the current unavailability, e.g.
   * from a managed SSH tunnel supervisor. Called only when a socket-level
   * event has no more specific detail of its own; a `null` return leaves the
   * generic detail in place. This is the seam a managed tunnel enriches
   * without this source knowing anything about SSH.
   */
  readonly diagnosticDetail?: () => string | null;
}

export class RelayStateSource implements StateSource {
  readonly id = 'relay';
  readonly label = 'Relay feed';

  private readonly options: RelaySourceOptions;
  private readonly factory: WebSocketFactory;
  private listener: ((event: SourceEvent) => void) | null = null;
  private socket: WebSocketLike | null = null;
  private reconnectTimer: number | NodeJS.Timeout | null = null;
  private nextDelayMs: number;
  private running = false;

  constructor(options: RelaySourceOptions) {
    this.options = options;
    this.factory = options.webSocketFactory ?? ((url) => new WebSocket(url));
    this.nextDelayMs = options.reconnect.initialDelayMs;
  }

  start(listener: (event: SourceEvent) => void): void {
    this.stop();
    this.listener = listener;
    this.running = true;
    this.nextDelayMs = this.options.reconnect.initialDelayMs;
    this.emit({ kind: 'connection', phase: 'connecting', detail: null });
    this.connect();
  }

  stop(): void {
    this.running = false;
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    const socket = this.socket;
    this.socket = null;
    socket?.close();
    this.listener = null;
  }

  private connect(): void {
    if (!this.running) return;

    let socket: WebSocketLike;
    try {
      socket = this.factory(this.options.url);
    } catch {
      this.reconnect('Unable to create relay connection.');
      return;
    }

    this.socket = socket;
    socket.addEventListener('open', () => {
      if (!this.running || this.socket !== socket) return;
      this.nextDelayMs = this.options.reconnect.initialDelayMs;
      this.emit({ kind: 'connection', phase: 'connected', detail: null });
    });
    socket.addEventListener('message', (event) => {
      if (!this.running || this.socket !== socket || !(event instanceof MessageEvent)) return;
      if (typeof event.data !== 'string') {
        this.emit({
          kind: 'protocol-error',
          error: { code: 'invalid_field', path: '<frame>', message: 'expected a text WebSocket frame' },
        });
        socket.close();
        return;
      }
      this.handleFrame(socket, event.data);
    });
    socket.addEventListener('error', () => {
      if (this.socket === socket) this.reconnect('Relay connection failed.');
    });
    socket.addEventListener('close', () => {
      if (this.socket === socket) this.reconnect('Relay connection closed.');
    });
  }

  private handleFrame(socket: WebSocketLike, frame: string): void {
    const decoded = decodeServerMessage(frame);
    if (!decoded.ok) {
      if (decoded.error.code !== 'unknown_type') {
        this.emit({ kind: 'protocol-error', error: decoded.error });
        socket.close();
      }
      return;
    }

    switch (decoded.value.type) {
      case 'hello':
        this.emit({ kind: 'hello', relay: decoded.value.relay });
        break;
      case 'snapshot':
        this.emit({
          kind: 'snapshot',
          seq: decoded.value.seq,
          at: decoded.value.at,
          context: decoded.value.context,
          state: decoded.value.state,
        });
        break;
      case 'status':
        this.emit({ kind: 'feed', status: decoded.value.feed, detail: decoded.value.detail });
        break;
      case 'text':
        this.emit({
          kind: 'text',
          context: decoded.value.context,
          at: decoded.value.at,
          text: decoded.value.text,
        });
        break;
    }
  }

  private reconnect(detail: string): void {
    if (!this.running || this.reconnectTimer !== null) return;
    this.socket = null;
    this.emit({
      kind: 'connection',
      phase: 'reconnecting',
      detail: this.options.diagnosticDetail?.() ?? detail,
    });

    const jitter = 0.75 + Math.random() * 0.5;
    const delay = Math.min(this.nextDelayMs, this.options.reconnect.maxDelayMs) * jitter;
    this.nextDelayMs = Math.min(
      this.options.reconnect.maxDelayMs,
      Math.max(this.options.reconnect.initialDelayMs, this.nextDelayMs * this.options.reconnect.factor),
    );
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  private emit(event: SourceEvent): void {
    this.listener?.(event);
  }
}
