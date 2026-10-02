import { afterEach, describe, expect, it, vi } from 'vitest';

const invoke = vi.hoisted(() => vi.fn());

vi.mock('@tauri-apps/api/core', () => ({
  invoke,
}));

import {
  loadConnectionSettings,
  saveConnectionSettings,
  watchConnectionDiagnostics,
} from '../src/lib/tunnel.ts';

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
  vi.useRealTimers();
});

describe('watchConnectionDiagnostics', () => {
  it('is a no-op outside Tauri: no polling, always null', () => {
    expect('__TAURI_INTERNALS__' in globalThis).toBe(false);

    const detail = watchConnectionDiagnostics('node');

    expect(detail()).toBeNull();
    expect(invoke).not.toHaveBeenCalled();
  });

  it('polls tunnel diagnostics for an SSH-backed consumer endpoint', async () => {
    vi.useFakeTimers();
    setTauriRuntime(true);

    invoke.mockImplementation((command: string) => {
      if (command === 'tunnel_status') {
        return Promise.resolve({ diagnostic: 'reconnecting' });
      }
      return Promise.reject(new Error(`unexpected command: ${command}`));
    });

    const detail = watchConnectionDiagnostics('tunnel');

    await vi.advanceTimersByTimeAsync(0);

    expect(detail()).toBe('Establishing SSH tunnel…');
    expect(invoke).toHaveBeenCalledWith('tunnel_status');
    expect(invoke).not.toHaveBeenCalledWith('node_status');
  });

  it('polls local-node diagnostics for a Local consumer endpoint', async () => {
    vi.useFakeTimers();
    setTauriRuntime(true);

    invoke.mockImplementation((command: string) => {
      if (command === 'node_status') {
        return Promise.resolve({ diagnostic: 'starting' });
      }
      return Promise.reject(new Error(`unexpected command: ${command}`));
    });

    const detail = watchConnectionDiagnostics('node');

    await vi.advanceTimersByTimeAsync(0);

    expect(detail()).toBe('Starting local Imp node…');
    expect(invoke).toHaveBeenCalledWith('node_status');
    expect(invoke).not.toHaveBeenCalledWith('tunnel_status');
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
      remoteUrl: 'wss://imp.example/state',
      hasPairingToken: true,
    });

    await expect(loadConnectionSettings()).resolves.toEqual({
      mode: 'direct',
      sshTarget: '',
      remoteUrl: 'wss://imp.example/state',
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
