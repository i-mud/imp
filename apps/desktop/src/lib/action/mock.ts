import { isValidActionCommand, type StateContext } from '@imp/protocol';

import type { ActionResult, ActionSink } from './types.ts';

export class MockActionSink implements ActionSink {
  readonly id = 'mock';
  readonly label = 'Demo actions';
  readonly sent: Array<{ readonly context: StateContext; readonly command: string }> = [];
  private readonly result: ActionResult;

  constructor(result: ActionResult = { status: 'forwarded', detail: null }) {
    this.result = result;
  }

  send(context: StateContext, command: string): Promise<ActionResult> {
    if (!isValidActionCommand(command)) {
      return Promise.resolve({
        status: 'rejected',
        detail: 'Command must be 1..512 printable ASCII characters.',
      });
    }
    this.sent.push({ context, command });
    return Promise.resolve(this.result);
  }
}
