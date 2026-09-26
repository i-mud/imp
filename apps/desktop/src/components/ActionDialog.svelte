<script lang="ts">
  import { onMount } from 'svelte';

  import {
    actionCommandError,
    actionLabelError,
    MAX_ACTION_DEFINITIONS,
    type ActionDefinition,
  } from '../lib/action/definitions.ts';

  import X from '@lucide/svelte/icons/x';

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

<div
  class="action-dialog manager-dialog"
  role="dialog"
  aria-modal="true"
  aria-labelledby="action-dialog-title"
>
  <header class="dialog-titlebar" data-tauri-drag-region>
    <div class="dialog-title" data-tauri-drag-region>
      <h2 id="action-dialog-title">Saved actions</h2>
      <p>Commands are stored locally and run only when you choose them.</p>
      {#if saveError !== null}
        <p class="persistence-error" role="alert">{saveError}</p>
      {/if}
    </div>
    <button class="dialog-close icon-btn" type="button" aria-label="Close action manager" onclick={onclose}
      ><X size={16} /></button
    >
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
              <button class="manager-btn secondary" type="button" onclick={() => editDefinition(definition)}
                >Edit</button
              >
              <button class="manager-btn danger" type="button" onclick={() => deleteDefinition(definition.id)}
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
        <button class="manager-btn primary" type="submit">Save</button>
        <button class="manager-btn secondary" type="button" onclick={onclose}>Close</button>
      </div>
    </form>
  </div>
</div>

<style>
  .action-dialog {
    display: grid;
    height: max-content;
    grid-template-rows: auto auto;
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
    gap: var(--space-8);
    padding: var(--section-pad);
    border-bottom: 1px solid var(--divider);
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
    font-size: var(--font-md);
  }

  h3 {
    color: var(--muted);
    font-size: var(--font-2xs);
    letter-spacing: var(--tracking-wide);
    text-transform: uppercase;
  }

  header p {
    margin-top: 0.22rem;
    overflow-wrap: anywhere;
    color: var(--muted);
    font-size: var(--font-xs);
  }

  header .persistence-error {
    color: var(--bad);
  }

  .dialog-close {
    flex: 0 0 auto;
  }

  .saved,
  .saved-row,
  form,
  label {
    min-width: 0;
  }

  .saved-row span {
    overflow: hidden;
    font-size: var(--font-xs);
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .empty {
    align-self: center;
    color: var(--muted);
    font-size: var(--font-xs);
    text-align: center;
  }

  form {
    display: grid;
    gap: var(--space-3);
    padding-top: var(--space-7);
    border-top: 1px solid var(--divider);
  }

  label {
    display: grid;
    gap: var(--space-2);
    color: var(--muted);
    font-size: var(--font-xs);
  }

  input,
  textarea {
    width: 100%;
    padding: var(--space-3) var(--space-4);
    border: 1px solid var(--input-border);
    border-radius: var(--radius);
    background: var(--input-bg);
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
    border-color: var(--accent-focus);
    outline: none;
  }

  .error {
    overflow-wrap: anywhere;
    color: var(--bad);
    font-size: var(--font-2xs);
  }

  .form-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: end;
    gap: var(--space-4);
    padding-top: var(--space-1);
  }
</style>
