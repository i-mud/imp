import {
  decodeServerMessage,
  isValidActionCommand,
  PROTOCOL_VERSION,
  type StateContext,
} from '@imp/protocol';

import type { ActionResult, ActionSink } from './types.ts';

interface ActionWebSocket {
  addEventListener(type: string, listener: (event: Event) => void): void;
  close(): void;
  send(data: string): void;
}

type ActionWebSocketFactory = (url: string) => ActionWebSocket;

interface RelayActionSinkOptions {
  readonly url: string;
  readonly timeoutMs?: number;
  readonly webSocketFactory?: ActionWebSocketFactory;
  readonly authenticationToken?: string;
}

export class RelayActionSink implements ActionSink {
  readonly id = 'relay';
  readonly label = 'Relay actions';

  private readonly url: string;
  private readonly timeoutMs: number;
  private readonly factory: ActionWebSocketFactory;
  private readonly authenticationToken: string | undefined;

  constructor(options: RelayActionSinkOptions) {
    this.url = options.url;
    this.timeoutMs = options.timeoutMs ?? 5_000;
    this.factory = options.webSocketFactory ?? ((url) => new WebSocket(url));
    this.authenticationToken = options.authenticationToken;
  }

  send(context: StateContext, command: string): Promise<ActionResult> {
    if (!isValidActionCommand(command)) {
      return Promise.resolve({
        status: 'rejected',
        detail: 'Command must be 1..512 printable ASCII characters.',
      });
    }

    const { promise, resolve } = Promise.withResolvers<ActionResult>();
    let socket: ActionWebSocket;
    try {
      socket = this.factory(this.url);
    } catch {
      resolve({ status: 'unknown', detail: 'Unable to create relay action connection.' });
      return promise;
    }

    let settled = false;
    const finish = (result: ActionResult): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      socket.close();
      resolve(result);
    };
    const timer = setTimeout(
      () => finish({ status: 'unknown', detail: 'Relay action timed out.' }),
      this.timeoutMs,
    );

    socket.addEventListener('open', () => {
      if (settled) return;
      try {
        if (this.authenticationToken !== undefined) {
          socket.send(
            JSON.stringify({
              type: 'auth',
              token: this.authenticationToken,
            }),
          );
        }
        socket.send(JSON.stringify({ type: 'action', protocol: PROTOCOL_VERSION, context, command }));
      } catch {
        finish({ status: 'unknown', detail: 'Unable to send relay action.' });
      }
    });
    socket.addEventListener('message', (event) => {
      if (!(event instanceof MessageEvent) || typeof event.data !== 'string') {
        finish({ status: 'unknown', detail: 'Relay returned an invalid action result.' });
        return;
      }
      const decoded = decodeServerMessage(event.data);
      if (!decoded.ok || decoded.value.type !== 'action-result') {
        finish({ status: 'unknown', detail: 'Relay returned an invalid action result.' });
        return;
      }
      finish({ status: decoded.value.status, detail: decoded.value.detail });
    });
    socket.addEventListener('error', () =>
      finish({ status: 'unknown', detail: 'Relay action connection failed.' }),
    );
    socket.addEventListener('close', () =>
      finish({ status: 'unknown', detail: 'Relay action connection closed without a result.' }),
    );
    return promise;
  }
}
