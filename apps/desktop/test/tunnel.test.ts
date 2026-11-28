import { afterEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';

const invoke = vi.hoisted(() => vi.fn());

vi.mock('@tauri-apps/api/core', () => ({
  invoke,
}));

import {
  cancelManagedConnection,
  checkManagedConnection,
  loadConnectionSettings,
  managedCheckPending,
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
  vi.unstubAllGlobals();
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

    const settings = {
      mode: 'direct',
      sshTarget: '',
      remoteUrl: 'wss://imp.example/state',
      hasPairingToken: true,
    };
    invoke.mockResolvedValueOnce({ running: settings, saved: settings, pendingRestart: false });

    await expect(loadConnectionSettings()).resolves.toEqual({
      running: settings,
      saved: settings,
      pendingRestart: false,
    });

    expect(invoke).toHaveBeenCalledWith('connection_settings');
  });

  it('passes the update as one named Tauri command argument', async () => {
    setTauriRuntime(true);

    const saved = {
      mode: 'managed',
      sshTarget: '',
      sshDestination: { host: 'mud.example.org', username: 'player' },
      remoteUrl: '',
      hasPairingToken: false,
    };
    invoke.mockResolvedValueOnce({ running: null, saved, pendingRestart: true });

    await expect(
      saveConnectionSettings({
        mode: 'managed',
        sshDestination: { host: 'mud.example.org', username: 'player' },
      }),
    ).resolves.toEqual({
      running: null,
      saved,
      pendingRestart: true,
    });

    expect(invoke).toHaveBeenCalledWith('save_connection_settings', {
      update: {
        mode: 'managed',
        sshDestination: { host: 'mud.example.org', username: 'player' },
      },
    });
  });

  it('omits pairingToken when preserving an existing Direct credential', async () => {
    setTauriRuntime(true);

    invoke.mockResolvedValueOnce({
      running: null,
      saved: {
        mode: 'direct',
        sshTarget: '',
        remoteUrl: 'wss://new.example/state',
        hasPairingToken: true,
      },
      pendingRestart: true,
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

describe('Managed connection check bridge', () => {
  it('does not start a diagnostic outside Tauri', () => {
    expect(checkManagedConnection({ sshTarget: 'configured-alias' })).toBeNull();
    expect(get(managedCheckPending)).toBe(false);
    expect(invoke).not.toHaveBeenCalled();
  });

  it('continues the session sequence and locks until the original command retires', async () => {
    setTauriRuntime(true);
    const sequence = new Map([['imp.managed-check-sequence', '950']]);
    vi.stubGlobal('sessionStorage', {
      getItem: (name: string) => sequence.get(name) ?? null,
      setItem: (name: string, value: string) => sequence.set(name, value),
    });
    const pending = Promise.withResolvers<string>();
    invoke
      .mockReturnValueOnce(pending.promise)
      .mockResolvedValueOnce(undefined)
      .mockResolvedValueOnce('live');

    const alias = { sshTarget: 'Configured.Alias' };
    const first = checkManagedConnection(alias)!;
    expect(first.requestId).toBe(951);
    expect(get(managedCheckPending)).toBe(true);
    expect(checkManagedConnection(alias)).toBeNull();
    await cancelManagedConnection(first.requestId);
    expect(get(managedCheckPending)).toBe(true);
    expect(invoke.mock.calls).toEqual([
      ['check_managed_connection', { requestId: 951, destination: alias }],
      ['cancel_managed_connection', { requestId: 951 }],
    ]);

    pending.resolve('cancelled');
    await expect(first.result).resolves.toBe('cancelled');
    expect(get(managedCheckPending)).toBe(false);

    const server = { sshDestination: { host: 'mud.fixture.invalid', username: 'player', port: 2222 } };
    const second = checkManagedConnection(server)!;
    await expect(second.result).resolves.toBe('live');
    expect(invoke).toHaveBeenLastCalledWith('check_managed_connection', {
      requestId: 952,
      destination: server,
    });
    expect(sequence).toEqual(new Map([['imp.managed-check-sequence', '952']]));
  });
});

it('latches a native save retirement fault and blocks subsequent checks and saves before IPC', async () => {
  // Exercise the app-lifetime latch in a fresh bridge without changing the shared test instance.
  vi.resetModules();
  const bridge = await import('../src/lib/tunnel.ts');
  setTauriRuntime(true);
  invoke.mockRejectedValueOnce('retirement_unconfirmed');
  const update = { mode: 'managed', sshTarget: 'configured-alias' } as const;

  await expect(bridge.saveConnectionSettings(update)).rejects.toBe('retirement_unconfirmed');
  expect(get(bridge.managedCheckRestartRequired)).toBe(true);
  expect(get(bridge.managedCheckPending)).toBe(false);
  expect(bridge.checkManagedConnection({ sshTarget: update.sshTarget })).toBeNull();
  await expect(bridge.saveConnectionSettings(update)).rejects.toBe('retirement_unconfirmed');
  expect(invoke).toHaveBeenCalledExactlyOnceWith('save_connection_settings', { update });
});
