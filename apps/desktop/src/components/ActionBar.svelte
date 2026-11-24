<script lang="ts">
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
    feedback: string | null;
    oninvoke: (definition: ActionDefinition) => void;
    onmanage: () => void;
  } = $props();
</script>

<section class="action-bar" aria-label="Saved actions">
  <div class="action-buttons">
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
  <button class="manage-btn" type="button" aria-label="Manage actions" onclick={() => onmanage()}
    >Manage</button
  >
  {#if feedback}
    <div class="action-feedback" aria-live="polite">{feedback}</div>
  {/if}
</section>

<style>
  .action-bar {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    min-width: 0;
    gap: var(--space-6);
    padding: var(--section-pad);
    border-top: 1px solid var(--divider);
  }

  .action-buttons {
    display: flex;
    /* The strip has a min-height; without this the buttons stretch to fill it
       and stop matching the compact panel's buttons. */
    align-items: center;
    min-width: 0;
    gap: var(--space-3);
    overflow-x: auto;
    scrollbar-width: thin;
  }

  .action-buttons button {
    flex: 0 0 auto;
    max-width: 9rem;
    min-width: 3rem;
    overflow: hidden;
  }

  .action-buttons span {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .action-feedback {
    grid-column: 1 / -1;
  }
</style>
