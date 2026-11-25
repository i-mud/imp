import { flushSync, mount, tick, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import Hud from '../src/components/Hud.svelte';
import { loadActionDefinitions, saveActionDefinitions } from '../src/lib/action/definitions.ts';
import type { ActionSink } from '../src/lib/action/types.ts';
import {
  DEFAULT_LOW_HEALTH_ALERT,
  loadAlertDefinitions,
  saveAlertDefinitions,
} from '../src/lib/alerts/definitions.ts';
import { INITIAL_MODEL } from '../src/lib/hud/model.ts';
import { saveDisplayMode, type DisplayMode } from '../src/lib/hud/presentation.ts';
import * as tunnel from '../src/lib/tunnel.ts';
import { closeWindow } from '../src/lib/window.ts';

vi.mock(import('../src/lib/window.ts'), async (importOriginal) => ({
  ...(await importOriginal()),
  closeWindow: vi.fn(),
}));

const context = { session: 'fixture', foreground: 1, connection: 1 };
const action = { id: 'look', label: 'Look', command: '  look  ' };
const alert = DEFAULT_LOW_HEALTH_ALERT;
const send = vi.fn<ActionSink['send']>();
let hud: object | undefined;

beforeEach(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    },
  );
});

afterEach(async () => {
  if (hud) await unmount(hud);
  hud = undefined;
  vi.useRealTimers();
  document.body.replaceChildren();
  localStorage.clear();
  send.mockReset();
  vi.mocked(closeWindow).mockClear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function mountHud(mode: DisplayMode, populated = false) {
  saveDisplayMode(mode);
  saveActionDefinitions(populated ? [action] : []);
  saveAlertDefinitions(populated ? [alert] : []);
  hud = mount(Hud, {
    target: document.body,
    props: {
      model: { ...INITIAL_MODEL, context },
      actionSink: { id: 'fixture', label: 'Fixture', send },
      subscribeText: () => () => {},
    },
  });
  flushSync();
}

function control<T extends HTMLElement = HTMLElement>(selector: string, root: ParentNode = document): T {
  const element = root.querySelector<T>(selector);
  if (!element) throw new Error(`Missing control: ${selector}`);
  return element;
}

function click(element: HTMLElement) {
  flushSync(() => element.click());
}

function keyboardActivate(button: HTMLElement) {
  button.focus();
  // jsdom does not perform the browser's default Enter/Space activation.
  flushSync(() => button.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 0 })));
}

function pressKey(key: string, shiftKey = false, repeat = false) {
  const event = new KeyboardEvent('keydown', { key, shiftKey, repeat, bubbles: true, cancelable: true });
  return flushSync(() => document.activeElement!.dispatchEvent(event));
}

function escape() {
  pressKey('Escape');
}

function pointerDown(target: HTMLElement) {
  flushSync(() => target.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true })));
}

function shortcut(label: string) {
  return control<HTMLButtonElement>(`button[aria-label="${label}"]`);
}

function expectFocus(target: HTMLElement) {
  return vi.waitFor(() => expect(document.activeElement).toBe(target));
}

