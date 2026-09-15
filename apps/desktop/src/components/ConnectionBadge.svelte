<script lang="ts">
  import type { FeedStatus } from '@tinyscry/protocol';
  import type { ConnectionPhase } from '../lib/source/types.ts';

  let { phase, feed }: { phase: ConnectionPhase; feed: FeedStatus | null } = $props();

  const label = $derived(
    phase === 'connected'
      ? (feed ?? 'connected').toUpperCase()
      : phase === 'reconnecting'
        ? 'RECONNECTING'
        : phase === 'connecting'
          ? 'CONNECTING'
          : phase === 'disconnected'
            ? 'DISCONNECTED'
            : 'IDLE',
  );
</script>

<span
  class:live={phase === 'connected' && feed === 'live'}
  class:warn={phase === 'reconnecting' || feed === 'stale'}
  class:down={phase === 'disconnected' || feed === 'down'}
  class="badge"
>
  {label}
</span>

<style>
  .badge {
    color: var(--muted);
    font-size: 0.61rem;
    font-weight: 750;
    letter-spacing: 0.08em;
  }

  .live {
    color: var(--good);
  }
  .warn {
    color: var(--warn);
  }
  .down {
    color: var(--bad);
  }
</style>
