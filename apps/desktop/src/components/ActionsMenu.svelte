<script lang="ts">
  import Swords from '@lucide/svelte/icons/swords';
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

<div class="actions-menu">
  <button
    class="icon-btn panel-shortcut"
    type="button"
    aria-label={expanded ? 'Close actions' : 'Actions'}
    aria-expanded={expanded}
    aria-controls={controls}
    onclick={(event) => {
      event.stopPropagation();
      if (event.detail <= 1) ontoggle(event.detail === 0);
    }}
  >
    <Swords size={14} aria-hidden="true" />
    {#if expanded}<X class="shortcut-dismiss" size={10} strokeWidth={3} aria-hidden="true" />{/if}
  </button>
</div>
