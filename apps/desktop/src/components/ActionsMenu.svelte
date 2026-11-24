<script lang="ts">
  import Swords from '@lucide/svelte/icons/swords';

  let {
    onopen,
    open = $bindable(false),
    trigger = $bindable<HTMLButtonElement | undefined>(),
  }: {
    onopen: (keyboard: boolean) => void;
    open?: boolean;
    trigger?: HTMLButtonElement | undefined;
  } = $props();
</script>

<div class="actions-menu">
  <button
    bind:this={trigger}
    class="icon-btn"
    type="button"
    aria-label="Actions"
    aria-expanded={open}
    aria-controls="compact-actions-panel"
    onclick={(event) => {
      event.stopPropagation();
      if (!open) onopen(event.detail === 0);
      open = !open;
    }}><Swords size={14} /></button
  >
</div>
