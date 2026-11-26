import { flushSync, mount, tick, unmount } from 'svelte';
import { afterEach, expect, it, vi } from 'vitest';
import type * as WindowApi from '@tauri-apps/api/window';
import type * as CoreApi from '@tauri-apps/api/core';

const native = vi.hoisted(() => ({ invoke: vi.fn(), setSize: vi.fn().mockResolvedValue(undefined) }));
vi.mock('@tauri-apps/api/core', async (importOriginal) => ({
  ...(await importOriginal<typeof CoreApi>()),
  invoke: native.invoke,
}));
vi.mock('@tauri-apps/api/app', () => ({ getVersion: async () => '0.0.0-fixture' }));
vi.mock('@tauri-apps/api/window', async (importOriginal) => ({
  ...(await importOriginal<typeof WindowApi>()),
  getCurrentWindow: () => ({ setSize: native.setSize }),
}));

import App from '../src/App.svelte';
import { createRuntimeClients } from '../src/lib/config.ts';
import type { ConnectionSettings, ConnectionSettingsUpdate } from '../src/lib/tunnel.ts';

let app: object | undefined;
const sockets: FixtureSocket[] = [];

// Only the browser socket and native APIs are replaced; App, Hud, store and clients are real.
class FixtureSocket extends EventTarget {
  readonly send = vi.fn();
  readonly close = vi.fn();
  readonly url: string;
  constructor(url: string) {
    super();
    this.url = url;
    sockets.push(this);
  }
}

afterEach(async () => {
  if (app) await unmount(app);
  app = undefined;
  sockets.splice(0);
  document.body.replaceChildren();
  localStorage.clear();
  Reflect.deleteProperty(globalThis, '__TAURI_INTERNALS__');
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  native.invoke.mockReset();
  native.setSize.mockClear();
});

async function click(selector: string) {
  const element = document.querySelector<HTMLElement>(selector);
  if (!element) throw new Error(`Missing control: ${selector}`);
  flushSync(() => element.click());
  await tick();
  flushSync();
}

