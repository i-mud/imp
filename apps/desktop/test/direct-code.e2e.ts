// Renderer half of scripts/test-direct-code-e2e.py. The clients are the real createRuntimeClients() output;
// only the Tauri IPC transport is replaced, and it returns configs produced by the native store after a
// restart. WebSockets are real: TLS verification trusts only NODE_EXTRA_CA_CERTS (the disposable authority).
// Failure messages carry fixed labels only, because the configs hold a pairing token.
import { afterAll, beforeAll, describe, inject, it, vi } from 'vitest';

import type { RuntimeConnectionConfig } from '../src/lib/tunnel.ts';

declare module 'vitest' {
  export interface ProvidedContext {
    directCodeRuntimeConfigs: Record<'code' | 'manual' | 'wrongToken', RuntimeConnectionConfig>;
  }
}

const native = vi.hoisted(() => ({ config: undefined as unknown }));
vi.mock('@tauri-apps/api/core', () => ({
  invoke: async (command: string) => {
    if (command !== 'connection_config') throw new Error('unexpected native command');
    return native.config;
  },
}));

import { createRuntimeClients } from '../src/lib/config.ts';
import type { SourceEvent } from '../src/lib/source/types.ts';

const CHARACTER = 'DirectE2E';
const CONTEXT = { session: 'session1', foreground: 1, connection: 1 };
const relay = `ws://127.0.0.1:${process.env.IMP_E2E_RELAY_PORT}`;

interface Frame {
  readonly type?: string;
  readonly id?: string;
  readonly command?: string;
  readonly context?: unknown;
  readonly state?: { readonly character?: { readonly name?: string } | null };
}

function ensure(condition: boolean, label: string): asserts condition {
  if (!condition) throw new Error(`DIRECT-E2E-FAIL ${label}`);
}

async function step(label: string, run: () => Promise<void>): Promise<void> {
  try {
    await run();
  } catch (error) {
    const known = error instanceof Error && error.message.startsWith('DIRECT-E2E-FAIL');
    throw known ? error : new Error(`DIRECT-E2E-FAIL ${label} (unexpected error)`);
  }
}

/** One relay WebSocket with an ordered inbox, so no frame is missed between send and wait. */
class Peer {
  readonly closed: Promise<void>;
  private readonly queue: Frame[] = [];
  private readonly waiters: Array<(frame: Frame) => void> = [];

  private readonly socket: WebSocket;

  private constructor(socket: WebSocket) {
    this.socket = socket;
    socket.addEventListener('message', (event) => {
      const frame = JSON.parse(String(event.data)) as Frame; // synthetic relay frames, shape fixed by protocol
      const waiter = this.waiters.shift();
      if (waiter) waiter(frame);
      else this.queue.push(frame);
    });
    const { promise, resolve } = Promise.withResolvers<void>();
    socket.addEventListener('close', () => resolve(), { once: true });
    this.closed = promise;
  }

  static open(path: string): Promise<Peer> {
    const { promise, resolve, reject } = Promise.withResolvers<Peer>();
    const socket = new WebSocket(`${relay}${path}`);
    socket.addEventListener('open', () => resolve(new Peer(socket)), { once: true });
    socket.addEventListener('error', () => reject(new Error('DIRECT-E2E-FAIL relay socket')), { once: true });
    return promise;
  }

  send(frame: unknown): void {
    this.socket.send(JSON.stringify(frame));
  }

  next(): Promise<Frame> {
    const frame = this.queue.shift();
    if (frame !== undefined) return Promise.resolve(frame);
    const { promise, resolve } = Promise.withResolvers<Frame>();
    this.waiters.push(resolve);
    return promise;
  }

  close(): void {
    this.socket.close();
  }
}

let producer: Peer | undefined;

beforeAll(async () => {
  vi.stubGlobal('__TAURI_INTERNALS__', {}); // selects the native-config path in createRuntimeClients
  await step('synthetic producer', async () => {
    producer = await Peer.open('/ingest');
    const empty = { character: null, target: null };
    producer.send({ type: 'select', protocol: 2, context: CONTEXT, state: empty });
    const state = {
      character: { name: CHARACTER, hp: { current: 37, max: 50 }, mana: null, moves: null },
      target: null,
    };
    producer.send({ type: 'publish', protocol: 2, context: CONTEXT, state });
    // The relay has applied the state once a plain subscriber sees it; clients then get it as retained.
    const observer = await Peer.open('/state');
    for (;;) {
      const frame = await observer.next();
      if (frame.type === 'snapshot' && frame.state?.character?.name === CHARACTER) break;
    }
    observer.close();
  });
});

afterAll(() => producer?.close());

