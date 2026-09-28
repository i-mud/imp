import { describe, expect, it } from 'vitest';

import { actionUrlForStateUrl, createRuntimeClients, relayConnectionFromNative } from '../src/lib/config.ts';

describe('desktop runtime configuration', () => {
  it('preserves a reverse-proxy prefix when deriving the action endpoint', () => {
    expect(actionUrlForStateUrl('wss://imp.example/imp/state')).toBe('wss://imp.example/imp/action');
  });

  it('keeps the historical local action endpoint fallback', () => {
    expect(actionUrlForStateUrl('ws://127.0.0.1:8787/custom')).toBe('ws://127.0.0.1:8787/action');
  });

  it('turns native Direct-WSS config into authenticated relay options', () => {
    expect(
      relayConnectionFromNative({
        mode: 'direct',
        stateUrl: 'wss://imp.example/imp/state',
        authenticationToken: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
      }),
    ).toEqual({
      mode: 'direct',
      stateUrl: 'wss://imp.example/imp/state',
      authenticationToken: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
    });
  });

  it('does not consult the local relay URL in Direct-WSS mode', () => {
    expect(
      relayConnectionFromNative(
        {
          mode: 'direct',
          stateUrl: 'wss://imp.example/state',
          authenticationToken: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
        },
        'not-a-url',
      ),
    ).toEqual({
      mode: 'direct',
      stateUrl: 'wss://imp.example/state',
      authenticationToken: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
    });
  });

  it('rejects insecure Direct-WSS renderer configuration', () => {
    expect(() =>
      relayConnectionFromNative({
        mode: 'direct',
        stateUrl: 'ws://imp.example/state',
        authenticationToken: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
      }),
    ).toThrow('must use wss');
  });

  it('keeps browser development on mock state and actions by default', async () => {
    expect('__TAURI_INTERNALS__' in globalThis).toBe(false);

    const runtime = await createRuntimeClients();

    expect(runtime.source.id).toBe('mock');
    expect(runtime.actionSink.id).toBe('mock');
  });
});
