<script lang="ts">
  import X from '@lucide/svelte/icons/x';

  import type { ConnectionMode, ConnectionSettings, ConnectionSettingsUpdate } from '../lib/tunnel.ts';

  let {
    settings,
    loadError,
    saveError,
    onsave,
    onclose,
  }: {
    settings: ConnectionSettings | null;
    loadError: string | null;
    saveError: string | null;
    onsave: (update: ConnectionSettingsUpdate) => Promise<boolean>;
    onclose: () => void;
  } = $props();

  let mode = $state<ConnectionMode>('external');
  let sshTarget = $state('');
  let remoteUrl = $state('');
  let pairingToken = $state('');
  let saving = $state(false);
  let saved = $state(false);
  let firstModeButton = $state<HTMLButtonElement>();
  let initialFocusApplied = false;

  $effect(() => {
    const current = settings;
    if (current === null) return;

    mode = current.mode;
    sshTarget = current.sshTarget;
    remoteUrl = current.remoteUrl;
    pairingToken = '';

    if (!initialFocusApplied) {
      initialFocusApplied = true;
      requestAnimationFrame(() => firstModeButton?.focus());
    }
  });

  $effect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !saving) onclose();
    };

    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  });

  function chooseMode(nextMode: ConnectionMode): void {
    mode = nextMode;
    saved = false;
  }

  async function save(): Promise<void> {
    if (settings === null || saving) return;

    let update: ConnectionSettingsUpdate;

    if (mode === 'external') {
      update = { mode: 'external' };
    } else if (mode === 'local') {
      update = { mode: 'local' };
    } else if (mode === 'managed') {
      update = {
        mode: 'managed',
        sshTarget,
      };
    } else {
      const direct: ConnectionSettingsUpdate = {
        mode: 'direct',
        remoteUrl,
      };

      update =
        pairingToken.length === 0
          ? direct
          : {
              ...direct,
              pairingToken,
            };
    }

    saving = true;
    saved = false;

    try {
      saved = await onsave(update);
    } finally {
      saving = false;
    }
  }
</script>

<div
  class="connection-dialog manager-dialog"
  role="dialog"
  aria-modal="true"
  aria-labelledby="connection-dialog-title"
