<script lang="ts">
  import { onMount, tick } from 'svelte';

  import ActionBar from './ActionBar.svelte';
  import ActionDialog from './ActionDialog.svelte';
  import AlertDialog from './AlertDialog.svelte';
  import AlertQuickList from './AlertQuickList.svelte';
  import AlertsMenu from './AlertsMenu.svelte';
  import ActionsMenu from './ActionsMenu.svelte';
  import CompactVital from './CompactVital.svelte';
  import ConnectionDialog from './ConnectionDialog.svelte';
  import ExitConfirmation from './ExitConfirmation.svelte';
  import SettingsMenu from './SettingsMenu.svelte';
  import SettingsPanel from './SettingsPanel.svelte';
  import StatusIndicator from './StatusIndicator.svelte';
  import TargetPanel from './TargetPanel.svelte';
  import VitalBar from './VitalBar.svelte';
  import {
    commitActionDefinitions,
    loadActionDefinitions,
    type ActionDefinition,
  } from '../lib/action/definitions.ts';
  import { invokeAction, type ActionInvocationState } from '../lib/action/invocation.ts';
  import type { ActionSink } from '../lib/action/types.ts';
  import {
    commitAlertDefinitions,
    loadAlertDefinitions,
    type AlertDefinition,
  } from '../lib/alerts/definitions.ts';
  import { dispatchAlert, textAlertEvent, vitalAlertEvent } from '../lib/alerts/effects.ts';
  import { DESKTOP_ALERT_EFFECTS } from '../lib/alerts/native.ts';
  import { evaluateTextAlerts, evaluateVitalAlerts } from '../lib/alerts/runtime.ts';
  import type { VitalAlertState } from '../lib/alerts/vitals.ts';
  import { freshnessOf, type HudModel } from '../lib/hud/model.ts';
  import type { TextSourceListener } from '../lib/hud/store.svelte.ts';
  import {
    loadConnectionSettings,
    saveConnectionSettings,
    type ConnectionSettingsState,
    type ConnectionSettingsUpdate,
  } from '../lib/tunnel.ts';
  import {
    loadDisplayMode,
    saveDisplayMode,
    statusIndicatorOf,
    statusLabelOf,
    type DisplayMode,
  } from '../lib/hud/presentation.ts';
  import {
    actionDialogWindowSize,
    compactPanelWindowSize,
    compactWindowSize,
    EXPANDED_WINDOW_WIDTH,
    resizeHudWindow,
  } from '../lib/window.ts';
  import { applyTheme, loadTheme, saveTheme, type ThemePreference } from '../lib/hud/theme.ts';
  import { loadAppVersion } from '../lib/version.ts';
  import X from '@lucide/svelte/icons/x';

  type Panel = 'settings' | 'actions' | 'alerts';
  type ManagerOrigin = 'footer-actions' | 'actions' | 'alerts';
  type HudProps =
    | {
        setupState?: undefined;
        model: HudModel;
        actionSink: ActionSink;
        subscribeText: (listener: TextSourceListener) => () => void;
      }
    | {
        setupState: 'unconfigured' | 'unavailable';
        model?: never;
        actionSink?: never;
        subscribeText?: never;
      };

  const ACTION_SUCCESS_MS = 3000;
  const ACTION_FADE_MS = 150;
  const SETUP_DISMISSED_STORAGE_KEY = 'imp.connection-setup-dismissed';
  const CONNECTION_FILE_UNREADABLE =
    'Imp could not read tunnel.json. Restore a valid file or move it aside, then restart Imp.';
  const CONNECTION_FILE_CHANGED = 'Connection settings changed on disk. Restart Imp before saving again.';

  let { model, actionSink, subscribeText, setupState }: HudProps = $props();
  let setupDismissed = $state(loadSetupDismissed());
  let setupPendingRestart = $state(false);
  let displayMode = $state(loadDisplayMode());
  let theme = $state(loadTheme());
  let alertDefinitions = $state<AlertDefinition[]>(loadAlertDefinitions());
  let actions = $state<ActionDefinition[]>(loadActionDefinitions());
  let appVersion = $state<string | null>(null);

  let panel = $state<HTMLElement>();
  let compactRow = $state<HTMLElement>();
  let compactPanel = $state<HTMLDivElement>();
  let compactControls = $state<HTMLElement>();
  let openPanel = $state<Panel | null>(null);
  let exitInvoker = $state<HTMLButtonElement | null>(null);
  let managerOrigin = $state<ManagerOrigin | null>(null);
  const actionDialogOpen = $derived(managerOrigin === 'footer-actions' || managerOrigin === 'actions');
  let connectionDialogOpen = $state(false);
  let actionDialogWidth = $state<number>(EXPANDED_WINDOW_WIDTH);
  let actionSaveError = $state<string | null>(null);
  let alertSaveError = $state<string | null>(null);
  let connectionSettings = $state<ConnectionSettingsState | null>(null);
  let connectionLoadError = $state<string | null>(null);
  let connectionSaveError = $state<string | null>(null);
  let connectionDialogInvoker: HTMLButtonElement | null = null;
  const actionInvocation = $state<ActionInvocationState>({
    pendingActionId: null,
    feedback: null,
  });
  let fadingFeedback = $state.raw<ActionInvocationState['feedback']>(null);
  const vitalAlertMemory: { current: Map<string, VitalAlertState> } = {
    current: new Map(),
  };

  onMount(() =>
    subscribeText?.((event) => {
      for (const trigger of evaluateTextAlerts(alertDefinitions, event.text)) {
        void dispatchAlert(textAlertEvent(trigger.definition), trigger.definition, DESKTOP_ALERT_EFFECTS);
      }
    }),
  );

  onMount(() => {
    let active = true;

    void loadAppVersion()
      .then((version) => {
        if (active) appVersion = version;
      })
      .catch(() => {
        if (active) appVersion = null;
      });

    return () => {
      active = false;
    };
  });

  const isFresh = $derived(model !== undefined && freshnessOf(model) === 'fresh' && model.hasData);
  const status = $derived(model === undefined ? 'down' : statusIndicatorOf(model));
  const statusLabel = $derived(model === undefined ? '' : statusLabelOf(model));
  const character = $derived(model?.state.character ?? null);
  const target = $derived(model?.state.target ?? null);
  const characterName = $derived(character?.name ?? 'Imp');
  const showMana = $derived(character?.mana?.max !== 0);
  const showSetupPrompt = $derived(setupState === 'unconfigured' && !setupDismissed && !setupPendingRestart);

  function loadSetupDismissed(): boolean {
    try {
      return globalThis.localStorage?.getItem(SETUP_DISMISSED_STORAGE_KEY) === 'true';
    } catch {
      return false;
    }
  }

  function dismissSetup(): void {
    setupDismissed = true;
    try {
      globalThis.localStorage?.setItem(SETUP_DISMISSED_STORAGE_KEY, 'true');
    } catch {
      // Local UI preferences must not interfere with HUD rendering.
    }
    void focusPanelControl('[aria-label="Settings"]');
  }

  function setDisplayMode(mode: DisplayMode, keyboard: boolean): void {
    openPanel = keyboard ? 'settings' : null;
    displayMode = mode;
    saveDisplayMode(mode);
    // Keyboard selection remounts Settings in the other layout; keep its radio focus.
    if (keyboard) {
      void focusPanelControl('input[name="display-mode"]:checked');
    }
  }

  function setAlertDefinitions(nextDefinitions: AlertDefinition[]): boolean {
    if (
      !commitAlertDefinitions(nextDefinitions, (committed) => {
        alertDefinitions = committed;
      })
    ) {
      alertSaveError = 'Could not save alerts locally.';
      return false;
    }

    alertSaveError = null;
    return true;
  }

  function setAlertDefinitionEnabled(id: string, enabled: boolean): boolean {
    return setAlertDefinitions(
      alertDefinitions.map((definition) => (definition.id === id ? { ...definition, enabled } : definition)),
    );
  }

  function setTheme(preference: ThemePreference): void {
    theme = preference;
    saveTheme(preference);
  }

  $effect(() => applyTheme(theme));

  function setActions(nextActions: ActionDefinition[]): boolean {
    if (
      !commitActionDefinitions(nextActions, (committed) => {
        actions = committed;
      })
    ) {
      actionSaveError = 'Could not save actions locally.';
      return false;
    }

    actionSaveError = null;
    return true;
  }

  function setManagerWidth(): void {
    if (displayMode === 'compact') {
      const row = compactRow;
      const hudPanel = panel;
      if (row !== undefined && hudPanel !== undefined) {
        const rowBounds = row.getBoundingClientRect();
        const frameWidth = hudPanel.offsetWidth - hudPanel.clientWidth;
        actionDialogWidth = compactWindowSize(rowBounds.width + frameWidth, rowBounds.height).width;
      }
    } else {
      actionDialogWidth = EXPANDED_WINDOW_WIDTH;
    }
  }

  async function focusPanelControl(selector = '.manage-btn'): Promise<void> {
    const expectedPanel = openPanel;
    await tick();
    if (openPanel !== expectedPanel || exitInvoker !== null) return;
    panel?.querySelector<HTMLElement>(selector)?.focus({ preventScroll: true });
  }

  function togglePanel(next: Panel, keyboard: boolean): void {
    if (openPanel === next) {
      dismissPanel();
      return;
    }
    openPanel = next;
    if (keyboard) {
      void focusPanelControl(next === 'settings' ? 'input[name="display-mode"]:checked' : '.manage-btn');
    }
  }

  function openManager(origin: ManagerOrigin): void {
    setManagerWidth();
    if (origin !== 'alerts') actionSaveError = null;
    openPanel = null;
    managerOrigin = origin;
  }

  function closeManager(): void {
    const origin = managerOrigin;
    if (origin === null) return;

    managerOrigin = null;
    openPanel = origin === 'footer-actions' ? null : origin;
    void focusPanelControl();
  }

  async function openConnectionDialog(invoker: HTMLButtonElement): Promise<void> {
    setManagerWidth();

    connectionDialogInvoker = invoker;
    connectionSettings = null;
    connectionLoadError = null;
    connectionSaveError = null;
    openPanel = null;
    connectionDialogOpen = true;

    try {
      const loaded = await loadConnectionSettings();
      if (connectionDialogInvoker !== invoker) return;

      if (loaded === null) {
        connectionLoadError = 'Connection settings are available only in the native Imp application.';
        return;
      }

      connectionSettings = loaded;
      setupPendingRestart = loaded.pendingRestart;
    } catch (error) {
      if (connectionDialogInvoker === invoker) {
        connectionLoadError =
          error === CONNECTION_FILE_UNREADABLE
            ? error
            : 'Could not load native connection settings. Restart Imp and try again.';
      }
    }
  }

  function closeConnectionDialog(): void {
    const enteredFromSettings = connectionDialogInvoker?.hasAttribute('data-connection-manager-trigger');
    connectionDialogInvoker = null;
    connectionDialogOpen = false;
    openPanel = enteredFromSettings ? 'settings' : null;

    void focusPanelControl(
      enteredFromSettings
        ? '[data-connection-manager-trigger]'
        : showSetupPrompt || setupState === 'unavailable'
          ? '[data-connection-setup-trigger]'
          : '[aria-label="Settings"]',
    );
  }

  async function setConnectionSettings(update: ConnectionSettingsUpdate): Promise<boolean> {
    connectionSaveError = null;

    try {
      connectionSettings = await saveConnectionSettings(update);
      setupPendingRestart = connectionSettings.pendingRestart;
      return true;
    } catch (error) {
      switch (error) {
        case CONNECTION_FILE_UNREADABLE:
        case CONNECTION_FILE_CHANGED:
          connectionSaveError = error;
          break;
        case 'managed mode requires a non-empty sshTarget':
          connectionSaveError = 'Enter your SSH target.';
          break;
        case 'direct pairing token is required when changing the remote URL':
          connectionSaveError = 'Enter the pairing token for this endpoint or restore the saved URL.';
          break;
        case 'direct pairing token is required':
        case 'direct pairing token is invalid':
        case 'direct pairing token must be canonical 256-bit base64url':
          connectionSaveError = 'Enter a valid 43-character pairing token from the server.';
          break;
        case 'direct remote URL is invalid':
        case 'direct remote URL must use wss':
        case 'direct remote URL must include a host':
        case 'direct remote URL must not contain credentials':
        case 'direct remote URL must not contain query or fragment data':
        case 'direct remote URL must end at /state':
          connectionSaveError =
            'Enter a WSS state URL ending in /state, with no credentials, query, or fragment.';
          break;
        default:
          connectionSaveError = 'Could not save native connection settings.';
      }
      return false;
    }
  }

  function invokeDefinition(definition: ActionDefinition): void {
    if (model === undefined || actionSink === undefined) return;
    const context = model.context;
    void invokeAction(actionInvocation, actionSink, context, definition);
  }

  $effect(() => {
    const feedback = actionInvocation.feedback;
    if (feedback?.status !== 'forwarded') return;

    const fade = setTimeout(() => {
      if (actionInvocation.feedback === feedback) fadingFeedback = feedback;
    }, ACTION_SUCCESS_MS - ACTION_FADE_MS);
    const dismiss = setTimeout(() => {
      if (actionInvocation.feedback === feedback) actionInvocation.feedback = null;
    }, ACTION_SUCCESS_MS);

    return () => {
      clearTimeout(fade);
      clearTimeout(dismiss);
      fadingFeedback = null;
    };
  });

  function dismissPanel(): void {
    const dismissedPanel = openPanel;
    if (dismissedPanel === null) return;

    openPanel = null;
    void focusPanelControl(`[aria-controls="${displayMode}-${dismissedPanel}-panel"]`);
  }

  $effect(() => {
    if (openPanel === null) return;
    const isCompact = displayMode === 'compact';
    const attachedPanel = compactPanel;

    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (exitInvoker !== null || attachedPanel === undefined) return;
      const target = event.target as Node;
      // Let shortcut and Close Imp clicks act on the current surface.
      if (!attachedPanel.contains(target) && compactControls?.contains(target) !== true) {
        openPanel = null;
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented || event.repeat) return;
      if (exitInvoker !== null || openPanel === null) return;
      event.preventDefault();
      dismissPanel();
    };
    if (isCompact) window.addEventListener('pointerdown', closeOnOutsidePointer);
    window.addEventListener('keydown', closeOnEscape);
    return () => {
      if (isCompact) window.removeEventListener('pointerdown', closeOnOutsidePointer);
      window.removeEventListener('keydown', closeOnEscape);
    };
  });

  $effect(() => {
    const evaluation = evaluateVitalAlerts(
      vitalAlertMemory.current,
      alertDefinitions,
      character,
      isFresh,
      target,
    );
    vitalAlertMemory.current = evaluation.states;

    for (const trigger of evaluation.triggered) {
      void dispatchAlert(
        vitalAlertEvent(trigger.definition, trigger.subjectLabel, trigger.percent),
        trigger.definition,
        DESKTOP_ALERT_EFFECTS,
      );
    }
  });

  $effect(() => {
    if (managerOrigin !== null || connectionDialogOpen) {
      const observer = new ResizeObserver(() => resize());

      const resize = () => {
        const dialog = document.querySelector<HTMLElement>('.manager-dialog');
        if (dialog === null) return;

        const bounds = dialog.getBoundingClientRect();
        resizeHudWindow(actionDialogWindowSize(actionDialogWidth, bounds.height));
      };

      const frame = requestAnimationFrame(() => {
        const dialog = document.querySelector<HTMLElement>('.manager-dialog');
        if (dialog === null) return;

        observer.observe(dialog);
        resize();
      });

      return () => {
        cancelAnimationFrame(frame);
        observer.disconnect();
      };
    }

    if (displayMode === 'expanded') {
      if (openPanel !== null) {
        const hudPanel = panel;
        if (hudPanel === undefined) return;

        const body = hudPanel.querySelector<HTMLElement>(`.${openPanel}-body`);
        const titlebar = hudPanel.querySelector<HTMLElement>('.titlebar');
        if (body === null || titlebar === null) return;

        const resize = () => {
          const titlebarHeight = titlebar.getBoundingClientRect().height;
          const bodyHeight = body.getBoundingClientRect().height;
          const frameHeight = hudPanel.offsetHeight - hudPanel.clientHeight;

          resizeHudWindow({
            width: EXPANDED_WINDOW_WIDTH,
            height: Math.ceil(titlebarHeight + bodyHeight + frameHeight),
          });
        };

        const frame = requestAnimationFrame(resize);
        const observer = new ResizeObserver(resize);
        observer.observe(body);
        observer.observe(titlebar);

        return () => {
          cancelAnimationFrame(frame);
          observer.disconnect();
        };
      }
      const hudPanel = panel;
      if (hudPanel === undefined) return;

      let frame: number | undefined;
      let lastHeight: number | undefined;
      const observer = new ResizeObserver(() => {
        if (frame !== undefined) return;
        frame = requestAnimationFrame(() => {
          frame = undefined;
          const height = Math.ceil(hudPanel.getBoundingClientRect().height);
          if (height === lastHeight) return;
          lastHeight = height;
          resizeHudWindow({ width: EXPANDED_WINDOW_WIDTH, height });
        });
      });
      observer.observe(hudPanel, { box: 'border-box' });
      return () => {
        observer.disconnect();
        if (frame !== undefined) cancelAnimationFrame(frame);
      };
    }

    const row = compactRow;
    const hudPanel = panel;
    const attachedPanel = openPanel === null ? undefined : compactPanel;
    if (row === undefined || hudPanel === undefined) return;

    const resize = () => {
      const rowBounds = row.getBoundingClientRect();
      const panelBounds = attachedPanel?.getBoundingClientRect();
      const left = Math.min(rowBounds.left, panelBounds?.left ?? rowBounds.left);
      const right = Math.max(rowBounds.right, panelBounds?.right ?? rowBounds.right);
      const bottom = Math.max(rowBounds.bottom, panelBounds?.bottom ?? rowBounds.bottom);
      const frameWidth = hudPanel.offsetWidth - hudPanel.clientWidth;
      const frameHeight = hudPanel.offsetHeight - hudPanel.clientHeight;
      const contentWidth = right - left + frameWidth;
      const contentHeight = bottom - rowBounds.top + frameHeight;
      resizeHudWindow(
        attachedPanel === undefined
          ? compactWindowSize(contentWidth, contentHeight)
          : compactPanelWindowSize(contentWidth, contentHeight),
      );
    };
    const frame = requestAnimationFrame(resize);
    const observer = new ResizeObserver(resize);

    observer.observe(row);
    if (attachedPanel !== undefined) observer.observe(attachedPanel);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  });
