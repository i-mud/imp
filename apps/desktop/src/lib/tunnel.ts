import { invoke } from '@tauri-apps/api/core';

/**
 * Reads the desktop tunnel supervisor's diagnostic without exposing SSH,
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

const POLL_INTERVAL_MS = 2_000;

const DETAIL_BY_DIAGNOSTIC: Partial<Record<TunnelDiagnostic, string>> = {
  local_port_unavailable: 'Local forwarded port is unavailable.',
  ssh_unavailable: 'SSH tunnel unavailable.',
  reconnecting: 'Establishing SSH tunnel…',
};

/**
 * Starts polling the managed tunnel's status if Tauri is present; a no-op
 * under the browser/mock dev loop, where there is no tunnel to ask about.
 * Returns a synchronous accessor for `RelaySourceOptions.diagnosticDetail`.
 */
export function watchTunnelDiagnostics(): () => string | null {
  if (!('__TAURI_INTERNALS__' in globalThis)) return () => null;

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
