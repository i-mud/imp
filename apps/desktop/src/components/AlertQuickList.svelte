<script lang="ts">
  import type { AlertDefinition } from '../lib/alerts/definitions.ts';

  let {
    definitions,
    saveError,
    ontoggle,
    onmanage,
  }: {
    definitions: readonly AlertDefinition[];
    saveError: string | null;
    ontoggle: (id: string, enabled: boolean) => boolean;
    onmanage: () => void;
  } = $props();
</script>

<div class="alert-quick-list contextual-list">
  <h2>Alerts</h2>
  <button class="manage-btn" type="button" aria-label="Manage alerts" onclick={() => onmanage()}
    >Manage</button
  >
  <div class="alert-toggle-list contextual-items">
    {#if definitions.length === 0}
      <span class="empty">No alerts defined.</span>
    {:else}
      {#each definitions as definition (definition.id)}
        <label class="alert-toggle-pill">
          <input
            type="checkbox"
            checked={definition.enabled}
            onchange={(event) => {
              if (!ontoggle(definition.id, event.currentTarget.checked)) {
                event.currentTarget.checked = definition.enabled;
              }
            }}
          />
          <span>{definition.label}</span>
        </label>
      {/each}
    {/if}
  </div>
  {#if saveError !== null}
    <div class="save-error" role="alert">{saveError}</div>
  {/if}
</div>

<style>
  .alert-toggle-pill {
    display: inline-flex;
    flex: 0 1 auto;
    align-items: center;
    max-width: 100%;
    min-width: 3rem;
    gap: var(--space-3);
    border: 1px solid var(--accent-border);
    background: var(--accent-subtle);
  }

  .alert-toggle-pill:hover,
  .alert-toggle-pill:focus-within {
    border-color: var(--accent-focus);
    background: var(--accent-hover);
  }

  .alert-toggle-pill input {
    flex: 0 0 auto;
    width: auto;
    margin: 0;
    accent-color: var(--accent);
  }

  .alert-toggle-pill span {
    min-width: 0;
    overflow-wrap: anywhere;
  }

  .empty,
  .save-error {
    color: var(--muted);
    font-size: var(--font-xs);
  }

  .save-error {
    grid-column: 1 / -1;
    color: var(--bad-text);
  }
</style>
