<script lang="ts">
  import { onMount } from 'svelte';

  import {
    actionCommandError,
    actionLabelError,
    MAX_ACTION_DEFINITIONS,
    type ActionDefinition,
  } from '../lib/action/definitions.ts';

  let {
    definitions,
    onchange,
    saveError,
    onclose,
  }: {
    definitions: readonly ActionDefinition[];
    onchange: (definitions: ActionDefinition[]) => boolean;
    saveError: string | null;
    onclose: () => void;
  } = $props();

  let editingId = $state<string | null>(null);
  let label = $state('');
  let command = $state('');
  let labelError = $state<string | null>(null);
  let commandError = $state<string | null>(null);
  let limitError = $state<string | null>(null);
  let labelInput = $state<HTMLInputElement>();

  onMount(() => labelInput?.focus());

  $effect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onclose();
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  });

  function resetForm(): void {
    editingId = null;
    label = '';
    command = '';
    labelError = null;
    commandError = null;
    limitError = null;
    requestAnimationFrame(() => labelInput?.focus());
  }

  function editDefinition(definition: ActionDefinition): void {
    editingId = definition.id;
    label = definition.label;
    command = definition.command;
    labelError = null;
    commandError = null;
    limitError = null;
    requestAnimationFrame(() => labelInput?.focus());
  }

  function deleteDefinition(id: string): void {
    const saved = onchange(definitions.filter((definition) => definition.id !== id));
    if (!saved) return;
    limitError = null;
    if (editingId === id) resetForm();
  }

  function saveDefinition(): void {
    labelError = actionLabelError(label);
    commandError = actionCommandError(command);
    if (labelError !== null || commandError !== null) return;

    if (editingId === null && definitions.length >= MAX_ACTION_DEFINITIONS) {
      limitError = `You can save up to ${MAX_ACTION_DEFINITIONS} actions.`;
      return;
    }
    limitError = null;

    const definition: ActionDefinition = {
      id: editingId ?? crypto.randomUUID(),
      label: label.trim(),
      command,
    };
    const saved = onchange(
      editingId === null
        ? [...definitions, definition]
        : definitions.map((current) => (current.id === editingId ? definition : current)),
    );
    if (saved) resetForm();
  }
</script>