async function input(type: string, value: string) {
  const field = document.querySelector<HTMLInputElement>(`input[type="${type}"]`)!;
  flushSync(() => {
    field.value = value;
    field.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await tick();
  flushSync();
}

async function submit() {
  await click('button[type="submit"]');
  await vi.waitFor(() => {
    flushSync();
    expect(document.querySelector('button[type="submit"]:disabled')).toBeNull();
  });
}

it('saving through App → Hud → ConnectionDialog keeps the active Direct client until restart', async () => {
  vi.stubEnv('VITE_IMP_SOURCE', 'relay');
  vi.stubGlobal('WebSocket', FixtureSocket);
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    },
  );
  Object.defineProperty(globalThis, '__TAURI_INTERNALS__', { configurable: true, value: {} });
  localStorage.setItem('imp.display-mode', 'expanded');
  const token = 'x'.repeat(43);
  const direct: ConnectionSettings = {
    mode: 'direct',
    sshTarget: '',
    remoteUrl: 'wss://active.fixture.invalid/state',
    hasPairingToken: true,
  };
  const replacementUrl = 'wss://replacement.fixture.invalid/state';
  const replacementToken = 'A'.repeat(43);
  let rejectReplacementOnce = true;
  const rawFailure = `Native save details: ${replacementToken}`;
  native.invoke.mockImplementation(async (command: string, args?: { update: ConnectionSettingsUpdate }) => {
    if (command === 'connection_config') {
      return { mode: 'direct', stateUrl: direct.remoteUrl, authenticationToken: token };
    }
    if (command === 'connection_settings') return direct;
    if (command === 'save_connection_settings') {
      if (args?.update.mode === 'direct') {
        if (args.update.pairingToken === undefined) {
          throw 'direct pairing token is required when changing the remote URL';
        }
        if (rejectReplacementOnce) {
          rejectReplacementOnce = false;
          throw rawFailure;
        }
        return { ...direct, remoteUrl: replacementUrl };
      }
      return { mode: 'local', sshTarget: '', remoteUrl: '', hasPairingToken: false };
    }
    throw new Error(`Unexpected native command: ${command}`);
  });

  const runtime = await createRuntimeClients();
  const start = vi.spyOn(runtime.source, 'start');
  const stop = vi.spyOn(runtime.source, 'stop');
  const target = document.createElement('div');
  document.body.append(target);
  flushSync(() => {
    app = mount(App, { target, props: runtime });
  });
  await tick();
  flushSync();
  expect(sockets).toHaveLength(1);
  const socket = sockets[0]!;
  const initialStops = stop.mock.calls.length;
  expect(start).toHaveBeenCalledOnce();
  expect(socket.url).toBe(direct.remoteUrl);

  await click('[aria-label="Settings"]');
  const connectionTrigger = document.querySelector('[data-connection-manager-trigger]');
  expect(connectionTrigger).not.toBeNull();
  await click('[data-connection-manager-trigger]');
  expect(document.querySelector('[role="dialog"][aria-labelledby="connection-dialog-title"]')).not.toBeNull();
  await vi.waitFor(() => {
    flushSync();
    expect(document.querySelector('input[name="connection-mode"][value="local"]')).not.toBeNull();
  });
  expect(document.querySelector<HTMLInputElement>('input[type="password"]')?.value).toBe('');
  await input('url', replacementUrl);
  await submit();
  expect(native.invoke).toHaveBeenCalledWith('save_connection_settings', {
    update: { mode: 'direct', remoteUrl: replacementUrl },
  });
  expect(document.querySelector('[role="alert"]')?.textContent).toBe(
    'Enter the pairing token for this endpoint or restore the saved URL.',
  );
  expect(document.querySelector('[role="status"]')).toBeNull();
  await input('url', direct.remoteUrl);
  expect(document.body.textContent).toContain('Leave the token blank to keep your saved credential.');
  await input('url', replacementUrl);

  await input('password', replacementToken);
  await submit();
  expect(document.querySelector('[role="alert"]')?.textContent).toBe(
    'Could not save native connection settings.',
  );
  expect(document.body.textContent).not.toContain(replacementToken);
  expect(document.querySelector('[role="status"]')).toBeNull();
  expect(document.querySelector<HTMLInputElement>('input[type="password"]')?.value).toBe(replacementToken);

  await submit();
  expect(document.querySelector('[role="alert"]')).toBeNull();
  expect(document.querySelector('[role="status"]')?.textContent).toContain('Saved. Restart Imp');
  expect(document.querySelector<HTMLInputElement>('input[type="password"]')?.value).toBe('');
  expect(document.querySelector<HTMLInputElement>('input[type="url"]')?.value).toBe(replacementUrl);
  expect(socket.url).toBe(direct.remoteUrl);

  await click('input[name="connection-mode"][value="local"]');
  await submit();
  expect(native.invoke.mock.calls.filter(([command]) => command === 'save_connection_settings')).toEqual([
    ['save_connection_settings', { update: { mode: 'direct', remoteUrl: replacementUrl } }],
    [
      'save_connection_settings',
      { update: { mode: 'direct', remoteUrl: replacementUrl, pairingToken: replacementToken } },
    ],
    [
      'save_connection_settings',
      { update: { mode: 'direct', remoteUrl: replacementUrl, pairingToken: replacementToken } },
    ],
  ]);
  await click('button[aria-describedby="discard-direct-warning"]');
  await vi.waitFor(() => {
    flushSync();
    expect(document.querySelector('[role="status"]')?.textContent).toContain('Saved. Restart Imp');
  });

  expect(native.invoke).toHaveBeenCalledWith('save_connection_settings', { update: { mode: 'local' } });
  expect(native.invoke.mock.calls.filter(([command]) => command === 'save_connection_settings')).toHaveLength(
    4,
  );
  expect(native.invoke.mock.calls.filter(([command]) => command === 'connection_config')).toHaveLength(1);
  expect(document.querySelector('[role="status"]')?.textContent).toContain('Saved. Restart Imp');
  expect(sockets).toHaveLength(1);
  expect(start).toHaveBeenCalledOnce();
  expect(stop).toHaveBeenCalledTimes(initialStops);
  expect(socket.close).not.toHaveBeenCalled();

  await click('.form-actions button:last-child');
  expect(connectionTrigger!.isConnected).toBe(false);
  expect(document.querySelector('[aria-label="Imp settings"]')).not.toBeNull();
  const returnedTrigger = document.querySelector('[data-connection-manager-trigger]');
  expect(returnedTrigger).not.toBeNull();
  expect(returnedTrigger).not.toBe(connectionTrigger);
  await vi.waitFor(() => expect(document.activeElement).toBe(returnedTrigger));
  // The retained real client still authenticates with its original runtime credential after saving.
  flushSync(() => socket.dispatchEvent(new Event('open')));
  expect(socket.send).toHaveBeenCalledExactlyOnceWith(JSON.stringify({ type: 'auth', token }));
  expect(sockets).toHaveLength(1);
  expect(stop).toHaveBeenCalledTimes(initialStops);
  expect(native.invoke.mock.calls.filter(([command]) => command === 'connection_config')).toHaveLength(1);
  await unmount(app!);
  app = undefined;
  expect(stop).toHaveBeenCalledTimes(initialStops + 1);
  expect(socket.close).toHaveBeenCalledOnce();
});
