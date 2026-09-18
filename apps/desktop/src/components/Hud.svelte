<script lang="ts">
  import CompactVital from './CompactVital.svelte';
  import SettingsMenu from './SettingsMenu.svelte';
  import StatusIndicator from './StatusIndicator.svelte';
  import TargetPanel from './TargetPanel.svelte';
  import VitalBar from './VitalBar.svelte';
  import { freshnessOf, type HudFreshness, type HudModel } from '../lib/hud/model.ts';
  import {
    loadDisplayMode,
    saveDisplayMode,
    statusIndicatorOf,
    statusLabelOf,
    type DisplayMode,
  } from '../lib/hud/presentation.ts';
  import { closeWindow, compactWindowSize, EXPANDED_WINDOW_SIZE, resizeHudWindow } from '../lib/window.ts';

  let { model }: { model: HudModel } = $props();
  let displayMode = $state(loadDisplayMode());

  let panel = $state<HTMLElement>();
  let compactRow = $state<HTMLElement>();
  let compactMenu = $state<HTMLDivElement>();
  let compactMenuOpen = $state(false);

  const freshness = $derived(freshnessOf(model));
  const isFresh = $derived(freshness === 'fresh' && model.hasData);
  const status = $derived(statusIndicatorOf(model));
  const statusLabel = $derived(statusLabelOf(model));
  const character = $derived(model.state.character);
  const characterName = $derived(character?.name ?? 'TinyScry');
  const showMana = $derived(character?.mana?.max !== 0);

  const STALE_WORDING: Record<Exclude<HudFreshness, 'fresh'>, string> = {
    reconnecting: 'reconnecting',
    offline: 'not connected',
    'feed-down': 'no game feed',
    'feed-stalled': 'feed stalled',
  };

  function setDisplayMode(mode: DisplayMode): void {
    displayMode = mode;
    saveDisplayMode(mode);
  }

  $effect(() => {
    if (displayMode === 'expanded') {
      resizeHudWindow(EXPANDED_WINDOW_SIZE);
      return;
    }
    const row = compactRow;
    const hudPanel = panel;
    const menu = compactMenuOpen ? compactMenu : undefined;
    if (row === undefined || hudPanel === undefined) return;

    const resize = () => {
      const rowBounds = row.getBoundingClientRect();
      const menuBounds = menu?.getBoundingClientRect();
      const left = Math.min(rowBounds.left, menuBounds?.left ?? rowBounds.left);
      const right = Math.max(rowBounds.right, menuBounds?.right ?? rowBounds.right);
      const bottom = Math.max(rowBounds.bottom, menuBounds?.bottom ?? rowBounds.bottom);
      const frameWidth = hudPanel.offsetWidth - hudPanel.clientWidth;
      const frameHeight = hudPanel.offsetHeight - hudPanel.clientHeight;
      resizeHudWindow(compactWindowSize(right - left + frameWidth, bottom - rowBounds.top + frameHeight));
    };
    const frame = requestAnimationFrame(resize);
    const observer = new ResizeObserver(resize);

    observer.observe(row);
    if (menu !== undefined) observer.observe(menu);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  });
</script>

<main
  class:compact={displayMode === 'compact'}
  class:offline={!isFresh}
  class="hud"
  aria-label="TinyScry companion HUD"
  data-tauri-drag-region
