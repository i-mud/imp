import { describe, expect, it } from 'vitest';
import { decodeClientMessage, decodeGameState, decodeServerMessage } from '../src/decode.ts';
import { LIMITS, isSafeText } from '../src/limits.ts';
import { describeError } from '../src/result.ts';
import { EMPTY_STATE, vitalFraction } from '../src/state.ts';

function snapshotFrame(state: unknown, extra: Record<string, unknown> = {}): string {
  return JSON.stringify({ type: 'snapshot', protocol: 2, seq: 1, at: 1, context: null, state, ...extra });
}

describe('forwards compatibility', () => {
  it('ignores unknown fields instead of rejecting or retaining them', () => {
    const result = decodeServerMessage(
      snapshotFrame(
        {
          character: { name: 'Example', hp: { current: 1, max: 2, regen: 9 }, level: 40 },
          target: null,
          room: { name: 'Temple' },
        },
        { region: 'midgaard' },
      ),
    );

    if (!result.ok) throw new Error(describeError(result.error));
    if (result.value.type !== 'snapshot') throw new Error('expected a snapshot');
    // Decoding is a projection onto the owned shape: unknown keys are dropped,
    // so a newer relay cannot smuggle fields into HUD state.
    expect(result.value.state).toEqual({
      character: { name: 'Example', hp: { current: 1, max: 2 }, mana: null, moves: null },
      target: null,
    });
  });

  it('treats an absent vital and an explicit null identically', () => {
    const absent = decodeGameState({ character: { name: 'A' }, target: null });
    const explicit = decodeGameState({
      character: { name: 'A', hp: null, mana: null, moves: null },
      target: null,
    });

    expect(absent.ok && explicit.ok).toBe(true);
    if (!absent.ok || !explicit.ok) return;
    expect(absent.value).toEqual(explicit.value);
  });
});

describe('hostile input', () => {
  it('rejects a lone surrogate in a name', () => {
    // JSON.parse happily produces unpaired surrogates from \uD800 escapes.
    const result = decodeServerMessage(snapshotFrame({ character: { name: 'Ev\ud800il' }, target: null }));
    expect(result.ok ? null : result.error.path).toBe('state.character.name');
  });

  it('accepts a frame at the size cap and rejects one character more', () => {
    const padTo = (length: number): string => {
      const base = snapshotFrame({ character: null, target: null }, { pad: '' });
      return base.replace('"pad":""', `"pad":"${'A'.repeat(length - base.length)}"`);
    };

    expect(decodeServerMessage(padTo(LIMITS.maxFrameChars)).ok).toBe(true);
    const overflow = decodeServerMessage(padTo(LIMITS.maxFrameChars + 1));
    expect(overflow.ok ? null : overflow.error.code).toBe('frame_too_large');
  });

  it('reports the failing path for nested values', () => {
    const result = decodeServerMessage(
      snapshotFrame({ character: { name: 'A', mana: { current: 5, max: -1 } }, target: null }),
    );
    expect(result.ok ? null : result.error.path).toBe('state.character.mana.max');
  });

  it('classifies control characters as unsafe text', () => {
    expect(isSafeText('Ancient Troll')).toBe(true);
    expect(isSafeText('Ancient\u001b[31m Troll')).toBe(false);
    expect(isSafeText('line\nbreak')).toBe(false);
  });
});

describe('vitalFraction', () => {
  it('clamps overheal to a full bar while leaving the numbers to the caller', () => {
    expect(vitalFraction({ current: 1200, max: 1000 })).toBe(1);
  });

  it('yields zero for an absent vital or a nonsensical maximum', () => {
    expect(vitalFraction(null)).toBe(0);
    expect(vitalFraction({ current: 5, max: 0 })).toBe(0);
  });

  it('maps a partial vital to its fraction', () => {
    expect(vitalFraction({ current: 250, max: 1000 })).toBeCloseTo(0.25);
  });
});

describe('describeError', () => {
  it('names the whole frame when the failure has no field path', () => {
    const result = decodeServerMessage('{');
    expect(result.ok ? '' : describeError(result.error)).toBe(
      'invalid_json at <frame>: frame is not valid JSON',
    );
  });
});

describe('EMPTY_STATE', () => {
  it('round-trips through the decoder', () => {
    const result = decodeGameState(EMPTY_STATE);
    expect(result.ok && result.value).toEqual(EMPTY_STATE);
  });
});

describe('transient text events', () => {
  const frame = JSON.stringify({
    type: 'text',
    protocol: 2,
    context: { session: 'session1', foreground: 2, connection: 3 },
    at: 1234,
    text: 'The ancient troll snarls at you.',
  });

  it('decodes the same bounded shape in both directions', () => {
    const server = decodeServerMessage(frame);
    const client = decodeClientMessage(frame);

    expect(server.ok && server.value).toEqual({
      type: 'text',
      protocol: 2,
      context: { session: 'session1', foreground: 2, connection: 3 },
      at: 1234,
      text: 'The ancient troll snarls at you.',
    });
    expect(client.ok && client.value).toEqual(server.ok ? server.value : null);
  });

  it('rejects unsafe and overlong text', () => {
    const unsafe = decodeServerMessage(
      JSON.stringify({
        type: 'text',
        protocol: 2,
        context: { session: 'session1', foreground: 2, connection: 3 },
        at: 1234,
        text: 'hello\u001b[31m',
      }),
    );
    expect(unsafe.ok ? null : unsafe.error.path).toBe('text');

    const overlong = decodeServerMessage(
      JSON.stringify({
        type: 'text',
        protocol: 2,
        context: { session: 'session1', foreground: 2, connection: 3 },
        at: 1234,
        text: 'x'.repeat(LIMITS.maxTextEventChars + 1),
      }),
    );
    expect(overlong.ok ? null : overlong.error.path).toBe('text');
  });
});
