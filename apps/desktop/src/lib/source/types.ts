import type { FeedStatus, GameState, ProtocolError, RelayInfo, StateContext } from '@imp/protocol';

export type ConnectionPhase = 'idle' | 'connecting' | 'connected' | 'reconnecting' | 'disconnected';

export type SourceEvent =
  | { readonly kind: 'connection'; readonly phase: ConnectionPhase; readonly detail: string | null }
  | { readonly kind: 'hello'; readonly relay: RelayInfo }
  | {
      readonly kind: 'snapshot';
      readonly seq: number;
      readonly at: number;
      readonly context: StateContext | null;
      readonly state: GameState;
    }
  | { readonly kind: 'feed'; readonly status: FeedStatus; readonly detail: string | null }
  | {
      readonly kind: 'text';
      readonly context: StateContext;
      readonly at: number;
      readonly text: string;
    }
  | { readonly kind: 'protocol-error'; readonly error: ProtocolError };

export interface StateSource {
  readonly id: string;
  readonly label: string;
  start(listener: (event: SourceEvent) => void): void;
  stop(): void;
}
