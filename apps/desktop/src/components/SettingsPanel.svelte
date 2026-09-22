<script lang="ts">
  import Monitor from '@lucide/svelte/icons/monitor';
  import Moon from '@lucide/svelte/icons/moon';
  import Sun from '@lucide/svelte/icons/sun';

  import { boundedThresholdPercent, type AlertSettings } from '../lib/alerts/settings.ts';
  import type { DisplayMode } from '../lib/hud/presentation.ts';
  import type { ThemePreference } from '../lib/hud/theme.ts';

  let {
    mode,
    theme,
    alertSettings,
    onmodechange,
    onthemechange,
    onalertsettingschange,
    onmanageactions,
  }: {
    mode: DisplayMode;
    theme: ThemePreference;
    alertSettings: AlertSettings;
    onmodechange: (mode: DisplayMode) => void;
    onthemechange: (theme: ThemePreference) => void;
    onalertsettingschange: (settings: AlertSettings) => void;
    onmanageactions: (invoker: HTMLButtonElement) => void;
  } = $props();

  const THEME_OPTIONS = [
    { value: 'dark', label: 'Dark', icon: Moon },
    { value: 'light', label: 'Light', icon: Sun },
    { value: 'system', label: 'System', icon: Monitor },
  ] as const;

  function updateAlertSettings(patch: Partial<AlertSettings>): void {
    onalertsettingschange({ ...alertSettings, ...patch });
  }
</script>

<div class="settings-panel">
  <section class="settings-section" aria-labelledby="display-settings-title">
    <div class="section-head">
      <div id="display-settings-title" class="menu-title">Display</div>
      <div class="segmented" role="radiogroup" aria-label="Display mode">
        {#each ['expanded', 'compact'] as const as option (option)}
          <button
            class:selected={mode === option}
            type="button"
            role="radio"
            aria-checked={mode === option}
            onclick={(event) => {
              event.stopPropagation();
              onmodechange(option);
            }}>{option === 'expanded' ? 'Expanded' : 'Compact'}</button
          >
        {/each}
      </div>
    </div>
  </section>

  <section class="settings-section" aria-labelledby="theme-settings-title">
    <div class="section-head">
      <div id="theme-settings-title" class="menu-title">Theme</div>
      <div class="segmented" role="radiogroup" aria-label="Theme">
        {#each THEME_OPTIONS as option (option.value)}
          <button
            class:selected={theme === option.value}
            type="button"
            role="radio"
            aria-checked={theme === option.value}
            aria-label={option.label}
            title={option.label}
            onclick={(event) => {
              event.stopPropagation();
              onthemechange(option.value);
            }}><option.icon size={12} /></button
          >
        {/each}
      </div>
    </div>
  </section>

  <section class="settings-section alerts" aria-labelledby="alert-settings-title">
    <div id="alert-settings-title" class="menu-title">Alerts</div>
    <label class="toggle primary-toggle">
      <input
        type="checkbox"
        checked={alertSettings.lowHpEnabled}
        onchange={(event) => updateAlertSettings({ lowHpEnabled: event.currentTarget.checked })}
      />
      <span>Low HP alert</span>
    </label>

    <div class:disabled={!alertSettings.lowHpEnabled} class="alert-details">
      <label class="threshold-row">
        <span>Threshold</span>
        <span class="threshold-input">
          <input
            type="number"
            min="1"
            max="100"
            step="1"
            value={alertSettings.lowHpThresholdPercent}
            disabled={!alertSettings.lowHpEnabled}
            onchange={(event) =>
              updateAlertSettings({
                lowHpThresholdPercent: boundedThresholdPercent(event.currentTarget.valueAsNumber),
              })}
          />
          <span aria-hidden="true">%</span>
        </span>
      </label>

      <div class="toggle-row">
        <label class="toggle">
          <input
            type="checkbox"
            checked={alertSettings.soundEnabled}
            disabled={!alertSettings.lowHpEnabled}
            onchange={(event) => updateAlertSettings({ soundEnabled: event.currentTarget.checked })}
          />
          <span>Sound</span>
        </label>

        <label class="toggle">
          <input
            type="checkbox"
            checked={alertSettings.notificationEnabled}
            disabled={!alertSettings.lowHpEnabled}
            onchange={(event) => updateAlertSettings({ notificationEnabled: event.currentTarget.checked })}
          />
          <span>Notification</span>
        </label>
      </div>
    </div>
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
  .settings-section:last-child {
    padding-bottom: var(--space-3);
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

  .segmented button {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: var(--space-2) var(--space-3);
    border: 0;
    background: transparent;
    color: var(--muted);
    cursor: pointer;
    font-size: var(--font-2xs);
    /* Collapse the default leading so the line box equals the label and the
       declared padding is the space that actually surrounds it. */
    line-height: 1;
  }

  .segmented button + button {
    border-left: 1px solid var(--divider);
  }

  .segmented button:hover,
  .segmented button:focus-visible {
    background: var(--accent-hover);
    outline: none;
  }

  .segmented button.selected {
    background: var(--accent-subtle);
    color: var(--text);
  }

  .alerts {
    gap: var(--space-2);
  }

  .toggle,
  .threshold-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-6);
    min-height: 1.3rem;
    padding: var(--space-1) var(--space-2);
    color: var(--text);
    font-size: var(--font-xs);
    cursor: pointer;
  }

  .primary-toggle {
    justify-content: flex-start;
    font-weight: var(--weight-label);
  }

  .toggle-row {
    display: flex;
    gap: var(--space-4);
  }

  .toggle-row .toggle {
    justify-content: flex-start;
    gap: var(--space-2);
  }

  .toggle input {
    margin: 0;
    accent-color: var(--accent);
  }

  .alert-details {
    display: grid;
    gap: var(--space-1);
    padding-left: 0.85rem;
  }

  .alert-details.disabled {
    opacity: 0.52;
  }

  .threshold-input {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    color: var(--muted);
  }

  .threshold-input input {
    width: 3.2rem;
    padding: var(--space-1) var(--space-2);
    border: 1px solid var(--input-border);
    border-radius: var(--radius);
    background: var(--input-bg);
    color: var(--text);
    font: inherit;
    text-align: right;
  }

  .threshold-input input:focus-visible {
    border-color: var(--accent-focus);
    outline: none;
  }

  .threshold-input input:disabled,
  .toggle input:disabled {
    cursor: default;
  }
</style>
