export type ThemePreference = 'dark' | 'light' | 'system';

const THEME_STORAGE_KEY = 'tinyscry.theme';
const DARK_QUERY = '(prefers-color-scheme: dark)';

export function themeFromPersisted(value: string | null): ThemePreference {
  return value === 'light' || value === 'system' ? value : 'dark';
}

export function loadTheme(): ThemePreference {
  try {
    return themeFromPersisted(globalThis.localStorage?.getItem(THEME_STORAGE_KEY) ?? null);
  } catch {
    return 'dark';
  }
}

export function saveTheme(theme: ThemePreference): void {
  try {
    globalThis.localStorage?.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Local UI preferences must not interfere with HUD rendering.
  }
}

/**
 * Resolves `system` against the OS preference so the stylesheet only has to
 * describe two concrete themes, and keeps the document in sync while the
 * operator stays on `system`. Returns a cleanup for the media listener.
 */
export function applyTheme(preference: ThemePreference): () => void {
  const root = globalThis.document?.documentElement;
  if (root === undefined) return () => {};

  if (preference !== 'system') {
    root.dataset.theme = preference;
    return () => {};
  }

  const query = globalThis.matchMedia?.(DARK_QUERY);
  const sync = () => {
    root.dataset.theme = query?.matches === true ? 'dark' : 'light';
  };
  sync();
  query?.addEventListener('change', sync);
  return () => query?.removeEventListener('change', sync);
}
