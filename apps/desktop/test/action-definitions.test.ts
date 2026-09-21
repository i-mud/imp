import { describe, expect, it } from 'vitest';

import {
  actionCommandError,
  commitActionDefinitions,
  actionDefinitionsFromPersisted,
  actionLabelError,
  serializeActionDefinitions,
  saveActionDefinitions,
  MAX_ACTION_DEFINITIONS,
  type ActionDefinition,
} from '../src/lib/action/definitions.ts';

const first: ActionDefinition = { id: 'first', label: 'Look', command: 'look' };
const second: ActionDefinition = { id: 'second', label: 'Greet', command: 'say hello' };

function definition(index: number): ActionDefinition {
  return { id: `action-${index}`, label: `Action ${index}`, command: `look ${index}` };
}

const maximumDefinitions = Array.from({ length: MAX_ACTION_DEFINITIONS }, (_, index) => definition(index));

describe('action definition persistence', () => {
  it('returns an empty list for absent or malformed storage', () => {
    expect(actionDefinitionsFromPersisted(null)).toEqual([]);
    expect(actionDefinitionsFromPersisted('{')).toEqual([]);
    expect(actionDefinitionsFromPersisted(JSON.stringify({ actions: [] }))).toEqual([]);
  });

  it('round-trips valid definitions in saved order', () => {
    expect(actionDefinitionsFromPersisted(serializeActionDefinitions([first, second]))).toEqual([
      first,
      second,
    ]);
  });

  it('skips malformed entries without discarding valid neighbors', () => {
    const stored = JSON.stringify([
      first,
      null,
      { id: 3, label: 'Bad ID', command: 'look' },
      { id: 'bad-label', label: '\u001b', command: 'look' },
      { id: 'bad-command', label: 'Bad command', command: 'look\nnorth' },
      second,
    ]);

    expect(actionDefinitionsFromPersisted(stored)).toEqual([first, second]);
  });

  it('retains only the first definition for a duplicate ID', () => {
    expect(
      actionDefinitionsFromPersisted(
        JSON.stringify([first, { id: first.id, label: 'Other', command: 'north' }, second]),
      ),
    ).toEqual([first, second]);
  });

  it('trims labels loaded from storage', () => {
    expect(
      actionDefinitionsFromPersisted(JSON.stringify([{ id: 'trimmed', label: '  Rest  ', command: 'rest' }])),
    ).toEqual([{ id: 'trimmed', label: 'Rest', command: 'rest' }]);
  });

  it('accepts and reloads the maximum number of definitions', () => {
    let persisted = '';

    expect(
      saveActionDefinitions(maximumDefinitions, {
        setItem(_key: string, value: string): void {
          persisted = value;
        },
      }),
    ).toBe(true);
    expect(actionDefinitionsFromPersisted(persisted)).toEqual(maximumDefinitions);
  });

  it('rejects a sixty-fifth definition before persistence or commit', () => {
    let current = maximumDefinitions;
    let attempts = 0;

    const saved = commitActionDefinitions(
      [...maximumDefinitions, definition(MAX_ACTION_DEFINITIONS)],
      (committed) => {
        current = committed;
      },
      {
        setItem(): void {
          attempts += 1;
        },
      },
    );

    expect(saved).toBe(false);
    expect(attempts).toBe(0);
    expect(current).toBe(maximumDefinitions);
  });

  it('permits editing, deleting, and adding after deletion at the limit', () => {
    let current = maximumDefinitions;
    let attempts = 0;
    const storage = {
      setItem(): void {
        attempts += 1;
      },
    };
    const commit = (definitions: ActionDefinition[]) => {
      current = definitions;
    };

    const edited = maximumDefinitions.map((item, index) =>
      index === 0 ? { ...item, label: 'Edited' } : item,
    );
    expect(commitActionDefinitions(edited, commit, storage)).toBe(true);
    expect(current[0]?.label).toBe('Edited');

    expect(commitActionDefinitions(current.slice(0, -1), commit, storage)).toBe(true);
    expect(current).toHaveLength(MAX_ACTION_DEFINITIONS - 1);

    expect(commitActionDefinitions([...current, definition(MAX_ACTION_DEFINITIONS)], commit, storage)).toBe(
      true,
    );
    expect(current).toHaveLength(MAX_ACTION_DEFINITIONS);
    expect(attempts).toBe(3);
  });

  it('loads only the first sixty-four valid unique definitions in order', () => {
    const stored = Array.from({ length: MAX_ACTION_DEFINITIONS + 6 }, (_, index) => definition(index));

    expect(actionDefinitionsFromPersisted(JSON.stringify(stored))).toEqual(maximumDefinitions);
  });

  it('does not count malformed or duplicate entries toward the load limit', () => {
    const valid = Array.from({ length: MAX_ACTION_DEFINITIONS + 1 }, (_, index) => definition(index));
    const stored = [
      null,
      valid[0],
      { ...valid[0], label: 'Duplicate' },
      { id: 'bad-command', label: 'Bad', command: 'look\nnorth' },
      ...valid.slice(1),
    ];

    expect(actionDefinitionsFromPersisted(JSON.stringify(stored))).toEqual(
      valid.slice(0, MAX_ACTION_DEFINITIONS),
    );
  });

  it('retains the current definitions after one failed persistence attempt', () => {
    const previous = [first];
    const proposed = [first, second];
    let current = previous;
    let attempts = 0;

    const saved = commitActionDefinitions(
      proposed,
      (committed) => {
        current = committed;
      },
      {
        setItem(): void {
          attempts += 1;
          throw new Error('storage unavailable');
        },
      },
    );

    expect(saved).toBe(false);
    expect(attempts).toBe(1);
    expect(current).toBe(previous);
  });

  it('selects the proposed definitions after one successful persistence attempt', () => {
    const previous = [first];
    const proposed = [first, second];
    let current = previous;
    let persisted = '';
    let attempts = 0;

    const saved = commitActionDefinitions(
      proposed,
      (committed) => {
        current = committed;
      },
      {
        setItem(_key: string, value: string): void {
          attempts += 1;
          persisted = value;
        },
      },
    );

    expect(saved).toBe(true);
    expect(attempts).toBe(1);
    expect(current).toBe(proposed);
    expect(actionDefinitionsFromPersisted(persisted)).toEqual(proposed);
  });

  it('reports unavailable storage without committing definitions', () => {
    const previous = [first];
    let current = previous;

    const saved = commitActionDefinitions(
      [first, second],
      (committed) => {
        current = committed;
      },
      null,
    );

    expect(saved).toBe(false);
    expect(current).toBe(previous);
    expect(saveActionDefinitions([first], null)).toBe(false);
  });
});

