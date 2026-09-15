/**
 * Normalized TinyScry game state.
 *
 * This shape is owned by TinyScry. It is deliberately *not* a mirror of any
 * GMCP package: normalization from MUD-specific packages happens in
 * `integrations/tinyfugue` so that MUD quirks never reach the HUD.
 *
 * `null` is used instead of optional properties because these values cross a
 * JSON boundary, where `null` round-trips and `undefined` does not. Producers
 * may omit a key; decoding treats absent and `null` identically.
 */

/** A current/max gauge such as hit points. */
export interface Vital {
  readonly current: number;
  readonly max: number;
}

export interface Character {
  readonly name: string;
  readonly hp: Vital | null;
  readonly mana: Vital | null;
  readonly moves: Vital | null;
}

export interface Target {
  readonly name: string;
  /** 0-100, or `null` when the MUD reports no usable target health. */
  readonly healthPercent: number | null;
}

export interface GameState {
  readonly character: Character | null;
  readonly target: Target | null;
}

/** State with nothing known yet; the HUD's "no data" condition. */
export const EMPTY_STATE: GameState = { character: null, target: null };

/**
 * Fill fraction for a vital, clamped to 0-1.
 *
 * `max <= 0` yields 0 rather than a division error: a MUD reporting a zero
 * maximum is nonsense, but it must not be able to break rendering. Values
 * above `max` are clamped because overheal is legitimate in many MUDs and the
 * raw numbers stay visible in the readout.
 */
export function vitalFraction(vital: Vital | null): number {
  if (vital === null || vital.max <= 0) return 0;
  const fraction = vital.current / vital.max;
  return fraction < 0 ? 0 : fraction > 1 ? 1 : fraction;
}
