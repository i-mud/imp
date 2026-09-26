import type { GameState } from './state.ts';

/** Breaking v2 cutover: state and actions are bound to a TinyFugue context. */
export const PROTOCOL_VERSION = 2;

export interface RelayInfo {
  readonly name: string;
  readonly version: string;
}

/** Opaque desktop-visible identity for one foreground TinyFugue connection. */
export interface StateContext {
  readonly session: string;
  readonly foreground: number;
  readonly connection: number;
}

export type FeedStatus = 'live' | 'stale' | 'down';
export type ActionStatus = 'forwarded' | 'rejected' | 'unknown';

export interface HelloMessage {
  readonly type: 'hello';
  readonly protocol: number;
  readonly at: number;
  readonly relay: RelayInfo;
}

export interface SnapshotMessage {
  readonly type: 'snapshot';
  readonly protocol: number;
  readonly seq: number;
  readonly at: number;
  readonly context: StateContext | null;
  readonly state: GameState;
}

export interface StatusMessage {
  readonly type: 'status';
  readonly protocol: number;
  readonly at: number;
  readonly feed: FeedStatus;
  readonly detail: string | null;
}

export interface TextMessage {
  readonly type: 'text';
  readonly protocol: number;
  readonly context: StateContext;
  readonly at: number;
  readonly text: string;
}

export interface ActionResultMessage {
  readonly type: 'action-result';
  readonly protocol: number;
  readonly status: ActionStatus;
  readonly detail: string | null;
}

export interface ConsumerReadyMessage {
  readonly type: 'consumer-ready';
  readonly protocol: number;
  readonly context: StateContext;
}

export interface DispatchMessage {
  readonly type: 'dispatch';
  readonly protocol: number;
  readonly id: string;
  readonly context: StateContext;
  readonly command: string;
}

export type ServerMessage =
  | HelloMessage
  | SnapshotMessage
  | StatusMessage
  | TextMessage
  | ActionResultMessage
  | ConsumerReadyMessage
  | DispatchMessage;

export interface SelectMessage {
  readonly type: 'select';
  readonly protocol: number;
  readonly context: StateContext | null;
  readonly state: GameState;
}

export interface PublishMessage {
  readonly type: 'publish';
  readonly protocol: number;
  readonly context: StateContext;
  readonly state: GameState;
}

export interface ActionMessage {
  readonly type: 'action';
  readonly protocol: number;
  readonly context: StateContext;
  readonly command: string;
}

export interface ConsumerMessage {
  readonly type: 'consumer';
  readonly protocol: number;
  readonly context: StateContext;
}

export interface ConsumerResultMessage {
  readonly type: 'consumer-result';
  readonly protocol: number;
  readonly id: string;
  readonly status: 'forwarded' | 'rejected';
}

export type ClientMessage =
  SelectMessage | PublishMessage | TextMessage | ActionMessage | ConsumerMessage | ConsumerResultMessage;
