<script lang="ts">
  import type { StatusIndicatorState } from '../lib/hud/presentation.ts';

  let { status, label }: { status: StatusIndicatorState; label: string } = $props();
</script>

<span
  class:up={status === 'up'}
  class:stale={status === 'stale'}
  class="indicator"
  role="img"
  aria-label={label}
  title={label}
></span>

<style>
  .indicator {
    display: block;
    width: 0.55rem;
    height: 0.55rem;
    flex: 0 0 auto;
    border-radius: 50%;
    background: var(--bad);
    box-shadow: 0 0 0 2px rgba(239, 91, 104, 0.12);
  }

  .up {
    background: var(--good);
    box-shadow: 0 0 0 2px rgba(88, 201, 148, 0.12);
  }

  .stale {
    background: var(--warn);
    box-shadow: 0 0 0 2px rgba(229, 171, 84, 0.12);
  }
</style>
