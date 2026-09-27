import { afterEach, describe, expect, it, vi } from 'vitest';

const invoke = vi.hoisted(() => vi.fn());

vi.mock('@tauri-apps/api/core', () => ({
  invoke,
}));

import { loadConnectionSettings, saveConnectionSettings, watchTunnelDiagnostics } from '../src/lib/tunnel.ts';

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
  invoke.mockReset();
});

describe('watchTunnelDiagnostics', () => {
  it('is a no-op outside Tauri: no polling, always null', () => {
    expect('__TAURI_INTERNALS__' in globalThis).toBe(false);

    const detail = watchTunnelDiagnostics();

    expect(detail()).toBeNull();
    expect(invoke).not.toHaveBeenCalled();
  });
});

describe('native connection settings', () => {
  it('does not invoke native settings commands in browser development', async () => {
    expect(await loadConnectionSettings()).toBeNull();

    await expect(
      saveConnectionSettings({
        mode: 'external',
      }),
    ).rejects.toThrow('unavailable outside Tauri');

    expect(invoke).not.toHaveBeenCalled();
  });

  it('loads the renderer-safe settings projection from Tauri', async () => {
    setTauriRuntime(true);

    invoke.mockResolvedValueOnce({
      mode: 'direct',
      sshTarget: '',
      remoteUrl: 'wss://tinyscry.example/state',
      hasPairingToken: true,
    });

    await expect(loadConnectionSettings()).resolves.toEqual({
      mode: 'direct',
      sshTarget: '',
      remoteUrl: 'wss://tinyscry.example/state',
      hasPairingToken: true,
    });

    expect(invoke).toHaveBeenCalledWith('connection_settings');
  });

  it('passes the update as one named Tauri command argument', async () => {
    setTauriRuntime(true);

    invoke.mockResolvedValueOnce({
      mode: 'managed',
      sshTarget: 'avatar',
      remoteUrl: '',
      hasPairingToken: false,
    });

    await expect(
      saveConnectionSettings({
        mode: 'managed',
        sshTarget: 'avatar',
      }),
    ).resolves.toEqual({
      mode: 'managed',
      sshTarget: 'avatar',
      remoteUrl: '',
      hasPairingToken: false,
    });

    expect(invoke).toHaveBeenCalledWith('save_connection_settings', {
      update: {
        mode: 'managed',
        sshTarget: 'avatar',
      },
    });
  });

  it('omits pairingToken when preserving an existing Direct credential', async () => {
    setTauriRuntime(true);

    invoke.mockResolvedValueOnce({
      mode: 'direct',
      sshTarget: '',
      remoteUrl: 'wss://new.example/state',
      hasPairingToken: true,
    });

    await saveConnectionSettings({
      mode: 'direct',
      remoteUrl: 'wss://new.example/state',
    });

    expect(invoke).toHaveBeenCalledWith('save_connection_settings', {
      update: {
        mode: 'direct',
        remoteUrl: 'wss://new.example/state',
      },
    });
  });
});