async function consumerFor(context: typeof CONTEXT): Promise<Peer> {
  const consumer = await Peer.open('/action-consumer');
  consumer.send({ type: 'consumer', protocol: 2, context });
  ensure((await consumer.next()).type === 'consumer-ready', 'consumer registration');
  return consumer;
}

/** Answers exactly one dispatch; resolves after the relay has unregistered and closed the consumer. */
async function serveOne(consumer: Peer): Promise<Frame> {
  const dispatch = await consumer.next();
  ensure(dispatch.type === 'dispatch', 'dispatch frame');
  consumer.send({ type: 'consumer-result', protocol: 2, id: dispatch.id, status: 'forwarded' });
  await consumer.closed;
  return dispatch;
}

async function clientsFor(config: RuntimeConnectionConfig) {
  native.config = config;
  const { source, actionSink } = await createRuntimeClients();
  ensure(source !== undefined && actionSink !== undefined, 'direct runtime was not created');
  return { source, actionSink };
}

/** Resolves with every event up to and including the first one `done` accepts. */
function until(
  source: { start(listener: (event: SourceEvent) => void): void },
  done: (event: SourceEvent) => boolean,
): Promise<SourceEvent[]> {
  const seen: SourceEvent[] = [];
  const { promise, resolve } = Promise.withResolvers<SourceEvent[]>();
  source.start((event) => {
    seen.push(event);
    if (done(event)) resolve(seen);
  });
  return promise;
}

async function authenticatedFlow(config: RuntimeConnectionConfig, thorough: boolean): Promise<void> {
  ensure(config.mode === 'direct', 'runtime config is not Direct');
  const { source, actionSink } = await clientsFor(config);
  try {
    const events = await until(
      source,
      (event) => event.kind === 'snapshot' && event.state.character?.name === CHARACTER,
    );
    const snapshot = events.at(-1);
    ensure(
      events.some((event) => event.kind === 'hello'),
      'authenticated hello',
    );
    ensure(
      events.some((event) => event.kind === 'connection' && event.phase === 'connected'),
      'connected phase',
    );
    ensure(
      snapshot?.kind === 'snapshot' &&
        snapshot.state.character?.hp?.current === 37 &&
        JSON.stringify(snapshot.context) === JSON.stringify(CONTEXT),
      'synthetic state with exact context',
    );

    const served = serveOne(await consumerFor(CONTEXT));
    ensure((await actionSink.send(CONTEXT, 'e2e exact action')).status === 'forwarded', 'action result');
    const dispatched = await served;
    ensure(
      dispatched.command === 'e2e exact action' &&
        JSON.stringify(dispatched.context) === JSON.stringify(CONTEXT),
      'action dispatched with the exact command and context',
    );

    if (!thorough) return;
    // A second consumer proves the first action is not replayed and a wrong context is never dispatched:
    // the first dispatch it ever receives must be the next correct action.
    const second = await consumerFor(CONTEXT);
    const other = { ...CONTEXT, connection: 2 };
    ensure((await actionSink.send(other, 'e2e wrong context')).status === 'rejected', 'wrong context');
    const servedSecond = serveOne(second);
    ensure((await actionSink.send(CONTEXT, 'e2e second action')).status === 'forwarded', 'second action');
    ensure((await servedSecond).command === 'e2e second action', 'no replay or wrong-context dispatch');
  } finally {
    source.stop();
  }
}

describe('Direct connection-code runtime', () => {
  it('authenticates a code-derived config, receives state and sends exact actions once', async () => {
    await step('code scenario', () => authenticatedFlow(inject('directCodeRuntimeConfigs').code, true));
  });

  it('works identically for manual setup', async () => {
    await step('manual scenario', () => authenticatedFlow(inject('directCodeRuntimeConfigs').manual, false));
  });

  it('refuses a wrong token for state and for actions without dispatching', async () => {
    await step('wrong token scenario', async () => {
      const { code, wrongToken } = inject('directCodeRuntimeConfigs');
      const refused = await clientsFor(wrongToken);
      const events = await until(
        refused.source,
        (event) => event.kind === 'connection' && event.phase === 'reconnecting',
      );
      refused.source.stop();
      ensure(
        events.every((event) => event.kind === 'connection' && event.phase !== 'connected'),
        'wrong token received state',
      );

      const served = serveOne(await consumerFor(CONTEXT));
      ensure((await refused.actionSink.send(CONTEXT, 'e2e refused action')).status === 'unknown', 'refusal');
      // The relay only sees the correct client's action: it is the consumer's first dispatch.
      const valid = await clientsFor(code);
      ensure(
        (await valid.actionSink.send(CONTEXT, 'e2e after refusal')).status === 'forwarded',
        'valid action after refusal',
      );
      ensure((await served).command === 'e2e after refusal', 'refused action reached the consumer');
    });
  });
});
