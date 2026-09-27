<script lang="ts">
  import { onMount } from 'svelte';

  import ActionBar from './ActionBar.svelte';
  import ActionDialog from './ActionDialog.svelte';
  import AlertDialog from './AlertDialog.svelte';
  import AlertQuickList from './AlertQuickList.svelte';
  import AlertsMenu from './AlertsMenu.svelte';
  import ActionsMenu from './ActionsMenu.svelte';
  import CompactVital from './CompactVital.svelte';
  import ConnectionDialog from './ConnectionDialog.svelte';
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
    type ConnectionSettings,
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
    ACTION_STRIP_HEIGHT,
    closeWindow,
    compactPanelWindowSize,
    compactWindowSize,
    EXPANDED_SETTINGS_WINDOW_SIZE,
    EXPANDED_WINDOW_SIZE,
    EXPANDED_WITH_TARGET_WINDOW_SIZE,
    resizeHudWindow,
  } from '../lib/window.ts';
  import { applyTheme, loadTheme, saveTheme, type ThemePreference } from '../lib/hud/theme.ts';
  import ChevronUp from '@lucide/svelte/icons/chevron-up';
  import X from '@lucide/svelte/icons/x';

  let {
    model,
    actionSink,
    subscribeText,
  }: {
    model: HudModel;
    actionSink: ActionSink;
    subscribeText: (listener: TextSourceListener) => () => void;
  } = $props();
  let displayMode = $state(loadDisplayMode());
  let theme = $state(loadTheme());
  let alertDefinitions = $state<AlertDefinition[]>(loadAlertDefinitions());
  let actions = $state<ActionDefinition[]>(loadActionDefinitions());

  let panel = $state<HTMLElement>();
  let compactRow = $state<HTMLElement>();
  let compactPanel = $state<HTMLDivElement>();
  let compactControls = $state<HTMLElement>();
  let compactSettingsTrigger = $state<HTMLButtonElement>();
  let compactActionsTrigger = $state<HTMLButtonElement>();
  let compactAlertsTrigger = $state<HTMLButtonElement>();
  let settingsOpen = $state(false);
  let actionsOpen = $state(false);
  let alertsOpen = $state(false);
  let actionDialogOpen = $state(false);
  let alertDialogOpen = $state(false);
  let connectionDialogOpen = $state(false);
  let actionDialogWidth = $state(EXPANDED_SETTINGS_WINDOW_SIZE.width);
  let actionSaveError = $state<string | null>(null);
  let alertSaveError = $state<string | null>(null);
  let connectionSettings = $state<ConnectionSettings | null>(null);
  let connectionLoadError = $state<string | null>(null);
  let connectionSaveError = $state<string | null>(null);
  let actionDialogInvoker: HTMLButtonElement | null = null;
  let alertDialogInvoker: HTMLButtonElement | null = null;
  let connectionDialogInvoker: HTMLButtonElement | null = null;
  const actionInvocation = $state<ActionInvocationState>({
    pendingActionId: null,
    feedback: null,
  });
  const vitalAlertMemory: { current: Map<string, VitalAlertState> } = {
    current: new Map(),
  };

  onMount(() =>
    subscribeText((event) => {
      for (const trigger of evaluateTextAlerts(alertDefinitions, event.text)) {
        void dispatchAlert(textAlertEvent(trigger.definition), trigger.definition, DESKTOP_ALERT_EFFECTS);
      }
    }),
  );

  const freshness = $derived(freshnessOf(model));
  const isFresh = $derived(freshness === 'fresh' && model.hasData);
  const status = $derived(statusIndicatorOf(model));
  const statusLabel = $derived(statusLabelOf(model));
  const character = $derived(model.state.character);
  const target = $derived(model.state.target);
  const characterName = $derived(character?.name ?? 'TinyScry');
  const showMana = $derived(character?.mana?.max !== 0);
  const expandedBase = $derived(target !== null ? EXPANDED_WITH_TARGET_WINDOW_SIZE : EXPANDED_WINDOW_SIZE);

  function setDisplayMode(mode: DisplayMode): void {
    settingsOpen = false;
    actionsOpen = false;
    alertsOpen = false;
    displayMode = mode;
    saveDisplayMode(mode);
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

  function openActionDialog(invoker: HTMLButtonElement): void {
    if (displayMode === 'compact') {
      const row = compactRow;
      const hudPanel = panel;
      if (row !== undefined && hudPanel !== undefined) {
        const rowBounds = row.getBoundingClientRect();
        const frameWidth = hudPanel.offsetWidth - hudPanel.clientWidth;
        actionDialogWidth = compactWindowSize(rowBounds.width + frameWidth, rowBounds.height).width;
      }
    } else {
      actionDialogWidth = EXPANDED_SETTINGS_WINDOW_SIZE.width;
    }

    actionDialogInvoker = invoker;
    actionSaveError = null;
    alertDialogOpen = false;
    connectionDialogOpen = false;
    settingsOpen = false;
    actionsOpen = false;
    alertsOpen = false;
    actionDialogOpen = true;
  }

  function closeActionDialog(): void {
    actionDialogOpen = false;
    settingsOpen = true;
    const invoker = actionDialogInvoker;
    requestAnimationFrame(() => {
      const target = invoker?.isConnected
        ? invoker
        : (document.querySelector<HTMLButtonElement>('[data-action-manager-trigger]') ??
          document.querySelector<HTMLButtonElement>('.settings-button') ??
          document.querySelector<HTMLButtonElement>('button.close'));
      target?.focus();
      actionDialogInvoker = null;
    });
  }

  function openAlertDialog(invoker: HTMLButtonElement): void {
    if (displayMode === 'compact') {
      const row = compactRow;
      const hudPanel = panel;
      if (row !== undefined && hudPanel !== undefined) {
        const rowBounds = row.getBoundingClientRect();
        const frameWidth = hudPanel.offsetWidth - hudPanel.clientWidth;
        actionDialogWidth = compactWindowSize(rowBounds.width + frameWidth, rowBounds.height).width;
      }
    } else {
      actionDialogWidth = EXPANDED_SETTINGS_WINDOW_SIZE.width;
    }

    alertDialogInvoker = invoker;
    actionDialogOpen = false;
    connectionDialogOpen = false;
    settingsOpen = false;
    actionsOpen = false;
    alertsOpen = false;
    alertDialogOpen = true;
  }

  function closeAlertDialog(): void {
    alertDialogOpen = false;
    settingsOpen = true;

    const invoker = alertDialogInvoker;
    requestAnimationFrame(() => {
      const target = invoker?.isConnected
        ? invoker
        : (document.querySelector<HTMLButtonElement>('[data-alert-manager-trigger]') ??
          document.querySelector<HTMLButtonElement>('.settings-button') ??
          document.querySelector<HTMLButtonElement>('button.close'));

      target?.focus();
      alertDialogInvoker = null;
    });
  }

  async function openConnectionDialog(invoker: HTMLButtonElement): Promise<void> {
    if (displayMode === 'compact') {
      const row = compactRow;
      const hudPanel = panel;
      if (row !== undefined && hudPanel !== undefined) {
        const rowBounds = row.getBoundingClientRect();
        const frameWidth = hudPanel.offsetWidth - hudPanel.clientWidth;
        actionDialogWidth = compactWindowSize(rowBounds.width + frameWidth, rowBounds.height).width;
      }
    } else {
      actionDialogWidth = EXPANDED_SETTINGS_WINDOW_SIZE.width;
    }

    connectionDialogInvoker = invoker;
    connectionSettings = null;
    connectionLoadError = null;
    connectionSaveError = null;
    actionDialogOpen = false;
    alertDialogOpen = false;
    settingsOpen = false;
    actionsOpen = false;
    alertsOpen = false;
    connectionDialogOpen = true;

    try {
      const loaded = await loadConnectionSettings();
      if (!connectionDialogOpen) return;

      if (loaded === null) {
        connectionLoadError = 'Connection settings are available only in the native TinyScry application.';
        return;
      }

      connectionSettings = loaded;
    } catch {
      if (connectionDialogOpen) {
        connectionLoadError = 'Could not load native connection settings.';
      }
    }
  }

  function closeConnectionDialog(): void {
    connectionDialogOpen = false;
    settingsOpen = true;

    const invoker = connectionDialogInvoker;
    requestAnimationFrame(() => {
      const target = invoker?.isConnected
        ? invoker
        : (document.querySelector<HTMLButtonElement>('[data-connection-manager-trigger]') ??
          document.querySelector<HTMLButtonElement>('.settings-button') ??
          document.querySelector<HTMLButtonElement>('button.close'));

      target?.focus();
      connectionDialogInvoker = null;
    });
  }

  async function setConnectionSettings(update: ConnectionSettingsUpdate): Promise<boolean> {
    connectionSaveError = null;

    try {
      connectionSettings = await saveConnectionSettings(update);
      return true;
    } catch (error) {
      connectionSaveError = typeof error === 'string' ? error : 'Could not save native connection settings.';
      return false;
    }
  }

  function invokeDefinition(definition: ActionDefinition): void {
    const context = model.context;
    void invokeAction(actionInvocation, actionSink, context, definition);
  }

  function closeOrDismiss(): void {
    if (settingsOpen) {
      settingsOpen = false;
    } else if (actionsOpen) {
      actionsOpen = false;
    } else if (alertsOpen) {
      alertsOpen = false;
    } else {
      closeWindow();
    }
  }

  const closeLabel = $derived(
    settingsOpen
      ? 'Close settings'
      : actionsOpen
        ? 'Close actions'
        : alertsOpen
          ? 'Close alerts'
          : 'Close TinyScry',
  );
  const CloseIcon = $derived(settingsOpen || actionsOpen || alertsOpen ? ChevronUp : X);

  $effect(() => {
    if (displayMode !== 'compact' || (!settingsOpen && !actionsOpen && !alertsOpen)) return;
    const attachedPanel = compactPanel;
    const activeTrigger = alertsOpen
      ? compactAlertsTrigger
      : actionsOpen
        ? compactActionsTrigger
        : compactSettingsTrigger;
    if (attachedPanel === undefined) return;

    const closeOnOutsidePointer = (event: PointerEvent) => {
      const target = event.target as Node;
      // The controls cluster owns its own open/close behaviour - the close
      // button dismisses the open panel on click, so an outside-pointer
      // dismissal here would swallow that state before the click lands.
      if (!attachedPanel.contains(target) && compactControls?.contains(target) !== true) {
        settingsOpen = false;
        actionsOpen = false;
        alertsOpen = false;
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      settingsOpen = false;
      actionsOpen = false;
      alertsOpen = false;
      requestAnimationFrame(() => activeTrigger?.focus());
    };
    window.addEventListener('pointerdown', closeOnOutsidePointer);
    window.addEventListener('keydown', closeOnEscape);
    return () => {
      window.removeEventListener('pointerdown', closeOnOutsidePointer);
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
    if (actionDialogOpen || alertDialogOpen || connectionDialogOpen) {
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
      if (settingsOpen || alertsOpen) {
        const hudPanel = panel;
        if (hudPanel === undefined) return;

        const body = hudPanel.querySelector<HTMLElement>(settingsOpen ? '.settings-body' : '.alerts-body');
        const titlebar = hudPanel.querySelector<HTMLElement>('.titlebar');
        if (body === null || titlebar === null) return;

        const resize = () => {
          const titlebarHeight = titlebar.getBoundingClientRect().height;
          const bodyHeight = body.getBoundingClientRect().height;
          const frameHeight = hudPanel.offsetHeight - hudPanel.clientHeight;

          resizeHudWindow({
            width: EXPANDED_SETTINGS_WINDOW_SIZE.width,
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
      if (actions.length === 0) {
        resizeHudWindow(expandedBase);
        return;
      }

      const hudPanel = panel;
      if (hudPanel === undefined) return;

      const resize = () => {
        const actionBarHeight =
          hudPanel.querySelector('.action-bar')?.getBoundingClientRect().height ?? ACTION_STRIP_HEIGHT;
        resizeHudWindow({
          width: expandedBase.width,
          height: expandedBase.height + Math.ceil(actionBarHeight),
        });
      };
      const frame = requestAnimationFrame(resize);
      const observer = new ResizeObserver(resize);
      const actionBar = hudPanel.querySelector('.action-bar');
      if (actionBar !== null) observer.observe(actionBar);
      return () => {
        cancelAnimationFrame(frame);
        observer.disconnect();
      };
    }

    const row = compactRow;
    const hudPanel = panel;
    const attachedPanel = settingsOpen || actionsOpen || alertsOpen ? compactPanel : undefined;
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
        settingsOpen || actionsOpen || alertsOpen
          ? compactPanelWindowSize(contentWidth, contentHeight)
          : compactWindowSize(contentWidth, contentHeight),
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

<main
  class:compact={displayMode === 'compact' && !actionDialogOpen && !alertDialogOpen && !connectionDialogOpen}
  class="hud"
  aria-label="TinyScry companion HUD"
  data-tauri-drag-region
>
  {#if actionDialogOpen}
    <ActionDialog
      definitions={actions}
      onchange={setActions}
      saveError={actionSaveError}
      onclose={closeActionDialog}
    />
  {:else if alertDialogOpen}
    <AlertDialog
      definitions={alertDefinitions}
      onchange={setAlertDefinitions}
      saveError={alertSaveError}
      onclose={closeAlertDialog}
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
      class:has-actions={displayMode === 'expanded' && !settingsOpen && !alertsOpen && actions.length > 0}
      class:has-expanded-panel={displayMode === 'expanded' && (settingsOpen || alertsOpen)}
      class:has-compact-panel={displayMode === 'compact' && (settingsOpen || actionsOpen || alertsOpen)}
      class="panel"
    >
      {#if displayMode === 'compact'}
        <section bind:this={compactRow} class="compact-row">
          <div class="compact-primary">
            <StatusIndicator {status} label={statusLabel} />
            <span class="compact-name">{characterName}</span>
            <CompactVital label="HP" vital={character?.hp ?? null} color="var(--hp)" />
            {#if showMana}
              <CompactVital label="MN" vital={character?.mana ?? null} color="var(--mana)" />
            {/if}
            <CompactVital label="MV" vital={character?.moves ?? null} color="var(--moves)" />
          </div>
          <div bind:this={compactControls} class="compact-controls">
            <span class="control-slot" class:hidden={settingsOpen || actionsOpen || alertsOpen}>
              <ActionsMenu
                onopen={() => {
                  settingsOpen = false;
                  alertsOpen = false;
                }}
                bind:open={actionsOpen}
                bind:trigger={compactActionsTrigger}
              />
            </span>
            <span class="control-slot" class:hidden={settingsOpen || actionsOpen || alertsOpen}>
              <AlertsMenu
                controls="compact-alerts-panel"
                onopen={() => {
                  settingsOpen = false;
                  actionsOpen = false;
                }}
                bind:open={alertsOpen}
                bind:trigger={compactAlertsTrigger}
              />
            </span>
            <span class="control-slot" class:hidden={settingsOpen || actionsOpen || alertsOpen}>
              <SettingsMenu
                bind:open={settingsOpen}
                bind:trigger={compactSettingsTrigger}
                onopen={() => {
                  actionsOpen = false;
                  alertsOpen = false;
                }}
              />
            </span>
            <button
              class="close icon-btn"
              aria-label={closeLabel}
              onclick={(event) => {
                event.stopPropagation();
                closeOrDismiss();
              }}><CloseIcon size={18} /></button
            >
          </div>
        </section>
        {#if settingsOpen}
          <div
            id="compact-settings-panel"
            bind:this={compactPanel}
            class="compact-attached-panel"
            role="dialog"
            aria-label="TinyScry settings"
          >
            <SettingsPanel
              mode={displayMode}
              {theme}
              {alertSaveError}
              onmodechange={setDisplayMode}
              onthemechange={setTheme}
              onmanageconnection={(invoker) => {
                void openConnectionDialog(invoker);
              }}
              onmanagealerts={openAlertDialog}
              onmanageactions={openActionDialog}
            />
          </div>
        {:else if actionsOpen}
          <div
            id="compact-actions-panel"
            bind:this={compactPanel}
            class="compact-attached-panel compact-actions-panel"
            role="region"
            aria-label="Saved actions"
          >
            <div class="compact-action-list">
              {#if actions.length === 0}
                <span class="compact-action-feedback">No actions defined.</span>
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
            {#if actionInvocation.feedback}
              <div class="compact-action-feedback" aria-live="polite">
                {actionInvocation.feedback ?? '\u00a0'}
              </div>
            {/if}
          </div>
        {:else if alertsOpen}
          <div
            id="compact-alerts-panel"
            bind:this={compactPanel}
            class="compact-attached-panel compact-alerts-panel"
            role="region"
            aria-label="Saved alerts"
          >
            <AlertQuickList
              definitions={alertDefinitions}
              saveError={alertSaveError}
              ontoggle={setAlertDefinitionEnabled}
            />
          </div>
        {/if}
      {:else}
        <header class="titlebar">
          <div class="identity" data-tauri-drag-region>
            <StatusIndicator {status} label={statusLabel} />
            <span>{characterName}</span>
          </div>
          <span class="control-slot" class:hidden={settingsOpen || alertsOpen}>
            <AlertsMenu
              controls="expanded-alerts-panel"
              onopen={() => {
                settingsOpen = false;
                actionsOpen = false;
              }}
              bind:open={alertsOpen}
            />
          </span>

          <span class="control-slot" class:hidden={settingsOpen || alertsOpen}>
            <SettingsMenu
              bind:open={settingsOpen}
              onopen={() => {
                actionsOpen = false;
                alertsOpen = false;
              }}
            />
          </span>
          <button
            class="close icon-btn"
            aria-label={closeLabel}
            onclick={(event) => {
              event.stopPropagation();
              closeOrDismiss();
            }}><CloseIcon size={18} /></button
          >
        </header>

        {#if settingsOpen}
          <div class="settings-body" role="dialog" aria-label="TinyScry settings">
            <SettingsPanel
              mode={displayMode}
              {theme}
              {alertSaveError}
              onmodechange={setDisplayMode}
              onthemechange={setTheme}
              onmanageconnection={(invoker) => {
                void openConnectionDialog(invoker);
              }}
              onmanagealerts={openAlertDialog}
              onmanageactions={openActionDialog}
            />
          </div>
        {:else if alertsOpen}
          <div id="expanded-alerts-panel" class="alerts-body" role="region" aria-label="Saved alerts">
            <AlertQuickList
              definitions={alertDefinitions}
              saveError={alertSaveError}
              ontoggle={setAlertDefinitionEnabled}
            />
          </div>
        {:else if isFresh && character !== null}
          <section class="content">
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
          <section class="content last-known">
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

        {#if !settingsOpen && !alertsOpen && actions.length > 0}
          <ActionBar
            definitions={actions}
            pendingActionId={actionInvocation.pendingActionId}
            feedback={actionInvocation.feedback}
            oninvoke={invokeDefinition}
          />
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
    grid-template-rows: auto minmax(0, 1fr);
  }

  .hud:not(.compact) .panel.has-actions {
    grid-template-rows: auto minmax(0, 1fr) auto;
  }

  .hud:not(.compact) .panel.has-expanded-panel {
    height: max-content;
    grid-template-rows: auto auto;
    align-content: start;
  }

  .titlebar {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto auto auto;
    align-items: center;
    gap: var(--space-4);
    min-height: 2.05rem;
    padding: 0 0.52rem 0 var(--space-7);
    border-bottom: 1px solid var(--divider);
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

  .control-slot.hidden {
    visibility: hidden;
  }

  .settings-body,
  .alerts-body {
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
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    gap: var(--space-3);
    width: max-content;
    max-width: calc(560px - 2px);
    min-height: 30px;
    padding: 0 var(--space-7);
  }

  .has-compact-panel .compact-row {
    border-bottom: 1px solid var(--divider);
  }

  .compact-attached-panel {
    width: 100%;
    min-width: 0;
    padding: var(--section-pad);
  }

  .compact-actions-panel,
  .compact-alerts-panel {
    /* Wrapped labels must not contribute max-content window width. */
    display: grid;
    width: 0;
    min-width: 100%;
    gap: var(--space-6);
  }

  .compact-action-list {
    display: flex;
    flex-wrap: wrap;
    align-content: flex-start;
    max-height: 12rem;
    min-height: 0;
    gap: var(--space-3);
    overflow-x: hidden;
    overflow-y: auto;
    padding-right: var(--space-1);
  }

  .compact-action-list button {
    flex: 0 1 auto;
    max-width: 100%;
    min-width: 3rem;
    overflow-wrap: anywhere;
    text-align: left;
  }

  .compact-action-feedback {
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
