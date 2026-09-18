<script lang="ts">
  import type { DisplayMode } from '../lib/hud/presentation.ts';

  let {
    mode,
    onmodechange,
    popover = $bindable<HTMLDivElement | undefined>(),
    onopenchange,
  }: {
    mode: DisplayMode;
    onmodechange: (mode: DisplayMode) => void;
    popover?: HTMLDivElement | undefined;
    onopenchange?: (open: boolean) => void;
  } = $props();

  let menu = $state<HTMLDivElement>();
  let open = $state(false);

  $effect(() => {
    if (!open) return;

    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (!menu?.contains(event.target as Node)) {
        open = false;
        onopenchange?.(false);
      }
    };
    window.addEventListener('pointerdown', closeOnOutsidePointer);
    return () => window.removeEventListener('pointerdown', closeOnOutsidePointer);
  });

  function selectMode(nextMode: DisplayMode): void {
    onmodechange(nextMode);
    open = false;
    onopenchange?.(false);
  }
</script>

<div class="settings" bind:this={menu}>
  <button
    class="settings-button"
    aria-label="Settings"
    aria-expanded={open}
    aria-haspopup="menu"
    onclick={(event) => {
      event.stopPropagation();
      open = !open;
      onopenchange?.(open);
    }}>⚙</button
  >
  {#if open}
    <div bind:this={popover} class="menu" role="menu" aria-label="Settings">
      <div class="menu-title">Display mode</div>
      <button
        role="menuitemradio"
        aria-checked={mode === 'expanded'}
        onclick={(event) => {
          event.stopPropagation();
          selectMode('expanded');
        }}
      >
        <span aria-hidden="true">{mode === 'expanded' ? '•' : '○'}</span> Expanded
      </button>
      <button
        role="menuitemradio"
        aria-checked={mode === 'compact'}
        onclick={(event) => {
          event.stopPropagation();
          selectMode('compact');
        }}
      >
        <span aria-hidden="true">{mode === 'compact' ? '•' : '○'}</span> Compact
      </button>
    </div>
  {/if}
</div>

<style>
  .settings {
    position: relative;
  }

  .settings-button,
  .menu button {
    border: 0;
    background: transparent;
    color: var(--muted);
    cursor: pointer;
  }

  .settings-button {
    width: 1.35rem;
    height: 1.35rem;
    padding: 0 0 3px;
    border-radius: 50%;
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
    display: grid;
    min-width: 8.5rem;
    gap: 0.1rem;
    padding: 0.4rem;
    border: 1px solid var(--panel-edge);
    border-radius: 0.45rem;
    background: rgba(18, 27, 39, 0.98);
    box-shadow: 0 8px 18px rgba(0, 0, 0, 0.32);
  }

  .menu-title {
    padding: 0.12rem 0.25rem 0.25rem;
    color: var(--muted);
    font-size: 0.58rem;
    font-weight: 750;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  .menu button {
    padding: 0.28rem 0.25rem;
    border-radius: 0.25rem;
    color: var(--text);
    font-size: 0.68rem;
    text-align: left;
  }

  .menu button:hover,
  .menu button:focus-visible {
    background: rgba(94, 157, 248, 0.16);
    outline: none;
  }

  .menu button span {
    display: inline-block;
    width: 0.75rem;
    color: var(--mana);
  }
</style>