>
  <header class="dialog-titlebar" data-tauri-drag-region>
    <div class="dialog-title" data-tauri-drag-region>
      <h2 id="connection-dialog-title">Connection</h2>
      <p>Choose how Imp reaches an Imp node. Changes take effect after restart.</p>
    </div>

    <button
      class="dialog-close icon-btn"
      type="button"
      aria-label="Close connection settings"
      disabled={saving}
      onclick={onclose}><X size={16} /></button
    >
  </header>

  <div class="dialog-content">
    {#if loadError !== null}
      <p class="error" role="alert">{loadError}</p>
    {:else if settings === null}
      <p class="loading">Loading connection settings…</p>
    {:else}
      <form
        onsubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <div class="selector-row">
          <span>Mode</span>

          <div class="segmented" role="radiogroup" aria-label="Connection mode">
            <button
              bind:this={firstModeButton}
              class:selected={mode === 'external'}
              type="button"
              role="radio"
              aria-checked={mode === 'external'}
              disabled={saving}
              onclick={() => chooseMode('external')}>External</button
            >
            <button
              class:selected={mode === 'local'}
              type="button"
              role="radio"
              aria-checked={mode === 'local'}
              disabled={saving}
              onclick={() => chooseMode('local')}>Local</button
            >
            <button
              class:selected={mode === 'managed'}
              type="button"
              role="radio"
              aria-checked={mode === 'managed'}
              disabled={saving}
              onclick={() => chooseMode('managed')}>Managed</button
            >
            <button
              class:selected={mode === 'direct'}
              type="button"
              role="radio"
              aria-checked={mode === 'direct'}
              disabled={saving}
              onclick={() => chooseMode('direct')}>Direct</button
            >
          </div>
        </div>

        {#if mode === 'external'}
          <p class="mode-help">
            Imp expects an existing local endpoint, such as a manually managed SSH forward, and owns no
            connection process.
          </p>
        {:else if mode === 'local'}
          <p class="mode-help">
            Imp runs or adopts a loopback Imp node on this computer for a local MUD client such as Mudlet or
            TinyFugue.
          </p>
        {:else if mode === 'managed'}
          <label>
            <span>SSH target</span>
            <input
              bind:value={sshTarget}
              type="text"
              spellcheck="false"
              autocomplete="off"
              placeholder="avatar"
              disabled={saving}
              oninput={() => {
                saved = false;
              }}
            />
          </label>

          <p class="mode-help">
            Use an existing Host alias from your SSH configuration. Imp uses the system OpenSSH client.
          </p>
        {:else}
          <label>
            <span>WSS state URL</span>
            <input
              bind:value={remoteUrl}
              type="url"
              spellcheck="false"
              autocomplete="off"
              placeholder="wss://imp.example/state"
              disabled={saving}
              oninput={() => {
                saved = false;
              }}
            />
          </label>

          <label>
            <span>Pairing token</span>
            <input
              bind:value={pairingToken}
              type="password"
              spellcheck="false"
              autocomplete="off"
              placeholder={settings.hasPairingToken ? 'Stored token will be kept' : 'Pairing token required'}
              disabled={saving}
              oninput={() => {
                saved = false;
              }}
            />
          </label>

          <p class="mode-help">
            {settings.hasPairingToken
              ? 'Leave the token blank to keep the currently stored credential.'
              : 'Paste the 43-character pairing token supplied by the gateway operator.'}
          </p>
        {/if}

        {#if saveError !== null}
          <p class="error" role="alert">{saveError}</p>
        {/if}

        {#if saved}
          <p class="saved-message" role="status">Saved. Restart Imp to use this connection.</p>
        {/if}

        <div class="form-actions">
          <button class="manager-btn primary" type="submit" disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </button>
          <button class="manager-btn secondary" type="button" disabled={saving} onclick={onclose}>
            Close
          </button>
        </div>
      </form>
    {/if}
  </div>
</div>

<style>
  .connection-dialog {
    display: grid;
    height: max-content;
    grid-template-rows: auto auto;
    overflow: hidden;
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius);
    background: var(--panel);
    color: var(--text);
  }

  header {
    display: flex;
    align-items: start;
    justify-content: space-between;
    gap: var(--space-8);
    padding: var(--section-pad);
    border-bottom: 1px solid var(--divider);
  }

  .dialog-title,
  form,
  label {
    min-width: 0;
  }

  h2,
  p {
    margin: 0;
  }

  h2 {
    font-size: var(--font-md);
  }

  header p {
    margin-top: 0.22rem;
    overflow-wrap: anywhere;
    color: var(--muted);
    font-size: var(--font-xs);
  }

  .dialog-close {
    flex: 0 0 auto;
  }

  form {
    display: grid;
    gap: var(--space-4);
  }

  label {
    display: grid;
    gap: var(--space-2);
    color: var(--muted);
    font-size: var(--font-xs);
  }

  .selector-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-4);
    color: var(--muted);
    font-size: var(--font-xs);
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

  input {
    width: 100%;
    padding: var(--space-3) var(--space-4);
    border: 1px solid var(--input-border);
    border-radius: var(--radius);
    background: var(--input-bg);
    color: var(--text);
    font: inherit;
    user-select: text;
  }

  input:focus-visible,
  button:focus-visible {
    border-color: var(--accent-focus);
    outline: none;
  }

  button:disabled,
  input:disabled {
    cursor: default;
    opacity: var(--opacity-disabled);
  }

  .mode-help,
  .loading,
  .error,
  .saved-message {
    overflow-wrap: anywhere;
    font-size: var(--font-xs);
    line-height: 1.35;
  }

  .mode-help,
  .loading {
    color: var(--muted);
  }

  .error {
    color: var(--bad);
  }

  .saved-message {
    color: var(--good);
  }

  .form-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: end;
    gap: var(--space-4);
    padding-top: var(--space-1);
  }
</style>
