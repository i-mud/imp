<script lang="ts">
  import { onMount } from 'svelte';

  import Hud from './components/Hud.svelte';
  import type { AppRuntime } from './lib/config.ts';
  import { HudStore } from './lib/hud/store.svelte.ts';

  let { source, actionSink, setupState }: AppRuntime = $props();
  let store = $state.raw<HudStore>();

  onMount(() => {
    if (source === undefined) return;
    const activeStore = new HudStore();
    activeStore.attach(source);
    store = activeStore;
    return () => activeStore.detach();
  });
</script>

{#if setupState}
  <Hud {setupState} />
{:else if store && actionSink}
  {@const activeStore = store}
  <Hud
    model={activeStore.model}
    {actionSink}
    subscribeText={(listener) => activeStore.subscribeText(listener)}
  />
{/if}
