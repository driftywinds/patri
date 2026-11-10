import { mount } from 'svelte';
import App from './App.svelte';
import './app.css';

// The typeface is baked into <html data-font="…"> by the server; /admin sets it.
mount(App, { target: document.getElementById('app') });

// Offline-ish: the worker serves the last train payload while it revalidates.
if ('serviceWorker' in navigator) {
  addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}
