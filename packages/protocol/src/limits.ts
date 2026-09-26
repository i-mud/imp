/**
 * Hard bounds applied to every externally supplied value.
 *
 * All protocol input originates (transitively) from a MUD server and is
 * therefore untrusted. These limits exist so that a hostile or broken server
 * cannot push unbounded work, unbounded memory, or terminal control sequences
 * into the HUD.
 */
export const LIMITS = {
  /**
   * Maximum accepted frame length in UTF-16 code units, checked before
   * `JSON.parse` so that parse cost stays bounded. The relay enforces a
   * separate byte-level cap on its sockets.
   */
  maxFrameChars: 16_384,
  /** Character and creature names. */
  maxNameChars: 64,
  /** Free-form diagnostic text carried by `status` messages. */
  maxDetailChars: 256,
  /** Relay identification strings. */
  maxRelayIdentChars: 64,
  /** Opaque TinyFugue session identifier. */
  maxContextSessionChars: 128,
  /** One received MUD text line carried as a transient event. */
  maxTextEventChars: 1024,
  /** One command forwarded to TinyFugue. */
  maxActionChars: 512,
  /** Relay-generated action dispatch identifier. */
  maxCorrelationChars: 64,
  /** Upper bound for vital `current` / `max` values. */
  maxVitalValue: 1_000_000_000,
} as const;

/**
 * C0 controls, DEL, C1 controls and unpaired surrogates.
 *
 * Control characters are rejected rather than stripped because a name
 * containing ANSI escapes is evidence of a normalization bug upstream, not
 * something the HUD should silently render.
 */
// eslint-disable-next-line no-control-regex -- matching control characters is the entire point of this pattern
const UNSAFE_TEXT = /[\u0000-\u001f\u007f-\u009f]|\p{Surrogate}/u;

/** True when `value` is safe to store and render as a label. */
export function isSafeText(value: string): boolean {
  return !UNSAFE_TEXT.test(value);
}

/** True when `value` is a non-empty printable-ASCII action command. */
export function isValidActionCommand(value: string): boolean {
  return value.length <= LIMITS.maxActionChars && /^[\x20-\x7e]+$/.test(value);
}
