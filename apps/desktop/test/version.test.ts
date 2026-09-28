import { afterEach, describe, expect, it, vi } from 'vitest';

const getVersion = vi.hoisted(() => vi.fn());

vi.mock('@tauri-apps/api/app', () => ({
  getVersion,
}));

import { loadAppVersion } from '../src/lib/version.ts';

function setTauriRuntime(enabled: boolean): void {
  if (enabled) {
    Object.defineProperty(globalThis, '__TAURI_INTERNALS__', {
      configurable: true,
      value: {},
    });
  } else {
    Reflect.deleteProperty(globalThis, '__TAURI_INTERNALS__');
  }
}

afterEach(() => {
  setTauriRuntime(false);
  getVersion.mockReset();
});

describe('loadAppVersion', () => {
  it('does not invent an application version outside Tauri', async () => {
    await expect(loadAppVersion()).resolves.toBeNull();
    expect(getVersion).not.toHaveBeenCalled();
  });

  it('reads the native application version from Tauri', async () => {
    setTauriRuntime(true);
    getVersion.mockResolvedValueOnce('0.1.0');

    await expect(loadAppVersion()).resolves.toBe('0.1.0');
    expect(getVersion).toHaveBeenCalledOnce();
  });
});
