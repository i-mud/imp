import { flushSync, mount, tick, unmount, type ComponentProps } from 'svelte';
import { fromStore, writable } from 'svelte/store';
import { afterEach, describe, expect, it, vi } from 'vitest';

import ConnectionDialog from '../src/components/ConnectionDialog.svelte';
import type {
  ConnectionSettings,
  ConnectionSettingsState,
  ConnectionSettingsUpdate,
} from '../src/lib/tunnel.ts';

const DIRECT: ConnectionSettings = {
  mode: 'direct',
  sshTarget: 'saved-host',
  remoteUrl: 'wss://fixture.invalid/state',
  hasPairingToken: true,
};
const LOCAL: ConnectionSettings = {
  mode: 'local',
  sshTarget: '',
  remoteUrl: '',
  hasPairingToken: false,
};
const TOKEN = 'x'.repeat(42) + 'A';

function connectionState(saved: ConnectionSettings | null): ConnectionSettingsState {
  return { running: saved, saved, pendingRestart: false };
}

const mounted: object[] = [];
afterEach(async () => {
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  for (const component of mounted.splice(0)) await unmount(component);
  document.body.replaceChildren();
  vi.restoreAllMocks();
});

async function setup(
  settings: ConnectionSettings | null = DIRECT,
  onsave = vi.fn<ComponentProps<typeof ConnectionDialog>['onsave']>().mockResolvedValue(true),
  loadError: string | null = null,
) {
  // JSDOM ties document focus to element focus instead of tracking an active window.
  vi.spyOn(document, 'hasFocus').mockReturnValue(true);
  const settingsStore = writable(connectionState(settings));
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
          loadError,
          onsave,
          onclose,
        },
      }),
    );
  });
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  const locations = target.querySelectorAll<HTMLInputElement>('input[name="client-location"]');
  const expected = loadError
    ? target.querySelector<HTMLButtonElement>('[aria-label="Close connection settings"]')
    : ([...locations].find((radio) => radio.checked) ?? locations[0]);
  expect(document.activeElement).toBe(expected);
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
  if (mode !== 'local') {
    const remote = document.querySelector<HTMLInputElement>('input[name="client-location"][value="remote"]')!;
    if (!remote.checked) await click(remote);
  }
  if (mode === 'external') {
    const advanced = document.querySelector<HTMLDetailsElement>('details')!;
    if (!advanced.open) await click(advanced.querySelector('summary')!);
  }
  const radio = document.querySelector<HTMLInputElement>(`input[type="radio"][value="${mode}"]`);
  if (!radio) throw new Error(`Missing route: ${mode}`);
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

