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
  <div class="action-feedback" aria-live="polite">{feedback ?? '\u00a0'}</div>
</section>

<style>
  .action-bar {
    display: grid;
    min-width: 0;
    height: var(--action-strip-height);
    gap: 0.12rem;
    padding: 0.25rem 0.65rem 0.18rem;
    border-top: 1px solid rgba(191, 215, 235, 0.13);
  }

  .action-buttons {
    display: flex;
    min-width: 0;
    gap: 0.35rem;
    overflow-x: auto;
    scrollbar-width: thin;
  }

  button {
    flex: 0 0 auto;
    max-width: 9rem;
    min-width: 3rem;
    padding: 0.2rem 0.5rem;
    overflow: hidden;
    border: 1px solid rgba(94, 157, 248, 0.34);
    border-radius: 0.3rem;
    background: rgba(94, 157, 248, 0.12);
    color: var(--text);
    cursor: pointer;
    font-size: 0.66rem;
  }

  button:hover,
  button:focus-visible {
    border-color: rgba(94, 157, 248, 0.72);
    background: rgba(94, 157, 248, 0.2);
    outline: none;
  }

  button:disabled {
    cursor: default;
    opacity: 0.55;
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
    font-size: 0.58rem;
    line-height: 1.15;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>