function input(element: HTMLInputElement | HTMLTextAreaElement, value: string) {
  flushSync(() => {
    element.value = value;
    element.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

const surfaces = [
  { mode: 'expanded', kind: 'action', trigger: null },
  { mode: 'compact', kind: 'action', trigger: 'Actions' },
  { mode: 'expanded', kind: 'alert', trigger: 'Alerts' },
  { mode: 'compact', kind: 'alert', trigger: 'Alerts' },
] as const;

describe.each(surfaces)('$mode $kind management', ({ mode, kind, trigger }) => {
  const quick = `${trigger === null ? '.action-bar' : ''}[aria-label="Saved ${kind}s"]`;
  const manage = `button[aria-label="Manage ${kind}s"]`;
  const editor = `[role="dialog"][aria-labelledby="${kind}-dialog-title"]`;
  const saved = `[aria-labelledby="saved-${kind}s-title"]`;
  const item = kind === 'action' ? '.action-btn' : 'input[type="checkbox"]';
  const headerClose = `button[aria-label="Close ${kind} manager"]`;

  function openQuick() {
    if (trigger !== null) keyboardActivate(control(`button[aria-label="${trigger}"]`));
    return control(quick);
  }

  it.each([false, true])('opens the editor without changes (populated=%s)', async (populated) => {
    mountHud(mode, populated);
    const writes = vi.spyOn(Storage.prototype, 'setItem');
    const surface = openQuick();
    expect(surface.querySelectorAll(item)).toHaveLength(populated ? 1 : 0);
    if (populated) {
      expect(surface.textContent).toContain(kind === 'action' ? action.label : alert.label);
    } else if (mode === 'compact' || kind === 'alert') {
      expect(surface.textContent).toContain(`No ${kind}s defined.`);
    }
    const entry = control<HTMLButtonElement>(manage, surface);
    expect(entry.textContent?.trim()).toBe('Manage');
    expect(entry.disabled).toBe(false);
    if (trigger !== null) await expectFocus(entry);
    keyboardActivate(entry);

    const dialog = control(editor);
    expect(entry.isConnected).toBe(false);
    expect(document.activeElement).toBe(control('form input[type="text"]', dialog));
    expect(control(saved, dialog).textContent).toContain(
      populated ? (kind === 'action' ? action.label : alert.label) : `No ${kind}s defined.`,
    );
    if (kind === 'alert' && populated) {
      expect(control<HTMLInputElement>(`${saved} ${item}`, dialog).checked).toBe(true);
    }
    expect(send).not.toHaveBeenCalled();
    expect(writes).not.toHaveBeenCalled();
    expect(loadAlertDefinitions()).toEqual(populated ? [alert] : []);
  });

  it.each(['header', 'footer', 'Escape'])('returns to the remounted Manage button via %s', async (close) => {
    mountHud(mode, true);
    const entry = control(manage, openQuick());
    click(entry);
    const dialog = control(editor);
    if (close === 'Escape') {
      const remove = control(`${saved} button:last-child`, dialog);
      expect(remove.textContent?.trim()).toBe('Delete');
      click(remove);
      expect(control(saved, dialog).textContent).toContain(`No ${kind}s defined.`);
      escape();
    } else {
      const button = control(close === 'header' ? headerClose : '.form-actions button:last-child', dialog);
      if (close === 'footer') expect(button.textContent?.trim()).toBe('Close');
      click(button);
    }

    expect(document.querySelector(editor)).toBeNull();
    expect(document.querySelector('[aria-label="Imp settings"]')).toBeNull();
    const returnedSurface = control(quick);
    const returnedEntry = control(manage, returnedSurface);
    expect(entry.isConnected).toBe(false);
    expect(returnedEntry).not.toBe(entry);
    await expectFocus(returnedEntry);
    const count = close === 'Escape' ? 0 : 1;
    expect(returnedSurface.querySelectorAll(item)).toHaveLength(count);
    expect(kind === 'action' ? loadActionDefinitions() : loadAlertDefinitions()).toHaveLength(count);
    expect(send).not.toHaveBeenCalled();
  });

  if (trigger !== null) {
    it('returns focus to the owning trigger when Escape dismisses its quick surface', async () => {
      mountHud(mode);
      await expectFocus(control(manage, openQuick()));
      escape();
      expect(document.querySelector(quick)).toBeNull();
      const owner = shortcut(trigger);
      await expectFocus(owner);
      expect(owner.getAttribute('aria-expanded')).toBe('false');
    });
  }
});

const panels = {
  Alerts: {
    selector: '[aria-label="Saved alerts"]',
    focus: 'button[aria-label="Manage alerts"]',
    closeLabel: 'Close alerts',
    icon: 'bell',
  },
  Actions: {
    selector: '[aria-label="Saved actions"]',
    focus: 'button[aria-label="Manage actions"]',
    closeLabel: 'Close actions',
    icon: 'swords',
  },
  Settings: {
    selector: '[aria-label="Imp settings"]',
    focus: 'input[name="display-mode"]:checked',
    closeLabel: 'Close settings',
    icon: 'settings',
  },
} as const;
type PanelShortcut = keyof typeof panels;

const shortcutOrder = ['Alerts', 'Actions', 'Settings'] as const;

describe.each(['expanded', 'compact'] as const)('%s panel shortcuts', (mode) => {
  function expectShortcuts(active?: PanelShortcut) {
    const cluster = control(mode === 'compact' ? '.compact-controls' : '.titlebar');
    const buttons = [...cluster.querySelectorAll('button')];
    expect(buttons.map((button) => button.getAttribute('aria-label'))).toEqual([
      ...shortcutOrder.map((label) => (active === label ? panels[label].closeLabel : label)),
      'Close Imp',
    ]);
    for (const button of buttons) {
      expect(button.disabled).toBe(false);
      expect(button.closest('[hidden], [aria-hidden="true"], .hidden')).toBeNull();
    }
    for (const [index, label] of shortcutOrder.entries()) {
      const button = buttons[index]!;
      const { icon, selector } = panels[label];
      expect(button.querySelector(`svg.lucide-${icon}[aria-hidden="true"]`)).not.toBeNull();
      const dismiss = button.querySelector('svg.lucide-x');
      expect(dismiss?.getAttribute('aria-hidden')).toBe(active === label ? 'true' : undefined);
      expect(button.getAttribute('aria-expanded')).toBe(String(active === label));
      if (active === label) expect(button.getAttribute('aria-controls')).toBe(control(selector).id);
    }
  }

  it('switches directly between every pair of panels and focuses the new panel', async () => {
    mountHud(mode);
    expectShortcuts();

    for (const label of [
      'Settings',
      'Alerts',
      'Actions',
      'Settings',
      'Actions',
      'Alerts',
      'Settings',
    ] as const) {
      keyboardActivate(shortcut(label));
      const { selector, focus } = panels[label];
      const surface = control(selector);
      expectShortcuts(label);
      for (const other of Object.values(panels)) {
        if (other.selector !== selector) expect(document.querySelector(other.selector)).toBeNull();
      }
      await expectFocus(control(focus, surface));
    }

    escape();
    expect(document.querySelector(panels.Settings.selector)).toBeNull();
    expectShortcuts();
    await expectFocus(shortcut('Settings'));
    expect(closeWindow).not.toHaveBeenCalled();
  });

  it.each(shortcutOrder)('toggles %s or restores focus on Escape', async (label) => {
    mountHud(mode);
    keyboardActivate(shortcut(label));
    const { selector, closeLabel } = panels[label];
    const surface = control(selector);
    expectShortcuts(label);
    const collapse = shortcut(closeLabel);
    pointerDown(collapse);
    expect(surface.isConnected).toBe(true);
    click(collapse);
    expect(surface.isConnected).toBe(false);
    expectShortcuts();
    await expectFocus(shortcut(label));

    keyboardActivate(shortcut(label));
    const reopened = control(selector);
    await expectFocus(control(panels[label].focus, reopened));
    escape();
    expect(reopened.isConnected).toBe(false);
    expectShortcuts();
    await expectFocus(shortcut(label));
    expect(closeWindow).not.toHaveBeenCalled();
  });

  it.each(['Actions', 'Alerts'] as const)('returns to routed %s after editing', async (label) => {
    mountHud(mode, true);
    const source = label === 'Actions' ? 'Alerts' : 'Actions';
    keyboardActivate(shortcut(source));
    keyboardActivate(shortcut(label));

    const { selector, focus } = panels[label];
    const surface = control(selector);
    const contextualActions = mode === 'expanded' && label === 'Actions';
    if (contextualActions) {
      expect(surface.id).toBe('expanded-actions-panel');
      expect(document.querySelector('.action-bar')).toBeNull();
    }
    const entry = control(focus, surface);
    keyboardActivate(entry);
    const editor = `[role="dialog"][aria-labelledby="${label === 'Actions' ? 'action' : 'alert'}-dialog-title"]`;
    expect(document.querySelector(editor)).not.toBeNull();
    expect(surface.isConnected).toBe(false);
    escape();
    expect(document.querySelector(editor)).toBeNull();
    const returned = control(selector);
    expect(returned.id).toBe(surface.id);
    expectShortcuts(label);
    if (contextualActions) expect(document.querySelector('.action-bar')).toBeNull();
    await expectFocus(control(focus, returned));

    escape();
    expect(returned.isConnected).toBe(false);
    expectShortcuts();
    await expectFocus(shortcut(label));
    if (contextualActions) expect(document.querySelector('.action-bar')).not.toBeNull();
    expect(send).not.toHaveBeenCalled();
  });

  it('returns to routed Settings after closing Connection management', async () => {
    mountHud(mode);
    keyboardActivate(shortcut('Alerts'));
    keyboardActivate(shortcut('Settings'));
    const { selector } = panels.Settings;
    const entry = control('[data-connection-manager-trigger]', control(selector));
    click(entry);
    expect(entry.isConnected).toBe(false);
    click(shortcut('Close connection settings'));
    const returned = control('[data-connection-manager-trigger]', control(selector));
    expectShortcuts('Settings');
    await expectFocus(returned);
  });

  const exitDialog = '[role="alertdialog"]';
  const exitBar = mode === 'compact' ? '.compact-row' : '.titlebar';

  function exitAction(label: string) {
    const button = [...control(exitDialog, control(exitBar)).querySelectorAll('button')].find(
      (candidate) => candidate.textContent?.trim() === label,
    );
    if (!button) throw new Error(`Missing exit action: ${label}`);
    return button;
  }

  it.each([null, ...shortcutOrder])('replaces the top bar and cancels exit with %s open', async (active) => {
    mountHud(mode);
    if (active !== null) keyboardActivate(shortcut(active));
    const surface = control(
      active === null ? (mode === 'compact' ? '.panel' : '.empty-state') : panels[active].selector,
    );
    const bar = control(exitBar);
    const regularControls = control('.row-controls', bar);
    const hasBody = !surface.contains(bar);
    const exit = shortcut('Close Imp');

    pointerDown(exit);
    expect(surface.isConnected).toBe(true);
    keyboardActivate(exit);
    const dialog = control(exitDialog, bar);
    const title = document.getElementById(dialog.getAttribute('aria-labelledby') ?? '');
    expect(title?.textContent?.trim()).toBe('Close Imp?');
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    expect([...dialog.querySelectorAll('button')].map((button) => button.textContent?.trim())).toEqual([
      'Cancel',
      'Confirm',
    ]);
    expect(regularControls.inert).toBe(true);
    expect(regularControls.contains(exit)).toBe(true);
    expect(regularControls.contains(dialog)).toBe(false);
    expect(surface.isConnected).toBe(true);
    if (hasBody) expect(surface.inert).toBe(true);
    const cancel = exitAction('Cancel');
    await expectFocus(cancel);
    expect(closeWindow).not.toHaveBeenCalled();

    pointerDown(cancel);
    click(cancel);
    expect(document.querySelector(exitDialog)).toBeNull();
    expect(surface.isConnected).toBe(true);
    expect(regularControls.inert).toBe(false);
    if (hasBody) expect(surface.inert).toBe(false);
    expectShortcuts(active ?? undefined);
    await expectFocus(exit);
    expect(closeWindow).not.toHaveBeenCalled();

    keyboardActivate(exit);
    await expectFocus(exitAction('Cancel'));
    escape();
    expect(document.querySelector(exitDialog)).toBeNull();
    expect(surface.isConnected).toBe(true);
    expect(regularControls.inert).toBe(false);
    if (hasBody) expect(surface.inert).toBe(false);
    expectShortcuts(active ?? undefined);
    await expectFocus(exit);

    if (active !== null) {
      escape();
      expect(surface.isConnected).toBe(false);
      await expectFocus(shortcut(active));
    }
    expect(closeWindow).not.toHaveBeenCalled();
  });

  it('closes Imp only after explicit confirmation', async () => {
    mountHud(mode);
    keyboardActivate(shortcut('Settings'));
    const exit = shortcut('Close Imp');
    keyboardActivate(exit);
    const defaultAction = exitAction('Cancel');
    await expectFocus(defaultAction);
    keyboardActivate(defaultAction);
    expect(document.querySelector(exitDialog)).toBeNull();
    await expectFocus(exit);
    expect(closeWindow).not.toHaveBeenCalled();

    keyboardActivate(exit);
    const cancel = exitAction('Cancel');
    const confirm = exitAction('Confirm');
    await expectFocus(cancel);

    pressKey('Tab', true);
    expect(document.activeElement).toBe(confirm);
    pressKey('Tab');
    expect(document.activeElement).toBe(cancel);
    expect(closeWindow).not.toHaveBeenCalled();

    keyboardActivate(confirm);
    expect(closeWindow).toHaveBeenCalledTimes(1);
  });

  it('does not activate exit controls twice from the same pointer or key gesture', async () => {
    mountHud(mode);
    const exit = shortcut('Close Imp');
    exit.focus();
    expect(pressKey('Enter', false, true)).toBe(false);
    expect(document.querySelector(exitDialog)).toBeNull();

    click(exit);
    const cancel = exitAction('Cancel');
    const confirm = exitAction('Confirm');
    await expectFocus(cancel);
    expect(pressKey('Enter', false, true)).toBe(false);
    pressKey('Tab');
    expect(pressKey(' ', false, true)).toBe(false);
    cancel.focus();
    const nextPress = new MouseEvent('mousedown', { bubbles: true, cancelable: true, detail: 2 });
    expect(confirm.dispatchEvent(nextPress)).toBe(false);
    flushSync(() => confirm.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 2 })));
    expect(closeWindow).not.toHaveBeenCalled();
    expect(document.querySelector(exitDialog)).not.toBeNull();
    expect(document.activeElement).toBe(cancel);

    flushSync(() => confirm.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1 })));
    expect(closeWindow).toHaveBeenCalledTimes(1);
    for (const label of [...shortcutOrder, 'Close Imp']) {
      flushSync(() => shortcut(label).dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 2 })));
      expectShortcuts();
      expect(document.querySelector(exitDialog)).toBeNull();
    }
  });
});