describe('ConnectionDialog route editor', () => {
  it('opens without an implicit route and saves Local without remote fields', async () => {
    const { onsave } = await setup(null);
    expect(document.querySelector('legend')?.textContent).toBe('Where does your MUD client run?');
    expect(document.querySelector('dd')?.textContent).toContain('No connection configured');
    expect(document.querySelector('input:checked')).toBeNull();
    expect(document.querySelector('input[name="remote-method"]')).toBeNull();
    expect(button('Save').disabled).toBe(true);
    await submit();
    expect(onsave).not.toHaveBeenCalled();

    await choose('local');
    expect(document.querySelector('input:not([type="radio"])')).toBeNull();
    await submit();
    expect(onsave).toHaveBeenCalledExactlyOnceWith({ mode: 'local' });
  });

  it('reveals only the selected remote form and keeps manual forwarding under Advanced', async () => {
    const { onsave } = await setup(null);
    await click(document.querySelector<HTMLInputElement>('input[name="client-location"][value="remote"]')!);
    expect(document.querySelector('fieldset:nth-of-type(2) legend')?.textContent).toBe(
      'How should Imp connect?',
    );
    expect(document.querySelector('input[name="remote-method"]:checked')).toBeNull();
    expect(document.querySelector('details')?.open).toBe(false);
    expect(document.querySelector('details summary')?.textContent).toBe('Advanced');
    expect(button('Save').disabled).toBe(true);

    await choose('managed');
    const ssh = document.querySelector<HTMLInputElement>('input[type="text"]');
    expect(ssh?.labels?.[0]?.textContent).toContain('SSH target');
    expect(document.querySelector('input[type="url"], input[type="password"]')).toBeNull();
    await input('text', '   ');
    await submit();
    expect(document.activeElement).toBe(ssh);
    expect(ssh?.getAttribute('aria-invalid')).toBe('true');
    expect(document.querySelector('[role="alert"]')?.textContent).toBe('Enter your SSH target.');
    expect(onsave).not.toHaveBeenCalled();

    await input('text', ' office-host ');
    await submit();
    expect(onsave).toHaveBeenNthCalledWith(1, { mode: 'managed', sshTarget: 'office-host' });

    await choose('external');
    expect(document.querySelector('details')?.open).toBe(true);
    expect(document.querySelector('details input:checked + span')?.textContent).toContain(
      'Use my own SSH tunnel',
    );
    expect(document.querySelector('details input:checked + span small')?.textContent).toBe('External SSH');
    expect(document.querySelector('input:not([type="radio"])')).toBeNull();
    await submit();
    expect(onsave).toHaveBeenNthCalledWith(2, { mode: 'external' });
  });

  it('reopens a saved route on its own location and method without further navigation', async () => {
    const tunnel = { ...LOCAL, mode: 'external' as const };
    const { onsave } = await setup(tunnel);
    expect(document.querySelector<HTMLInputElement>('input[name="client-location"]:checked')?.value).toBe(
      'remote',
    );
    expect(document.querySelector<HTMLInputElement>('input[name="remote-method"]:checked')?.value).toBe(
      'external',
    );
    expect(document.querySelector('details')?.open).toBe(true);
    expect(document.activeElement).toBe(document.querySelector('input[name="client-location"]:checked'));
    expect(button('Save').disabled).toBe(false);
    const labels = [...document.querySelectorAll('.choice small')].map((item) => item.textContent);
    expect(labels).toEqual(['Managed SSH', 'Direct WSS', 'External SSH']);
    await submit();
    expect(onsave).toHaveBeenCalledExactlyOnceWith({ mode: 'external' });
  });

  it('reopens Local and Direct with their fields visible and no technical names on Local', async () => {
    await setup(LOCAL);
    expect(document.querySelector<HTMLInputElement>('input[name="client-location"]:checked')?.value).toBe(
      'local',
    );
    expect(document.querySelector('small')).toBeNull();
    expect(document.querySelector('input[name="remote-method"]')).toBeNull();
    await unmount(mounted.pop()!);
    document.body.replaceChildren();
    await setup(DIRECT);
    expect(document.querySelector<HTMLInputElement>('input[name="remote-method"]:checked')?.value).toBe(
      'direct',
    );
    expect(document.querySelector<HTMLInputElement>('input[type="url"]')?.value).toBe(DIRECT.remoteUrl);
  });

  it('keeps user edits when only the save error changes', async () => {
    const { errorStore } = await setup({ ...DIRECT, mode: 'managed', sshTarget: 'saved-host' });
    await input('text', 'typed-host');
    flushSync(() => errorStore.set('Could not save native connection settings.'));
    expect(document.querySelector<HTMLInputElement>('input[type="text"]')?.value).toBe('typed-host');
  });

  it('shows the running route separately from the saved pending route', async () => {
    const { settingsStore } = await setup(DIRECT);
    expect(document.querySelector('dl')?.textContent).toContain('In use');
    expect(document.querySelector('dl')?.textContent).not.toContain('Saved for next launch');
    flushSync(() => settingsStore.set({ running: DIRECT, saved: LOCAL, pendingRestart: true }));
    const summaries = [...document.querySelectorAll('dl > div')].map((row) => row.textContent);
    expect(summaries[0]).toContain(DIRECT.remoteUrl);
    expect(summaries[1]).toContain('Saved for next launch');
    expect(summaries[1]).toContain('This computer');

    flushSync(() => settingsStore.set({ running: DIRECT, saved: DIRECT, pendingRestart: true }));
    expect(document.querySelector('dl')?.textContent).toContain('A different pairing token is saved.');
  });

  it('shows the settings-read error and does not offer an editor or a save', async () => {
    const message = 'Restore the saved connection file, then restart Imp.';
    const { onsave } = await setup(null, undefined, message);
    expect(document.querySelector('[role="alert"]')?.textContent).toBe(message);
    expect(document.querySelector('form')).toBeNull();
    expect(onsave).not.toHaveBeenCalled();
  });

  it('wraps keyboard focus inside the modal at both tab boundaries', async () => {
    await setup();
    const titleClose = document.querySelector<HTMLButtonElement>('[aria-label="Close connection settings"]')!;
    const footerClose = button('Close');
    footerClose.focus();
    footerClose.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }));
    expect(document.activeElement).toBe(titleClose);
    titleClose.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true }));
    expect(document.activeElement).toBe(footerClose);
  });
});

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
    expect(document.querySelector('[role="status"]')?.textContent).toContain('Connection settings saved.');
    await vi.waitFor(() => {
      flushSync();
      expect(document.activeElement).toBe(button('Save'));
    });
  });

  it('Cancel keeps pending edits and the saved settings without saving', async () => {
    const { onsave } = await setup();
    const pendingToken = TOKEN;
    await choose('direct');
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
      [...document.querySelectorAll('button, input')].every((element) => element.matches(':disabled')),
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
      document.querySelector<HTMLInputElement>('input[name="client-location"][value="remote"]')!.click();
      staleConfirm.click();
    });
    await settle();
    expect(warning()).toBeNull();
    expect(onsave).not.toHaveBeenCalled();
    await choose('external');
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
    flushSync(() => settingsStore.set(connectionState({ ...DIRECT, sshTarget: 'refreshed-host' })));
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

  it('reuses only the saved endpoint and requires a replacement token for a new endpoint', async () => {
    const { onsave } = await setup();
    await choose('direct');
    const keep = 'Leave the token blank to keep your saved credential.';
    const equivalentUrl = 'wss://FIXTURE.invalid:443/state';
    const replacementUrl = 'wss://replacement.invalid/state';
    const token = document.querySelector<HTMLInputElement>('input[type="password"]')!;
    expect(token.value).toBe('');
    expect(document.body.textContent).toContain(keep);

    await input('url', equivalentUrl);
    expect(document.body.textContent).toContain(keep);
    await submit();
    expect(onsave).toHaveBeenNthCalledWith(1, { mode: 'direct', remoteUrl: equivalentUrl });

    await input('url', replacementUrl);
    expect(document.body.textContent).not.toContain(keep);
    await submit();
    expect(onsave).toHaveBeenCalledTimes(1);
    expect(token.getAttribute('aria-invalid')).toBe('true');
    expect(document.activeElement).toBe(token);

    await input('password', TOKEN);
    await input('url', 'wss://other.invalid/state');
    expect(token.value).toBe('');
    await input('url', replacementUrl);
    await input('password', TOKEN);
    await submit();
    expect(onsave).toHaveBeenNthCalledWith(2, {
      mode: 'direct',
      remoteUrl: replacementUrl,
      pairingToken: TOKEN,
    });
    expect(token.value).toBe('');
    expect(warning()).toBeNull();
  });

  it.each([
    'https://fixture.invalid/state',
    'wss://me:secret@fixture.invalid/state',
    'wss://fixture.invalid/state?token=secret',
    'wss://fixture.invalid/state#',
    'wss://fixture.invalid/other',
  ])('blocks an unsafe or invalid Direct URL', async (url) => {
    const { onsave } = await setup(null);
    await choose('direct');
    await input('url', url);
    await input('password', TOKEN);
    await submit();
    const field = document.querySelector('input[type="url"]');
    expect(field?.getAttribute('aria-invalid')).toBe('true');
    expect(document.activeElement).toBe(field);
    expect(document.querySelector('[role="alert"]')?.textContent).not.toContain('secret');
    expect(onsave).not.toHaveBeenCalled();
  });

  it('requires a canonical token when no Direct credential is saved', async () => {
    const { onsave } = await setup(null);
    await choose('direct');
    await input('url', DIRECT.remoteUrl);
    await input('password', 'x'.repeat(43));
    await submit();
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('Enter a valid 43-character');
    expect(onsave).not.toHaveBeenCalled();
    await input('password', TOKEN);
    await submit();
    expect(onsave).toHaveBeenCalledExactlyOnceWith({
      mode: 'direct',
      remoteUrl: DIRECT.remoteUrl,
      pairingToken: TOKEN,
    });
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

describe('ConnectionDialog save focus', () => {
  it.each([
    { success: true, focus: 'lost' },
    { success: false, focus: 'lost' },
    { success: true, focus: 'outside' },
    { success: true, focus: 'inactive' },
  ])('handles $focus focus after an ordinary save (success: $success)', async ({ success, focus }) => {
    const deferred = Promise.withResolvers<boolean>();
    await setup({ ...DIRECT, mode: 'external' }, vi.fn().mockReturnValue(deferred.promise));
    await choose('external');
    const save = button('Save');
    save.focus();
    await click(save);
    expect(button('Saving…').disabled).toBe(true);

    const outside = document.createElement('button');
    document.body.append(outside);
    outside.focus();
    if (focus !== 'outside') outside.blur();
    if (focus === 'inactive') vi.mocked(document.hasFocus).mockReturnValue(false);

    deferred.resolve(success);
    await vi.waitFor(() => {
      expect(button('Save').disabled).toBe(false);
      expect(document.activeElement).toBe(
        focus === 'lost' ? save : focus === 'outside' ? outside : document.body,
      );
    });
  });
});