describe('action definition validation', () => {
  it('rejects empty, control-containing, and overlong labels', () => {
    expect(actionLabelError('')).not.toBeNull();
    expect(actionLabelError('   ')).not.toBeNull();
    expect(actionLabelError('Bad\u001bLabel')).not.toBeNull();
    expect(actionLabelError('x'.repeat(65))).not.toBeNull();
    expect(actionLabelError('Résumé 北')).toBeNull();
  });

  it('preserves exact valid commands including surrounding spaces', () => {
    const command = '  say hello  ';
    const stored = serializeActionDefinitions([{ id: 'spaces', label: 'Greet', command }]);

    expect(actionDefinitionsFromPersisted(stored)[0]?.command).toBe(command);
    expect(actionCommandError(command)).toBeNull();
  });

  it('rejects commands outside the protocol command contract', () => {
    expect(actionCommandError('')).not.toBeNull();
    expect(actionCommandError('look\nnorth')).not.toBeNull();
    expect(actionCommandError('look\rnorth')).not.toBeNull();
    expect(actionCommandError('look\tnorth')).not.toBeNull();
    expect(actionCommandError('say café')).not.toBeNull();
    expect(actionCommandError('x'.repeat(513))).not.toBeNull();
    expect(actionCommandError('x'.repeat(512))).toBeNull();
  });
});
