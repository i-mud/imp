import { LIMITS, isSafeText, isValidActionCommand } from './limits.ts';
import {
  PROTOCOL_VERSION,
  type ActionStatus,
  type ClientMessage,
  type FeedStatus,
  type RelayInfo,
  type ServerMessage,
  type StateContext,
} from './messages.ts';
import { fail, ok, type DecodeResult } from './result.ts';
import type { Character, GameState, Target, Vital } from './state.ts';

type JsonObject = Record<string, unknown>;

/** Longest accepted `type` discriminator. */
const MAX_TYPE_CHARS = 32;
const SESSION_PATTERN = /^[A-Za-z0-9_]+$/;

function readObject(value: unknown, path: string): DecodeResult<JsonObject> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return fail('invalid_field', path, 'expected an object');
  }
  return ok(value as JsonObject);
}

function readText(value: unknown, path: string, maxChars: number): DecodeResult<string> {
  if (typeof value !== 'string') return fail('invalid_field', path, 'expected a string');
  if (value.length === 0) return fail('invalid_field', path, 'expected a non-empty string');
  if (value.length > maxChars) return fail('invalid_field', path, `longer than ${maxChars} characters`);
  if (!isSafeText(value)) return fail('invalid_field', path, 'contains control or malformed characters');
  return ok(value);
}

function readInteger(value: unknown, path: string, min: number, max: number): DecodeResult<number> {
  if (typeof value !== 'number' || !Number.isInteger(value)) {
    return fail('invalid_field', path, 'expected an integer');
  }
  if (value < min || value > max) return fail('invalid_field', path, `outside ${min}..${max}`);
  return ok(value);
}

function readContext(value: unknown, path: string): DecodeResult<StateContext> {
  const object = readObject(value, path);
  if (!object.ok) return object;

  const session = readText(object.value['session'], `${path}.session`, LIMITS.maxContextSessionChars);
  if (!session.ok) return session;
  if (!SESSION_PATTERN.test(session.value)) {
    return fail('invalid_field', `${path}.session`, 'expected letters, digits or underscores');
  }
  const foreground = readInteger(
    object.value['foreground'],
    `${path}.foreground`,
    1,
    Number.MAX_SAFE_INTEGER,
  );
  if (!foreground.ok) return foreground;
  const connection = readInteger(
    object.value['connection'],
    `${path}.connection`,
    1,
    Number.MAX_SAFE_INTEGER,
  );
  if (!connection.ok) return connection;

  return ok({ session: session.value, foreground: foreground.value, connection: connection.value });
}

function readNullableContext(value: unknown, path: string): DecodeResult<StateContext | null> {
  return value === null ? ok(null) : readContext(value, path);
}

function readVital(value: unknown, path: string): DecodeResult<Vital | null> {
  if (value === undefined || value === null) return ok(null);
  const object = readObject(value, path);
  if (!object.ok) return object;

  const current = readInteger(object.value['current'], `${path}.current`, 0, LIMITS.maxVitalValue);
  if (!current.ok) return current;
  const max = readInteger(object.value['max'], `${path}.max`, 0, LIMITS.maxVitalValue);
  if (!max.ok) return max;
  return ok({ current: current.value, max: max.value });
}

function readCharacter(value: unknown, path: string): DecodeResult<Character | null> {
  if (value === undefined || value === null) return ok(null);
  const object = readObject(value, path);
  if (!object.ok) return object;

  const name = readText(object.value['name'], `${path}.name`, LIMITS.maxNameChars);
  if (!name.ok) return name;
  const hp = readVital(object.value['hp'], `${path}.hp`);
  if (!hp.ok) return hp;
  const mana = readVital(object.value['mana'], `${path}.mana`);
  if (!mana.ok) return mana;
  const moves = readVital(object.value['moves'], `${path}.moves`);
  if (!moves.ok) return moves;

  return ok({ name: name.value, hp: hp.value, mana: mana.value, moves: moves.value });
}