it('ignores a Connection load from a dismissed manager after reopening', async () => {
  const stale = Promise.withResolvers<tunnel.ConnectionSettings | null>();
  const current = Promise.withResolvers<tunnel.ConnectionSettings | null>();
  vi.spyOn(tunnel, 'loadConnectionSettings')
    .mockReturnValueOnce(stale.promise)
    .mockReturnValueOnce(current.promise);

  mountHud('expanded');
  click(shortcut('Settings'));
  click(control('[data-connection-manager-trigger]'));
  click(shortcut('Close connection settings'));
  click(control('[data-connection-manager-trigger]'));

  stale.reject(new Error('Stale load failed'));
  await expect(stale.promise).rejects.toThrow('Stale load failed');
  await tick();
  expect(control('.connection-dialog').textContent).toContain('Loading connection settings');
  expect(document.querySelector('.connection-dialog [role="alert"]')).toBeNull();

  current.resolve({ mode: 'local', sshTarget: '', remoteUrl: '', hasPairingToken: false });
  await current.promise;
  await tick();
  expect(control<HTMLInputElement>('input[name="connection-mode"][value="local"]').checked).toBe(true);
});

it('keeps only display, theme, and Connection management in Settings', () => {
  mountHud('expanded');
  click(control('button[aria-label="Settings"]'));
  const settings = control('[aria-label="Imp settings"]');
  expect(control('[aria-label="Display mode"]', settings)).toBeDefined();
  expect(control('[aria-label="Theme"]', settings)).toBeDefined();
  expect(settings.textContent).not.toMatch(/\b(?:Actions|Alerts)\b/);
  const buttons = Array.from(settings.querySelectorAll('button'));
  expect(buttons).toEqual([control('[data-connection-manager-trigger]', settings)]);
});

