import { flushSync, mount, tick, unmount, type ComponentProps } from 'svelte';
import { fromStore, writable } from 'svelte/store';
import { afterEach, describe, expect, it, vi } from 'vitest';

import ConnectionDialog from '../src/components/ConnectionDialog.svelte';
import type { ConnectionSettings, ConnectionSettingsUpdate } from '../src/lib/tunnel.ts';

const DIRECT: ConnectionSettings = {
  mode: 'direct',
  sshTarget: 'saved-host',
  remoteUrl: 'wss://fixture.invalid/state',
  hasPairingToken: true,
};
const mounted: object[] = [];
afterEach(async () => {
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  for (const component of mounted.splice(0)) await unmount(component);
  document.body.replaceChildren();
});

async function setup(
  settings = DIRECT,
  onsave = vi.fn<ComponentProps<typeof ConnectionDialog>['onsave']>().mockResolvedValue(true),
) {
  const settingsStore = writable(settings);
  const errorStore = writable<string | null>(null);
  const currentSettings = fromStore(settingsStore);
  const currentError = fromStore(errorStore);
  const onclose = vi.fn();
  const target = document.createElement('div');
  document.body.append(target);
  flushSync(() => {
    mounted.push(
      mount(ConnectionDialog, {
        target,
        props: {
          get settings() {
            return currentSettings.current;
          },
          get saveError() {
            return currentError.current;
          },
          loadError: null,
          onsave,
          onclose,
        },
      }),
    );
  });
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  expect(document.activeElement).toBe(target.querySelector('input[name="connection-mode"]:checked'));
  return { onsave, onclose, settingsStore, errorStore };
}

function button(label: string): HTMLButtonElement {
  const found = [...document.querySelectorAll('button')].find((item) => item.textContent?.trim() === label);
  if (!found) throw new Error(`Missing button: ${label}`);
  return found;
}

async function settle(): Promise<void> {
  await tick();
  flushSync();
}

async function click(element: HTMLElement): Promise<void> {
  flushSync(() => element.click());
  await settle();
}

async function choose(mode: string): Promise<void> {
  const radio = document.querySelector<HTMLInputElement>(`input[name="connection-mode"][value="${mode}"]`);
  if (!radio) throw new Error(`Missing mode: ${mode}`);
  await click(radio);
}

async function input(type: string, value: string): Promise<void> {
  const field = document.querySelector<HTMLInputElement>(`input[type="${type}"]`);
  if (!field) throw new Error(`Missing input: ${type}`);
  flushSync(() => {
    field.value = value;
    field.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await settle();
}

async function submit(): Promise<void> {
  flushSync(() =>
    document.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })),
  );
  await settle();
}

function warning(): HTMLElement | null {
  return document.querySelector('#discard-direct-warning');
}

