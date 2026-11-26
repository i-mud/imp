<script lang="ts">
  import X from '@lucide/svelte/icons/x';
  import { tick } from 'svelte';

  import type {
    ConnectionMode,
    ConnectionSettings,
    ConnectionSettingsState,
    ConnectionSettingsUpdate,
  } from '../lib/tunnel.ts';

  let {
    settings,
    loadError,
    saveError,
    onsave,
    onclose,
  }: {
    settings: ConnectionSettingsState | null;
    loadError: string | null;
    saveError: string | null;
    onsave: (update: ConnectionSettingsUpdate) => Promise<boolean>;
    onclose: () => void;
  } = $props();

  const LOCATION_CHOICES = [
    { value: 'local', label: 'On this computer' },
    { value: 'remote', label: 'On another computer or server' },
  ] as const;
  const REMOTE_CHOICES = [
    { value: 'managed', label: 'Use my server login', technical: 'Managed SSH' },
    { value: 'direct', label: 'Use remote Imp credentials', technical: 'Direct WSS' },
  ] as const;
  const METHOD_NAMES: Record<ConnectionMode, string> = {
    local: 'This computer',
    managed: 'Server login',
    direct: 'Remote Imp credentials',
    external: 'Own SSH tunnel',
  };

  let location = $state<'local' | 'remote' | null>(null);
  let mode = $state<ConnectionMode | null>(null);
  let sshTarget = $state('');
  let remoteUrl = $state('');
  let pairingToken = $state('');
  let saving = $state(false);
  let saved = $state(false);
  let confirmingDiscard = $state(false);
  let cancelDiscardButton = $state<HTMLButtonElement>();
  let saveButton = $state<HTMLButtonElement>();
  let locationGroup = $state<HTMLFieldSetElement>();
  let titleClose = $state<HTMLButtonElement>();
  let footerClose = $state<HTMLButtonElement>();
  let sshField = $state<HTMLInputElement>();
  let urlField = $state<HTMLInputElement>();
  let tokenField = $state<HTMLInputElement>();
  let issue = $state<{ field: 'ssh' | 'url' | 'token'; message: string } | null>(null);
  let showSaveError = $state(false);
  let advancedOpen = $state(false);
  let initialFocusApplied = false;

  const stored = $derived(settings?.saved ?? null);
  const canKeepPairingToken = $derived(
    stored?.mode === 'direct' && stored.hasPairingToken && sameEndpoint(stored.remoteUrl, remoteUrl.trim()),
  );

  // Rebuild the draft only when the native settings actually load or save.
  $effect(() => {
    if (settings === null) return;
    const current = settings.saved;
    mode = current?.mode ?? null;
    location = current === null ? null : current.mode === 'local' ? 'local' : 'remote';
    advancedOpen = current?.mode === 'external';
    sshTarget = current?.sshTarget ?? '';
    remoteUrl = current?.remoteUrl ?? '';
    pairingToken = '';
    confirmingDiscard = false;
    issue = null;
  });

  $effect(() => {
    if (initialFocusApplied) return;
    const ready = settings !== null || loadError !== null;
    const frame = requestAnimationFrame(() => {
      initialFocusApplied = ready;
      if (document.activeElement === document.body || document.activeElement === titleClose) {
        const group = locationGroup;
        (
          group?.querySelector<HTMLInputElement>('input:checked') ??
          group?.querySelector<HTMLInputElement>('input') ??
          titleClose
        )?.focus();
      }
    });
    return () => cancelAnimationFrame(frame);
  });

  $effect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !saving) onclose();
    };

    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  });

  function stateUrl(value: string): URL | null {
    try {
      const url = new URL(value);
      if (
        url.protocol === 'wss:' &&
        url.hostname &&
        !url.username &&
        !url.password &&
        !/[?#]/.test(value) &&
        url.pathname.endsWith('/state')
      ) {
        return url;
      }
    } catch {
      return null;
    }
    return null;
  }

  function sameEndpoint(previous: string, next: string): boolean {
    if (previous === next) return true;
    // Match native credential reuse for exact URLs and simple ASCII equivalents.
    const simple = /^[a-z0-9:/.[\]_-]+$/i;
    const requested = simple.test(next) ? stateUrl(next) : null;
    return requested !== null && simple.test(previous) && stateUrl(previous)?.href === requested.href;
  }

  function endpoint(summary: ConnectionSettings): string {
    return summary.mode === 'managed'
      ? summary.sshTarget
      : summary.mode === 'direct'
        ? summary.remoteUrl
        : '';
  }

  function edit(): void {
    confirmingDiscard = false;
    saved = false;
    issue = null;
    showSaveError = false;
  }

  function chooseLocation(next: 'local' | 'remote'): void {
    location = next;
    chooseMode(next === 'local' ? 'local' : null);
  }

  function chooseMode(nextMode: ConnectionMode | null): void {
    mode = nextMode;
    if (nextMode === 'external') advancedOpen = true;
    edit();
  }

  function editUrl(next: string): void {
    if (!sameEndpoint(remoteUrl.trim(), next.trim())) pairingToken = '';
    remoteUrl = next;
    edit();
  }

  function wrapFocus(event: KeyboardEvent): void {
    if (event.key !== 'Tab' || saving) return;
    const first = titleClose;
    const last = footerClose ?? first;
    if (event.shiftKey ? document.activeElement === first : document.activeElement === last) {
      event.preventDefault();
      (event.shiftKey ? last : first)?.focus();
    }
  }

  async function save(discardConfirmed = false): Promise<void> {
    if (
      loadError !== null ||
      settings === null ||
      mode === null ||
      saving ||
      (discardConfirmed && !confirmingDiscard)
    ) {
      return;
    }

    const target = sshTarget.trim();
    const url = remoteUrl.trim();
    if (mode === 'managed' && !target) {
      issue = { field: 'ssh', message: 'Enter your SSH target.' };
      sshField?.focus();
      return;
    }
    if (mode === 'direct') {
      if (!stateUrl(url)) {
        issue = {
          field: 'url',
          message: 'Enter a WSS state URL ending in /state, with no credentials, query, or fragment.',
        };
        urlField?.focus();
        return;
      }
      if (!pairingToken && !canKeepPairingToken) {
        issue = { field: 'token', message: 'Enter the pairing token for this endpoint.' };
        tokenField?.focus();
        return;
      }
      if (pairingToken && !/^[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$/.test(pairingToken)) {
        issue = { field: 'token', message: 'Enter a valid 43-character pairing token from the server.' };
        tokenField?.focus();
        return;
      }
    }

    if (stored?.mode === 'direct' && stored.hasPairingToken && mode !== 'direct' && !discardConfirmed) {
      if (!confirmingDiscard) {
        confirmingDiscard = true;
        saved = false;
        await tick();
        cancelDiscardButton?.focus();
      }
      return;
    }

    let update: ConnectionSettingsUpdate;
    if (mode === 'managed') {
      update = { mode, sshTarget: target };
    } else if (mode === 'direct') {
      update = pairingToken ? { mode, remoteUrl: url, pairingToken } : { mode, remoteUrl: url };
    } else {
      update = { mode };
    }

    saving = true;
    confirmingDiscard = false;
    saved = false;
    issue = null;
    showSaveError = true;

    try {
      saved = await onsave(update);
      if (saved) pairingToken = '';
    } finally {
      saving = false;
      await tick();
      if (saveButton?.isConnected && document.hasFocus() && document.activeElement === document.body) {
        saveButton.focus();
      }
    }
  }
</script>

<div
  class="connection-dialog manager-dialog"
  role="dialog"
  aria-modal="true"
  aria-labelledby="connection-dialog-title"
  tabindex="-1"
  onkeydown={wrapFocus}
>
  <header class="dialog-titlebar" data-tauri-drag-region>
    <div class="dialog-title" data-tauri-drag-region>
      <h2 id="connection-dialog-title">Connection</h2>
      <p>Changes take effect the next time you start Imp.</p>
    </div>

    <button
      bind:this={titleClose}
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
      <p class="loading" role="status">Loading connection settings…</p>
    {:else}
      <dl class="connection-summary">
        <div>
          <dt>In use</dt>
          <dd>
            {settings.running ? METHOD_NAMES[settings.running.mode] : 'No connection configured'}
            {#if settings.running && endpoint(settings.running)}
              <span>{endpoint(settings.running)}</span>
            {/if}
          </dd>
        </div>
        {#if settings.pendingRestart && stored}
          <div>
            <dt>Saved for next launch</dt>
            <dd>
              {METHOD_NAMES[stored.mode]}
              {#if endpoint(stored)}<span>{endpoint(stored)}</span>{/if}
              {#if settings.running?.mode === 'direct' && stored.mode === 'direct' && sameEndpoint(settings.running.remoteUrl, stored.remoteUrl)}
                <span>A different pairing token is saved.</span>
              {/if}
            </dd>
          </div>
        {/if}
      </dl>

      <form
        novalidate
        onsubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <fieldset bind:this={locationGroup} disabled={saving}>
          <legend>Where does your MUD client run?</legend>
          {#each LOCATION_CHOICES as option (option.value)}
            <label class="choice">
              <input
                type="radio"
                name="client-location"
                value={option.value}
                checked={location === option.value}
                onchange={() => chooseLocation(option.value)}
              />
              <span>{option.label}</span>
            </label>
          {/each}
        </fieldset>

        {#if location === 'remote'}
          <fieldset disabled={saving}>
            <legend>How should Imp connect?</legend>
            {#each REMOTE_CHOICES as option (option.value)}
              <label class="choice">
                <input
                  type="radio"
                  name="remote-method"
                  value={option.value}
                  checked={mode === option.value}
                  onchange={() => chooseMode(option.value)}
                />
                <span>{option.label} <small>{option.technical}</small></span>
              </label>
            {/each}
            <details
              bind:open={advancedOpen}
              ontoggle={(event) => {
                if (!event.currentTarget.open && mode === 'external' && !saving) chooseMode(null);
              }}
            >
              <summary
                aria-disabled={saving || undefined}
                tabindex={saving ? -1 : undefined}
                onclick={(event) => {
                  if (saving) event.preventDefault();
                }}>Advanced</summary
              >
              <label class="choice">
                <input
                  type="radio"
                  name="remote-method"
                  value="external"
                  checked={mode === 'external'}
                  onchange={() => chooseMode('external')}
                />
                <span>Use my own SSH tunnel <small>External SSH</small></span>
              </label>
            </details>
          </fieldset>
        {/if}

        {#if mode === 'external'}
          <p class="mode-help">
            Start your SSH tunnel before opening Imp. Imp uses the existing local endpoint and does not start
            or stop the tunnel.
          </p>
        {:else if mode === 'local'}
          <p class="mode-help">
            Use this for Mudlet or another supported MUD client running here. Save and restart Imp, then
            follow your MUD client's setup guide.
          </p>
        {:else if mode === 'managed'}
          <label>
            <span>SSH target</span>
            <input
              bind:this={sshField}
              bind:value={sshTarget}
              type="text"
              spellcheck="false"
              autocomplete="off"
              placeholder="avatar"
              required
              disabled={saving}
              aria-invalid={issue?.field === 'ssh' || undefined}
              aria-describedby={issue?.field === 'ssh' ? 'ssh-help connection-field-error' : 'ssh-help'}
              oninput={edit}
            />
          </label>

          <p id="ssh-help" class="mode-help">
            Enter an existing Host alias from your SSH configuration. Imp uses the system OpenSSH client.
          </p>
        {:else if mode === 'direct'}
          <label>
            <span>WSS state URL</span>
            <input
              bind:this={urlField}
              value={remoteUrl}
              type="url"
              spellcheck="false"
              autocomplete="off"
              placeholder="wss://imp.example/state"
              required
              disabled={saving}
              aria-invalid={issue?.field === 'url' || undefined}
              aria-describedby={issue?.field === 'url' ? 'wss-help connection-field-error' : 'wss-help'}
              oninput={(event) => editUrl(event.currentTarget.value)}
            />
          </label>

          <label>
            <span>Pairing token</span>
            <input
              bind:this={tokenField}
              bind:value={pairingToken}
              type="password"
              spellcheck="false"
              autocomplete="off"
              required={!canKeepPairingToken}
              disabled={saving}
              aria-invalid={issue?.field === 'token' || undefined}
              aria-describedby={issue?.field === 'token' ? 'token-help connection-field-error' : 'token-help'}
              oninput={edit}
            />
          </label>

          <p id="wss-help" class="mode-help">
            Get the WSS URL and token from <code>imp-direct-wss setup</code> on the server. Never append the token
            to the URL.
          </p>

          <p id="token-help" class="mode-help">
            {canKeepPairingToken
              ? 'Leave the token blank to keep your saved credential.'
              : 'Enter the pairing token for this endpoint.'}
          </p>
        {/if}

        {#if issue !== null}
          <p id="connection-field-error" class="error" role="alert">{issue.message}</p>
        {:else if showSaveError && saveError !== null}
          <p class="error" role="alert">{saveError}</p>
        {/if}

        {#if saved}
          <p class="saved-message" role="status">
            {settings.pendingRestart
              ? 'Saved. Restart Imp to use this connection.'
              : 'Connection settings saved.'}
          </p>
        {/if}

        {#if confirmingDiscard}
          <p id="discard-direct-warning" class="mode-help" role="alert">
            Saving this mode removes the locally stored Direct URL and pairing token. Cancel keeps your saved
            configuration and pending changes. Returning to Direct requires re-entering the URL and token; if
            the token is no longer available, obtain a new token by deliberately rotating the server
            credential. Changes take effect after restart.
          </p>
        {/if}

        <div class="form-actions">
          {#if confirmingDiscard}
            <button
              bind:this={cancelDiscardButton}
              class="manager-btn secondary"
              type="button"
              onclick={async () => {
                confirmingDiscard = false;
                await tick();
                saveButton?.focus();
              }}>Cancel</button
            >
            <button
              class="manager-btn danger"
              type="button"
              aria-describedby="discard-direct-warning"
              onclick={() => void save(true)}>Remove credentials and save</button
            >
          {:else}
            <button
              bind:this={saveButton}
              class="manager-btn primary"
              type="submit"
              disabled={saving || mode === null}
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
          {/if}
          <button
            bind:this={footerClose}
            class="manager-btn secondary"
            type="button"
            disabled={saving}
            onclick={onclose}
          >
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
  fieldset,
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

  .connection-summary {
    display: grid;
    gap: var(--space-3);
    margin: 0 0 var(--space-7);
    padding-bottom: var(--space-5);
    border-bottom: 1px solid var(--divider);
    font-size: var(--font-xs);
  }

  .connection-summary dt {
    color: var(--muted);
  }

  .connection-summary dd {
    margin: 0;
    overflow-wrap: anywhere;
  }

  .connection-summary dd span {
    display: block;
    color: var(--muted);
  }

  fieldset {
    margin: 0;
    padding: 0;
    border: 0;
  }

  legend {
    padding: 0;
    font-size: var(--font-xs);
    font-weight: 600;
  }

  .choice {
    grid-template-columns: auto minmax(0, 1fr);
    align-items: center;
    gap: var(--space-4);
    min-height: var(--manager-button-height);
    color: var(--text);
    cursor: pointer;
  }

  .choice small {
    color: var(--muted);
    font-size: var(--font-2xs);
  }

  .choice input {
    margin: 0;
    accent-color: var(--accent);
  }

  details {
    color: var(--muted);
    font-size: var(--font-xs);
  }

  summary {
    width: fit-content;
    padding-block: var(--space-2);
    cursor: pointer;
  }

  input:not([type='radio']) {
    width: 100%;
    min-width: 0;
    padding: var(--space-3) var(--space-4);
    border: 1px solid var(--input-border);
    border-radius: var(--radius);
    background: var(--input-bg);
    color: var(--text);
    font: inherit;
    user-select: text;
  }

  input:focus-visible,
  summary:focus-visible {
    outline: 2px solid var(--accent-focus);
    outline-offset: 1px;
  }

  button:disabled,
  fieldset:disabled,
  fieldset:disabled .choice {
    cursor: default;
  }

  button:disabled,
  fieldset:disabled {
    opacity: var(--opacity-disabled);
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
    color: var(--bad-text);
  }

  .saved-message {
    color: var(--good-text);
  }

  .form-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: end;
    gap: var(--space-4);
    padding-top: var(--space-1);
  }
</style>