function readTarget(value: unknown, path: string): DecodeResult<Target | null> {
  if (value === undefined || value === null) return ok(null);
  const object = readObject(value, path);
  if (!object.ok) return object;

  const name = readText(object.value['name'], `${path}.name`, LIMITS.maxNameChars);
  if (!name.ok) return name;
  const rawPercent = object.value['healthPercent'];
  if (rawPercent === undefined || rawPercent === null) return ok({ name: name.value, healthPercent: null });
  if (typeof rawPercent !== 'number' || !Number.isFinite(rawPercent) || rawPercent < 0 || rawPercent > 100) {
    return fail('invalid_field', `${path}.healthPercent`, 'expected a finite number in 0..100');
  }
  return ok({ name: name.value, healthPercent: rawPercent });
}

function readRelayInfo(value: unknown, path: string): DecodeResult<RelayInfo> {
  const object = readObject(value, path);
  if (!object.ok) return object;
  const name = readText(object.value['name'], `${path}.name`, LIMITS.maxRelayIdentChars);
  if (!name.ok) return name;
  const version = readText(object.value['version'], `${path}.version`, LIMITS.maxRelayIdentChars);
  if (!version.ok) return version;
  return ok({ name: name.value, version: version.value });
}

function readFeedStatus(value: unknown, path: string): DecodeResult<FeedStatus> {
  if (value === 'live' || value === 'stale' || value === 'down') return ok(value);
  return fail('invalid_field', path, 'expected "live", "stale" or "down"');
}

function readActionStatus(value: unknown, path: string): DecodeResult<ActionStatus> {
  if (value === 'forwarded' || value === 'rejected' || value === 'unknown') return ok(value);
  return fail('invalid_field', path, 'expected "forwarded", "rejected" or "unknown"');
}

function readConsumerStatus(value: unknown, path: string): DecodeResult<'forwarded' | 'rejected'> {
  if (value === 'forwarded' || value === 'rejected') return ok(value);
  return fail('invalid_field', path, 'expected "forwarded" or "rejected"');
}

function readDetail(value: unknown, path: string): DecodeResult<string | null> {
  if (value === undefined || value === null) return ok(null);
  return readText(value, path, LIMITS.maxDetailChars);
}

function readCommand(value: unknown, path: string): DecodeResult<string> {
  if (typeof value !== 'string' || !isValidActionCommand(value)) {
    return fail('invalid_field', path, `expected 1..${LIMITS.maxActionChars} printable ASCII characters`);
  }
  return ok(value);
}

function readCorrelation(value: unknown, path: string): DecodeResult<string> {
  const correlation = readText(value, path, LIMITS.maxCorrelationChars);
  if (!correlation.ok) return correlation;
  if (!SESSION_PATTERN.test(correlation.value)) {
    return fail('invalid_field', path, 'expected letters, digits or underscores');
  }
  return correlation;
}

/** Validate a normalized state object. */
export function decodeGameState(value: unknown, path = 'state'): DecodeResult<GameState> {
  const object = readObject(value, path);
  if (!object.ok) return object;
  const character = readCharacter(object.value['character'], `${path}.character`);
  if (!character.ok) return character;
  const target = readTarget(object.value['target'], `${path}.target`);
  if (!target.ok) return target;
  return ok({ character: character.value, target: target.value });
}

interface Envelope {
  readonly type: string;
  readonly protocol: number;
  readonly body: JsonObject;
}

function readEnvelope(raw: string): DecodeResult<Envelope> {
  if (raw.length > LIMITS.maxFrameChars) {
    return fail('frame_too_large', '', `frame longer than ${LIMITS.maxFrameChars} characters`);
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return fail('invalid_json', '', 'frame is not valid JSON');
  }
  const object = readObject(parsed, '');
  if (!object.ok) return object;
  const protocol = readInteger(object.value['protocol'], 'protocol', 0, Number.MAX_SAFE_INTEGER);
  if (!protocol.ok) return protocol;
  if (protocol.value !== PROTOCOL_VERSION) {
    return fail('unsupported_protocol', 'protocol', `expected version ${PROTOCOL_VERSION}`);
  }
  const type = readText(object.value['type'], 'type', MAX_TYPE_CHARS);
  if (!type.ok) return type;
  return ok({ type: type.value, protocol: protocol.value, body: object.value });
}