>
  <div bind:this={panel} class="panel">
    {#if displayMode === 'compact'}
      <section bind:this={compactRow} class:compact-last-known={!isFresh} class="compact-row">
        <div class="compact-primary">
          <StatusIndicator {status} label={statusLabel} />
          <span class="compact-name">{characterName}</span>
          <CompactVital label="HP" vital={character?.hp ?? null} color="var(--hp)" />
          {#if showMana}
            <CompactVital label="MN" vital={character?.mana ?? null} color="var(--mana)" />
          {/if}
          <CompactVital label="MV" vital={character?.moves ?? null} color="var(--moves)" />
        </div>
        <div class="compact-controls">
          <SettingsMenu
            bind:popover={compactMenu}
            mode={displayMode}
            onmodechange={setDisplayMode}
            onopenchange={(open) => (compactMenuOpen = open)}
          />
          <button
            class="close"
            aria-label="Close TinyScry"
            onclick={(event) => {
              event.stopPropagation();
              closeWindow();
            }}>×</button
          >
        </div>
        {#if !isFresh}
          <span class="sr-only"
            >{character === null
              ? 'Waiting for usable game data'
              : `Last known values — ${freshness === 'fresh' ? 'waiting for update' : STALE_WORDING[freshness]}`}</span
          >
        {/if}
      </section>
    {:else}
      <header class="titlebar">
        <div class="identity" data-tauri-drag-region>
          <StatusIndicator {status} label={statusLabel} />
          <span>{characterName}</span>
        </div>
        <SettingsMenu mode={displayMode} onmodechange={setDisplayMode} />
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
          <div class="vitals">
            <VitalBar label="HP" vital={character.hp} color="var(--hp)" />
            {#if showMana}
              <VitalBar label="MANA" vital={character.mana} color="var(--mana)" />
            {/if}
            <VitalBar label="MV" vital={character.moves} color="var(--moves)" />
          </div>
          <div class="target-slot">
            {#if model.state.target !== null}
              <TargetPanel target={model.state.target} />
            {/if}
          </div>
        </section>
      {:else if character !== null}
        <section class="content last-known">
          <div class="vitals">
            <VitalBar label="HP" vital={character.hp} color="var(--hp)" />
            {#if showMana}
              <VitalBar label="MANA" vital={character.mana} color="var(--mana)" />
            {/if}
            <VitalBar label="MV" vital={character.moves} color="var(--moves)" />
          </div>
          <div class="target-slot">
            {#if model.state.target !== null}
              <TargetPanel target={model.state.target} />
            {/if}
          </div>
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
    {/if}
  </div>
</main>

<style>
  .hud {
    width: 100%;
    height: 100vh;
  }

  .panel {
    display: grid;
    height: 100vh;
    min-height: 0;
    overflow: hidden;
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius);
    background: var(--panel);
    backdrop-filter: blur(14px);
  }

  .offline {
    opacity: 0.82;
  }

  .titlebar {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto auto;
    align-items: center;
    gap: 0.4rem;
    min-height: 2.05rem;
    padding: 0 0.52rem 0 0.75rem;
    border-bottom: 1px solid rgba(191, 215, 235, 0.13);
  }

  .identity {
    display: flex;
    align-items: center;
    min-width: 0;
    gap: 0.45rem;
    min-height: 2.05rem;
    color: var(--text);
    font-size: 0.75rem;
    font-weight: 800;
    letter-spacing: 0.035em;
  }

  .identity span:last-child,
  .compact-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .close {
    width: 1.35rem;
    height: 1.35rem;
    padding: 0 0 3px;
    border: 0;
    border-radius: 50%;
    background: transparent;
    color: var(--muted);
    cursor: pointer;
    font-size: 1.15rem;
    line-height: 1;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .close:hover {
    background: rgba(91, 180, 239, 0.18);
    color: #fff;
  }

  .content {
    display: grid;
    align-content: start;
    gap: 0.4rem;
    padding: 0.5rem 0.75rem 0.25rem;
  }

  .vitals {
    display: grid;
    gap: 0.45rem;
  }

  .target-slot {
    min-height: 2.25rem;
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

  .compact .panel {
    width: max-content;
    height: max-content;
    align-content: start;
    overflow: visible;
    border-radius: var(--radius-compact);
  }

  .compact-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    gap: 0.35rem;
    min-height: 2.05rem;
    padding: 0.3rem 0.75rem;
    width: max-content;
    max-width: calc(560px - 2px);
    min-height: 40px;
  }

  .compact-primary {
    display: grid;
    grid-template-columns: auto minmax(2.5rem, 1fr) repeat(3, max-content);
    align-items: center;
    min-width: 0;
    gap: 0.75rem;
  }

  .compact-name {
    color: var(--text);
    font-size: 0.68rem;
    font-weight: 800;
    letter-spacing: 0.02em;
  }

  .compact-controls {
    display: flex;
    align-items: center;
  }

  .compact-last-known {
    opacity: 0.8;
  }

  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    clip-path: inset(50%);
    white-space: nowrap;
  }
</style>
