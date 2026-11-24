<script lang="ts">
  import Bell from '@lucide/svelte/icons/bell';
  import X from '@lucide/svelte/icons/x';

  let {
    controls,
    expanded,
    ontoggle,
  }: {
    controls: string;
    expanded: boolean;
    ontoggle: (keyboard: boolean) => void;
  } = $props();
</script>

<div class="alerts-menu">
  <button
    class="icon-btn panel-shortcut"
    type="button"
    aria-label={expanded ? 'Close alerts' : 'Alerts'}
    aria-expanded={expanded}
    aria-controls={controls}
    onclick={(event) => {
      event.stopPropagation();
      if (event.detail <= 1) ontoggle(event.detail === 0);
    }}
  >
    <Bell size={14} aria-hidden="true" />
    {#if expanded}<X class="shortcut-dismiss" size={10} strokeWidth={3} aria-hidden="true" />{/if}
  </button>
</div>
