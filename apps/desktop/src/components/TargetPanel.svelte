<script lang="ts">
  import type { Target } from '@tinyscry/protocol';

  let { target }: { target: Target } = $props();
</script>

<section class="target" aria-label="Current target">
  <div class="target-name">{target.name}</div>
  {#if target.healthPercent === null}
    <span class="unknown">Health unknown</span>
  {:else}
    <div class="health-row">
      <div class="track"><div class="fill" style={`width: ${target.healthPercent}%`}></div></div>
      <span>{Math.round(target.healthPercent)}%</span>
    </div>
  {/if}
</section>

<style>
  .target {
    display: grid;
    gap: var(--space-3);
    padding-top: var(--space-3);
    border-top: 1px solid var(--divider);
  }

  .target-name {
    overflow: hidden;
    color: var(--text);
    font-size: var(--font-sm);
    font-weight: var(--weight-strong);
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .health-row {
    display: flex;
    align-items: center;
    gap: var(--space-6);
    color: var(--target);
    font-size: var(--font-xs);
    font-variant-numeric: tabular-nums;
  }
  .track {
    flex: 1;
    height: var(--track-height);
    overflow: hidden;
    border-radius: 99px;
    background: var(--track-bg);
  }
  .fill {
    height: 100%;
    border-radius: inherit;
    background: var(--target);
    transition: width 180ms ease-out;
  }
  .unknown {
    color: var(--dimmed);
    font-size: var(--font-xs);
  }
</style>
