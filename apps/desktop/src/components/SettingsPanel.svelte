<script lang="ts">
  import { boundedThresholdPercent, type AlertSettings } from '../lib/alerts/settings.ts';
  import type { DisplayMode } from '../lib/hud/presentation.ts';

  let {
    mode,
    alertSettings,
    onmodechange,
    onalertsettingschange,
    onmanageactions,
  }: {
    mode: DisplayMode;
    alertSettings: AlertSettings;
    onmodechange: (mode: DisplayMode) => void;
    onalertsettingschange: (settings: AlertSettings) => void;
    onmanageactions: (invoker: HTMLButtonElement) => void;
  } = $props();

  function updateAlertSettings(patch: Partial<AlertSettings>): void {
    onalertsettingschange({ ...alertSettings, ...patch });
  }
</script>

<div class="settings-panel">
  <section class="settings-section" aria-labelledby="display-settings-title">
    <div id="display-settings-title" class="menu-title">Display</div>
    <div class="mode-options" role="radiogroup" aria-label="Display mode">
      <button
        role="radio"
        aria-checked={mode === 'expanded'}
        onclick={(event) => {
          event.stopPropagation();
          onmodechange('expanded');
        }}
      >
        <span aria-hidden="true">{mode === 'expanded' ? '•' : '○'}</span> Expanded
      </button>
      <button
        role="radio"
        aria-checked={mode === 'compact'}
        onclick={(event) => {
          event.stopPropagation();
          onmodechange('compact');
        }}
      >
        <span aria-hidden="true">{mode === 'compact' ? '•' : '○'}</span> Compact
      </button>
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
        <span>Desktop notification</span>
      </label>
    </div>
  </section>

  <section class="settings-section" aria-labelledby="action-settings-title">
    <div id="action-settings-title" class="menu-title">Actions</div>
    <button
      class="manage-actions"
      type="button"
      data-action-manager-trigger
      onclick={(event) => {
        event.stopPropagation();
        onmanageactions(event.currentTarget);
      }}>Manage actions…</button
    >
  </section>
</div>

<style>
  .settings-panel {
    display: grid;
    width: 100%;
    gap: 0.35rem;
  }

  .settings-section {
    display: grid;
    gap: 0.18rem;
  }

  .settings-section + .settings-section {
    padding-top: 0.35rem;
    border-top: 1px solid rgba(191, 215, 235, 0.13);
  }

  .menu-title {
    padding: 0.08rem 0.25rem 0.16rem;
    color: var(--muted);
    font-size: 0.58rem;
    font-weight: 750;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  .mode-options {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.18rem;
  }

  .mode-options button {
    padding: 0.24rem 0.35rem;
    border: 0;
    border-radius: 0.25rem;
    background: transparent;
    color: var(--text);
    cursor: pointer;
    font-size: 0.68rem;
    text-align: left;
  }

  .mode-options button:hover,
  .mode-options button:focus-visible {
    background: rgba(94, 157, 248, 0.16);
    outline: none;
  }

  .mode-options button span {
    display: inline-block;
    width: 0.75rem;
    color: var(--mana);
  }

  .manage-actions {
    padding: 0.26rem 0.35rem;
    border: 0;
    border-radius: 0.25rem;
    background: transparent;
    color: var(--text);
    cursor: pointer;
    font-size: 0.68rem;
    text-align: left;
  }

  .manage-actions:hover,
  .manage-actions:focus-visible {
    background: rgba(94, 157, 248, 0.16);
    outline: none;
  }

  .alerts {
    gap: 0.2rem;
  }

  .toggle,
  .threshold-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.6rem;
    min-height: 1.3rem;
    padding: 0.08rem 0.25rem;
    color: var(--text);
    font-size: 0.68rem;
    cursor: pointer;
  }

  .primary-toggle {
    justify-content: flex-start;
    font-weight: 700;
  }

  .toggle input {
    margin: 0;
    accent-color: var(--mana);
  }

  .alert-details {
    display: grid;
    gap: 0.08rem;
    padding-left: 0.85rem;
  }

  .alert-details.disabled {
    opacity: 0.52;
  }

  .threshold-input {
    display: flex;
    align-items: center;
    gap: 0.2rem;
    color: var(--muted);
  }

  .threshold-input input {
    width: 3.2rem;
    padding: 0.14rem 0.25rem;
    border: 1px solid rgba(191, 215, 235, 0.2);
    border-radius: 0.25rem;
    background: rgba(8, 13, 20, 0.8);
    color: var(--text);
    font: inherit;
    text-align: right;
  }

  .threshold-input input:focus-visible {
    border-color: rgba(94, 157, 248, 0.72);
    outline: none;
  }

  .threshold-input input:disabled,
  .toggle input:disabled {
    cursor: default;
  }
</style>
