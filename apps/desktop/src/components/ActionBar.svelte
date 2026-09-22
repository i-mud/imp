<script lang="ts">
  import type { ActionDefinition } from '../lib/action/definitions.ts';

  let {
    definitions,
    pendingActionId,
    feedback,
    oninvoke,
  }: {
    definitions: readonly ActionDefinition[];
    pendingActionId: string | null;
    feedback: string | null;
    oninvoke: (definition: ActionDefinition) => void;
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
  {#if feedback}
    <div class="action-feedback" aria-live="polite">{feedback}</div>
  {/if}
</section>

<style>
  .action-bar {
    display: grid;
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

  button {
    flex: 0 0 auto;
    max-width: 9rem;
    min-width: 3rem;
    overflow: hidden;
  }

  button span {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .action-feedback {
    min-width: 0;
    overflow: hidden;
    color: var(--muted);
    font-size: var(--font-2xs);
    line-height: 1;
  }
</style>
