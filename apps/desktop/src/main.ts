import { mount } from 'svelte';

import './app.css';
import App from './App.svelte';

const target = document.getElementById('app');

if (target === null) {
  throw new Error('TinyScry could not find its application root.');
}

mount(App, { target });
