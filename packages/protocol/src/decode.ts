import { LIMITS, isSafeText } from './limits.ts';
import {
  PROTOCOL_VERSION,
  type ClientMessage,
  type FeedStatus,
  type RelayInfo,
  type ServerMessage,
} from './messages.ts';
import { fail, ok, type DecodeResult } from './result.ts';
import type { Character, GameState, Target, Vital } from './state.ts';

type JsonObject = Record<string, unknown>;

/**
 * Longest accepted `type` discriminator. Keeps an oversized string out of
 * error paths before the type is even recognised.
 */
const MAX_TYPE_CHARS = 32;

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
  // Number.isInteger rejects NaN and both infinities, so finiteness is covered.
  if (typeof value !== 'number' || !Number.isInteger(value)) {
    return fail('invalid_field', path, 'expected an integer');
  }
  if (value < min || value > max) return fail('invalid_field', path, `outside ${min}..${max}`);
  return ok(value);
}

function readVital(value: unknown, path: string): DecodeResult<Vital | null> {
  if (value === undefined || value === null) return ok(null);
  const object = readObject(value, path);
  if (!object.ok) return object;

  const current = readInteger(object.value['current'], `${path}.current`, 0, LIMITS.maxVitalValue);
  if (!current.ok) return current;
  const max = readInteger(object.value['max'], `${path}.max`, 0, LIMITS.maxVitalValue);
  if (!max.ok) return max;

  // `current > max` is deliberately accepted: overheal and temporary buffs are
  // legitimate in MUDs. Rendering clamps the bar; the readout keeps the truth.
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
  if (rawPercent === undefined || rawPercent === null) {
    return ok({ name: name.value, healthPercent: null });
  }
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

function readDetail(value: unknown, path: string): DecodeResult<string | null> {
  if (value === undefined || value === null) return ok(null);
  return readText(value, path, LIMITS.maxDetailChars);
}

/**
 * Validate a normalized state object.
 *
 * Exported so producers can verify their own output before publishing, which
 * keeps malformed state from ever reaching the wire.
 */
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

/**
 * Decode one relay -> HUD frame.
 *
 * Fails closed: nothing is returned unless the entire frame validates, so a
 * caller can never apply a partially decoded snapshot. Callers should treat
 * `unknown_type` as "ignore this frame" (a newer relay sent something we do
 * not model) and every other code as a protocol violation.
 */
export function decodeServerMessage(raw: string): DecodeResult<ServerMessage> {
  const envelope = readEnvelope(raw);
  if (!envelope.ok) return envelope;
  const { type, protocol, body } = envelope.value;

  const at = readInteger(body['at'], 'at', 0, Number.MAX_SAFE_INTEGER);
  if (!at.ok) return at;

  switch (type) {
    case 'hello': {
      const relay = readRelayInfo(body['relay'], 'relay');
      if (!relay.ok) return relay;
      return ok({ type: 'hello', protocol, at: at.value, relay: relay.value });
    }
    case 'snapshot': {
      const seq = readInteger(body['seq'], 'seq', 0, Number.MAX_SAFE_INTEGER);
      if (!seq.ok) return seq;
      const state = decodeGameState(body['state']);
      if (!state.ok) return state;
      return ok({ type: 'snapshot', protocol, seq: seq.value, at: at.value, state: state.value });
    }
    case 'status': {
      const feed = readFeedStatus(body['feed'], 'feed');
      if (!feed.ok) return feed;
      const detail = readDetail(body['detail'], 'detail');
      if (!detail.ok) return detail;
      return ok({ type: 'status', protocol, at: at.value, feed: feed.value, detail: detail.value });
    }
    default:
      return fail('unknown_type', 'type', 'unsupported message type');
  }
}

/** Decode one producer -> relay frame. */
export function decodeClientMessage(raw: string): DecodeResult<ClientMessage> {
  const envelope = readEnvelope(raw);
  if (!envelope.ok) return envelope;
  const { type, protocol, body } = envelope.value;

  if (type !== 'publish') return fail('unknown_type', 'type', 'unsupported message type');

  const state = decodeGameState(body['state']);
  if (!state.ok) return state;
  return ok({ type: 'publish', protocol, state: state.value });
}
