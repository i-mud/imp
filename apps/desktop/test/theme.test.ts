import { afterEach, describe, expect, it } from 'vitest';

import { applyTheme, loadTheme, saveTheme, themeFromPersisted } from '../src/lib/hud/theme.ts';

const GLOBALS = ['document', 'localStorage', 'matchMedia'] as const;
const originalDescriptors: Record<(typeof GLOBALS)[number], PropertyDescriptor | undefined> = {
  document: Object.getOwnPropertyDescriptor(globalThis, 'document'),
  localStorage: Object.getOwnPropertyDescriptor(globalThis, 'localStorage'),
  matchMedia: Object.getOwnPropertyDescriptor(globalThis, 'matchMedia'),
};

class FakeMediaQuery {
  readonly listeners = new Set<() => void>();
  addCount = 0;
  removeCount = 0;
  matches: boolean;

  constructor(matches: boolean) {
    this.matches = matches;
  }

  addEventListener(type: string, listener: () => void): void {
    if (type !== 'change') return;
    this.addCount += 1;
    this.listeners.add(listener);
  }

  removeEventListener(type: string, listener: () => void): void {
    if (type !== 'change') return;
    this.removeCount += 1;
    this.listeners.delete(listener);
  }

  emit(matches: boolean): void {
    this.matches = matches;
    for (const listener of this.listeners) listener();
  }
}

function installGlobal(key: (typeof GLOBALS)[number], value: unknown): void {
  Object.defineProperty(globalThis, key, { configurable: true, value });
}

function installThemeDom(): Record<string, string> {
  const dataset: Record<string, string> = {};
  installGlobal('document', { documentElement: { dataset } });
  return dataset;
}

afterEach(() => {
  for (const key of GLOBALS) {
    const descriptor = originalDescriptors[key];
    if (descriptor === undefined) Reflect.deleteProperty(globalThis, key);
    else Object.defineProperty(globalThis, key, descriptor);
  }
});

describe('themeFromPersisted', () => {
  it('restores every supported preference', () => {
    expect(themeFromPersisted('dark')).toBe('dark');
    expect(themeFromPersisted('light')).toBe('light');
    expect(themeFromPersisted('system')).toBe('system');
  });

  it('falls back to system for invalid stored values', () => {
    expect(themeFromPersisted('solarized')).toBe('system');
    expect(themeFromPersisted(null)).toBe('system');
  });
});

describe('theme persistence', () => {
  it('falls back to dark and ignores unavailable storage', () => {
    installGlobal('localStorage', {
      getItem(): never {
        throw new Error('storage unavailable');
      },
      setItem(): never {
        throw new Error('storage unavailable');
      },
    });

    expect(loadTheme()).toBe('system');
    expect(() => saveTheme('system')).not.toThrow();
  });

  it('stores the logical system preference', () => {
    let stored: string | null = null;
    installGlobal('localStorage', {
      getItem(): null {
        return null;
      },
      setItem(_key: string, value: string): void {
        stored = value;
      },
    });

    saveTheme('system');

    expect(stored).toBe('system');
  });
});

describe('system theme', () => {
  it('resolves system to a concrete dark or light DOM theme', () => {
    const dataset = installThemeDom();
    const query = new FakeMediaQuery(true);
    installGlobal('matchMedia', () => query);

    const cleanupDark = applyTheme('system');
    expect(dataset.theme).toBe('dark');
    cleanupDark();

    query.matches = false;
    const cleanupLight = applyTheme('system');
    expect(dataset.theme).toBe('light');
    expect(['dark', 'light']).toContain(dataset.theme);
    cleanupLight();
  });

  it('updates one system listener and cleans it up between transitions', () => {
    const dataset = installThemeDom();
    const query = new FakeMediaQuery(true);
    installGlobal('matchMedia', () => query);

    const firstCleanup = applyTheme('system');
    expect(query.addCount).toBe(1);
    expect(query.listeners.size).toBe(1);

    query.emit(false);
    expect(dataset.theme).toBe('light');

    firstCleanup();
    expect(query.removeCount).toBe(1);
    expect(query.listeners.size).toBe(0);

    applyTheme('dark')();
    applyTheme('light')();
    expect(query.listeners.size).toBe(0);

    const secondCleanup = applyTheme('system');
    expect(query.addCount).toBe(2);
    expect(query.listeners.size).toBe(1);
    secondCleanup();
    expect(query.removeCount).toBe(2);
    expect(query.listeners.size).toBe(0);
  });
});
