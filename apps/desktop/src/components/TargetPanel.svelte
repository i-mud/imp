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
    gap: 0.32rem;
    padding-top: 0.35rem;
    border-top: 1px solid rgba(191, 215, 235, 0.14);
  }

  .target-name {
    overflow: hidden;
    color: var(--text);
    font-size: 0.74rem;
    font-weight: 650;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .health-row {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    color: var(--target);
    font-size: 0.64rem;
    font-variant-numeric: tabular-nums;
  }
  .track {
    flex: 1;
    height: 0.34rem;
    overflow: hidden;
    border-radius: 99px;
    background: rgba(229, 171, 84, 0.15);
  }
  .fill {
    height: 100%;
    border-radius: inherit;
    background: var(--target);
    transition: width 180ms ease-out;
  }
  .unknown {
    color: var(--dimmed);
    font-size: 0.65rem;
  }
</style>