describe('ConnectionDialog credential-discard interactions', () => {
  it.each(['local', 'external', 'managed'])('requires explicit approval for Direct → %s', async (mode) => {
    const { onsave } = await setup();
    await choose(mode);
    if (mode === 'managed') await input('text', 'reviewed-host');
    await click(button('Save'));

    expect(onsave).not.toHaveBeenCalled();
    expect(warning()?.getAttribute('role')).toBe('alert');
    const message = warning()?.textContent?.replace(/\s+/g, ' ');
    expect(message).toMatch(/locally stored.*Direct URL.*pairing token/);
    expect(message).toMatch(/Cancel.*saved configuration.*pending changes/);
    expect(message).toMatch(/token.*deliberately rotating.*server credential/);
    expect(message).toMatch(/after restart/);
    expect(document.activeElement).toBe(button('Cancel'));
    const confirm = button('Remove credentials and save');
    expect(confirm.type).toBe('button');
    expect(confirm.getAttribute('aria-describedby')).toBe(warning()?.id);

    await click(confirm);
    expect(onsave).toHaveBeenCalledExactlyOnceWith(
      mode === 'managed' ? { mode, sshTarget: 'reviewed-host' } : { mode },
    );
    expect(warning()).toBeNull();
    expect(document.querySelector('[role="status"]')?.textContent).toContain('Restart Imp');
    await vi.waitFor(() => {
      flushSync();
      expect(document.activeElement).toBe(button('Save'));
    });
  });

  it('Cancel keeps pending edits and the saved settings without saving', async () => {
    const { onsave } = await setup();
    const pendingToken = 'p'.repeat(43);
    await input('url', 'wss://pending.fixture.invalid/state');
    await input('password', pendingToken);
    await choose('managed');
    await input('text', 'pending-host');
    await submit();
    await click(button('Cancel'));
    expect(onsave).not.toHaveBeenCalled();
    expect(warning()).toBeNull();
    expect(document.querySelector<HTMLInputElement>('input[type="text"]')?.value).toBe('pending-host');
    expect(document.querySelector<HTMLInputElement>('input[value="managed"]')?.checked).toBe(true);
    expect(document.activeElement).toBe(button('Save'));
    await submit();
    expect(warning()).not.toBeNull();
    expect(onsave).not.toHaveBeenCalled();
    await click(button('Cancel'));
    await choose('direct');
    expect(document.querySelector<HTMLInputElement>('input[type="url"]')?.value).toBe(
      'wss://pending.fixture.invalid/state',
    );
    expect(document.querySelector<HTMLInputElement>('input[type="password"]')?.value).toBe(pendingToken);
    expect(onsave).not.toHaveBeenCalled();
  });

  it('repeated submits cannot approve removal or duplicate a deferred save', async () => {
    const deferred = Promise.withResolvers<boolean>();
    const { onsave, onclose } = await setup(DIRECT, vi.fn().mockReturnValue(deferred.promise));
    await choose('managed');
    await input('text', 'reviewed-host');
    await submit();
    await submit();
    await submit();
    expect(onsave).not.toHaveBeenCalled();
    const confirm = button('Remove credentials and save');
    flushSync(() => {
      confirm.click();
      confirm.click();
    });
    await settle();
    await submit();
    expect(onsave).toHaveBeenCalledExactlyOnceWith({ mode: 'managed', sshTarget: 'reviewed-host' });
    expect(
      [...document.querySelectorAll('button, input')].every(
        (element) => (element as HTMLInputElement).disabled,
      ),
    ).toBe(true);
    await click(button('Saving…'));
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(onclose).not.toHaveBeenCalled();
    deferred.resolve(true);
    await settle();
    expect(button('Save').disabled).toBe(false);
    expect(document.querySelector('[role="status"]')).not.toBeNull();
  });

  it('mode changes invalidate approval and require a fresh warning', async () => {
    const { onsave } = await setup();
    await choose('local');
    await submit();
    const staleConfirm = button('Remove credentials and save');
    flushSync(() => {
      document.querySelector<HTMLInputElement>('input[value="external"]')!.click();
      staleConfirm.click();
    });
    await settle();
    expect(warning()).toBeNull();
    expect(onsave).not.toHaveBeenCalled();
    await submit();
    expect(onsave).not.toHaveBeenCalled();
    await click(button('Remove credentials and save'));
    expect(onsave).toHaveBeenCalledExactlyOnceWith({ mode: 'external' });
  });

  it('SSH target edits invalidate approval and only a fresh confirmation saves the edited target', async () => {
    const { onsave } = await setup();
    await choose('managed');
    await input('text', 'first-host');
    await submit();
    const staleConfirm = button('Remove credentials and save');
    flushSync(() => {
      const field = document.querySelector<HTMLInputElement>('input[type="text"]')!;
      field.value = 'second-host';
      field.dispatchEvent(new Event('input', { bubbles: true }));
      staleConfirm.click();
    });
    await settle();
    expect(warning()).toBeNull();
    expect(onsave).not.toHaveBeenCalled();
    await submit();
    expect(onsave).not.toHaveBeenCalled();
    await click(button('Remove credentials and save'));
    expect(onsave).toHaveBeenCalledExactlyOnceWith({ mode: 'managed', sshTarget: 'second-host' });
  });

  it('failed saves show saveError and require confirmation again before retry', async () => {
    const onsave = vi
      .fn<(update: ConnectionSettingsUpdate) => Promise<boolean>>()
      .mockResolvedValueOnce(false)
      .mockResolvedValueOnce(true);
    const { errorStore } = await setup(DIRECT, onsave);
    await choose('local');
    await submit();
    await click(button('Remove credentials and save'));
    flushSync(() => errorStore.set('Could not save native connection settings.'));
    expect(document.querySelector('[role="alert"]')?.textContent).toBe(
      'Could not save native connection settings.',
    );
    expect(document.querySelector('[role="status"]')).toBeNull();
    expect(warning()).toBeNull();
    await submit();
    expect(onsave).toHaveBeenCalledTimes(1);
    expect(warning()).not.toBeNull();
    await click(button('Remove credentials and save'));
    expect(onsave).toHaveBeenCalledTimes(2);
    expect(onsave).toHaveBeenLastCalledWith({ mode: 'local' });
  });

  it.each(['Close', 'titlebar', 'Escape'])('%s dismisses the warning without saving', async (action) => {
    const { onsave, onclose } = await setup();
    await choose('local');
    await submit();
    if (action === 'Escape') {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    } else {
      await click(
        action === 'titlebar'
          ? document.querySelector<HTMLButtonElement>('[aria-label="Close connection settings"]')!
          : button('Close'),
      );
    }
    expect(onclose).toHaveBeenCalledOnce();
    expect(onsave).not.toHaveBeenCalled();
  });

  it('refreshing settings cancels stale approval and resets pending fields', async () => {
    const { onsave, settingsStore } = await setup();
    await choose('managed');
    await input('text', 'pending-host');
    await submit();
    flushSync(() => settingsStore.set({ ...DIRECT, sshTarget: 'refreshed-host' }));
    expect(warning()).toBeNull();
    expect(document.querySelector<HTMLInputElement>('input[value="direct"]')?.checked).toBe(true);
    expect(onsave).not.toHaveBeenCalled();
    await choose('managed');
    expect(document.querySelector<HTMLInputElement>('input[type="text"]')?.value).toBe('refreshed-host');
    await submit();
    expect(onsave).not.toHaveBeenCalled();
    await click(button('Remove credentials and save'));
    expect(onsave).toHaveBeenCalledExactlyOnceWith({ mode: 'managed', sshTarget: 'refreshed-host' });
  });

  it('Direct → Direct omits an empty token and passes an explicit replacement unchanged', async () => {
    const { onsave } = await setup();
    await input('url', 'wss://replacement.invalid/state');
    await submit();
    expect(warning()).toBeNull();
    expect(onsave).toHaveBeenNthCalledWith(1, {
      mode: 'direct',
      remoteUrl: 'wss://replacement.invalid/state',
    });
    const replacement = 'x'.repeat(43);
    await input('password', replacement);
    await submit();
    expect(onsave).toHaveBeenNthCalledWith(2, {
      mode: 'direct',
      remoteUrl: 'wss://replacement.invalid/state',
      pairingToken: replacement,
    });
    expect(warning()).toBeNull();
  });

  it.each([
    { ...DIRECT, hasPairingToken: false },
    { ...DIRECT, mode: 'external' as const },
  ])(
    'ordinary saves do not warn without a saved Direct credential ($mode / $hasPairingToken)',
    async (settings) => {
      const { onsave } = await setup(settings);
      await choose('local');
      await submit();
      expect(warning()).toBeNull();
      expect(onsave).toHaveBeenCalledExactlyOnceWith({ mode: 'local' });
    },
  );
});
