import type { StateContext } from '@tinyscry/protocol';

import type { ActionDefinition } from './definitions.ts';
import { describeActionResult, LOCAL_ACTION_UNAVAILABLE } from './presentation.ts';
import type { ActionSink } from './types.ts';

export interface ActionInvocationState {
  pendingActionId: string | null;
  feedback: string | null;
}

export async function invokeAction(
  state: ActionInvocationState,
  sink: ActionSink,
  context: StateContext | null,
  definition: ActionDefinition,
): Promise<void> {
  if (state.pendingActionId !== null) return;

  if (context === null) {
    state.feedback = LOCAL_ACTION_UNAVAILABLE;
    return;
  }

  state.pendingActionId = definition.id;
  state.feedback = `Forwarding “${definition.label}”…`;
  try {
    state.feedback = describeActionResult(await sink.send(context, definition.command));
  } catch {
    state.feedback = describeActionResult({
      status: 'unknown',
      detail: 'Action request failed',
    });
  } finally {
    state.pendingActionId = null;
  }
}
