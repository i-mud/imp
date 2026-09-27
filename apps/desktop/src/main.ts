import { mount } from 'svelte';

import './app.css';
import App from './App.svelte';
import { createRuntimeClients } from './lib/config.ts';

const target = document.getElementById('app');

if (target === null) {
  throw new Error('TinyScry could not find its application root.');
}

const runtime = await createRuntimeClients();

mount(App, {
  target,
  props: runtime,
});
