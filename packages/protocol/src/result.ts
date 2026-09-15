export type ProtocolErrorCode =
  'frame_too_large' | 'invalid_json' | 'unsupported_protocol' | 'unknown_type' | 'invalid_field';

export interface ProtocolError {
  readonly code: ProtocolErrorCode;
  /** Dotted location of the offending value, e.g. `state.character.hp.max`. */
  readonly path: string;
  readonly message: string;
}

export type DecodeResult<T> =
  { readonly ok: true; readonly value: T } | { readonly ok: false; readonly error: ProtocolError };

export function ok<T>(value: T): DecodeResult<T> {
  return { ok: true, value };
}

export function fail(code: ProtocolErrorCode, path: string, message: string): DecodeResult<never> {
  return { ok: false, error: { code, path, message } };
}

/**
 * Single-line, length-capped rendering of a decode failure, safe to log.
 *
 * Rejected input is never included: it is attacker-controlled and the whole
 * point of rejecting it is to keep it out of logs and terminals.
 */
export function describeError(error: ProtocolError): string {
  const path = error.path === '' ? '<frame>' : error.path;
  return `${error.code} at ${path}: ${error.message}`;
}
