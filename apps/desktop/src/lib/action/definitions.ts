import { isSafeText, isValidActionCommand } from '@tinyscry/protocol';

export interface ActionDefinition {
  readonly id: string;
  readonly label: string;
  readonly command: string;
}

const MAX_ACTION_LABEL_CHARACTERS = 64;
export const MAX_ACTION_DEFINITIONS = 64;

const ACTION_DEFINITIONS_STORAGE_KEY = 'tinyscry.actions';
const MAX_ACTION_ID_CHARACTERS = 128;

function isValidActionId(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    value.length > 0 &&
    value.length <= MAX_ACTION_ID_CHARACTERS &&
    isSafeText(value)
  );
}

export function actionLabelError(value: string): string | null {
  const label = value.trim();
  if (label.length === 0) return 'Enter a label.';
  if (!isSafeText(label)) return 'Label cannot contain control or malformed characters.';
  if ([...label].length > MAX_ACTION_LABEL_CHARACTERS) {
    return `Label must be at most ${MAX_ACTION_LABEL_CHARACTERS} characters.`;
  }
  return null;
}

export function actionCommandError(value: string): string | null {
  return isValidActionCommand(value)
    ? null
    : 'Command must be 1–512 printable ASCII characters with no line breaks.';
}

export function actionDefinitionsFromPersisted(value: string | null): ActionDefinition[] {
  if (value === null) return [];

  try {
    const parsed: unknown = JSON.parse(value);
    if (!Array.isArray(parsed)) return [];

    const definitions: ActionDefinition[] = [];
    const ids = new Set<string>();
    for (const entry of parsed) {
      if (typeof entry !== 'object' || entry === null || Array.isArray(entry)) continue;
      if (!('id' in entry) || !isValidActionId(entry.id) || ids.has(entry.id)) continue;
      if (
        !('label' in entry) ||
        typeof entry.label !== 'string' ||
        !('command' in entry) ||
        typeof entry.command !== 'string'
      ) {
        continue;
      }

      const label = entry.label.trim();
      if (actionLabelError(label) !== null || actionCommandError(entry.command) !== null) continue;

      ids.add(entry.id);
      definitions.push({ id: entry.id, label, command: entry.command });
      if (definitions.length === MAX_ACTION_DEFINITIONS) break;
    }
    return definitions;
  } catch {
    return [];
  }
}

export function serializeActionDefinitions(definitions: readonly ActionDefinition[]): string {
  return JSON.stringify(definitions);
}

export function loadActionDefinitions(): ActionDefinition[] {
  try {
    return actionDefinitionsFromPersisted(
      globalThis.localStorage?.getItem(ACTION_DEFINITIONS_STORAGE_KEY) ?? null,
    );
  } catch {
    return [];
  }
}

export function saveActionDefinitions(
  definitions: readonly ActionDefinition[],
  storage?: Pick<Storage, 'setItem'> | null,
): boolean {
  if (definitions.length > MAX_ACTION_DEFINITIONS) return false;
  try {
    const target = storage === undefined ? globalThis.localStorage : storage;
    if (target === null || target === undefined) return false;
    target.setItem(ACTION_DEFINITIONS_STORAGE_KEY, serializeActionDefinitions(definitions));
    return true;
  } catch {
    return false;
  }
}

export function commitActionDefinitions(
  definitions: ActionDefinition[],
  commit: (definitions: ActionDefinition[]) => void,
  storage?: Pick<Storage, 'setItem'> | null,
): boolean {
  if (!saveActionDefinitions(definitions, storage)) return false;
  commit(definitions);
  return true;
}
