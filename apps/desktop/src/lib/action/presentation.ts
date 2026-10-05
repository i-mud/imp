import type { ActionResult } from './types.ts';

export const LOCAL_ACTION_UNAVAILABLE = 'No current MUD game context.';

export function describeActionResult(result: ActionResult): string {
  if (result.status === 'forwarded') {
    return 'Forwarded to the game client. Final MUD delivery is not confirmed.';
  }

  if (result.status === 'unknown') {
    const detail = result.detail ?? 'No result was returned';
    const punctuation = /[.!?]$/u.test(detail) ? '' : '.';
    return `Delivery uncertain. Imp did not retry. Detail: ${detail}${punctuation}`;
  }

  switch (result.detail) {
    case 'context is not active':
      return 'Action was not forwarded because the game context changed.';
    case 'no matching local action consumer':
      return 'Action was not forwarded because the game client action helper is unavailable for this context.';
    case 'another action is in flight':
      return 'Action was not forwarded because another action is already in progress.';
    default:
      return result.detail === null
        ? 'Action was not forwarded.'
        : `Action was not forwarded: ${result.detail}`;
  }
}
