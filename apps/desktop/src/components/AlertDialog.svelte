<script lang="ts">
  import { onMount } from 'svelte';
  import X from '@lucide/svelte/icons/x';

  import {
    alertLabelError,
    alertThresholdError,
    MAX_ALERT_DEFINITIONS,
    textPatternError,
    type AlertDefinition,
    type AlertVital,
    type TextMatchMode,
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
  let kind = $state<AlertDefinition['kind']>('vital');
  let label = $state('');
  let vital = $state<AlertVital>('health');
  let thresholdPercent = $state(25);
  let matchMode = $state<TextMatchMode>('contains');
  let pattern = $state('');
  let caseSensitive = $state(false);
  let soundEnabled = $state(true);
  let notificationEnabled = $state(true);

  let labelError = $state<string | null>(null);
  let thresholdError = $state<string | null>(null);
  let patternError = $state<string | null>(null);
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
    kind = 'vital';
    label = '';
    vital = 'health';
    thresholdPercent = 25;
    matchMode = 'contains';
    pattern = '';
    caseSensitive = false;
    soundEnabled = true;
    notificationEnabled = true;
    labelError = null;
    thresholdError = null;
    patternError = null;
    limitError = null;
    requestAnimationFrame(() => labelInput?.focus());
  }

  function setKind(nextKind: AlertDefinition['kind']): void {
    kind = nextKind;
    thresholdError = null;
    patternError = null;
  }

  function editDefinition(definition: AlertDefinition): void {
    editingId = definition.id;
    kind = definition.kind;
    label = definition.label;
    soundEnabled = definition.soundEnabled;
    notificationEnabled = definition.notificationEnabled;

    if (definition.kind === 'vital') {
      vital = definition.vital;
      thresholdPercent = definition.thresholdPercent;
      matchMode = 'contains';
      pattern = '';
      caseSensitive = false;
    } else {
      vital = 'health';
      thresholdPercent = 25;
      matchMode = definition.matchMode;
      pattern = definition.pattern;
      caseSensitive = definition.caseSensitive;
    }

    labelError = null;
    thresholdError = null;
    patternError = null;
    limitError = null;
    requestAnimationFrame(() => labelInput?.focus());
  }

  function deleteDefinition(id: string): void {
    const saved = onchange(definitions.filter((definition) => definition.id !== id));
    if (!saved) return;

    limitError = null;
    if (editingId === id) resetForm();
  }

  function setDefinitionEnabled(id: string, enabled: boolean): boolean {
    return onchange(
      definitions.map((definition) => (definition.id === id ? { ...definition, enabled } : definition)),
    );
  }

  function saveDefinition(): void {
    labelError = alertLabelError(label);
    thresholdError = kind === 'vital' ? alertThresholdError(thresholdPercent) : null;
    patternError = kind === 'text' ? textPatternError(pattern) : null;

    if (labelError !== null || thresholdError !== null || patternError !== null) return;

    if (editingId === null && definitions.length >= MAX_ALERT_DEFINITIONS) {
      limitError = `You can save up to ${MAX_ALERT_DEFINITIONS} alerts.`;
      return;
    }

    limitError = null;

    const existing =
      editingId === null ? null : definitions.find((definition) => definition.id === editingId);

    const base = {
      id: editingId ?? crypto.randomUUID(),
      label: label.trim(),
      enabled: existing?.enabled ?? true,
      soundEnabled,
      notificationEnabled,
    };

    const definition: AlertDefinition =
      kind === 'vital'
        ? {
            ...base,
            kind: 'vital',
            vital,
            thresholdPercent,
          }
        : {
            ...base,
            kind: 'text',
            matchMode,
            pattern,
            caseSensitive,
          };

    const saved = onchange(
      editingId === null
        ? [...definitions, definition]
        : definitions.map((current) => (current.id === editingId ? definition : current)),
    );

    if (saved) resetForm();
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
            <div class="saved-row alert-saved-row">
              <input
                class="row-enabled"
                type="checkbox"
                checked={definition.enabled}
                aria-label={'Enable ' + definition.label}
                onchange={(event) => {
                  if (!setDefinitionEnabled(definition.id, event.currentTarget.checked)) {
                    event.currentTarget.checked = definition.enabled;
                  }
                }}
              />

              <span class="saved-label" title={definition.label}>{definition.label}</span>

              <button class="manager-btn secondary" type="button" onclick={() => editDefinition(definition)}>
                Edit
              </button>

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
      <h3>{editingId === null ? 'Define alert' : 'Edit alert'}</h3>

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

      <div class="selector-row">
        <span>Trigger</span>
        <div class="segmented" role="radiogroup" aria-label="Alert trigger">
          <label>
            <input
              type="radio"
              name="alert-trigger"
              checked={kind === 'vital'}
              onchange={() => setKind('vital')}
            />
            <span>Vitals</span>
          </label>
          <label>
            <input
              type="radio"
              name="alert-trigger"
              checked={kind === 'text'}
              onchange={() => setKind('text')}
            />
            <span>Text</span>
          </label>
        </div>
      </div>

      {#if kind === 'vital'}
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
      {:else}
        <div class="selector-row">
          <span>Match</span>
          <div class="segmented" role="radiogroup" aria-label="Text match mode">
            <label>
              <input
                type="radio"
                name="text-match-mode"
                checked={matchMode === 'contains'}
                onchange={() => {
                  matchMode = 'contains';
                }}
              />
              <span>Contains</span>
            </label>
            <label>
              <input
                type="radio"
                name="text-match-mode"
                checked={matchMode === 'wildcard'}
                onchange={() => {
                  matchMode = 'wildcard';
                }}
              />
              <span>Wildcard</span>
            </label>
          </div>
        </div>

        <label>
          <span>Text to match</span>
          <input
            bind:value={pattern}
            type="text"
            aria-invalid={patternError !== null}
            aria-describedby={patternError === null ? undefined : 'alert-pattern-error'}
          />
        </label>

        {#if patternError !== null}
          <span id="alert-pattern-error" class="error">{patternError}</span>
        {/if}
      {/if}

      <div class="toggles">
        <label class="toggle">
          <input type="checkbox" bind:checked={soundEnabled} />
          <span>Sound</span>
        </label>

        <label class="toggle">
          <input type="checkbox" bind:checked={notificationEnabled} />
          <span>Notification</span>
        </label>

        {#if kind === 'text'}
          <label class="toggle">
            <input type="checkbox" bind:checked={caseSensitive} />
            <span>Case sensitive</span>
          </label>
        {/if}
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

  .alert-dialog .saved-row.alert-saved-row {
    grid-template-columns: auto minmax(0, 1fr) auto auto;
  }

  .saved-label {
    min-width: 0;
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

  .selector-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-4);
    color: var(--muted);
    font-size: var(--font-xs);
  }

  .segmented {
    display: flex;
    flex: 0 0 auto;
    overflow: hidden;
    border: 1px solid var(--divider);
    border-radius: var(--radius);
  }

  .segmented label {
    position: relative;
    display: block;
    cursor: pointer;
  }

  .segmented input {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    margin: 0;
    padding: 0;
    opacity: 0;
    cursor: inherit;
  }

  .segmented span {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: var(--space-2) var(--space-3);
    color: var(--muted);
    font-size: var(--font-2xs);
    line-height: 1;
  }

  .segmented label + label {
    border-left: 1px solid var(--divider);
  }

  .segmented label:hover span {
    background: var(--accent-hover);
  }

  .segmented input:checked + span {
    background: var(--accent-subtle);
    color: var(--text);
  }

  .segmented input:focus-visible + span {
    outline: 2px solid var(--accent-focus);
    outline-offset: -2px;
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
    flex-wrap: nowrap;
    gap: var(--space-4);
  }

  .toggle {
    display: flex;
    grid-template-columns: none;
    align-items: center;
    gap: var(--space-2);
    white-space: nowrap;
  }

  .toggle input,
  .row-enabled {
    width: auto;
    margin: 0;
    padding: 0;
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
