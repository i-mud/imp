<script lang="ts">
  import Monitor from '@lucide/svelte/icons/monitor';
  import Moon from '@lucide/svelte/icons/moon';
  import Sun from '@lucide/svelte/icons/sun';
  import type { DisplayMode } from '../lib/hud/presentation.ts';
  import type { ThemePreference } from '../lib/hud/theme.ts';

  let {
    mode,
    theme,
    appVersion,
    alertSaveError,
    onmodechange,
    onthemechange,
    onmanageconnection,
    onmanagealerts,
    onmanageactions,
  }: {
    mode: DisplayMode;
    theme: ThemePreference;
    appVersion: string | null;
    alertSaveError: string | null;
    onmodechange: (mode: DisplayMode, keyboard: boolean) => void;
    onthemechange: (theme: ThemePreference) => void;
    onmanageconnection: (invoker: HTMLButtonElement) => void;
    onmanagealerts: (invoker: HTMLButtonElement) => void;
    onmanageactions: (invoker: HTMLButtonElement) => void;
  } = $props();

  const THEME_OPTIONS = [
    { value: 'dark', label: 'Dark', icon: Moon },
    { value: 'light', label: 'Light', icon: Sun },
    { value: 'system', label: 'System', icon: Monitor },
  ] as const;
</script>

<div class="settings-panel">
  <section class="settings-section" aria-labelledby="display-settings-title">
    <div class="section-head">
      <div id="display-settings-title" class="menu-title">Display</div>
      <div class="segmented" role="radiogroup" aria-label="Display mode">
        {#each ['expanded', 'compact'] as const as option (option)}
          <label>
            <input
              type="radio"
              name="display-mode"
              value={option}
              checked={mode === option}
              onchange={(event) => onmodechange(option, event.currentTarget.matches(':focus-visible'))}
            />
            <span>{option === 'expanded' ? 'Expanded' : 'Compact'}</span>
          </label>
        {/each}
      </div>
    </div>
  </section>

  <section class="settings-section" aria-labelledby="theme-settings-title">
    <div class="section-head">
      <div id="theme-settings-title" class="menu-title">Theme</div>
      <div class="segmented" role="radiogroup" aria-label="Theme">
        {#each THEME_OPTIONS as option (option.value)}
          <label title={option.label}>
            <input
              type="radio"
              name="theme"
              value={option.value}
              checked={theme === option.value}
              aria-label={option.label}
              onchange={() => onthemechange(option.value)}
            />
            <span><option.icon size={12} /></span>
          </label>
        {/each}
      </div>
    </div>
  </section>

  <section class="settings-section" aria-labelledby="connection-settings-title">
    <div class="section-head">
      <div id="connection-settings-title" class="menu-title">Connection</div>
      <button
        class="menu-button head-action"
        type="button"
        data-connection-manager-trigger
        onclick={(event) => {
          event.stopPropagation();
          onmanageconnection(event.currentTarget);
        }}>Manage</button
      >
    </div>
  </section>

  <section class="settings-section" aria-labelledby="alert-settings-title">
    <div class="section-head">
      <div id="alert-settings-title" class="menu-title">Alerts</div>
      <button
        class="menu-button head-action"
        type="button"
        data-alert-manager-trigger
        onclick={(event) => {
          event.stopPropagation();
          onmanagealerts(event.currentTarget);
        }}>Manage</button
      >
    </div>

    {#if alertSaveError !== null}
      <p class="alert-save-error" role="alert">{alertSaveError}</p>
    {/if}
  </section>

  <section class="settings-section" aria-labelledby="action-settings-title">
    <div class="section-head">
      <div id="action-settings-title" class="menu-title">Actions</div>
      <button
        class="menu-button head-action"
        type="button"
        data-action-manager-trigger
        onclick={(event) => {
          event.stopPropagation();
          onmanageactions(event.currentTarget);
        }}>Manage</button
      >
    </div>
  </section>

  {#if appVersion !== null}
    <div class="version-label">Imp v{appVersion}</div>
  {/if}
</div>

<style>
  .settings-panel {
    display: grid;
    width: 100%;
    gap: var(--space-3);
  }

  .settings-section {
    display: grid;
    gap: var(--space-2);
  }

  .settings-section + .settings-section {
    padding-top: var(--space-3);
    border-top: 1px solid var(--divider);
  }

  /* Mirrors the inter-section padding so the last row is not flush with the
     panel edge. */
  .settings-section:last-of-type {
    padding-bottom: var(--space-3);
  }

  .version-label {
    padding: 0 var(--space-2) var(--space-1);
    color: var(--muted);
    font-size: var(--font-2xs);
    line-height: 1;
    text-align: right;
  }

  .menu-title {
    padding: var(--space-1) var(--space-2);
    color: var(--muted);
    font-size: var(--font-2xs);
    font-weight: var(--weight-label);
    letter-spacing: var(--tracking-wide);
    text-transform: uppercase;
  }

  .section-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-4);
  }

  .menu-button {
    padding: var(--space-3) var(--space-4);
    border: 0;
    border-radius: var(--radius);
    background: transparent;
    color: var(--text);
    cursor: pointer;
    font-size: var(--font-xs);
    text-align: left;
  }

  .menu-button:hover,
  .menu-button:focus-visible {
    background: var(--accent-hover);
    outline: none;
  }

  .head-action {
    flex: 0 0 auto;
    padding-block: var(--space-1);
    color: var(--accent);
  }

  .segmented {
    display: flex;
    flex: 0 0 auto;
    overflow: hidden;
    border: 1px solid var(--divider);
    border-radius: var(--radius);
  }

  .segmented label {
    position: relative;
    cursor: pointer;
  }

  .segmented input {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    margin: 0;
    opacity: 0;
    cursor: inherit;
  }

  .segmented span {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: var(--space-2) var(--space-3);
    color: var(--muted);
    font-size: var(--font-2xs);
    line-height: 1;
  }

  .segmented label + label {
    border-left: 1px solid var(--divider);
  }

  .segmented label:hover span {
    background: var(--accent-hover);
  }

  .segmented input:checked + span {
    background: var(--accent-subtle);
    color: var(--text);
  }

  .segmented input:focus-visible + span {
    outline: 2px solid var(--accent-focus);
    outline-offset: -2px;
  }

  .alert-save-error {
    margin: 0;
    padding: var(--space-1) var(--space-2);
    font-size: var(--font-xs);
  }

  .alert-save-error {
    color: var(--bad);
  }
</style>