it.each(['action', 'alert'] as const)('retains a failed %s draft and saves it on retry', (kind) => {
  mountHud('expanded');
  if (kind === 'alert') click(control('button[aria-label="Alerts"]'));
  click(control(`button[aria-label="Manage ${kind}s"]`));
  const load = kind === 'action' ? loadActionDefinitions : loadAlertDefinitions;
  const label = control<HTMLInputElement>('form input[type="text"]');
  const value = kind === 'action' ? 'look' : '40';
  const definitionValue = control<HTMLInputElement | HTMLTextAreaElement>(
    kind === 'action' ? 'form textarea' : 'form input[type="number"]',
  );
  input(label, 'My draft');
  input(definitionValue, value);
  const save = control('button[type="submit"]');
  const writes = vi.spyOn(Storage.prototype, 'setItem').mockImplementationOnce(() => {
    throw new Error('storage unavailable');
  });

  click(save);
  expect(control('[role="alert"]').textContent).toContain(`Could not save ${kind}s locally.`);
  expect(label.value).toBe('My draft');
  expect(definitionValue.value).toBe(value);
  expect(load()).toEqual([]);

  click(save);
  expect(writes).toHaveBeenCalledTimes(2);
  expect(document.querySelector('[role="alert"]')).toBeNull();
  expect(label.value).toBe('');
  expect(load()).toMatchObject([
    kind === 'action' ? { label: 'My draft', command: value } : { label: 'My draft', thresholdPercent: 40 },
  ]);
  expect(send).not.toHaveBeenCalled();
});

