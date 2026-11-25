import type { StateContext } from '@imp/protocol';

import type { ActionDefinition } from './definitions.ts';
import { describeActionResult, LOCAL_ACTION_UNAVAILABLE } from './presentation.ts';
import type { ActionResult, ActionSink } from './types.ts';

export interface ActionInvocationState {
  pendingActionId: string | null;
  feedback: { status: ActionResult['status'] | 'pending'; message: string } | null;
}

export async function invokeAction(
  state: ActionInvocationState,
  sink: ActionSink,
  context: StateContext | null,
  definition: ActionDefinition,
): Promise<void> {
  if (state.pendingActionId !== null) return;

  if (context === null) {
    state.feedback = { status: 'rejected', message: LOCAL_ACTION_UNAVAILABLE };
    return;
  }

  state.pendingActionId = definition.id;
  state.feedback = { status: 'pending', message: `Forwarding “${definition.label}”…` };
  try {
    const result = await sink.send(context, definition.command);
    state.feedback = { status: result.status, message: describeActionResult(result) };
  } catch {
    state.feedback = {
      status: 'unknown',
      message: describeActionResult({ status: 'unknown', detail: 'Action request failed' }),
    };
  } finally {
    state.pendingActionId = null;
  }
}
