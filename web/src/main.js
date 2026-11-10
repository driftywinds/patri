import { mount } from 'svelte';
import App from './App.svelte';
import './app.css';

// The typeface is baked into <html data-font="…"> by the server; /admin sets it.
mount(App, { target: document.getElementById('app') });
