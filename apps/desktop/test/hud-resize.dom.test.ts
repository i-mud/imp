import { flushSync, mount, unmount } from 'svelte';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import Hud from '../src/components/Hud.svelte';
import { saveActionDefinitions } from '../src/lib/action/definitions.ts';
import { INITIAL_MODEL } from '../src/lib/hud/model.ts';
import { saveDisplayMode, type DisplayMode } from '../src/lib/hud/presentation.ts';
import { resizeHudWindow } from '../src/lib/window.ts';

vi.mock(import('../src/lib/window.ts'), async (importOriginal) => ({
  ...(await importOriginal()),
  resizeHudWindow: vi.fn(),
}));

let hud: object | undefined;
const bounds = new Map<Element, DOMRect>();
const observers: FixtureResizeObserver[] = [];

class FixtureResizeObserver {
  readonly observe = vi.fn<(target: Element, options?: ResizeObserverOptions) => void>();
  readonly disconnect = vi.fn();
  readonly deliver: () => void;

  constructor(deliver: () => void) {
    this.deliver = deliver;
    observers.push(this);
  }
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame'] });
  vi.stubGlobal('ResizeObserver', FixtureResizeObserver);
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    return bounds.get(this) ?? new DOMRect();
  });
});

afterEach(async () => {
  if (hud) await unmount(hud);
  hud = undefined;
  bounds.clear();
  observers.splice(0);
  document.body.replaceChildren();
  localStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.mocked(resizeHudWindow).mockClear();
});

function mountHud(mode: DisplayMode = 'expanded') {
  saveDisplayMode(mode);
  hud = mount(Hud, {
    target: document.body,
    props: {
      model: INITIAL_MODEL,
      actionSink: { id: 'fixture', label: 'Fixture', send: vi.fn() },
      subscribeText: () => () => {},
    },
  });
  flushSync();
}

function element(selector: string) {
  const result = document.querySelector<HTMLElement>(selector);
  if (!result) throw new Error(`Missing element: ${selector}`);
  return result;
}

function click(selector: string) {
  flushSync(() => element(selector).click());
}

function setBounds(target: Element, height: number, width = 320) {
  bounds.set(target, new DOMRect(0, 0, width, height));
}

function observerOf(target: Element) {
  const observer = observers.find((candidate) =>
    candidate.observe.mock.calls.some(([observed]) => observed === target),
  );
  if (!observer) throw new Error(`Missing resize observer for ${target.className}`);
  return observer;
}

function requestedSizes() {
  return vi.mocked(resizeHudWindow).mock.calls.map(([size]) => size);
}

it('resizes expanded HUD from the full panel even when the action strip does not change', () => {
  saveActionDefinitions([{ id: 'fixture', label: 'Fixture', command: 'look' }]);
  mountHud();
  const panel = element('.panel');
  const strip = element('.action-bar');
  const observer = observerOf(panel);
  expect(observer.observe).toHaveBeenCalledWith(panel, { box: 'border-box' });

  setBounds(strip, 38.2);
  setBounds(panel, 167.2, 319.5);
  observer.deliver();
  expect(requestedSizes()).toEqual([]);
  vi.advanceTimersToNextFrame();
  expect(requestedSizes()).toEqual([{ width: 320, height: 168 }]);

  setBounds(panel, 194.1);
  observer.deliver();
  setBounds(panel, 206.7);
  observer.deliver();
  expect(vi.getTimerCount()).toBe(1);
  expect(resizeHudWindow).toHaveBeenCalledTimes(1);
  vi.advanceTimersToNextFrame();

  setBounds(panel, 155.1);
  observer.deliver();
  vi.advanceTimersToNextFrame();
  observer.deliver();
  vi.advanceTimersToNextFrame();
  setBounds(panel, 155.9);
  observer.deliver();
  vi.advanceTimersToNextFrame();

  expect(element('.action-bar')).toBe(strip);
  expect(requestedSizes()).toEqual([
    { width: 320, height: 168 },
    { width: 320, height: 207 },
    { width: 320, height: 156 },
  ]);
});

it('cancels a pending normal resize on branch exit and starts fresh after the action manager', () => {
  mountHud();
  const panel = element('.panel');
  const observer = observerOf(panel);
  setBounds(panel, 133.1);
  observer.deliver();
  vi.advanceTimersToNextFrame();

  setBounds(panel, 259.9);
  observer.deliver();
  click('button[aria-label="Settings"]');
  expect(observer.disconnect).toHaveBeenCalledOnce();
  setBounds(element('.titlebar'), 23.2);
  setBounds(element('.settings-body'), 177.4);
  vi.advanceTimersToNextFrame();

  click('[data-action-manager-trigger]');
  setBounds(element('.manager-dialog'), 294.1);
  vi.advanceTimersToNextFrame();

  click('button[aria-label="Close action manager"]');
  setBounds(element('.titlebar'), 23.2);
  setBounds(element('.settings-body'), 175.4);
  vi.advanceTimersToNextFrame();
  click('button[aria-label="Close settings"]');
  const returnedPanel = element('.panel');
  setBounds(returnedPanel, 133.1);
  observerOf(returnedPanel).deliver();
  vi.advanceTimersToNextFrame();

  expect(requestedSizes()).toEqual([
    { width: 320, height: 134 },
    { width: 320, height: 201 },
    { width: 320, height: 295 },
    { width: 320, height: 199 },
    { width: 320, height: 134 },
  ]);
});

it('continues to size the closed compact window from the row', () => {
  mountHud('compact');
  const row = element('.compact-row');
  setBounds(element('.panel'), 92.2, 500);
  setBounds(row, 39.1, 347.2);
  vi.advanceTimersToNextFrame();
  expect(requestedSizes()).toEqual([{ width: 348, height: 40 }]);
});
