<script lang="ts">
  import { vitalFraction, type Vital } from '@tinyscry/protocol';

  let { label, vital, color }: { label: string; vital: Vital | null; color: string } = $props();
</script>

<div class="vital" style={`--vital-color: ${color}; --fraction: ${vitalFraction(vital)}`}>
  <div class="value">
    <span class="label">{label}</span>
    {#if vital === null}
      <span class="unknown">—</span>
    {:else}
      <span>{vital.current}/{vital.max}</span>
    {/if}
  </div>
  <div
    class="track"
    aria-label={`${label}: ${vital === null ? 'unknown' : `${vital.current} of ${vital.max}`}`}
  >
    <div class="fill"></div>
  </div>
</div>

<style>
  .vital {
    display: grid;
    min-width: 0;
    gap: 0.2rem;
  }

  .value {
    display: flex;
    align-items: baseline;
    gap: 0.18rem;
    min-width: 0;
    color: var(--text);
    font-size: 0.61rem;
    line-height: 0.61rem;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }

  .label {
    color: var(--muted);
    font-size: 0.58rem;
    font-weight: 750;
    letter-spacing: 0.05em;
  }

  .unknown {
    color: var(--dimmed);
  }

  .track {
    height: 0.2rem;
    overflow: hidden;
    border-radius: 99px;
    background: rgba(198, 219, 237, 0.12);
  }

  .fill {
    width: calc(var(--fraction) * 100%);
    height: 100%;
    border-radius: inherit;
    background: var(--vital-color);
    transition: width 180ms ease-out;
  }
</style>
