import type { GameState } from './state.ts';

/**
 * Wire protocol version.
 *
 * Bumped only for breaking changes. Additive, ignorable fields keep the same
 * version: decoders ignore unknown object keys and unknown message types, so
 * a newer relay can talk to an older HUD as long as the required fields of
 * `hello` and `snapshot` are unchanged.
 */
export const PROTOCOL_VERSION = 1;

export interface RelayInfo {
  readonly name: string;
  readonly version: string;
}

/**
 * Liveness of the upstream game feed, which is independent of socket
 * liveness: the HUD can hold a healthy relay connection while TinyFugue is
 * gone. Distinguishing the two is what lets the HUD show "no data" instead of
 * stale vitals.
 */
export type FeedStatus = 'live' | 'stale' | 'down';

/** First frame the relay sends on every subscriber connection. */
export interface HelloMessage {
  readonly type: 'hello';
  readonly protocol: number;
  /** Relay wall clock in epoch milliseconds. */
  readonly at: number;
  readonly relay: RelayInfo;
}

/**
 * Complete current state. The relay never sends partial updates, which is why
 * a rejected frame can never leave the HUD holding half-applied state.
 */
export interface SnapshotMessage {
  readonly type: 'snapshot';
  readonly protocol: number;
  /** Monotonic per-relay-process counter, used to drop out-of-order frames. */
  readonly seq: number;
  readonly at: number;
  readonly state: GameState;
}

export interface StatusMessage {
  readonly type: 'status';
  readonly protocol: number;
  readonly at: number;
  readonly feed: FeedStatus;
  readonly detail: string | null;
}

/** Relay -> HUD. */
export type ServerMessage = HelloMessage | SnapshotMessage | StatusMessage;

/** Producer -> relay, on the ingest endpoint. */
export interface PublishMessage {
  readonly type: 'publish';
  readonly protocol: number;
  readonly state: GameState;
}

export type ClientMessage = PublishMessage;
