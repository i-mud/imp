import { describe, expect, it, vi } from 'vitest';

import type { ActionInvocationState } from '../src/lib/action/invocation.ts';
import { invokeAction } from '../src/lib/action/invocation.ts';
import type { ActionSink } from '../src/lib/action/types.ts';

const context = { session: 'session1', foreground: 2, connection: 3 } as const;
const action = { id: 'look', label: 'Look', command: '  look  ' } as const;

function initialState(): ActionInvocationState {
  return { pendingActionId: null, feedback: null };
}

function sinkWith(send: ActionSink['send']): ActionSink {
  return { id: 'test', label: 'Test actions', send };
}

describe('action invocation state', () => {
  it('rejects a null context locally without calling the sink', async () => {
    const send = vi.fn<ActionSink['send']>();
    const state = initialState();

    await invokeAction(state, sinkWith(send), null, action);

    expect(send).not.toHaveBeenCalled();
    expect(state).toEqual({
      pendingActionId: null,
      feedback: 'No current TinyFugue game context.',
    });
  });

  it('forwards the captured context and exact command once, then clears pending state', async () => {
    const send = vi.fn<ActionSink['send']>().mockResolvedValue({
      status: 'rejected',
      detail: 'context is not active',
    });
    const state = initialState();

    await invokeAction(state, sinkWith(send), context, action);

    expect(send).toHaveBeenCalledOnce();
    expect(send).toHaveBeenCalledWith(context, '  look  ');
    expect(state.pendingActionId).toBeNull();
    expect(state.feedback).toBe('Action was not forwarded because the game context changed.');
  });

  it('does not dispatch a second action while the first promise is pending', async () => {
    const pending = Promise.withResolvers<{ status: 'forwarded'; detail: null }>();
    const send = vi.fn<ActionSink['send']>().mockReturnValue(pending.promise);
    const state = initialState();

    const first = invokeAction(state, sinkWith(send), context, action);
    await invokeAction(state, sinkWith(send), context, {
      id: 'north',
      label: 'North',
      command: 'north',
    });

    expect(state.pendingActionId).toBe(action.id);
    expect(send).toHaveBeenCalledOnce();

    pending.resolve({ status: 'forwarded', detail: null });
    await first;
    expect(state.pendingActionId).toBeNull();
  });

  it('contains a rejected promise as unknown feedback without retrying', async () => {
    const send = vi.fn<ActionSink['send']>().mockRejectedValue(new Error('failed'));
    const state = initialState();

    await expect(invokeAction(state, sinkWith(send), context, action)).resolves.toBeUndefined();

    expect(send).toHaveBeenCalledOnce();
    expect(state.pendingActionId).toBeNull();
    expect(state.feedback).toBe('Delivery uncertain. Imp did not retry. Detail: Action request failed.');
  });

  it('contains a synchronous sink failure as unknown feedback without retrying', async () => {
    const send = vi.fn<ActionSink['send']>().mockImplementation(() => {
      throw new Error('failed');
    });
    const state = initialState();

    await expect(invokeAction(state, sinkWith(send), context, action)).resolves.toBeUndefined();

    expect(send).toHaveBeenCalledOnce();
    expect(state.pendingActionId).toBeNull();
    expect(state.feedback).toBe('Delivery uncertain. Imp did not retry. Detail: Action request failed.');
  });
});
