import { MockActionSink } from './action/mock.ts';
import { RelayActionSink } from './action/relay.ts';
import type { ActionSink } from './action/types.ts';
import { MockStateSource } from './source/mock.ts';
import { RelayStateSource } from './source/relay.ts';
import type { StateSource } from './source/types.ts';
import { watchTunnelDiagnostics } from './tunnel.ts';

const DEFAULT_RELAY_URL = 'ws://127.0.0.1:8787/state';

function readRelayUrl(value: string): string {
  const url = new URL(value);
  if (url.protocol !== 'ws:' && url.protocol !== 'wss:') {
    throw new Error('VITE_TINYSCRY_RELAY_URL must use ws: or wss:.');
  }
  return url.toString();
}

function configuredSource(): string {
  return (
    import.meta.env.VITE_TINYSCRY_SOURCE ??
    (import.meta.env.TAURI_ENV_PLATFORM === undefined ? 'mock' : 'relay')
  );
}

function configuredRelayUrl(): string {
  return readRelayUrl(import.meta.env.VITE_TINYSCRY_RELAY_URL ?? DEFAULT_RELAY_URL);
}

export function createStateSource(): StateSource {
  // A Tauri-driven build is the real pipeline; only the browser dev loop gets
  // the demo source, which reports itself live. `TAURI_ENV_PLATFORM` is set by
  // the Tauri CLI and exposed by `envPrefix` in vite.config.ts.
  const source = configuredSource();
  if (source === 'mock') return new MockStateSource();
  if (source === 'relay') {
    return new RelayStateSource({
      url: configuredRelayUrl(),
      reconnect: { initialDelayMs: 500, maxDelayMs: 10_000, factor: 2 },
      diagnosticDetail: watchTunnelDiagnostics(),
    });
  }
  throw new Error('VITE_TINYSCRY_SOURCE must be "mock" or "relay".');
}

export function createActionSink(): ActionSink {
  const source = configuredSource();
  if (source === 'mock') return new MockActionSink();
  if (source === 'relay') {
    const url = new URL(configuredRelayUrl());
    url.pathname = '/action';
    url.search = '';
    url.hash = '';
    return new RelayActionSink({ url: url.toString() });
  }
  throw new Error('VITE_TINYSCRY_SOURCE must be "mock" or "relay".');
}
