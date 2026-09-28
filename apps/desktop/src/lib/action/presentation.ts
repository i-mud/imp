import type { ActionResult } from './types.ts';

export const LOCAL_ACTION_UNAVAILABLE = 'No current TinyFugue game context.';

export function describeActionResult(result: ActionResult): string {
  if (result.status === 'forwarded') {
    return 'Forwarded to TinyFugue. Final MUD delivery is not confirmed.';
  }

  if (result.status === 'unknown') {
    const detail = result.detail ?? 'No result was returned';
    const punctuation = /[.!?]$/u.test(detail) ? '' : '.';
    return `Delivery uncertain. Imp did not retry. Detail: ${detail}${punctuation}`;
  }

  switch (result.detail) {
    case 'context is not active':
      return 'Action was not forwarded because the game context changed.';
    case 'no matching TinyFugue consumer':
      return 'Action was not forwarded because the TinyFugue action helper is unavailable for this context.';
    case 'another action is in flight':
      return 'Action was not forwarded because another action is already in progress.';
    default:
      return result.detail === null
        ? 'Action was not forwarded.'
        : `Action was not forwarded: ${result.detail}`;
  }
}
