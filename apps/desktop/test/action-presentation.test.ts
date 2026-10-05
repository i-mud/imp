import { describe, expect, it } from 'vitest';

import { describeActionResult, LOCAL_ACTION_UNAVAILABLE } from '../src/lib/action/presentation.ts';

describe('action result presentation', () => {
  it('describes forwarding without claiming MUD execution or delivery', () => {
    const wording = describeActionResult({ status: 'forwarded', detail: null });

    expect(wording).toContain('Forwarded');
    expect(wording).not.toMatch(/TinyFugue|Mudlet/);
    expect(wording).not.toMatch(/executed|delivered|sent to (?:the )?MUD|success/i);
  });

  it.each([
    ['context is not active', /context changed/i],
    ['no matching local action consumer', /client action helper is unavailable/i],
    ['another action is in flight', /already in progress/i],
  ])('explains the current rejection reason %s without claiming forwarding', (detail, reason) => {
    const wording = describeActionResult({ status: 'rejected', detail });
    expect(wording).toMatch(reason);
    expect(wording).toMatch(/not forwarded/i);
    expect(wording).not.toMatch(/TinyFugue|Mudlet/);
  });

  it('shows other bounded relay rejection details without stronger claims', () => {
    expect(describeActionResult({ status: 'rejected', detail: 'command declined' })).toContain(
      'command declined',
    );
  });

  it('reports uncertain delivery and explicitly rules out automatic retry', () => {
    const wording = describeActionResult({ status: 'unknown', detail: 'consumer disconnected' });
    expect(wording).toMatch(/uncertain/i);
    expect(wording).toMatch(/did not retry/i);
    expect(wording).toContain('consumer disconnected');
  });

  it('keeps local context unavailability separate from wire statuses', () => {
    expect(LOCAL_ACTION_UNAVAILABLE).toMatch(/no current .*context/i);
    expect(LOCAL_ACTION_UNAVAILABLE).not.toMatch(/TinyFugue|Mudlet/);
    expect(['forwarded', 'rejected', 'unknown']).not.toContain(LOCAL_ACTION_UNAVAILABLE);
  });
});
