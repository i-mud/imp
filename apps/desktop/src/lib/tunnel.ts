import { invoke } from '@tauri-apps/api/core';
import { get, readonly, writable } from 'svelte/store';

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

type NodeDiagnostic = 'starting' | 'owned' | 'local_port_unavailable' | 'spawn_unavailable' | 'down';

interface NodeStatus {
  readonly diagnostic: NodeDiagnostic;
}

export type RuntimeConnectionConfig =
  | {
      readonly mode: 'local';
      readonly stateUrl: string | null;
      readonly authenticationToken: null;
    }
  | {
      readonly mode: 'direct';
      readonly stateUrl: string;
      readonly authenticationToken: string;
    }
  | {
      readonly mode: 'unconfigured' | 'unavailable';
      readonly stateUrl: null;
      readonly authenticationToken: null;
    };

export type ConnectionMode = 'external' | 'local' | 'managed' | 'direct';

interface SshDestination {
  readonly host: string;
  readonly username: string;
  readonly port?: number;
}

export interface ConnectionSettings {
  readonly mode: ConnectionMode;
  readonly sshTarget: string;
  readonly sshDestination?: SshDestination;
  readonly remoteUrl: string;
  readonly hasPairingToken: boolean;
}

export interface ConnectionSettingsState {
  readonly running: ConnectionSettings | null;
  readonly saved: ConnectionSettings | null;
  readonly pendingRestart: boolean;
}

export interface ConnectionSettingsUpdate {
  readonly mode: ConnectionMode;
  readonly sshTarget?: string;
  readonly sshDestination?: SshDestination;
  readonly remoteUrl?: string;
  /**
   * Omit to keep the saved Direct token for the same state endpoint.
   * Any supplied string, including an empty one, is validated as a replacement.
   */
  readonly pairingToken?: string;
}

export type ManagedCheckDestination =
  { readonly sshTarget: string } | { readonly sshDestination: SshDestination };

export type ManagedCheckResult =
  | 'invalid_destination'
  | 'ssh_unavailable'
  | 'live'
  | 'stale'
  | 'down'
  | 'no_usable_state'
  | 'host_verification'
  | 'authentication'
  | 'forwarding_unavailable'
  | 'invalid_protocol'
  | 'timeout'
  | 'cancelled'
  | 'connection_failed'
  | 'busy';

const checkPending = writable(false);
export const managedCheckPending = readonly(checkPending);
const MANAGED_CHECK_SEQUENCE_KEY = 'imp.managed-check-sequence';

const POLL_INTERVAL_MS = 2_000;

const DETAIL_BY_DIAGNOSTIC: Partial<Record<TunnelDiagnostic, string>> = {
  local_port_unavailable: 'Local forwarded port is unavailable.',
  ssh_unavailable: 'SSH tunnel unavailable.',
  reconnecting: 'Establishing SSH tunnel…',
};

const DETAIL_BY_NODE_DIAGNOSTIC: Partial<Record<NodeDiagnostic, string>> = {
  starting: 'Starting local Imp node…',
  local_port_unavailable: 'Local Imp node port is unavailable.',
  spawn_unavailable: 'Local Imp node could not start.',
  down: 'Local Imp node is down.',
};

function isTauriRuntime(): boolean {
  return '__TAURI_INTERNALS__' in globalThis;
}

/**
 * Returns the renderer-facing native connection tuple.
 *
 * Browser relay development has no native configuration. A missing or failed
 * bridge in the desktop build must not fall back to the development endpoint.
 */
export async function loadConnectionConfig(): Promise<RuntimeConnectionConfig> {
  if (!isTauriRuntime()) {
    return {
      mode: import.meta.env.TAURI_ENV_PLATFORM === undefined ? 'local' : 'unavailable',
      stateUrl: null,
      authenticationToken: null,
    };
  }

  try {
    return await invoke<RuntimeConnectionConfig>('connection_config');
  } catch {
    return {
      mode: 'unavailable',
      stateUrl: null,
      authenticationToken: null,
    };
  }
}

/**
 * Returns editable native connection settings without exposing an existing
 * plaintext Direct-WSS pairing token.
 *
 * Browser/mock development has no native settings store, so it reports null
 * rather than invoking a Tauri command.
 */
export async function loadConnectionSettings(): Promise<ConnectionSettingsState | null> {
  if (!isTauriRuntime()) return null;

  return invoke<ConnectionSettingsState>('connection_settings');
}

/**
 * Persists native connection settings for the next application start.
 *
 * This intentionally does not mutate the current runtime transport.
 */
export async function saveConnectionSettings(
  update: ConnectionSettingsUpdate,
): Promise<ConnectionSettingsState> {
  if (!isTauriRuntime()) {
    throw new Error('Native connection settings are unavailable outside Tauri.');
  }

  return invoke<ConnectionSettingsState>('save_connection_settings', { update });
}

export function checkManagedConnection(
  destination: ManagedCheckDestination,
): { requestId: number; result: Promise<ManagedCheckResult> } | null {
  if (!isTauriRuntime() || get(checkPending)) return null;

  // Native stays up across WebView reloads; keep only the request counter in this session.
  const requestId = Number(sessionStorage.getItem(MANAGED_CHECK_SEQUENCE_KEY)) + 1;
  if (!Number.isSafeInteger(requestId) || requestId < 1) {
    throw new Error('Invalid Managed connection check sequence.');
  }
  sessionStorage.setItem(MANAGED_CHECK_SEQUENCE_KEY, String(requestId));

  checkPending.set(true);
  const result = (async (): Promise<ManagedCheckResult> => {
    try {
      return await invoke<ManagedCheckResult>('check_managed_connection', { requestId, destination });
    } catch {
      return 'connection_failed';
    } finally {
      // Cancellation only signals the native check; its original command confirms retirement.
      checkPending.set(false);
    }
  })();
  return { requestId, result };
}

export async function cancelManagedConnection(requestId: number): Promise<void> {
  await invoke<void>('cancel_managed_connection', { requestId });
}

/**
 * Starts polling the supervisor for the endpoint the HUD actually consumes.
 *
 * The same-host node is always supervised, but it is relevant to HUD
 * connectivity only in Local mode. Managed and External modes consume the
 * separate SSH endpoint instead.
 *
 * Returns a synchronous accessor for `RelaySourceOptions.diagnosticDetail`.
 */
export function watchConnectionDiagnostics(source: 'node' | 'tunnel'): () => string | null {
  if (!isTauriRuntime()) return () => null;

  let lastKnownDetail: string | null = null;
  const pollOnce = (): void => {
    const request =
      source === 'node'
        ? invoke<NodeStatus>('node_status').then(
            (status) => DETAIL_BY_NODE_DIAGNOSTIC[status.diagnostic] ?? null,
          )
        : invoke<TunnelStatus>('tunnel_status').then(
            (status) => DETAIL_BY_DIAGNOSTIC[status.diagnostic] ?? null,
          );

    request
      .then((detail) => {
        lastKnownDetail = detail;
      })
      .catch(() => {
        lastKnownDetail = null;
      });
  };

  pollOnce();
  setInterval(pollOnce, POLL_INTERVAL_MS);
  return () => lastKnownDetail;
}
