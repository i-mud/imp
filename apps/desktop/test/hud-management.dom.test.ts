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

function escape() {
  const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
  flushSync(() => document.activeElement!.dispatchEvent(event));
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
  const quick = `[aria-label="Saved ${kind}s"]`;
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
    expect(entry.textContent?.trim()).toBe(kind === 'action' ? 'Manage' : 'Manage alerts');
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
      const owner = control(`button[aria-label="${trigger}"]`);
      await expectFocus(control(manage, openQuick()));
      escape();
      expect(document.querySelector(quick)).toBeNull();
      await expectFocus(owner);
      expect(owner.getAttribute('aria-expanded')).toBe('false');
    });
  }
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
  if (mode === 'compact') click(control('button[aria-label="Actions"]'));
  click(control('.action-btn'));
  expect(send).toHaveBeenCalledExactlyOnceWith(context, action.command);
  expect(control<HTMLButtonElement>('.action-btn').disabled).toBe(true);
  expect(control('[aria-live="polite"]').textContent).toContain('Forwarding');

  click(control('button[aria-label="Manage actions"]'));
  click(control('button[aria-label="Close action manager"]'));
  const returnedAction = control<HTMLButtonElement>('.action-btn');
  expect(returnedAction.disabled).toBe(true);
  click(returnedAction);
  expect(send).toHaveBeenCalledTimes(1);

  pending.resolve({ status: 'forwarded', detail: null });
  await pending.promise;
  await tick();
  expect(returnedAction.disabled).toBe(false);
  expect(control('[aria-live="polite"]').textContent).toContain('Forwarded to the game client.');
});

it('dismisses compact quick panels from outside without letting the chevron close Imp', () => {
  mountHud('compact');
  const actions = control('button[aria-label="Actions"]');
  const alerts = control('button[aria-label="Alerts"]');
  const pointerDown = (target: HTMLElement) => {
    flushSync(() => target.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true })));
  };

  click(actions);
  const chevron = control('button[aria-label="Close actions"]');
  pointerDown(chevron);
  expect(actions.getAttribute('aria-expanded')).toBe('true');
  click(chevron);
  expect(actions.getAttribute('aria-expanded')).toBe('false');
  expect(closeWindow).not.toHaveBeenCalled();

  click(alerts);
  pointerDown(control('button[aria-label="Manage alerts"]'));
  expect(alerts.getAttribute('aria-expanded')).toBe('true');
  pointerDown(document.body);
  expect(document.querySelector('[aria-label="Saved alerts"]')).toBeNull();
  expect(alerts.getAttribute('aria-expanded')).toBe('false');
  expect(closeWindow).not.toHaveBeenCalled();
});
