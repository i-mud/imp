<script lang="ts">
  import X from '@lucide/svelte/icons/x';
  import { onDestroy, tick, untrack } from 'svelte';

  import {
    cancelManagedConnection,
    checkManagedConnection,
    managedCheckPending,
    managedCheckRestartRequired,
    MANAGED_CHECK_RESTART_MESSAGE,
    RETIREMENT_UNCONFIRMED,
    type ConnectionMode,
    type ConnectionSettings,
    type ConnectionSettingsState,
    type ConnectionSettingsUpdate,
    type ManagedCheckDestination,
    type ManagedCheckResult,
  } from '../lib/tunnel.ts';
  import { parseConnectionCode } from '../lib/connection-code.ts';

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
  const SSH_USERNAME = /^[a-z0-9_][a-z0-9_.-]{0,63}$/i;
  const CHECK_MESSAGES = {
    invalid_destination: 'Check the server login or SSH alias and try again.',
    ssh_unavailable: 'OpenSSH is unavailable. Install or enable the OpenSSH client, then try again.',
    live: 'The remote Imp node reported live state during this check.',
    stale:
      'The remote Imp node reported stale state. Check your MUD client adapter and try again during play.',
    down: 'The remote Imp node reported its feed is down. Check that your MUD client adapter is running.',
    no_usable_state:
      'No usable state was received. Check that the remote Imp node and MUD client adapter are running.',
    host_verification: 'SSH could not verify the server. Verify its host key in OpenSSH before retrying.',
    authentication: 'SSH sign-in failed. Set up a key or SSH agent for sign-in without prompts.',
    forwarding_unavailable:
      'SSH could not reach the remote Imp port. Check that the node is running and SSH allows forwarding.',
    invalid_protocol:
      'The response was not a supported Imp state feed. Check the remote node and its version.',
    timeout: 'The check timed out. Check that the server and Imp node are reachable, then try again.',
    cancelled: 'Connection check cancelled.',
    connection_failed: 'Could not check the connection. Check your SSH setup and network, then try again.',
    busy: 'Another connection check is running. Try again after it finishes.',
  } satisfies Record<ManagedCheckResult, string>;

  let location = $state<'local' | 'remote' | null>(null);
  let mode = $state<ConnectionMode | null>(null);
  let sshTarget = $state('');
  let useSshAlias = $state(false);
  let sshHost = $state('');
  let sshUsername = $state('');
  let sshPort = $state('');
  let remoteUrl = $state('');
  let pairingToken = $state('');
  let connectionCode = $state('');
  let manualEntry = $state(false);
  let codeField = $state<HTMLInputElement>();
  let saving = $state(false);
  let saved = $state(false);
  let activeCheck: number | null = null;
  let checking = $state(false);
  let cancelRequested = $state(false);
  let checkResult = $state<ManagedCheckResult | null>(null);
  let checkButton = $state<HTMLButtonElement>();
  let cancelCheckButton = $state<HTMLButtonElement>();
  let confirmingDiscard = $state(false);
  let cancelDiscardButton = $state<HTMLButtonElement>();
  let saveButton = $state<HTMLButtonElement>();
  let locationGroup = $state<HTMLFieldSetElement>();
  let titleClose = $state<HTMLButtonElement>();
  let footerClose = $state<HTMLButtonElement>();
  let sshField = $state<HTMLInputElement>();
  let serverField = $state<HTMLInputElement>();
  let usernameField = $state<HTMLInputElement>();
  let portField = $state<HTMLInputElement>();
  let urlField = $state<HTMLInputElement>();
  let tokenField = $state<HTMLInputElement>();
  let issue = $state<{
    field: 'ssh' | 'server' | 'username' | 'port' | 'url' | 'token' | 'code';
    message: string;
  } | null>(null);
  let showSaveError = $state(false);
  let advancedOpen = $state(false);
  let initialFocusApplied = false;

  const stored = $derived(settings?.saved ?? null);
  const canKeepPairingToken = $derived(
    stored?.mode === 'direct' && stored.hasPairingToken && sameEndpoint(stored.remoteUrl, remoteUrl.trim()),
  );
  const codeDestination = $derived(parseConnectionCode(connectionCode));
  const checkBlocked = $derived(
    checking || cancelRequested || $managedCheckPending || $managedCheckRestartRequired,
  );
  const checkStatus = $derived(
    $managedCheckRestartRequired
      ? ''
      : checking
        ? 'Checking connection…'
        : cancelRequested
          ? 'Cancelling check…'
          : $managedCheckPending
            ? 'Finishing previous check…'
            : checkResult === null
              ? ''
              : CHECK_MESSAGES[checkResult],
  );

  // Rebuild the draft only when the native settings actually load or save.
  $effect(() => {
    untrack(invalidateCheck);
    if (settings === null) return;
    const current = settings.saved;
    mode = current?.mode ?? null;
    location = current === null ? null : current.mode === 'local' ? 'local' : 'remote';
    advancedOpen = current?.mode === 'external';
    const destination = current?.sshDestination;
    const target = current?.mode === 'managed' ? current.sshTarget : '';
    sshTarget = target;
    useSshAlias = target !== '' && !destination;
    sshHost = destination?.host ?? '';
    sshUsername = destination?.username ?? '';
    sshPort = destination?.port?.toString() ?? '';
    remoteUrl = current?.remoteUrl ?? '';
    pairingToken = '';
    connectionCode = '';
    manualEntry = current?.mode === 'direct';
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
      if (event.key === 'Escape' && !saving) close();
    };

    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  });

  onDestroy(invalidateCheck);

  function close(): void {
    invalidateCheck();
    onclose();
  }

  function invalidateCheck(): void {
    const requestId = activeCheck;
    if (requestId !== null && !cancelRequested) void cancelManagedConnection(requestId).catch(() => {});
    activeCheck = null;
    checking = false;
    cancelRequested = false;
    checkResult = null;
  }

  function cancelCheck(): void {
    if (activeCheck === null || cancelRequested) return;
    if (document.activeElement === cancelCheckButton) checkButton?.focus();
    checking = false;
    cancelRequested = true;
    void cancelManagedConnection(activeCheck).catch(() => {});
  }

  async function checkConnection(): Promise<void> {
    if (mode !== 'managed' || saving || checkBlocked) return;
    issue = null;
    checkResult = null;
    const destination = managedDestination(true);
    if (!destination) return;

    try {
      const request = checkManagedConnection(destination);
      if (request === null) {
        checkResult = 'ssh_unavailable';
        return;
      }
      activeCheck = request.requestId;
      checking = true;
      const result = await request.result;
      if (activeCheck !== request.requestId) return;

      if (document.activeElement === cancelCheckButton) checkButton?.focus();
      activeCheck = null;
      checking = false;
      if (result !== RETIREMENT_UNCONFIRMED && (!cancelRequested || result === 'cancelled')) {
        checkResult = Object.hasOwn(CHECK_MESSAGES, result) ? result : 'connection_failed';
      }
      cancelRequested = false;
    } catch {
      checkResult = 'connection_failed';
    }
  }

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

  function validSshHost(host: string): boolean {
    if (host.includes(':')) {
      if (!/^[a-f0-9:.]+$/i.test(host)) return false;
      // Use only the URL parser's IPv6 grammar; do not apply its IPv4/DNS normalization.
      try {
        return new URL(`http://[${host}]/`).hostname !== '';
      } catch {
        return false;
      }
    }

    if (/^(?:(?:[0-9]+|0x[0-9a-f]*)\.)*(?:[0-9]+|0x[0-9a-f]*)\.?$/i.test(host)) {
      const parts = host.split('.');
      return (
        parts.length === 4 &&
        parts.every((part) => /^(?:0|[1-9][0-9]{0,2})$/.test(part) && Number(part) <= 255)
      );
    }

    const name = host.endsWith('.') ? host.slice(0, -1) : host;
    return (
      name.length <= 253 &&
      name.split('.').every((label) => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(label))
    );
  }

  function validCheckSshTarget(target: string): boolean {
    if (/\s/.test(target)) return false;
    const at = target.indexOf('@');
    const host = at < 0 ? target : target.slice(at + 1);
    return (
      (at < 0 || SSH_USERNAME.test(target.slice(0, at))) &&
      (/^[a-z0-9_][a-z0-9_.-]{0,252}$/i.test(host) || (host.includes(':') && validSshHost(host)))
    );
  }

  function managedDestination(checking = false): ManagedCheckDestination | null {
    if (useSshAlias) {
      const target = sshTarget.trim();
      if (!target || (checking && !validCheckSshTarget(target))) {
        issue = {
          field: 'ssh',
          message: target
            ? 'To check this connection, enter an SSH alias or host with an optional username and no port.'
            : 'Enter your SSH target.',
        };
        sshField?.focus();
        return null;
      }
      return { sshTarget: target };
    }

    const host = sshHost.replace(/^ +| +$/g, '');
    const username = sshUsername.replace(/^ +| +$/g, '');
    const port = sshPort.replace(/^ +| +$/g, '');
    if (!validSshHost(host)) {
      issue = { field: 'server', message: 'Enter a server hostname or IP address.' };
      serverField?.focus();
      return null;
    }
    if (!SSH_USERNAME.test(username)) {
      issue = { field: 'username', message: 'Enter a valid SSH username.' };
      usernameField?.focus();
      return null;
    }
    const portNumber = Number(port);
    if (port && (!/^[0-9]+$/.test(port) || portNumber < 1 || portNumber > 65535)) {
      issue = { field: 'port', message: 'Enter a port from 1 to 65535.' };
      portField?.focus();
      return null;
    }
    return { sshDestination: port ? { host, username, port: portNumber } : { host, username } };
  }

  function endpoint(summary: ConnectionSettings): string {
    if (summary.mode === 'managed') {
      const destination = summary.sshDestination;
      if (!destination) return summary.sshTarget;
      const { host, username, port } = destination;
      const address = host.includes(':') ? `[${host}]` : host;
      return `${username}@${address}${port === undefined ? '' : `:${port}`}`;
    }
    return summary.mode === 'direct' ? summary.remoteUrl : '';
  }

  function edit(): void {
    invalidateCheck();
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
    if (nextMode !== 'direct') connectionCode = '';
    if (nextMode === 'external') advancedOpen = true;
    edit();
  }

  async function switchSshRepresentation(): Promise<void> {
    useSshAlias = !useSshAlias;
    edit();
    await tick();
    (useSshAlias ? sshField : serverField)?.focus();
  }

  function editUrl(next: string): void {
    if (!sameEndpoint(remoteUrl.trim(), next.trim())) pairingToken = '';
    remoteUrl = next;
    edit();
  }

  async function toggleManualEntry(): Promise<void> {
    manualEntry = !manualEntry;
    connectionCode = '';
    pairingToken = '';
    edit();
    await tick();
    (manualEntry ? urlField : codeField)?.focus();
  }

  // Browsers strip CR/LF from single-line inputs, which would silently repair pasted data.
  // Validate the pasted text itself and replace the field with it only when it is a valid code.
  function pasteCode(event: ClipboardEvent): void {
    event.preventDefault();
    const text = event.clipboardData?.getData('text/plain') ?? '';
    edit();
    if (parseConnectionCode(text)) {
      connectionCode = text.replace(/^[ \t\r\n]+|[ \t\r\n]+$/g, '');
      return;
    }
    connectionCode = '';
    issue = {
      field: 'code',
      message:
        'This connection code is not valid. Copy it again from the server, or enter the URL and token manually.',
    };
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
      checkBlocked ||
      (discardConfirmed && !confirmingDiscard)
    ) {
      return;
    }

    let update: ConnectionSettingsUpdate;
    if (mode === 'managed') {
      const destination = managedDestination();
      if (!destination) return;
      update = { mode, ...destination };
    } else if (mode === 'direct' && !manualEntry) {
      if (!codeDestination) {
        issue = {
          field: 'code',
          message: connectionCode
            ? 'This connection code is not valid. Copy it again from the server, or enter the URL and token manually.'
            : 'Paste the connection code from the server, or enter the URL and token manually.',
        };
        codeField?.focus();
        return;
      }
      update = { mode, connectionCode };
    } else if (mode === 'direct') {
      const url = remoteUrl.trim();
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
      update = pairingToken ? { mode, remoteUrl: url, pairingToken } : { mode, remoteUrl: url };
    } else {
      update = { mode };
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

    saving = true;
    confirmingDiscard = false;
    saved = false;
    issue = null;
    showSaveError = true;

    try {
      saved = await onsave(update);
      if (saved) {
        pairingToken = '';
        connectionCode = '';
      }
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
      onclick={close}><X size={16} /></button
    >
  </header>

  <div class="dialog-content">
    {#if $managedCheckRestartRequired}
      <p id="managed-check-restart-required" class="error" role="alert">{MANAGED_CHECK_RESTART_MESSAGE}</p>
    {/if}
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
          {#if useSshAlias}
            <label>
              <span>SSH target</span>
              <input
                bind:this={sshField}
                bind:value={sshTarget}
                name="ssh-target"
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
            <p id="ssh-help" class="mode-help">Enter an existing Host alias from your SSH configuration.</p>
          {:else}
            <label>
              <span>Server</span>
              <input
                bind:this={serverField}
                bind:value={sshHost}
                name="ssh-host"
                type="text"
                spellcheck="false"
                autocomplete="off"
                placeholder="mud.example.org"
                required
                disabled={saving}
                aria-invalid={issue?.field === 'server' || undefined}
                aria-describedby={issue?.field === 'server' ? 'connection-field-error' : undefined}
                oninput={edit}
              />
            </label>
            <div class="ssh-login">
              <label>
                <span>Username</span>
                <input
                  bind:this={usernameField}
                  bind:value={sshUsername}
                  name="ssh-username"
                  type="text"
                  spellcheck="false"
                  autocomplete="off"
                  required
                  disabled={saving}
                  aria-invalid={issue?.field === 'username' || undefined}
                  aria-describedby={issue?.field === 'username' ? 'connection-field-error' : undefined}
                  oninput={edit}
                />
              </label>
              <label>
                <span>Port (optional)</span>
                <input
                  bind:this={portField}
                  bind:value={sshPort}
                  name="ssh-port"
                  type="text"
                  inputmode="numeric"
                  autocomplete="off"
                  disabled={saving}
                  aria-invalid={issue?.field === 'port' || undefined}
                  aria-describedby={issue?.field === 'port'
                    ? 'ssh-port-help connection-field-error'
                    : 'ssh-port-help'}
                  oninput={edit}
                />
              </label>
            </div>
            <p id="ssh-port-help" class="mode-help">Leave port blank to use your OpenSSH configuration.</p>
          {/if}
          <button
            class="manager-btn secondary ssh-representation"
            name="ssh-representation"
            type="button"
            disabled={saving}
            onclick={switchSshRepresentation}
          >
            {useSshAlias ? 'Use server and username' : 'Use an existing SSH alias'}
          </button>
          <div>
            <div class="form-actions check-actions">
              <button
                bind:this={checkButton}
                class="manager-btn secondary"
                name="check-managed-connection"
                type="button"
                disabled={saving}
                aria-disabled={checkBlocked || undefined}
                aria-describedby={$managedCheckRestartRequired
                  ? 'managed-check-help managed-check-restart-required'
                  : 'managed-check-help'}
                onclick={() => void checkConnection()}>Check connection</button
              >
              {#if checking}
                <button
                  bind:this={cancelCheckButton}
                  class="manager-btn secondary"
                  name="cancel-managed-check"
                  type="button"
                  onclick={cancelCheck}>Cancel check</button
                >
              {/if}
            </div>
            <p id="managed-check-help" class="mode-help">
              Checks the remote Imp node without saving. It does not verify your character, updates during
              play, or actions.
            </p>
            <p id="managed-check-status" role="status" class="mode-help">{checkStatus}</p>
          </div>
        {:else if mode === 'direct'}
          {#if manualEntry}
            <label>
              <span>WSS state URL</span>
              <input
                bind:this={urlField}
                value={remoteUrl}
                name="direct-url"
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
                name="direct-token"
                type="password"
                spellcheck="false"
                autocomplete="off"
                required={!canKeepPairingToken}
                disabled={saving}
                aria-invalid={issue?.field === 'token' || undefined}
                aria-describedby={issue?.field === 'token'
                  ? 'token-help connection-field-error'
                  : 'token-help'}
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
          {:else}
            <label>
              <span>Connection code</span>
              <input
                bind:this={codeField}
                bind:value={connectionCode}
                name="connection-code"
                type="password"
                spellcheck="false"
                autocomplete="off"
                autocapitalize="none"
                disabled={saving}
                aria-invalid={issue?.field === 'code' || undefined}
                aria-describedby={issue?.field === 'code'
                  ? 'connection-code-help connection-field-error'
                  : 'connection-code-help'}
                oninput={edit}
                onpaste={pasteCode}
              />
            </label>
            <p id="connection-code-help" class="mode-help">
              Paste the code printed by <code>imp-direct-wss setup</code> on the server. It contains your pairing
              token, so it is hidden.
            </p>
            <p class="mode-help destination" role="status" data-connection-code-preview>
              {#if codeDestination}Imp will connect to <code>{codeDestination}</code>{/if}
            </p>
          {/if}
          <button
            class="manager-btn secondary"
            name="direct-manual-entry"
            type="button"
            disabled={saving}
            onclick={toggleManualEntry}
          >
            {manualEntry ? 'Use a connection code instead' : 'Enter URL and token manually'}
          </button>
        {/if}

        {#if mode !== 'managed' && $managedCheckPending}
          <p class="mode-help" role="status">{checkStatus}</p>
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
              disabled={checkBlocked}
              onclick={() => void save(true)}>Remove credentials and save</button
            >
          {:else}
            <button
              bind:this={saveButton}
              class="manager-btn primary"
              type="submit"
              disabled={saving || mode === null || checkBlocked}
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
          {/if}
          <button
            bind:this={footerClose}
            class="manager-btn secondary"
            type="button"
            disabled={saving}
            onclick={close}
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

  .ssh-login {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 5.5rem);
    gap: var(--space-4);
  }

  .ssh-representation {
    justify-self: start;
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

  .destination:empty {
    display: none;
  }

  .form-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: end;
    gap: var(--space-4);
    padding-top: var(--space-1);
  }

  .check-actions {
    justify-content: start;
    margin-bottom: var(--space-4);
  }

  .check-actions button[aria-disabled='true'] {
    opacity: var(--opacity-disabled);
    cursor: default;
  }

  #managed-check-status:not(:empty) {
    margin-top: var(--space-4);
  }
</style>
