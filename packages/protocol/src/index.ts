export { LIMITS, isSafeText, isValidActionCommand } from './limits.ts';
export { PROTOCOL_VERSION } from './messages.ts';
export type {
  ActionMessage,
  ActionResultMessage,
  ActionStatus,
  ClientMessage,
  ConsumerMessage,
  ConsumerReadyMessage,
  ConsumerResultMessage,
  DispatchMessage,
  FeedStatus,
  HelloMessage,
  PublishMessage,
  RelayInfo,
  SelectMessage,
  ServerMessage,
  SnapshotMessage,
  StateContext,
  StatusMessage,
  TextMessage,
} from './messages.ts';
export { describeError } from './result.ts';
export type { DecodeResult, ProtocolError, ProtocolErrorCode } from './result.ts';
export { EMPTY_STATE, vitalFraction } from './state.ts';
export type { Character, GameState, Target, Vital } from './state.ts';
export { decodeClientMessage, decodeGameState, decodeServerMessage } from './decode.ts';
