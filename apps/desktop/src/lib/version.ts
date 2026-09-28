import { getVersion } from '@tauri-apps/api/app';

function isTauriRuntime(): boolean {
  return '__TAURI_INTERNALS__' in globalThis;
}

/**
 * Returns the version embedded in the native Imp application.
 *
 * Browser/mock development has no native application metadata and deliberately
 * returns null rather than inventing a second version source.
 */
export async function loadAppVersion(): Promise<string | null> {
  if (!isTauriRuntime()) return null;

  return getVersion();
}