it('rolls back a failed quick alert toggle and clears the error on successful retry', () => {
  mountHud('compact', true);
  click(control('button[aria-label="Alerts"]'));
  const surface = control('[aria-label="Saved alerts"]');
  const toggle = control<HTMLInputElement>('input[type="checkbox"]', surface);
  vi.spyOn(Storage.prototype, 'setItem').mockImplementationOnce(() => {
    throw new Error('storage unavailable');
  });
  expect(toggle.checked).toBe(true);
  click(toggle);
  expect(toggle.checked).toBe(true);
  expect(control('[role="alert"]', surface).textContent).toContain('Could not save alerts locally.');
  expect(loadAlertDefinitions()).toEqual([alert]);

  click(toggle);
  expect(toggle.checked).toBe(false);
  expect(surface.querySelector('[role="alert"]')).toBeNull();
  expect(loadAlertDefinitions()).toEqual([{ ...alert, enabled: false }]);
  expect(send).not.toHaveBeenCalled();
});

it.each(['expanded', 'compact'] as const)('keeps invocation pending across %s management', async (mode) => {
  const pending = Promise.withResolvers<{ status: 'forwarded'; detail: null }>();
  send.mockReturnValue(pending.promise);
  mountHud(mode, true);
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  if (mode === 'compact') click(control('button[aria-label="Actions"]'));
  click(control('.action-btn'));
  expect(send).toHaveBeenCalledExactlyOnceWith(context, action.command);
  expect(control<HTMLButtonElement>('.action-btn').disabled).toBe(true);
  expect(control('[aria-live="polite"]').textContent).toContain('Forwarding');

  click(control('button[aria-label="Manage actions"]'));
  click(control('button[aria-label="Close action manager"]'));
  const returnedAction = control<HTMLButtonElement>('.action-btn');
  flushSync(() => vi.advanceTimersByTime(3000));
  expect(control('[aria-live="polite"]').textContent).toContain('Forwarding');
  expect(returnedAction.disabled).toBe(true);
  click(returnedAction);
  expect(send).toHaveBeenCalledTimes(1);

  pending.resolve({ status: 'forwarded', detail: null });
  await pending.promise;
  await tick();
  expect(returnedAction.disabled).toBe(false);
  expect(control('[aria-live="polite"]').textContent).toContain('Forwarded to the game client.');
});

