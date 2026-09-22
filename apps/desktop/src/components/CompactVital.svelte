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
    gap: var(--space-2);
  }

  .value {
    display: flex;
    align-items: baseline;
    gap: 0.18rem;
    min-width: 0;
    color: var(--text);
    font-size: var(--font-2xs);
    line-height: var(--font-2xs);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }

  .label {
    color: var(--muted);
    font-size: var(--font-2xs);
    font-weight: var(--weight-label);
    letter-spacing: var(--tracking-wide);
  }

  .unknown {
    color: var(--dimmed);
  }

  .track {
    height: var(--track-height-compact);
    overflow: hidden;
    border-radius: 99px;
    background: var(--track-bg);
  }

  .fill {
    width: calc(var(--fraction) * 100%);
    height: 100%;
    border-radius: inherit;
    background: var(--vital-color);
    transition: width 180ms ease-out;
  }
</style>
