import { flushSync, mount, tick, unmount } from 'svelte';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
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
import { waitForDomTransition } from './dom-transition.ts';

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

beforeEach(() => {
  vi.stubGlobal('WebSocket', FixtureSocket);
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    },
  );
});

afterEach(async () => {
  if (app) await unmount(app);
  app = undefined;
  sockets.splice(0);
  document.body.replaceChildren();
  localStorage.clear();
  sessionStorage.clear();
  Reflect.deleteProperty(globalThis, '__TAURI_INTERNALS__');
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.useRealTimers();
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

async function input(key: string, value: string) {
  const field = document.querySelector<HTMLInputElement>(`input[name="${key}"], input[type="${key}"]`)!;
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

const SSH_DESTINATION = { host: 'mud.fixture.invalid', username: 'player' };
const GUIDED: ConnectionSettings = {
  mode: 'managed',
  sshTarget: '',
  sshDestination: SSH_DESTINATION,
  remoteUrl: '',
  hasPairingToken: false,
};

it.each([
  ['absent configuration', 'unconfigured', 'Set up connection'],
  ['unavailable configuration', 'unavailable', 'Review connection settings'],
  ['rejected connection bridge', 'rejected', 'Review connection settings'],
  ['missing connection bridge', 'missing', 'Review connection settings'],
])('starts without a consumer for %s', async (_, config, entryLabel) => {
  vi.stubEnv('VITE_IMP_SOURCE', config === 'missing' ? 'mock' : undefined);
  vi.stubEnv('TAURI_ENV_PLATFORM', 'windows');
  const interval = vi.spyOn(globalThis, 'setInterval');
  if (config !== 'missing') vi.stubGlobal('__TAURI_INTERNALS__', {});
  native.invoke.mockImplementation(async (command: string) => {
    if (command === 'connection_config') {
      if (config === 'rejected') throw new Error('private bridge failure detail');
      return { mode: config, stateUrl: null, authenticationToken: null };
    }
    if (command === 'connection_settings') {
      if (config === 'unavailable') throw new Error('private settings file detail');
      return { running: null, saved: null, pendingRestart: false };
    }
    if (command === 'save_connection_settings') {
      return {
        running: null,
        saved: GUIDED,
        pendingRestart: true,
      };
    }
    throw new Error(`Unexpected native command: ${command}`);
  });
  const target = document.createElement('div');
  target.id = 'app';
  document.body.append(target);
  try {
    const runtime = await createRuntimeClients();
    expect(runtime).toEqual({ setupState: config === 'unconfigured' ? 'unconfigured' : 'unavailable' });
    expect(interval).not.toHaveBeenCalled();
    app = mount(App, { target, props: runtime });
    await tick();
    expect(target.querySelector('.vitals, .compact-primary, .action-bar')).toBeNull();
    expect(target.textContent).not.toContain('private bridge failure detail');
    const entry = [...target.querySelectorAll('button')].find(
      (button) => button.textContent?.trim() === entryLabel,
    );
    expect(entry).toBeDefined();
    expect(target.querySelector('[aria-label="Settings"]')).not.toBeNull();
    expect(native.invoke.mock.calls).toEqual(config === 'missing' ? [] : [['connection_config']]);

    if (config === 'unconfigured') {
      await click('[data-connection-setup-trigger]');
      await vi.waitFor(() => {
        expect(target.querySelector('input[name="client-location"][value="local"]')).not.toBeNull();
      });
      await click('input[name="client-location"][value="remote"]');
      await click('input[name="remote-method"][value="managed"]');
      await input('ssh-host', SSH_DESTINATION.host);
      await input('ssh-username', SSH_DESTINATION.username);
      await click('button[type="submit"]');
      await vi.waitFor(() => {
        flushSync();
        expect(target.querySelector('button[type="submit"]:disabled')).toBeNull();
      });
      expect(native.invoke).toHaveBeenCalledWith('save_connection_settings', {
        update: { mode: 'managed', sshDestination: SSH_DESTINATION },
      });
      expect(target.textContent).toContain('Saved. Restart Imp');
      expect(target.querySelector('.vitals, .compact-primary, .action-bar')).toBeNull();
      await click('[aria-label="Close connection settings"]');
      expect(target.textContent).toContain('Restart Imp to use the saved connection.');
      expect(target.querySelector('[data-connection-setup-trigger]')).toBeNull();
    } else if (config === 'unavailable') {
      await click('[aria-label="Settings"]');
      await click('[data-connection-manager-trigger]');
      await vi.waitFor(() => {
        expect(target.querySelector('[role="alert"]')?.textContent).toContain(
          'Could not load native connection settings',
        );
      });
      expect(target.textContent).not.toContain('private settings file detail');
      expect(target.querySelector('button[type="submit"]')).toBeNull();
    }

    expect(native.invoke.mock.calls.some(([command]) => command === 'check_managed_connection')).toBe(false);
    expect(sockets).toHaveLength(0);
    expect(native.invoke).not.toHaveBeenCalledWith('node_status');
    expect(native.invoke).not.toHaveBeenCalledWith('tunnel_status');
  } finally {
    interval.mockRestore();
  }
});

it('saving through App → Hud → ConnectionDialog keeps the active Direct client until restart', async ({
  signal,
}) => {
  vi.stubEnv('VITE_IMP_SOURCE', 'relay');
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
  let saved = direct;
  const replacementToken = 'A'.repeat(43);
  let rejectReplacementOnce = true;
  const rawFailure = `Native save details: ${replacementToken}`;
  const check = Promise.withResolvers<string>();
  native.invoke.mockImplementation(async (command: string, args?: { update: ConnectionSettingsUpdate }) => {
    if (command === 'connection_config') {
      return { mode: 'direct', stateUrl: direct.remoteUrl, authenticationToken: token };
    }
    if (command === 'connection_settings')
      return { running: direct, saved, pendingRestart: saved !== direct };
    if (command === 'check_managed_connection') return check.promise;
    if (command === 'save_connection_settings') {
      if (args?.update.mode === 'direct') {
        if (args.update.pairingToken === undefined) {
          throw 'direct pairing token is required when changing the remote URL';
        }
        if (rejectReplacementOnce) {
          rejectReplacementOnce = false;
          throw rawFailure;
        }
        saved = { ...direct, remoteUrl: replacementUrl };
      } else {
        saved = GUIDED;
      }
      return { running: direct, saved, pendingRestart: true };
    }
    throw new Error(`Unexpected native command: ${command}`);
  });

  const runtime = await createRuntimeClients();
  if (runtime.setupState !== undefined) throw new Error('Expected a running Direct consumer.');
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
  const feedLabel = document.querySelector('.indicator')?.getAttribute('aria-label');
  expect(feedLabel).toBe('Connecting to relay');

  await click('[aria-label="Settings"]');
  const connectionTrigger = document.querySelector('[data-connection-manager-trigger]');
  expect(connectionTrigger).not.toBeNull();
  await click('[data-connection-manager-trigger]');
  expect(document.querySelector('[role="dialog"][aria-labelledby="connection-dialog-title"]')).not.toBeNull();
  await vi.waitFor(() => {
    flushSync();
    expect(document.querySelector('input[name="client-location"][value="remote"]')).not.toBeNull();
  });
  expect(document.body.textContent).toContain('In use');
  expect(document.body.textContent).not.toContain('Saved for next launch');
  expect(document.querySelector<HTMLInputElement>('input[name="client-location"]:checked')?.value).toBe(
    'remote',
  );
  expect(document.querySelector<HTMLInputElement>('input[name="remote-method"]:checked')?.value).toBe(
    'direct',
  );
  await click('input[name="client-location"][value="remote"]');
  await click('input[name="remote-method"][value="direct"]');
  expect(document.querySelector<HTMLInputElement>('input[type="password"]')?.value).toBe('');
  await input('url', replacementUrl);
  await submit();
  expect(native.invoke).not.toHaveBeenCalledWith('save_connection_settings', expect.anything());
  expect(document.querySelector('[role="alert"]')?.textContent).toBe(
    'Enter the pairing token for this endpoint.',
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
  expect(document.body.textContent).toContain('Saved for next launch');
  expect(document.querySelector<HTMLInputElement>('input[type="password"]')?.value).toBe('');
  expect(document.querySelector<HTMLInputElement>('input[type="url"]')?.value).toBe(replacementUrl);
  expect(socket.url).toBe(direct.remoteUrl);

  await click('input[name="remote-method"][value="managed"]');
  await input('ssh-host', SSH_DESTINATION.host);
  await input('ssh-username', SSH_DESTINATION.username);
  const storage = Object.entries(localStorage);
  await click('button[name="check-managed-connection"]');
  const invocation = native.invoke.mock.calls.find(([command]) => command === 'check_managed_connection');
  const requestId = invocation?.[1]?.requestId;
  expect(native.invoke).toHaveBeenLastCalledWith('check_managed_connection', {
    requestId,
    destination: { sshDestination: SSH_DESTINATION },
  });
  expect(document.querySelector('#managed-check-status')?.textContent).toBe('Checking connection…');
  expect(document.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled).toBe(true);
  check.resolve('live');
  await waitForDomTransition(() => !document.querySelector('button[name="cancel-managed-check"]'), signal);
  expect(document.querySelector('#managed-check-status')?.textContent).toBe(
    'The remote Imp node reported live state during this check.',
  );
  expect(Object.entries(localStorage)).toEqual(storage);
  expect(saved.remoteUrl).toBe(replacementUrl);
  expect(sockets).toEqual([socket]);
  expect(start).toHaveBeenCalledOnce();
  expect(stop).toHaveBeenCalledTimes(initialStops);
  expect(socket.close).not.toHaveBeenCalled();
  await submit();
  expect(native.invoke.mock.calls.filter(([command]) => command === 'save_connection_settings')).toEqual([
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
    expect(document.querySelector('.saved-message')?.textContent).toContain('Saved. Restart Imp');
  });

  expect(native.invoke).toHaveBeenCalledWith('save_connection_settings', {
    update: { mode: 'managed', sshDestination: SSH_DESTINATION },
  });
  expect(native.invoke.mock.calls.filter(([command]) => command === 'save_connection_settings')).toHaveLength(
    3,
  );
  expect(native.invoke.mock.calls.filter(([command]) => command === 'connection_config')).toHaveLength(1);
  expect(document.querySelector('.saved-message')?.textContent).toContain('Saved. Restart Imp');
  expect(sockets).toHaveLength(1);
  expect(start).toHaveBeenCalledOnce();
  expect(stop).toHaveBeenCalledTimes(initialStops);
  expect(socket.close).not.toHaveBeenCalled();

  await click('[aria-label="Close connection settings"]');
  expect(connectionTrigger!.isConnected).toBe(false);
  expect(document.querySelector('[aria-label="Imp settings"]')).not.toBeNull();
  const returnedTrigger = document.querySelector('[data-connection-manager-trigger]');
  expect(returnedTrigger).not.toBeNull();
  expect(returnedTrigger).not.toBe(connectionTrigger);
  await vi.waitFor(() => expect(document.activeElement).toBe(returnedTrigger));
  expect(document.querySelector('.indicator')?.getAttribute('aria-label')).toBe(feedLabel);
  await click('[data-connection-manager-trigger]');
  await vi.waitFor(() => expect(document.body.textContent).toContain('Saved for next launch'));
  expect(document.body.textContent).toContain('In use');
  expect(document.body.textContent).toContain(direct.remoteUrl);
  expect(document.body.textContent).toContain('player@mud.fixture.invalid');
  expect(document.querySelector<HTMLInputElement>('input[name="ssh-host"]')?.value).toBe(
    SSH_DESTINATION.host,
  );
  expect(document.querySelector<HTMLInputElement>('input[name="ssh-username"]')?.value).toBe(
    SSH_DESTINATION.username,
  );
  expect(document.querySelector<HTMLInputElement>('input[name="ssh-port"]')?.value).toBe('');
  expect(document.body.textContent).not.toContain(replacementUrl);
  await input('ssh-host', 'unsaved.fixture.invalid');
  window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
  await vi.waitFor(() =>
    expect(document.activeElement?.hasAttribute('data-connection-manager-trigger')).toBe(true),
  );
  await click('[data-connection-manager-trigger]');
  await vi.waitFor(() =>
    expect(document.querySelector<HTMLInputElement>('input[name="ssh-host"]')?.value).toBe(
      SSH_DESTINATION.host,
    ),
  );
  expect(document.body.textContent).not.toContain('unsaved.fixture.invalid');
  expect(native.invoke.mock.calls.filter(([command]) => command === 'save_connection_settings')).toHaveLength(
    3,
  );
  await click('[aria-label="Close connection settings"]');
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

it('keeps an active Direct action pending across a different destination check and a late retirement fault', async ({
  signal,
}) => {
  vi.stubGlobal('__TAURI_INTERNALS__', {});
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  localStorage.setItem('imp.display-mode', 'expanded');
  localStorage.setItem('imp.actions', JSON.stringify([{ id: 'look', label: 'Look', command: 'look' }]));
  const direct: ConnectionSettings = {
    mode: 'direct',
    sshTarget: '',
    remoteUrl: 'wss://active.fixture.invalid/state',
    hasPairingToken: true,
  };
  const token = 'x'.repeat(43);
  const normal = Promise.withResolvers<string>();
  const fault = Promise.withResolvers<string>();
  const checks = [normal.promise, fault.promise];
  native.invoke.mockImplementation(async (command: string) => {
    if (command === 'connection_config') {
      return { mode: 'direct', stateUrl: direct.remoteUrl, authenticationToken: token };
    }
    if (command === 'connection_settings') return { running: direct, saved: direct, pendingRestart: false };
    if (command === 'check_managed_connection') return checks.shift();
    if (command === 'cancel_managed_connection') return;
    throw new Error(`Unexpected native command: ${command}`);
  });

  const runtime = await createRuntimeClients();
  app = mount(App, { target: document.body, props: runtime });
  await tick();
  expect(sockets).toHaveLength(1);
  const state = sockets[0]!;
  const context = { session: 'fixture', foreground: 1, connection: 1 };
  flushSync(() => {
    state.dispatchEvent(new Event('open'));
    state.dispatchEvent(
      new MessageEvent('message', {
        data: JSON.stringify({
          type: 'snapshot',
          protocol: 2,
          seq: 1,
          at: 100,
          context,
          state: {
            character: { name: 'Aria', hp: { current: 90, max: 100 }, mana: null, moves: null },
            target: null,
          },
        }),
      }),
    );
  });
  await click('button[aria-label="Look"]');
  expect(sockets).toHaveLength(2);
  const action = sockets[1]!;
  expect(action.url).toBe('wss://active.fixture.invalid/action');
  action.dispatchEvent(new Event('open'));
  expect(action.send.mock.calls).toEqual([
    [JSON.stringify({ type: 'auth', token })],
    [JSON.stringify({ type: 'action', protocol: 2, context, command: 'look' })],
  ]);

  async function openDraft() {
    await click('[data-connection-manager-trigger]');
    await waitForDomTransition(() => !!document.querySelector('input[name="remote-method"]'), signal);
    await click('input[name="remote-method"][value="managed"]');
    await input('ssh-host', SSH_DESTINATION.host);
    await input('ssh-username', SSH_DESTINATION.username);
  }

  await click('[aria-label="Settings"]');
  await openDraft();
  await click('button[name="check-managed-connection"]');
  expect(native.invoke).toHaveBeenLastCalledWith('check_managed_connection', {
    requestId: expect.any(Number),
    destination: { sshDestination: SSH_DESTINATION },
  });
  normal.resolve('live');
  await waitForDomTransition(() => !document.querySelector('button[name="cancel-managed-check"]'), signal);
  expect(document.querySelector('#managed-check-status')?.textContent).toContain('reported live state');
  await click('[aria-label="Close connection settings"]');
  await click('[aria-label="Close settings"]');
  expect(document.querySelector<HTMLButtonElement>('button[aria-label="Look"]')?.disabled).toBe(true);
  expect(document.querySelector('.action-feedback')?.textContent).toBe('Forwarding “Look”…');
  expect(action.close).not.toHaveBeenCalled();

  await click('[aria-label="Settings"]');
  await openDraft();
  await click('button[name="check-managed-connection"]');
  const requestId = native.invoke.mock.lastCall?.[1]?.requestId;
  await click('button[name="cancel-managed-check"]');
  expect(document.querySelector('#managed-check-status')?.textContent).toBe('Cancelling check…');
  await click('input[name="client-location"][value="local"]');
  await click('[aria-label="Close connection settings"]');
  expect(native.invoke.mock.calls.filter(([command]) => command === 'cancel_managed_connection')).toEqual([
    ['cancel_managed_connection', { requestId }],
  ]);
  fault.resolve('retirement_unconfirmed');
  await waitForDomTransition(() => !!document.querySelector('.connection-check-warning'), signal);
  const warning = document.querySelector('.connection-check-warning');
  expect(warning?.getAttribute('role')).toBe('alert');
  expect(warning?.textContent).toContain('Quit and reopen Imp before checking or saving connections.');
  await openDraft();
  expect(document.querySelector('#managed-check-restart-required')?.textContent).toBe(warning?.textContent);
  expect(document.querySelector('#managed-check-status')?.textContent).toBe('');
  expect(document.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled).toBe(true);
  const checkButton = document.querySelector('button[name="check-managed-connection"]');
  expect(checkButton?.getAttribute('aria-disabled')).toBe('true');
  expect(checkButton?.getAttribute('aria-describedby')).toContain('managed-check-restart-required');
  await click('button[name="check-managed-connection"]');
  expect(native.invoke.mock.calls.filter(([command]) => command === 'check_managed_connection')).toHaveLength(
    2,
  );
  await click('input[name="client-location"][value="local"]');
  expect(document.querySelector('#managed-check-restart-required')).not.toBeNull();
  expect(document.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled).toBe(true);
  flushSync(() =>
    document
      .querySelector('.connection-dialog form')!
      .dispatchEvent(new Event('submit', { cancelable: true })),
  );
  expect(native.invoke.mock.calls.filter(([command]) => command === 'save_connection_settings')).toHaveLength(
    0,
  );
  await click('[aria-label="Close connection settings"]');
  await click('[aria-label="Close settings"]');

  expect(document.querySelector('.action-feedback')?.textContent).toBe('Forwarding “Look”…');
  action.dispatchEvent(
    new MessageEvent('message', {
      data: JSON.stringify({
        type: 'action-result',
        protocol: 2,
        status: 'rejected',
        detail: 'fixture action result',
      }),
    }),
  );
  await waitForDomTransition(
    () =>
      document.querySelector('.action-feedback')?.textContent ===
      'Action was not forwarded: fixture action result',
    signal,
  );
  expect(document.querySelector<HTMLButtonElement>('button[aria-label="Look"]')?.disabled).toBe(false);
  expect(sockets).toEqual([state, action]);
  expect(state.close).not.toHaveBeenCalled();
  expect(action.close).toHaveBeenCalledOnce();
});