it.each(['expanded', 'compact'] as const)('expires success in the %s action list', async (mode) => {
  send.mockResolvedValue({ status: 'forwarded', detail: null });
  mountHud(mode, true);
  if (mode === 'compact') click(shortcut('Actions'));
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });

  const region = control('[aria-live="polite"]');
  expect(region.textContent).toBe('');
  click(control('.action-btn'));
  await tick();

  expect(region.textContent).toContain('Forwarded to the game client.');
  expect(region.getAttribute('aria-atomic')).toBe('true');
  flushSync(() => vi.advanceTimersByTime(2849));
  expect(region.classList.contains('fading')).toBe(false);
  flushSync(() => vi.advanceTimersByTime(1));
  expect(region.classList.contains('fading')).toBe(true);
  flushSync(() => vi.advanceTimersByTime(149));
  expect(region.textContent).toContain('Final MUD delivery is not confirmed.');
  flushSync(() => vi.advanceTimersByTime(1));
  expect(control('[aria-live="polite"]')).toBe(region);
  expect(region.textContent).toBe('');
  expect(document.querySelector('.action-feedback')).toBeNull();
});

it('gives a newer success its own three seconds after replacing a fading success', async () => {
  send.mockResolvedValue({ status: 'forwarded', detail: null });
  mountHud('expanded', true);
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });

  const region = control('[aria-live="polite"]');
  click(control('.action-btn'));
  await tick();
  flushSync(() => vi.advanceTimersByTime(2900));
  expect(region.classList.contains('fading')).toBe(true);

  click(control('.action-btn'));
  expect(region.textContent).toContain('Forwarding');
  await tick();
  expect(region.classList.contains('fading')).toBe(false);
  flushSync(() => vi.advanceTimersByTime(2999));
  expect(region.textContent).toContain('Forwarded to the game client.');
  flushSync(() => vi.advanceTimersByTime(1));
  expect(region.textContent).toBe('');
  expect(send).toHaveBeenCalledTimes(2);
});

