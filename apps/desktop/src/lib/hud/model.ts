import {
  EMPTY_STATE,
  type FeedStatus,
  type GameState,
  type ProtocolError,
  type RelayInfo,
  type StateContext,
} from '@tinyscry/protocol';

import type { ConnectionPhase, SourceEvent } from '../source/types.ts';

export interface HudModel {
  readonly phase: ConnectionPhase;
  readonly detail: string | null;
  readonly relay: RelayInfo | null;
  readonly feed: FeedStatus | null;
  readonly context: StateContext | null;
  readonly state: GameState;
  readonly lastSeq: number;
  readonly lastUpdateAt: number | null;
  readonly lastError: ProtocolError | null;
  readonly hasData: boolean;
}

export const INITIAL_MODEL: HudModel = {
  phase: 'idle',
  detail: null,
  relay: null,
  feed: null,
  context: null,
  state: EMPTY_STATE,
  lastSeq: -1,
  lastUpdateAt: null,
  lastError: null,
  hasData: false,
};

/** Why the displayed values may not reflect the current game state. */
export type HudFreshness = 'fresh' | 'reconnecting' | 'offline' | 'feed-down' | 'feed-stalled';

/**
 * Classify the pipeline's health.
 *
 * Socket liveness and feed liveness fail independently, and the state that
 * matters most is a healthy relay whose producer has gone away: the socket is
 * fine, the vitals are minutes old. Presenting those as current is the one
 * failure this HUD must not have, so a single "connected" flag is not enough.
 *
 * A `null` feed means the relay has not reported a status yet, which is not
 * evidence of staleness.
 */
export function freshnessOf(model: HudModel): HudFreshness {
  if (model.phase === 'connecting' || model.phase === 'reconnecting') return 'reconnecting';
  if (model.phase !== 'connected') return 'offline';
  if (model.feed === 'down') return 'feed-down';
  if (model.feed === 'stale') return 'feed-stalled';
  return 'fresh';
}

/**
 * The last accepted state remains visible outside a connection so a brief outage does not erase the HUD.
 * `hasData` means fresh data from the current connection only, therefore it becomes false on reconnect/disconnect.
 */
export function applyEvent(model: HudModel, event: SourceEvent): HudModel {
  switch (event.kind) {
    case 'connection':
      return {
        ...model,
        phase: event.phase,
        detail: event.detail,
        context: event.phase === 'connected' ? model.context : null,
        lastSeq: -1,
        hasData: event.phase === 'connected' ? model.hasData : false,
      };
    case 'hello':
      return { ...model, relay: event.relay, lastSeq: -1 };
    case 'snapshot': {
      if (event.seq <= model.lastSeq) return model;
      const contextChanged =
        model.context?.session !== event.context?.session ||
        model.context?.foreground !== event.context?.foreground ||
        model.context?.connection !== event.context?.connection;
      return {
        ...model,
        feed: contextChanged ? 'stale' : model.feed,
        context: event.context,
        state: event.state,
        lastSeq: event.seq,
        lastUpdateAt: event.at,
        lastError: null,
        hasData: true,
      };
    }
    case 'feed':
      return { ...model, feed: event.status, detail: event.detail };
    case 'protocol-error':
      return { ...model, lastError: event.error };
  }
}
