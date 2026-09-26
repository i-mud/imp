<script lang="ts">
  import { onMount } from 'svelte';
  import X from '@lucide/svelte/icons/x';

  import {
    alertLabelError,
    alertThresholdError,
    MAX_ALERT_DEFINITIONS,
    type AlertDefinition,
    type AlertVital,
    type VitalAlertDefinition,
  } from '../lib/alerts/definitions.ts';

  let {
    definitions,
    onchange,
    saveError,
    onclose,
  }: {
    definitions: readonly AlertDefinition[];
    onchange: (definitions: AlertDefinition[]) => boolean;
    saveError: string | null;
    onclose: () => void;
  } = $props();

  let editingId = $state<string | null>(null);
  let label = $state('');
  let vital = $state<AlertVital>('health');
  let thresholdPercent = $state(25);
  let enabled = $state(true);
  let soundEnabled = $state(true);
  let notificationEnabled = $state(true);

  let labelError = $state<string | null>(null);
  let thresholdError = $state<string | null>(null);
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
    vital = 'health';
    thresholdPercent = 25;
    enabled = true;
    soundEnabled = true;
    notificationEnabled = true;
    labelError = null;
    thresholdError = null;
    limitError = null;
    requestAnimationFrame(() => labelInput?.focus());
  }

  function editDefinition(definition: VitalAlertDefinition): void {
    editingId = definition.id;
    label = definition.label;
    vital = definition.vital;
    thresholdPercent = definition.thresholdPercent;
    enabled = definition.enabled;
    soundEnabled = definition.soundEnabled;
    notificationEnabled = definition.notificationEnabled;
    labelError = null;
    thresholdError = null;
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
    labelError = alertLabelError(label);
    thresholdError = alertThresholdError(thresholdPercent);

    if (labelError !== null || thresholdError !== null) return;

    if (editingId === null && definitions.length >= MAX_ALERT_DEFINITIONS) {
      limitError = `You can save up to ${MAX_ALERT_DEFINITIONS} alerts.`;
      return;
    }

    limitError = null;

    const definition: VitalAlertDefinition = {
      id: editingId ?? crypto.randomUUID(),
      kind: 'vital',
      label: label.trim(),
      enabled,
      vital,
      thresholdPercent,
      soundEnabled,
      notificationEnabled,
    };

    const saved = onchange(
      editingId === null
        ? [...definitions, definition]
        : definitions.map((current) => (current.id === editingId ? definition : current)),
    );

    if (saved) resetForm();
  }

  function vitalLabel(value: AlertVital): string {
    switch (value) {
      case 'health':
        return 'Health';
      case 'mana':
        return 'Mana';
      case 'moves':
        return 'Moves';
      case 'target-health':
        return 'Target health';
    }
  }
</script>

