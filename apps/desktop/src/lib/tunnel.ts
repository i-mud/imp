import { invoke } from '@tauri-apps/api/core';

/**
 * Reads the desktop transport supervisor's diagnostic without exposing SSH,
 * process, or argv details to any component. Only `config.ts` calls this;
 * see `docs/architecture/objects/state-source.md` for the seam.
 */

type TunnelDiagnostic =
  | 'external'
  | 'direct'
  | 'external_port_in_use'
  | 'local_port_unavailable'
  | 'ssh_unavailable'
  | 'reconnecting'
  | 'live'
  | 'down';

interface TunnelStatus {
  readonly diagnostic: TunnelDiagnostic;
}

export type RuntimeConnectionConfig =
  | {
      readonly mode: 'local';
      readonly stateUrl: null;
      readonly authenticationToken: null;
    }
  | {
      readonly mode: 'direct';
      readonly stateUrl: string;
      readonly authenticationToken: string;
    };

export type ConnectionMode = 'external' | 'managed' | 'direct';

export interface ConnectionSettings {
  readonly mode: ConnectionMode;
  readonly sshTarget: string;
  readonly remoteUrl: string;
  readonly hasPairingToken: boolean;
}

export interface ConnectionSettingsUpdate {
  readonly mode: ConnectionMode;
  readonly sshTarget?: string;
  readonly remoteUrl?: string;
  /**
   * Omit to preserve an existing Direct-WSS token. Supplying a string,
   * including an empty string, explicitly replaces it and is validated by
   * the native boundary.
   */
  readonly pairingToken?: string;
}

const POLL_INTERVAL_MS = 2_000;

const DETAIL_BY_DIAGNOSTIC: Partial<Record<TunnelDiagnostic, string>> = {
  local_port_unavailable: 'Local forwarded port is unavailable.',
  ssh_unavailable: 'SSH tunnel unavailable.',
  reconnecting: 'Establishing SSH tunnel…',
};

function isTauriRuntime(): boolean {
  return '__TAURI_INTERNALS__' in globalThis;
}

/**
 * Returns the renderer-facing native connection tuple.
 *
 * Outside Tauri there is no native configuration, so browser development
 * behaves like the existing local relay path.
 */
export async function loadConnectionConfig(): Promise<RuntimeConnectionConfig> {
  if (!isTauriRuntime()) {
    return {
      mode: 'local',
      stateUrl: null,
      authenticationToken: null,
    };
  }

  return invoke<RuntimeConnectionConfig>('connection_config');
}

/**
 * Returns editable native connection settings without exposing an existing
 * plaintext Direct-WSS pairing token.
 *
 * Browser/mock development has no native settings store, so it reports null
 * rather than invoking a Tauri command.
 */
export async function loadConnectionSettings(): Promise<ConnectionSettings | null> {
  if (!isTauriRuntime()) return null;

  return invoke<ConnectionSettings>('connection_settings');
}

/**
 * Persists native connection settings for the next application start.
 *
 * This intentionally does not mutate the current runtime transport.
 */
export async function saveConnectionSettings(update: ConnectionSettingsUpdate): Promise<ConnectionSettings> {
  if (!isTauriRuntime()) {
    throw new Error('Native connection settings are unavailable outside Tauri.');
  }

  return invoke<ConnectionSettings>('save_connection_settings', { update });
}

/**
 * Starts polling the managed tunnel's status if Tauri is present; a no-op
 * under the browser/mock dev loop, where there is no tunnel to ask about.
 * Returns a synchronous accessor for `RelaySourceOptions.diagnosticDetail`.
 */
export function watchTunnelDiagnostics(): () => string | null {
  if (!isTauriRuntime()) return () => null;

  let lastKnownDetail: string | null = null;
  const pollOnce = (): void => {
    invoke<TunnelStatus>('tunnel_status')
      .then((status) => {
        lastKnownDetail = DETAIL_BY_DIAGNOSTIC[status.diagnostic] ?? null;
      })
      .catch(() => {
        lastKnownDetail = null;
      });
  };

  pollOnce();
  setInterval(pollOnce, POLL_INTERVAL_MS);
  return () => lastKnownDetail;
}