</script>

{#snippet panelControls()}
  <AlertsMenu
    controls={`${displayMode}-alerts-panel`}
    expanded={openPanel === 'alerts'}
    ontoggle={(keyboard) => togglePanel('alerts', keyboard)}
  />
  <ActionsMenu
    controls={`${displayMode}-actions-panel`}
    expanded={openPanel === 'actions'}
    ontoggle={(keyboard) => togglePanel('actions', keyboard)}
  />
  <SettingsMenu
    controls={`${displayMode}-settings-panel`}
    expanded={openPanel === 'settings'}
    ontoggle={(keyboard) => togglePanel('settings', keyboard)}
  />
  <button
    class="close icon-btn"
    type="button"
    aria-label="Close Imp"
    aria-haspopup="dialog"
    onkeydown={(event) => {
      if (event.repeat && (event.key === 'Enter' || event.key === ' ')) event.preventDefault();
    }}
    onclick={(event) => {
      event.stopPropagation();
      if (event.detail > 1) return;
      exitInvoker = event.currentTarget;
    }}><X size={18} /></button
  >
{/snippet}

{#snippet connectionSetup()}
  <section class="empty-state" inert={exitInvoker !== null}>
    {#if setupState === 'unavailable'}
      <strong>Connection settings unavailable</strong>
      <span>Review your settings to find out how to recover your connection.</span>
      <div class="setup-controls">
        <button
          class="action-btn"
          type="button"
          data-connection-setup-trigger
          onclick={(event) => void openConnectionDialog(event.currentTarget)}
          >Review connection settings</button
        >
      </div>
    {:else if setupPendingRestart}
      <strong>Connection saved</strong>
      <span>Restart Imp to use the saved connection.</span>
    {:else}
      <strong>Connection not set up</strong>
      {#if showSetupPrompt}
        <span>Set up a connection to see your character’s vitals.</span>
        <div class="setup-controls">
          <button
            class="action-btn"
            type="button"
            data-connection-setup-trigger
            onclick={(event) => void openConnectionDialog(event.currentTarget)}>Set up connection</button
          >
          <button class="manage-btn" type="button" onclick={dismissSetup}>Not now</button>
        </div>
      {:else}
        <span>Open Settings → Connection → Manage to set up a connection.</span>
      {/if}
    {/if}
  </section>
{/snippet}

{#snippet actionFeedback()}
  <div
    class="action-feedback-region"
    class:action-feedback={actionInvocation.feedback !== null}
    class:fading={fadingFeedback !== null && fadingFeedback === actionInvocation.feedback}
    style:--action-feedback-fade={`${ACTION_FADE_MS}ms`}
    aria-live="polite"
    aria-atomic="true"
  >
    {actionInvocation.feedback?.message ?? ''}
  </div>
{/snippet}

{#snippet actionList()}
  <div class="contextual-heading">
    <h2>Actions</h2>
    <button
      class="manage-btn"
      type="button"
      aria-label="Manage actions"
      onclick={() => openManager('actions')}
    >
      Manage
    </button>
  </div>
  <div class="compact-action-list contextual-items">
    {#if actions.length === 0}
      <span class="compact-action-empty">No actions defined.</span>
    {:else}
      {#each actions as definition (definition.id)}
        <button
          class="action-btn"
          type="button"
          disabled={actionInvocation.pendingActionId !== null}
          onclick={() => invokeDefinition(definition)}>{definition.label}</button
        >
      {/each}
    {/if}
  </div>
  {@render actionFeedback()}
{/snippet}

<main
  class:compact={displayMode === 'compact' && managerOrigin === null && !connectionDialogOpen}
  class="hud"
  aria-label="Imp companion HUD"
  data-tauri-drag-region
>
  {#if actionDialogOpen}
    <ActionDialog
      definitions={actions}
      onchange={setActions}
      saveError={actionSaveError}
      onclose={closeManager}
    />
  {:else if managerOrigin !== null}
    <AlertDialog
      definitions={alertDefinitions}
      onchange={setAlertDefinitions}
      saveError={alertSaveError}
      onclose={closeManager}
    />
  {:else if connectionDialogOpen}
    <ConnectionDialog
      settings={connectionSettings}
      loadError={connectionLoadError}
      saveError={connectionSaveError}
      onsave={setConnectionSettings}
      onclose={closeConnectionDialog}
    />
  {:else}
    <div
      bind:this={panel}
      style:--expanded-width={`${EXPANDED_WINDOW_WIDTH}px`}
      class:has-compact-panel={displayMode === 'compact' && openPanel !== null}
      class="panel"
    >
      {#if displayMode === 'compact'}
        <section bind:this={compactRow} class="compact-row" class:setup-row={setupState !== undefined}>
          <div class="row-controls" inert={exitInvoker !== null}>
            {#if setupState === undefined}
              <div class="compact-primary">
                <StatusIndicator {status} label={statusLabel} />
                <span class="compact-name">{characterName}</span>
                <CompactVital label="HP" vital={character?.hp ?? null} color="var(--hp)" />
                {#if showMana}
                  <CompactVital label="MN" vital={character?.mana ?? null} color="var(--mana)" />
                {/if}
                <CompactVital label="MV" vital={character?.moves ?? null} color="var(--moves)" />
              </div>
            {:else}
              <span class="compact-name">Imp</span>
            {/if}
            <div bind:this={compactControls} class="compact-controls">
              {@render panelControls()}
            </div>
          </div>
          {#if exitInvoker !== null}
            <ExitConfirmation invoker={exitInvoker} onclose={() => (exitInvoker = null)} />
          {/if}
          {#if setupState !== undefined && openPanel === null}
            {@render connectionSetup()}
          {/if}
        </section>
        {#if openPanel === 'settings'}
          <div
            id="compact-settings-panel"
            bind:this={compactPanel}
            class="compact-attached-panel"
            role="dialog"
            aria-label="Imp settings"
            inert={exitInvoker !== null}
          >
            <SettingsPanel
              mode={displayMode}
              {theme}
              {appVersion}
              onmodechange={setDisplayMode}
              onthemechange={setTheme}
              onmanageconnection={(invoker) => {
                void openConnectionDialog(invoker);
              }}
            />
          </div>
        {:else if openPanel === 'actions'}
          <div
            id="compact-actions-panel"
            bind:this={compactPanel}
            class="compact-attached-panel compact-actions-panel contextual-list"
            role="region"
            aria-label="Saved actions"
            inert={exitInvoker !== null}
          >
            {@render actionList()}
          </div>
        {:else if openPanel === 'alerts'}
          <div
            id="compact-alerts-panel"
            bind:this={compactPanel}
            class="compact-attached-panel compact-alerts-panel"
            role="region"
            aria-label="Saved alerts"
            inert={exitInvoker !== null}
          >
            <AlertQuickList
              definitions={alertDefinitions}
              saveError={alertSaveError}
              ontoggle={setAlertDefinitionEnabled}
              onmanage={() => openManager('alerts')}
            />
          </div>
        {/if}
      {:else}
        <header class="titlebar">
          <div class="row-controls" inert={exitInvoker !== null}>
            <div class="identity" data-tauri-drag-region>
              {#if setupState === undefined}
                <StatusIndicator {status} label={statusLabel} />
              {/if}
              <span>{characterName}</span>
            </div>
            {@render panelControls()}
          </div>
          {#if exitInvoker !== null}
            <ExitConfirmation invoker={exitInvoker} onclose={() => (exitInvoker = null)} />
          {/if}
        </header>

        {#if openPanel === 'settings'}
          <div
            id="expanded-settings-panel"
            class="settings-body"
            role="dialog"
            aria-label="Imp settings"
            inert={exitInvoker !== null}
          >
            <SettingsPanel
              mode={displayMode}
              {theme}
              {appVersion}
              onmodechange={setDisplayMode}
              onthemechange={setTheme}
              onmanageconnection={(invoker) => {
                void openConnectionDialog(invoker);
              }}
            />
          </div>
        {:else if openPanel === 'actions'}
          <div
            id="expanded-actions-panel"
            class="actions-body contextual-list"
            role="region"
            aria-label="Saved actions"
            inert={exitInvoker !== null}
          >
            {@render actionList()}
          </div>
        {:else if openPanel === 'alerts'}
          <div
            id="expanded-alerts-panel"
            class="alerts-body"
            role="region"
            aria-label="Saved alerts"
            inert={exitInvoker !== null}
          >
            <AlertQuickList
              definitions={alertDefinitions}
              saveError={alertSaveError}
              ontoggle={setAlertDefinitionEnabled}
              onmanage={() => openManager('alerts')}
            />
          </div>
        {:else if setupState !== undefined}
          {@render connectionSetup()}
        {:else if isFresh && character !== null}
          <section class="content" inert={exitInvoker !== null}>
            <div class="vitals">
              <VitalBar label="HP" vital={character.hp} color="var(--hp)" />
              {#if showMana}
                <VitalBar label="MANA" vital={character.mana} color="var(--mana)" />
              {/if}
              <VitalBar label="MV" vital={character.moves} color="var(--moves)" />
            </div>
            {#if target !== null}
              <TargetPanel {target} />
            {/if}
          </section>
        {:else if character !== null}
          <section class="content last-known" inert={exitInvoker !== null}>
            <div class="vitals">
              <VitalBar label="HP" vital={character.hp} color="var(--hp)" />
              {#if showMana}
                <VitalBar label="MANA" vital={character.mana} color="var(--mana)" />
              {/if}
              <VitalBar label="MV" vital={character.moves} color="var(--moves)" />
            </div>
            {#if target !== null}
              <TargetPanel {target} />
            {/if}
          </section>
        {:else if model?.phase === 'connected'}
          <section class="empty-state" inert={exitInvoker !== null}>
            <strong>Connected to relay</strong>
            <span>Waiting for game data…</span>
          </section>
        {:else}
          <section class="empty-state" inert={exitInvoker !== null}>
            <strong>{model?.phase === 'reconnecting' ? 'Reconnecting to relay…' : 'Relay unavailable'}</strong
            >
            <span>{model?.detail ?? 'Start the relay, then connect your MUD client.'}</span>
          </section>
        {/if}

        {#if openPanel === null && setupState === undefined}
          <div class="action-footer" inert={exitInvoker !== null}>
            <ActionBar
              definitions={actions}
              pendingActionId={actionInvocation.pendingActionId}
              feedback={actionFeedback}
              oninvoke={invokeDefinition}
              onmanage={() => openManager('footer-actions')}
            />
          </div>
        {/if}
      {/if}
    </div>
  {/if}
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

  .hud:not(.compact) .panel {
    height: max-content;
    align-content: start;
    /* Measure at the destination width, not the previous surface's viewport. */
    width: var(--expanded-width);
  }

  .titlebar {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    min-height: 2.05rem;
    padding: 0 0.52rem 0 var(--space-7);
    border-bottom: 1px solid var(--divider);
  }

  .row-controls {
    display: grid;
    grid-area: 1 / 1;
    align-items: center;
  }

  .row-controls[inert] {
    visibility: hidden;
  }

  .titlebar .row-controls {
    grid-template-columns: minmax(0, 1fr) repeat(4, auto);
    gap: var(--space-4);
  }

  .compact-row .row-controls {
    grid-template-columns: minmax(0, 1fr) auto;
    gap: var(--space-3);
  }

  .identity {
    display: flex;
    align-items: center;
    min-width: 0;
    gap: var(--space-5);
    min-height: 2.05rem;
    color: var(--text);
    font-size: var(--font-sm);
    font-weight: var(--weight-strong);
    letter-spacing: var(--tracking-normal);
  }

  .identity span:last-child,
  .compact-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .settings-body,
  .alerts-body,
  .actions-body {
    min-height: 0;
    overflow: hidden;
    padding: var(--section-pad);
  }

  .content {
    display: grid;
    align-content: start;
    gap: var(--space-4);
    padding: var(--section-pad);
  }

  .vitals {
    display: grid;
    gap: var(--space-5);
  }

  .empty-state {
    display: grid;
    align-content: center;
    gap: var(--space-4);
    padding: var(--section-pad);
    color: var(--muted);
    text-align: center;
  }

  .empty-state strong {
    color: var(--text);
    font-size: var(--font-md);
  }

  .empty-state span {
    font-size: var(--font-xs);
    line-height: 1.35;
  }

  .setup-controls {
    display: flex;
    justify-content: center;
    gap: var(--space-4);
  }

  .action-footer {
    display: grid;
    min-width: 0;
  }

  .compact .panel {
    width: max-content;
    height: max-content;
    align-content: start;
    overflow: hidden;
    border-radius: var(--radius);
  }

  .compact .panel.has-compact-panel {
    /* Temporary native window widths must not become compact sizing input. */
    width: max-content;
    grid-template-rows: auto auto;
    border-radius: var(--radius);
  }

  .compact-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    width: max-content;
    max-width: calc(560px - 2px);
    min-height: 30px;
    padding: 0 var(--space-7);
  }

  .compact-row.setup-row {
    width: calc(var(--expanded-width) - 2px);
    grid-template-rows: minmax(30px, auto) auto;
  }

  .has-compact-panel .compact-row {
    box-shadow: inset 0 -1px var(--divider);
  }

  .compact-attached-panel {
    width: 100%;
    min-width: 0;
    padding: var(--section-pad);
  }

  .compact-actions-panel,
  .compact-alerts-panel {
    /* Wrapped labels must not contribute max-content window width. */
    width: 0;
    min-width: 100%;
  }

  .action-feedback-region:not(.action-feedback) {
    position: absolute;
  }

  @media (prefers-reduced-motion: no-preference) {
    .action-feedback-region.fading {
      opacity: 0;
      transition: opacity var(--action-feedback-fade) ease-out;
    }
  }

  .compact-action-list button {
    flex: 0 1 auto;
    max-width: 100%;
    min-width: 3rem;
    overflow-wrap: anywhere;
    text-align: left;
  }

  .compact-action-empty {
    overflow: hidden;
    color: var(--muted);
    font-size: var(--font-2xs);
    line-height: 1;
  }

  .compact-primary {
    display: grid;
    grid-template-columns: auto minmax(2.5rem, 1fr) repeat(3, max-content);
    align-items: center;
    min-width: 0;
    gap: var(--space-7);
  }

  .compact-name {
    color: var(--text);
    font-size: var(--font-xs);
    font-weight: var(--weight-strong);
    letter-spacing: var(--tracking-normal);
  }

  .compact-controls {
    display: flex;
    align-items: center;
    gap: var(--space-1);
  }
</style>
