<script lang="ts">
  import ConnectionBadge from './ConnectionBadge.svelte';
  import TargetPanel from './TargetPanel.svelte';
  import VitalBar from './VitalBar.svelte';
  import { freshnessOf, type HudFreshness, type HudModel } from '../lib/hud/model.ts';
  import { closeWindow } from '../lib/window.ts';

  let { model }: { model: HudModel } = $props();
  const freshness = $derived(freshnessOf(model));
  const isFresh = $derived(freshness === 'fresh' && model.hasData);
  const character = $derived(model.state.character);

  // Names which signal failed, so the operator is pointed at the right thing.
  const STALE_WORDING: Record<Exclude<HudFreshness, 'fresh'>, string> = {
    reconnecting: 'reconnecting',
    offline: 'not connected',
    'feed-down': 'no game feed',
    'feed-stalled': 'feed stalled',
  };
</script>

<main class:offline={!isFresh} class="panel" aria-label="TinyScry companion HUD">
  <header class="titlebar">
    <div class="identity" data-tauri-drag-region>
      <span class="mark">◈</span>
      <span>TinyScry</span>
    </div>
    <ConnectionBadge phase={model.phase} feed={model.feed} />
    <button
      class="close"
      aria-label="Close TinyScry"
      onclick={(event) => {
        event.stopPropagation();
        closeWindow();
      }}>×</button
    >
  </header>

  {#if isFresh && character !== null}
    <section class="content">
      <div class="character-name">{character.name}</div>
      <div class="vitals">
        <VitalBar label="HP" vital={character.hp} color="var(--hp)" />
        <VitalBar label="MANA" vital={character.mana} color="var(--mana)" />
        <VitalBar label="MV" vital={character.moves} color="var(--moves)" />
      </div>
      {#if model.state.target !== null}
        <TargetPanel target={model.state.target} />
      {/if}
    </section>
  {:else if character !== null}
    <section class="content last-known">
      <div class="state-note">
        Last known values — {freshness === 'fresh' ? 'waiting for update' : STALE_WORDING[freshness]}
      </div>
      <div class="character-name">{character.name}</div>
      <div class="vitals">
        <VitalBar label="HP" vital={character.hp} color="var(--hp)" />
        <VitalBar label="MANA" vital={character.mana} color="var(--mana)" />
        <VitalBar label="MV" vital={character.moves} color="var(--moves)" />
      </div>
      {#if model.state.target !== null}
        <TargetPanel target={model.state.target} />
      {/if}
    </section>
  {:else if model.phase === 'connected'}
    <section class="empty-state">
      <strong>Connected to relay</strong>
      <span>Waiting for game data…</span>
    </section>
  {:else}
    <section class="empty-state">
      <strong>{model.phase === 'reconnecting' ? 'Reconnecting to relay…' : 'Relay unavailable'}</strong>
      <span>{model.detail ?? 'Start the relay, then connect TinyFugue.'}</span>
    </section>
  {/if}
</main>

<style>
  .panel {
    display: grid;
    min-height: 210px;
    overflow: hidden;
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius);
    background: var(--panel);
    box-shadow: 0 8px 26px rgba(0, 0, 0, 0.34);
    backdrop-filter: blur(14px);
  }

  .offline {
    opacity: 0.82;
  }

  .titlebar {
    display: grid;
    grid-template-columns: 1fr auto auto;
    align-items: center;
    gap: 0.55rem;
    min-height: 2.05rem;
    padding: 0 0.52rem 0 0.75rem;
    border-bottom: 1px solid rgba(191, 215, 235, 0.13);
  }

  .identity {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    min-height: 2.05rem;
    color: var(--text);
    font-size: 0.72rem;
    font-weight: 800;
    letter-spacing: 0.035em;
  }
  .mark {
    color: var(--mana);
    font-size: 0.86rem;
  }
  .close {
    width: 1.35rem;
    height: 1.35rem;
    padding: 0;
    border: 0;
    border-radius: 5px;
    background: transparent;
    color: var(--muted);
    cursor: pointer;
    font-size: 1.15rem;
    line-height: 1;
  }
  .close:hover {
    background: rgba(239, 91, 104, 0.18);
    color: #fff;
  }

  .content {
    display: grid;
    align-content: start;
    gap: 0.56rem;
    padding: 0.62rem 0.75rem 0.7rem;
  }
  .character-name {
    overflow: hidden;
    color: var(--text);
    font-size: 0.93rem;
    font-weight: 750;
    letter-spacing: 0.01em;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .vitals {
    display: grid;
    gap: 0.45rem;
  }
  .last-known .state-note {
    color: var(--warn);
    font-size: 0.62rem;
    font-weight: 650;
    letter-spacing: 0.035em;
  }

  .empty-state {
    display: grid;
    align-content: center;
    gap: 0.35rem;
    padding: 1rem 0.8rem;
    color: var(--muted);
    text-align: center;
  }
  .empty-state strong {
    color: var(--text);
    font-size: 0.78rem;
  }
  .empty-state span {
    font-size: 0.69rem;
    line-height: 1.35;
  }
</style>