/** Decode one relay -> client frame. */
export function decodeServerMessage(raw: string): DecodeResult<ServerMessage> {
  const envelope = readEnvelope(raw);
  if (!envelope.ok) return envelope;
  const { type, protocol, body } = envelope.value;

  switch (type) {
    case 'hello': {
      const at = readInteger(body['at'], 'at', 0, Number.MAX_SAFE_INTEGER);
      if (!at.ok) return at;
      const relay = readRelayInfo(body['relay'], 'relay');
      if (!relay.ok) return relay;
      return ok({ type, protocol, at: at.value, relay: relay.value });
    }
    case 'snapshot': {
      const seq = readInteger(body['seq'], 'seq', 0, Number.MAX_SAFE_INTEGER);
      if (!seq.ok) return seq;
      const at = readInteger(body['at'], 'at', 0, Number.MAX_SAFE_INTEGER);
      if (!at.ok) return at;
      const context = readNullableContext(body['context'], 'context');
      if (!context.ok) return context;
      const state = decodeGameState(body['state']);
      if (!state.ok) return state;
      return ok({ type, protocol, seq: seq.value, at: at.value, context: context.value, state: state.value });
    }
    case 'status': {
      const at = readInteger(body['at'], 'at', 0, Number.MAX_SAFE_INTEGER);
      if (!at.ok) return at;
      const feed = readFeedStatus(body['feed'], 'feed');
      if (!feed.ok) return feed;
      const detail = readDetail(body['detail'], 'detail');
      if (!detail.ok) return detail;
      return ok({ type, protocol, at: at.value, feed: feed.value, detail: detail.value });
    }
    case 'action-result': {
      const status = readActionStatus(body['status'], 'status');
      if (!status.ok) return status;
      const detail = readDetail(body['detail'], 'detail');
      if (!detail.ok) return detail;
      return ok({ type, protocol, status: status.value, detail: detail.value });
    }
    case 'consumer-ready': {
      const context = readContext(body['context'], 'context');
      if (!context.ok) return context;
      return ok({ type, protocol, context: context.value });
    }
    case 'dispatch': {
      const id = readCorrelation(body['id'], 'id');
      if (!id.ok) return id;
      const context = readContext(body['context'], 'context');
      if (!context.ok) return context;
      const command = readCommand(body['command'], 'command');
      if (!command.ok) return command;
      return ok({ type, protocol, id: id.value, context: context.value, command: command.value });
    }
    default:
      return fail('unknown_type', 'type', 'unsupported message type');
  }
}

/** Decode one client -> relay frame. */
export function decodeClientMessage(raw: string): DecodeResult<ClientMessage> {
  const envelope = readEnvelope(raw);
  if (!envelope.ok) return envelope;
  const { type, protocol, body } = envelope.value;

  switch (type) {
    case 'select': {
      const context = readNullableContext(body['context'], 'context');
      if (!context.ok) return context;
      const state = decodeGameState(body['state']);
      if (!state.ok) return state;
      return ok({ type, protocol, context: context.value, state: state.value });
    }
    case 'publish': {
      const context = readContext(body['context'], 'context');
      if (!context.ok) return context;
      const state = decodeGameState(body['state']);
      if (!state.ok) return state;
      return ok({ type, protocol, context: context.value, state: state.value });
    }
    case 'action': {
      const context = readContext(body['context'], 'context');
      if (!context.ok) return context;
      const command = readCommand(body['command'], 'command');
      if (!command.ok) return command;
      return ok({ type, protocol, context: context.value, command: command.value });
    }
    case 'consumer': {
      const context = readContext(body['context'], 'context');
      if (!context.ok) return context;
      return ok({ type, protocol, context: context.value });
    }
    case 'consumer-result': {
      const id = readCorrelation(body['id'], 'id');
      if (!id.ok) return id;
      const status = readConsumerStatus(body['status'], 'status');
      if (!status.ok) return status;
      return ok({ type, protocol, id: id.value, status: status.value });
    }
    default:
      return fail('unknown_type', 'type', 'unsupported message type');
  }
}
