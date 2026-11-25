<script lang="ts">
  import type { Snippet } from 'svelte';

  import type { ActionDefinition } from '../lib/action/definitions.ts';

  let {
    definitions,
    pendingActionId,
    feedback,
    oninvoke,
    onmanage,
  }: {
    definitions: readonly ActionDefinition[];
    pendingActionId: string | null;
    feedback: Snippet;
    oninvoke: (definition: ActionDefinition) => void;
    onmanage: () => void;
  } = $props();
</script>

<section class="action-bar contextual-list" aria-label="Saved actions">
  <div class="contextual-heading">
    <h2>Actions</h2>
    <button class="manage-btn" type="button" aria-label="Manage actions" onclick={() => onmanage()}
      >Manage</button
    >
  </div>
  <div class="action-buttons contextual-items">
    {#each definitions as definition (definition.id)}
      <button
        class="action-btn"
        type="button"
        title={definition.label}
        aria-label={definition.label}
        disabled={pendingActionId !== null}
        onclick={() => oninvoke(definition)}
      >
        <span>{definition.label}</span>
      </button>
    {/each}
  </div>
  {@render feedback()}
</section>

<style>
  .action-bar {
    padding: var(--section-pad);
    border-top: 1px solid var(--divider);
  }

  .action-buttons {
    /* Three single-line buttons, including padding, borders and two row gaps. */
    font-size: var(--font-xs);
    line-height: 1.25;
    max-height: round(up, 3lh + 6 * var(--space-1) + 6px + 2 * var(--space-3), 1px);
    scrollbar-width: thin;
  }

  .action-buttons button {
    line-height: inherit;
    max-width: min(9rem, 100%);
    min-width: 3rem;
    overflow: hidden;
  }

  .action-buttons span {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>
