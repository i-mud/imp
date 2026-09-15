export { LIMITS, isSafeText } from './limits.ts';
export { PROTOCOL_VERSION } from './messages.ts';
export type {
  ClientMessage,
  FeedStatus,
  HelloMessage,
  PublishMessage,
  RelayInfo,
  ServerMessage,
  SnapshotMessage,
  StatusMessage,
} from './messages.ts';
export { describeError } from './result.ts';
export type { DecodeResult, ProtocolError, ProtocolErrorCode } from './result.ts';
export { EMPTY_STATE, vitalFraction } from './state.ts';
export type { Character, GameState, Target, Vital } from './state.ts';
export { decodeClientMessage, decodeGameState, decodeServerMessage } from './decode.ts';