<div class="alert-dialog manager-dialog" role="dialog" aria-modal="true" aria-labelledby="alert-dialog-title">
  <header class="dialog-titlebar" data-tauri-drag-region>
    <div class="dialog-title" data-tauri-drag-region>
      <h2 id="alert-dialog-title">Saved alerts</h2>
      <p>Alerts are evaluated locally and never send commands to the MUD.</p>
      {#if saveError !== null}
        <p class="persistence-error" role="alert">{saveError}</p>
      {/if}
    </div>

    <button class="dialog-close icon-btn" type="button" aria-label="Close alert manager" onclick={onclose}>
      <X size={16} />
    </button>
  </header>

  <div class="dialog-content">
    <section class="saved" aria-labelledby="saved-alerts-title">
      <h3 id="saved-alerts-title">Defined alerts</h3>

      {#if definitions.length === 0}
        <p class="empty">No alerts defined.</p>
      {:else}
        <div class="saved-list">
          {#each definitions as definition (definition.id)}
            <div class="saved-row">
              <div class="saved-description">
                <span title={definition.label}>{definition.label}</span>
                <small>
                  {#if definition.kind === 'vital'}
                    {vitalLabel(definition.vital)} ≤ {definition.thresholdPercent}%
                  {:else}
                    Text trigger — pending text transport
                  {/if}
                </small>
              </div>

              {#if definition.kind === 'vital'}
                <button
                  class="manager-btn secondary"
                  type="button"
                  onclick={() => editDefinition(definition)}
                >
                  Edit
                </button>
              {:else}
                <span class="pending">Pending</span>
              {/if}

              <button
                class="manager-btn danger"
                type="button"
                onclick={() => deleteDefinition(definition.id)}
              >
                Delete
              </button>
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
      <h3>{editingId === null ? 'Define threshold alert' : 'Edit threshold alert'}</h3>

      <label>
        <span>Label</span>
        <input
          bind:this={labelInput}
          bind:value={label}
          type="text"
          aria-invalid={labelError !== null}
          aria-describedby={labelError === null ? undefined : 'alert-label-error'}
        />
      </label>

      {#if labelError !== null}
        <span id="alert-label-error" class="error">{labelError}</span>
      {/if}

      <div class="condition-row">
        <label>
          <span>Metric</span>
          <select bind:value={vital}>
            <option value="health">Health</option>
            <option value="mana">Mana</option>
            <option value="moves">Moves</option>
            <option value="target-health">Target health</option>
          </select>
        </label>

        <label>
          <span>Threshold</span>
          <div class="threshold-input">
            <input
              type="number"
              min="1"
              max="100"
              step="1"
              value={thresholdPercent}
              aria-invalid={thresholdError !== null}
              aria-describedby={thresholdError === null ? undefined : 'alert-threshold-error'}
              oninput={(event) => {
                thresholdPercent = event.currentTarget.valueAsNumber;
              }}
            />
            <span aria-hidden="true">%</span>
          </div>
        </label>
      </div>

      {#if thresholdError !== null}
        <span id="alert-threshold-error" class="error">{thresholdError}</span>
      {/if}

      <div class="toggles">
        <label class="toggle">
          <input type="checkbox" bind:checked={enabled} />
          <span>Enabled</span>
        </label>

        <label class="toggle">
          <input type="checkbox" bind:checked={soundEnabled} />
          <span>Sound</span>
        </label>

        <label class="toggle">
          <input type="checkbox" bind:checked={notificationEnabled} />
          <span>Notification</span>
        </label>
      </div>

      {#if limitError !== null}
        <span class="error" role="alert">{limitError}</span>
      {/if}

      <div class="form-actions">
        <button class="manager-btn primary" type="submit">
          {editingId === null ? 'Add alert' : 'Save'}
        </button>

        {#if editingId !== null}
          <button class="manager-btn secondary" type="button" onclick={resetForm}>Cancel edit</button>
        {/if}

        <button class="manager-btn secondary" type="button" onclick={onclose}>Close</button>
      </div>
    </form>
  </div>
</div>

<style>
  .alert-dialog {
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

  .dialog-title,
  .saved,
  .saved-row,
  .saved-description,
  form,
  label {
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

  .saved-description {
    display: flex;
    align-items: center;
    min-width: 0;
    gap: var(--space-3);
  }

  .saved-description span {
    min-width: 0;
    overflow: hidden;
    font-size: var(--font-xs);
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .saved-description small,
  .pending {
    flex: 0 0 auto;
    color: var(--muted);
    font-size: var(--font-2xs);
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
  select {
    width: 100%;
    padding: var(--space-3) var(--space-4);
    border: 1px solid var(--input-border);
    border-radius: var(--radius);
    background: var(--input-bg);
    color: var(--text);
    font: inherit;
    user-select: text;
  }

  input:focus-visible,
  select:focus-visible,
  button:focus-visible {
    border-color: var(--accent-focus);
    outline: none;
  }

  .condition-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: var(--space-4);
  }

  .threshold-input {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    gap: var(--space-2);
  }

  .toggles {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-4);
  }

  .toggle {
    display: flex;
    grid-template-columns: none;
    align-items: center;
    gap: var(--space-2);
  }

  .toggle input {
    width: auto;
    margin: 0;
    accent-color: var(--accent);
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
