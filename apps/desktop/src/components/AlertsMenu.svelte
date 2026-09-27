<script lang="ts">
  import Bell from '@lucide/svelte/icons/bell';

  let {
    controls,
    onopen,
    open = $bindable(false),
    trigger = $bindable<HTMLButtonElement | undefined>(),
  }: {
    controls: string;
    onopen: () => void;
    open?: boolean;
    trigger?: HTMLButtonElement | undefined;
  } = $props();
</script>

<div class="alerts-menu">
  <button
    bind:this={trigger}
    class="icon-btn"
    type="button"
    aria-label="Alerts"
    aria-expanded={open}
    aria-controls={controls}
    onclick={(event) => {
      event.stopPropagation();
      if (!open) onopen();
      open = !open;
    }}><Bell size={14} /></button
  >
</div>
