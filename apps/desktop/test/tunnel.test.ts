import { describe, expect, it } from 'vitest';

import { watchTunnelDiagnostics } from '../src/lib/tunnel.ts';

describe('watchTunnelDiagnostics', () => {
  it('is a no-op outside Tauri: no polling, always null', () => {
    expect('__TAURI_INTERNALS__' in globalThis).toBe(false);

    const detail = watchTunnelDiagnostics();

    expect(detail()).toBeNull();
  });
});
