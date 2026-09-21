<script lang="ts">
  let {
    onopen,
    open = $bindable(false),
    trigger = $bindable<HTMLButtonElement | undefined>(),
  }: {
    onopen: () => void;
    open?: boolean;
    trigger?: HTMLButtonElement | undefined;
  } = $props();
</script>

<div class="actions-menu">
  <button
    bind:this={trigger}
    class="actions-button"
    type="button"
    aria-label="Actions"
    aria-expanded={open}
    aria-controls="compact-actions-panel"
    onclick={(event) => {
      event.stopPropagation();
      if (!open) onopen();
      open = !open;
    }}>⚡</button
  >
</div>

<style>
  .actions-button {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 1.35rem;
    height: 1.35rem;
    padding: 0;
    border: 0;
    border-radius: 50%;
    background: transparent;
    color: var(--muted);
    cursor: pointer;
    font-size: 0.78rem;
    line-height: 1;
  }

  .actions-button:hover,
  .actions-button[aria-expanded='true'] {
    background: rgba(91, 180, 239, 0.18);
    color: var(--text);
  }
</style>