<div class="action-dialog" role="dialog" aria-modal="true" aria-labelledby="action-dialog-title">
  <header class="dialog-titlebar" data-tauri-drag-region>
    <div class="dialog-title" data-tauri-drag-region>
      <h2 id="action-dialog-title">Saved actions</h2>
      <p>Commands are stored locally and run only when you choose them.</p>
      {#if saveError !== null}
        <p class="persistence-error" role="alert">{saveError}</p>
      {/if}
    </div>
    <button class="dialog-close" type="button" aria-label="Close action manager" onclick={onclose}>×</button>
  </header>

  <div class="dialog-content">
    <section class="saved" aria-labelledby="saved-actions-title">
      <h3 id="saved-actions-title">Defined actions</h3>
      {#if definitions.length === 0}
        <p class="empty">No actions defined.</p>
      {:else}
        <div class="saved-list">
          {#each definitions as definition (definition.id)}
            <div class="saved-row">
              <span title={definition.label}>{definition.label}</span>
              <button type="button" onclick={() => editDefinition(definition)}>Edit</button>
              <button class="delete" type="button" onclick={() => deleteDefinition(definition.id)}
                >Delete</button
              >
            </div>
          {/each}
        </div>
      {/if}
    </section>

    <form
      onsubmit={(event) => {
        event.preventDefault();
        saveDefinition();
      }}
    >
      <h3>{editingId === null ? 'Define action' : 'Edit action'}</h3>
      <label>
        <span>Label</span>
        <input
          bind:this={labelInput}
          bind:value={label}
          type="text"
          aria-invalid={labelError !== null}
          aria-describedby={labelError === null ? undefined : 'action-label-error'}
        />
      </label>
      {#if labelError !== null}
        <span id="action-label-error" class="error">{labelError}</span>
      {/if}

      <label>
        <span>Command</span>
        <textarea
          bind:value={command}
          rows="2"
          spellcheck="false"
          aria-invalid={commandError !== null}
          aria-describedby={commandError === null ? undefined : 'action-command-error'}
        ></textarea>
      </label>
      {#if commandError !== null}
        <span id="action-command-error" class="error">{commandError}</span>
      {/if}
      {#if limitError !== null}
        <span class="error" role="alert">{limitError}</span>
      {/if}

      <div class="form-actions">
        <button type="submit">Save</button>
        <button type="button" onclick={onclose}>Cancel</button>
      </div>
    </form>
  </div>
</div>

<style>
  .action-dialog {
    display: grid;
    height: 100%;
    grid-template-rows: auto minmax(0, 1fr);
    overflow: hidden;
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius);
    background: var(--panel);
    color: var(--text);
  }

  header {
    display: flex;
    align-items: start;
    justify-content: space-between;
    gap: 1rem;
    padding: 0.75rem 0.8rem 0.6rem;
    border-bottom: 1px solid rgba(191, 215, 235, 0.13);
  }

  .dialog-title {
    min-width: 0;
  }

  h2,
  h3,
  p {
    margin: 0;
  }

  h2 {
    font-size: 0.88rem;
  }

  h3 {
    color: var(--muted);
    font-size: 0.61rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  header p {
    margin-top: 0.22rem;
    overflow-wrap: anywhere;
    color: var(--muted);
    font-size: 0.64rem;
  }

  header .persistence-error {
    color: #ff9ca5;
  }

  .dialog-close {
    flex: 0 0 auto;
    width: 1.5rem;
    height: 1.5rem;
    padding: 0 0 3px;
    border: 0;
    border-radius: 50%;
    background: transparent;
    color: var(--muted);
    cursor: pointer;
    font-size: 1.2rem;
  }

  .dialog-content {
    display: grid;
    min-width: 0;
    min-height: 0;
    grid-template-rows: minmax(5rem, 1fr) auto;
    gap: 0.7rem;
    padding: 0.7rem 0.8rem 0.8rem;
  }

  .saved {
    display: grid;
    min-height: 0;
    grid-template-rows: auto minmax(0, 1fr);
    gap: 0.35rem;
  }

  .saved,
  .saved-row,
  form,
  label {
    min-width: 0;
  }

  .saved-list {
    min-height: 0;
    max-height: 9rem;
    overflow-y: auto;
  }

  .saved-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto auto;
    align-items: center;
    gap: 0.3rem;
    padding: 0.28rem 0;
    border-bottom: 1px solid rgba(191, 215, 235, 0.1);
  }

  .saved-row span {
    overflow: hidden;
    font-size: 0.68rem;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .saved-row button,
  .form-actions button {
    padding: 0.24rem 0.48rem;
    border: 1px solid rgba(94, 157, 248, 0.34);
    border-radius: 0.25rem;
    background: rgba(94, 157, 248, 0.12);
    color: var(--text);
    cursor: pointer;
    font-size: 0.62rem;
  }

  .saved-row .delete {
    border-color: rgba(239, 91, 104, 0.34);
    background: rgba(239, 91, 104, 0.1);
  }

  .empty {
    align-self: center;
    color: var(--muted);
    font-size: 0.68rem;
    text-align: center;
  }

  form {
    display: grid;
    gap: 0.3rem;
    padding-top: 0.65rem;
    border-top: 1px solid rgba(191, 215, 235, 0.13);
  }

  label {
    display: grid;
    gap: 0.18rem;
    color: var(--muted);
    font-size: 0.64rem;
  }

  input,
  textarea {
    width: 100%;
    padding: 0.34rem 0.42rem;
    border: 1px solid rgba(191, 215, 235, 0.2);
    border-radius: 0.25rem;
    background: rgba(8, 13, 20, 0.8);
    color: var(--text);
    font: inherit;
    user-select: text;
  }

  textarea {
    resize: none;
  }

  input:focus-visible,
  textarea:focus-visible,
  button:focus-visible {
    border-color: rgba(94, 157, 248, 0.72);
    outline: none;
  }

  .error {
    overflow-wrap: anywhere;
    color: #ff9ca5;
    font-size: 0.6rem;
  }

  .form-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: end;
    gap: 0.35rem;
    padding-top: 0.15rem;
  }
</style>
