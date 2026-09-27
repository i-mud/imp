<script lang="ts">
  import { onMount } from 'svelte';

  import Hud from './components/Hud.svelte';
  import type { ActionSink } from './lib/action/types.ts';
  import { HudStore, type TextSourceListener } from './lib/hud/store.svelte.ts';
  import type { StateSource } from './lib/source/types.ts';

  let {
    source,
    actionSink,
  }: {
    source: StateSource;
    actionSink: ActionSink;
  } = $props();

  const store = new HudStore();
  const subscribeText = (listener: TextSourceListener) => store.subscribeText(listener);

  onMount(() => {
    store.attach(source);
    return () => store.detach();
  });
</script>

<Hud model={store.model} {actionSink} {subscribeText} />
