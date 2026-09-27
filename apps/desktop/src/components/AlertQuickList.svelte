<script lang="ts">
  import type { AlertDefinition } from '../lib/alerts/definitions.ts';

  let {
    definitions,
    saveError,
    ontoggle,
  }: {
    definitions: readonly AlertDefinition[];
    saveError: string | null;
    ontoggle: (id: string, enabled: boolean) => boolean;
  } = $props();
</script>

<div class="alert-quick-list">
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

<style>
  .alert-quick-list {
    display: flex;
    flex-wrap: wrap;
    align-content: flex-start;
    max-height: 12rem;
    min-height: 0;
    gap: var(--space-3);
    overflow-x: hidden;
    overflow-y: auto;
    padding-right: var(--space-1);
  }

  .alert-toggle-pill {
    display: inline-flex;
    flex: 0 1 auto;
    align-items: center;
    max-width: 100%;
    min-width: 3rem;
    gap: var(--space-3);
    padding: var(--space-3) var(--space-6);
    border: 1px solid var(--accent-border);
    border-radius: var(--radius);
    background: var(--accent-subtle);
    color: var(--text);
    cursor: pointer;
    font-size: var(--font-xs);
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
    margin-top: var(--space-4);
    color: var(--bad);
  }
</style>
