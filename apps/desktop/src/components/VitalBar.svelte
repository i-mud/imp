<script lang="ts">
  import { vitalFraction, type Vital } from '@tinyscry/protocol';

  let { label, vital, color }: { label: string; vital: Vital | null; color: string } = $props();
</script>

<div class="vital" style={`--vital-color: ${color}; --fraction: ${vitalFraction(vital)}`}>
  <div class="labels">
    <span>{label}</span>
    {#if vital === null}
      <span class="unknown">—</span>
    {:else}
      <span class="numbers"
        >{vital.current.toLocaleString()}<span class="slash"> / </span>{vital.max.toLocaleString()}</span
      >
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
    gap: var(--space-2);
  }

  .labels {
    display: flex;
    justify-content: space-between;
    color: var(--muted);
    font-size: var(--font-xs);
    font-weight: var(--weight-label);
    letter-spacing: var(--tracking-wide);
  }

  .numbers {
    color: var(--text);
    font-variant-numeric: tabular-nums;
    letter-spacing: 0.02em;
  }
  .slash,
  .unknown {
    color: var(--dimmed);
  }

  .track {
    height: var(--track-height);
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
