import { MockStateSource } from './source/mock.ts';
import { RelayStateSource } from './source/relay.ts';
import type { StateSource } from './source/types.ts';

const DEFAULT_RELAY_URL = 'ws://127.0.0.1:8787/state';

function readRelayUrl(value: string): string {
  const url = new URL(value);
  if (url.protocol !== 'ws:' && url.protocol !== 'wss:') {
    throw new Error('VITE_TINYSCRY_RELAY_URL must use ws: or wss:.');
  }
  return url.toString();
}

export function createStateSource(): StateSource {
  const source = import.meta.env.VITE_TINYSCRY_SOURCE ?? 'mock';
  if (source === 'mock') return new MockStateSource();
  if (source === 'relay') {
    return new RelayStateSource({
      url: readRelayUrl(import.meta.env.VITE_TINYSCRY_RELAY_URL ?? DEFAULT_RELAY_URL),
      reconnect: { initialDelayMs: 500, maxDelayMs: 10_000, factor: 2 },
    });
  }
  throw new Error('VITE_TINYSCRY_SOURCE must be "mock" or "relay".');
}
