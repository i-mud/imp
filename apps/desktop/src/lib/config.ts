import { MockActionSink } from './action/mock.ts';
import { RelayActionSink } from './action/relay.ts';
import type { ActionSink } from './action/types.ts';
import { MockStateSource } from './source/mock.ts';
import { RelayStateSource } from './source/relay.ts';
import type { StateSource } from './source/types.ts';
import { loadConnectionConfig, type RuntimeConnectionConfig, watchTunnelDiagnostics } from './tunnel.ts';

const DEFAULT_RELAY_URL = 'ws://127.0.0.1:8787/state';

function readRelayUrl(value: string): string {
  const url = new URL(value);
  if (url.protocol !== 'ws:' && url.protocol !== 'wss:') {
    throw new Error('VITE_IMP_RELAY_URL must use ws: or wss:.');
  }
  return url.toString();
}

function readDirectStateUrl(value: string): string {
  const url = new URL(value);

  if (url.protocol !== 'wss:') {
    throw new Error('Direct Imp transport must use wss:.');
  }
  if (url.hostname.length === 0) {
    throw new Error('Direct Imp transport requires a host.');
  }
  if (url.username !== '' || url.password !== '') {
    throw new Error('Direct Imp transport URL must not contain credentials.');
  }
  if (url.search !== '' || url.hash !== '') {
    throw new Error('Direct Imp transport URL must not contain query or fragment data.');
  }

  const segments = url.pathname.split('/');
  if (segments.at(-1) !== 'state') {
    throw new Error('Direct Imp transport URL must end at /state.');
  }

  return url.toString();
}

function configuredSource(): string {
  return (
    import.meta.env.VITE_IMP_SOURCE ?? (import.meta.env.TAURI_ENV_PLATFORM === undefined ? 'mock' : 'relay')
  );
}

function configuredRelayUrl(): string {
  return readRelayUrl(import.meta.env.VITE_IMP_RELAY_URL ?? DEFAULT_RELAY_URL);
}

export function actionUrlForStateUrl(stateUrl: string): string {
  const url = new URL(stateUrl);
  const segments = url.pathname.split('/');

  if (segments.at(-1) === 'state') {
    segments[segments.length - 1] = 'action';
    url.pathname = segments.join('/');
  } else {
    // Preserve the historical local/custom relay behaviour.
    url.pathname = '/action';
  }

  url.search = '';
  url.hash = '';
  return url.toString();
}

interface RelayConnection {
  readonly mode: 'local' | 'direct';
  readonly stateUrl: string;
  readonly authenticationToken?: string;
}

export function relayConnectionFromNative(
  native: RuntimeConnectionConfig,
  localStateUrl?: string,
): RelayConnection {
  if (native.mode === 'local') {
    return {
      mode: 'local',
      stateUrl: readRelayUrl(localStateUrl ?? configuredRelayUrl()),
    };
  }

  if (!/^[A-Za-z0-9_-]{43}$/.test(native.authenticationToken)) {
    throw new Error('Native Direct-WSS pairing token is invalid.');
  }

  return {
    mode: 'direct',
    stateUrl: readDirectStateUrl(native.stateUrl),
    authenticationToken: native.authenticationToken,
  };
}

export interface AppRuntime {
  readonly source: StateSource;
  readonly actionSink: ActionSink;
}

export async function createRuntimeClients(): Promise<AppRuntime> {
  const sourceKind = configuredSource();

  if (sourceKind === 'mock') {
    return {
      source: new MockStateSource(),
      actionSink: new MockActionSink(),
    };
  }

  if (sourceKind !== 'relay') {
    throw new Error('VITE_IMP_SOURCE must be "mock" or "relay".');
  }

  const native = await loadConnectionConfig();
  const connection = relayConnectionFromNative(native);

  const authentication =
    connection.authenticationToken === undefined
      ? {}
      : { authenticationToken: connection.authenticationToken };

  const diagnostic = connection.mode === 'local' ? { diagnosticDetail: watchTunnelDiagnostics() } : {};

  return {
    source: new RelayStateSource({
      url: connection.stateUrl,
      reconnect: {
        initialDelayMs: 500,
        maxDelayMs: 10_000,
        factor: 2,
      },
      ...authentication,
      ...diagnostic,
    }),
    actionSink: new RelayActionSink({
      url: actionUrlForStateUrl(connection.stateUrl),
      ...authentication,
    }),
  };
}
