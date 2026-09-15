import { describe, expect, it } from 'vitest';

import { INITIAL_MODEL, applyEvent, freshnessOf } from '../src/lib/hud/model.ts';

const firstState = {
  character: { name: 'Aria', hp: { current: 90, max: 100 }, mana: null, moves: null },
  target: null,
};
const secondState = {
  character: { name: 'Aria', hp: { current: 80, max: 100 }, mana: null, moves: null },
  target: null,
};

const connected = { kind: 'connection', phase: 'connected', detail: null } as const;

describe('applyEvent', () => {
  it('drops out-of-order snapshots', () => {
    const afterFirst = applyEvent(applyEvent(INITIAL_MODEL, connected), {
      kind: 'snapshot',
      seq: 4,
      at: 10,
      state: firstState,
    });
    const afterOlder = applyEvent(afterFirst, { kind: 'snapshot', seq: 3, at: 11, state: secondState });

    expect(afterOlder).toBe(afterFirst);
  });

  it('resets sequence tracking after a reconnect', () => {
    const initial = applyEvent(applyEvent(INITIAL_MODEL, connected), {
      kind: 'snapshot',
      seq: 9,
      at: 10,
      state: firstState,
    });
    const reconnecting = applyEvent(initial, { kind: 'connection', phase: 'reconnecting', detail: 'closed' });
    const reconnected = applyEvent(reconnecting, connected);
    const next = applyEvent(reconnected, { kind: 'snapshot', seq: 0, at: 12, state: secondState });

    expect(next.lastSeq).toBe(0);
    expect(next.state).toEqual(secondState);
  });

  it('records protocol errors without changing state', () => {
    const withState = applyEvent(applyEvent(INITIAL_MODEL, connected), {
      kind: 'snapshot',
      seq: 0,
      at: 10,
      state: firstState,
    });
    const afterError = applyEvent(withState, {
      kind: 'protocol-error',
      error: { code: 'invalid_field', path: 'state.character.hp.current', message: 'outside range' },
    });

    expect(afterError.state).toBe(withState.state);
    expect(afterError.lastError?.code).toBe('invalid_field');
  });

  it('transitions from no data to fresh data', () => {
    const connectedModel = applyEvent(INITIAL_MODEL, connected);
    const withSnapshot = applyEvent(connectedModel, { kind: 'snapshot', seq: 0, at: 10, state: firstState });

    expect(connectedModel.hasData).toBe(false);
    expect(withSnapshot.hasData).toBe(true);
  });

  it('retains last values but marks them non-fresh while reconnecting', () => {
    const withSnapshot = applyEvent(applyEvent(INITIAL_MODEL, connected), {
      kind: 'snapshot',
      seq: 0,
      at: 10,
      state: firstState,
    });
    const reconnecting = applyEvent(withSnapshot, {
      kind: 'connection',
      phase: 'reconnecting',
      detail: 'closed',
    });

    expect(reconnecting.state).toBe(withSnapshot.state);
    expect(reconnecting.hasData).toBe(false);
  });
});

describe('freshnessOf', () => {
  const live = applyEvent(
    applyEvent(applyEvent(INITIAL_MODEL, connected), { kind: 'snapshot', seq: 0, at: 10, state: firstState }),
    { kind: 'feed', status: 'live', detail: null },
  );

  it('treats a connected socket with a live feed as fresh', () => {
    expect(freshnessOf(live)).toBe('fresh');
    expect(live.hasData).toBe(true);
  });

  /**
   * Regression: the HUD used to derive freshness from the socket alone, so a
   * relay that outlived its producer kept showing minutes-old vitals at full
   * confidence. Socket health is not feed health.
   */
  it('is not fresh when the socket is up but the feed died', () => {
    const producerGone = applyEvent(live, { kind: 'feed', status: 'down', detail: 'no producer connected' });

    expect(producerGone.phase).toBe('connected');
    expect(producerGone.hasData).toBe(true);
    expect(freshnessOf(producerGone)).toBe('feed-down');
  });

  it('distinguishes a stalled feed from a dead one', () => {
    expect(freshnessOf(applyEvent(live, { kind: 'feed', status: 'stale', detail: null }))).toBe(
      'feed-stalled',
    );
  });

  it('blames the socket, not the feed, while reconnecting', () => {
    const reconnecting = applyEvent(live, { kind: 'connection', phase: 'reconnecting', detail: 'closed' });

    // The last feed status is still `live`, but the socket outage outranks it.
    expect(reconnecting.feed).toBe('live');
    expect(freshnessOf(reconnecting)).toBe('reconnecting');
  });

  it('reports offline before any connection attempt succeeds', () => {
    expect(freshnessOf(INITIAL_MODEL)).toBe('offline');
    expect(
      freshnessOf(applyEvent(INITIAL_MODEL, { kind: 'connection', phase: 'disconnected', detail: null })),
    ).toBe('offline');
  });
});
