<script lang="ts">
  import Settings from '@lucide/svelte/icons/settings';
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

<div class="settings">
  <button
    class="settings-button icon-btn panel-shortcut"
    type="button"
    aria-label={expanded ? 'Close settings' : 'Settings'}
    aria-expanded={expanded}
    aria-controls={controls}
    aria-haspopup="dialog"
    onclick={(event) => {
      event.stopPropagation();
      if (event.detail <= 1) ontoggle(event.detail === 0);
    }}
  >
    <Settings size={14} aria-hidden="true" />
    {#if expanded}<X class="shortcut-dismiss" size={10} strokeWidth={3} aria-hidden="true" />{/if}
  </button>
</div>
