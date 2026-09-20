import type { ActionStatus, StateContext } from '@tinyscry/protocol';

export interface ActionResult {
  readonly status: ActionStatus;
  readonly detail: string | null;
}

export interface ActionSink {
  readonly id: string;
  readonly label: string;
  send(context: StateContext, command: string): Promise<ActionResult>;
}
