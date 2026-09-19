<script lang="ts">
  import type { AlertSettings } from '../lib/alerts/settings.ts';
  import type { DisplayMode } from '../lib/hud/presentation.ts';
  import SettingsPanel from './SettingsPanel.svelte';

  let {
    mode,
    alertSettings,
    onmodechange,
    onalertsettingschange,
    open = $bindable(false),
    popover = $bindable<HTMLDivElement | undefined>(),
  }: {
    mode: DisplayMode;
    alertSettings: AlertSettings;
    onmodechange: (mode: DisplayMode) => void;
    onalertsettingschange: (settings: AlertSettings) => void;
    open?: boolean;
    popover?: HTMLDivElement | undefined;
  } = $props();

  let menu = $state<HTMLDivElement>();

  $effect(() => {
    if (!open || mode !== 'compact') return;

    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (!menu?.contains(event.target as Node)) open = false;
    };
    window.addEventListener('pointerdown', closeOnOutsidePointer);
    return () => window.removeEventListener('pointerdown', closeOnOutsidePointer);
  });

  function selectMode(nextMode: DisplayMode): void {
    open = false;
    onmodechange(nextMode);
  }
</script>

<div class="settings" bind:this={menu}>
  <button
    class="settings-button"
    aria-label="Settings"
    aria-expanded={open}
    aria-haspopup="dialog"
    onclick={(event) => {
      event.stopPropagation();
      open = !open;
    }}>⚙</button
  >
  {#if open && mode === 'compact'}
    <div bind:this={popover} class="menu" role="dialog" aria-label="TinyScry settings">
      <SettingsPanel {mode} {alertSettings} onmodechange={selectMode} {onalertsettingschange} />
    </div>
  {/if}
</div>

<style>
  .settings {
    position: relative;
  }

  .settings-button {
    width: 1.35rem;
    height: 1.35rem;
    padding: 0 0 3px;
    border: 0;
    border-radius: 50%;
    background: transparent;
    color: var(--muted);
    cursor: pointer;
    font-size: 0.9rem;
    line-height: 1;
  }

  .settings-button:hover,
  .settings-button[aria-expanded='true'] {
    background: rgba(91, 180, 239, 0.18);
    color: var(--text);
  }

  .menu {
    position: absolute;
    z-index: 1;
    top: calc(100% + 0.3rem);
    right: 0;
    min-width: 15rem;
    padding: 0.55rem;
    border: 1px solid var(--panel-edge);
    border-radius: 0.45rem;
    background: rgba(18, 27, 39, 0.98);
    box-shadow: 0 8px 18px rgba(0, 0, 0, 0.32);
  }
</style>