it.each([
  ['rejected', 'Action was not forwarded: fixture detail'],
  ['unknown', 'Delivery uncertain. Imp did not retry. Detail: fixture detail.'],
] as const)('retains %s feedback past an older success deadline', async (status, message) => {
  send
    .mockResolvedValueOnce({ status: 'forwarded', detail: null })
    .mockResolvedValueOnce({ status, detail: 'fixture detail' });
  mountHud('expanded', true);
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  click(control('.action-btn'));
  await tick();
  flushSync(() => vi.advanceTimersByTime(1000));

  click(control('.action-btn'));
  await tick();
  flushSync(() => vi.advanceTimersByTime(10_000));
  const region = control('.action-feedback');
  expect(region.textContent).toBe(message);
  expect(region.classList.contains('fading')).toBe(false);
  expect(control<HTMLButtonElement>('.action-btn').disabled).toBe(false);
});

it('cancels the success timers when the HUD unmounts', async () => {
  send.mockResolvedValue({ status: 'forwarded', detail: null });
  mountHud('expanded', true);
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  const before = vi.getTimerCount();

  click(control('.action-btn'));
  await tick();
  expect(control('.action-feedback').textContent).toContain('Forwarded to the game client.');
  expect(vi.getTimerCount()).toBeGreaterThan(before);
  await unmount(hud!);
  hud = undefined;
  expect(vi.getTimerCount()).toBe(before);
});

it('dismisses a compact panel from outside, not from its controls', () => {
  mountHud('compact');
  click(shortcut('Actions'));
  const actions = control(panels.Actions.selector);
  const alerts = shortcut('Alerts');
  pointerDown(alerts);
  expect(actions.isConnected).toBe(true);
  click(alerts);
  expect(actions.isConnected).toBe(false);

  const alertPanel = control(panels.Alerts.selector);
  pointerDown(control(panels.Alerts.focus, alertPanel));
  expect(alertPanel.isConnected).toBe(true);
  pointerDown(document.body);
  expect(alertPanel.isConnected).toBe(false);
  expect(shortcut('Alerts').getAttribute('aria-expanded')).toBe('false');
  expect(closeWindow).not.toHaveBeenCalled();
});
