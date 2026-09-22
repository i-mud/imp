<script lang="ts">
  import Settings from '@lucide/svelte/icons/settings';

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

<div class="settings">
  <button
    bind:this={trigger}
    class="settings-button icon-btn"
    aria-label="Settings"
    aria-expanded={open}
    aria-haspopup="dialog"
    onclick={(event) => {
      event.stopPropagation();
      if (!open) onopen();
      open = !open;
    }}><Settings size={14} /></button
  >
</div>
