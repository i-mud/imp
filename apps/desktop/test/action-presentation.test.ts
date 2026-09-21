import { describe, expect, it } from 'vitest';

import { describeActionResult, LOCAL_ACTION_UNAVAILABLE } from '../src/lib/action/presentation.ts';

describe('action result presentation', () => {
  it('describes forwarding without claiming MUD execution or delivery', () => {
    const wording = describeActionResult({ status: 'forwarded', detail: null });

    expect(wording).toBe('Forwarded to TinyFugue. Final MUD delivery is not confirmed.');
    expect(wording).not.toMatch(/executed|delivered|sent to (?:the )?MUD|success/i);
  });

  it.each([
    ['context is not active', 'Action was not forwarded because the game context changed.'],
    [
      'no matching TinyFugue consumer',
      'Action was not forwarded because the TinyFugue action helper is unavailable for this context.',
    ],
    [
      'another action is in flight',
      'Action was not forwarded because another action is already in progress.',
    ],
  ])('maps the known rejection %s', (detail, expected) => {
    expect(describeActionResult({ status: 'rejected', detail })).toBe(expected);
  });

  it('shows other bounded relay rejection details without stronger claims', () => {
    expect(describeActionResult({ status: 'rejected', detail: 'command declined' })).toBe(
      'Action was not forwarded: command declined',
    );
  });

  it('reports uncertain delivery and explicitly rules out automatic retry', () => {
    expect(describeActionResult({ status: 'unknown', detail: 'consumer disconnected' })).toBe(
      'Delivery uncertain. TinyScry did not retry. Detail: consumer disconnected.',
    );
  });

  it('does not duplicate punctuation from a bounded unknown detail', () => {
    expect(describeActionResult({ status: 'unknown', detail: 'Relay action timed out.' })).toBe(
      'Delivery uncertain. TinyScry did not retry. Detail: Relay action timed out.',
    );
  });

  it('keeps local context unavailability separate from wire statuses', () => {
    expect(LOCAL_ACTION_UNAVAILABLE).toBe('No current TinyFugue game context.');
    expect(['forwarded', 'rejected', 'unknown']).not.toContain(LOCAL_ACTION_UNAVAILABLE);
  });
});
