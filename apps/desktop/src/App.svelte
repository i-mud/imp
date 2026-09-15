<script lang="ts">
  import { onMount } from 'svelte';

  import Hud from './components/Hud.svelte';
  import { createStateSource } from './lib/config.ts';
  import { HudStore } from './lib/hud/store.svelte.ts';

  const store = new HudStore();
  const source = createStateSource();

  onMount(() => {
    store.attach(source);
    return () => store.detach();
  });
</script>

<Hud model={store.model} />
