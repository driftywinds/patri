<script>
  import { onMount } from 'svelte';

  const FONTS = [
    { id: 'inter', label: 'Inter', stack: "'Inter', system-ui, sans-serif" },
    { id: 'plex', label: 'IBM Plex Sans', stack: "'IBM Plex Sans', system-ui, sans-serif" },
  ];

  let font = $state('inter');
  let authed = $state(false);
  let key = $state('');
  let note = $state('');
  let error = $state('');
  let busy = $state(false);
  let loaded = $state(false);

  onMount(async () => {
    try {
      const res = await fetch('/api/settings');
      if (res.ok) {
        const body = await res.json();
        font = body.font || 'inter';
        authed = !!body.admin;
      }
    } catch {
      /* keep defaults */
    }
    loaded = true;
  });

  // Live preview while choosing; Save makes it stick for everyone.
  $effect(() => {
    document.documentElement.dataset.font = font;
  });

  async function login(event) {
    event.preventDefault();
    busy = true;
    error = '';
    note = '';
    try {
      const res = await fetch('/api/admin/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: key }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error || `sign in failed (${res.status})`);
      authed = true;
      key = '';
    } catch (e) {
      error = e.message;
    } finally {
      busy = false;
    }
  }

  async function logout() {
    await fetch('/api/admin/logout', { method: 'POST' }).catch(() => {});
    authed = false;
    note = '';
    error = '';
  }

  async function save() {
    busy = true;
    error = '';
    note = '';
    try {
      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ font }),
      });
      const body = await res.json().catch(() => ({}));
      if (res.status === 401) {
        authed = false;
        throw new Error('session expired — unlock again');
      }
      if (!res.ok) throw new Error(body.error || `save failed (${res.status})`);
      document.documentElement.dataset.font = body.font;
      note = 'Saved. Every page now uses this typeface.';
    } catch (e) {
      error = e.message;
    } finally {
      busy = false;
    }
  }
</script>

<div class="admin">
  <header class="top">
    <h1>Admin</h1>
    <a class="back" href="/">← Site</a>
  </header>

  {#if !loaded}
    <p class="state">Loading…</p>
  {:else if !authed}
    <section class="panel">
      <h2>Sign in</h2>
      <p class="hint">
        The typeface setting is behind the admin key from <code>.env</code>. It is checked
        on the server and kept in an HttpOnly cookie — the browser never stores it.
      </p>
      <form onsubmit={login}>
        <label class="key">
          <span>Admin key</span>
          <input
            type="password"
            bind:value={key}
            autocomplete="current-password"
            placeholder="ADMIN_TOKEN"
          />
        </label>
        <div class="actions">
          <button type="submit" disabled={busy || !key}>
            {busy ? 'Checking…' : 'Unlock'}
          </button>
          {#if error}<span class="msg err">{error}</span>{/if}
        </div>
      </form>
    </section>
  {:else}
    <section class="panel">
      <div class="head">
        <h2>Typeface</h2>
        <button class="link" onclick={logout}>Lock</button>
      </div>
      <p class="hint">
        Applies to every page. Stored in d9 as a <code>patri_cache</code> setting, so it
        survives restarts and applies to every device.
      </p>

      <div class="choices">
        {#each FONTS as f (f.id)}
          <label class="choice {font === f.id ? 'on' : ''}" style="--preview: {f.stack}">
            <input type="radio" name="font" value={f.id} bind:group={font} />
            <span class="mark" aria-hidden="true"></span>
            <span class="body">
              <span class="name">{f.label}</span>
              <span class="sample">18:47–18:49 · ASANSOL JN · +13m</span>
            </span>
          </label>
        {/each}
      </div>

      <div class="actions">
        <button onclick={save} disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
        {#if note}<span class="msg ok">{note}</span>{/if}
        {#if error}<span class="msg err">{error}</span>{/if}
      </div>
    </section>
  {/if}
</div>

<style>
  .admin {
    max-width: 560px;
    margin: 0 auto;
    padding: 28px 16px 64px;
  }
  .top {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    margin-bottom: 22px;
  }
  h1 {
    font-size: 1.45rem;
    letter-spacing: -0.03em;
    margin: 0;
  }
  .back {
    color: var(--muted);
    font-size: 0.85rem;
  }
  .back:hover {
    color: var(--text);
  }
  .state {
    color: var(--muted);
  }
  .panel {
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 16px;
    padding: 18px;
  }
  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }
  h2 {
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: var(--muted);
    font-weight: 600;
    margin: 0 0 4px;
  }
  .link {
    background: none;
    border: 0;
    color: var(--muted);
    font: inherit;
    font-size: 0.8rem;
    cursor: pointer;
    padding: 4px 2px;
  }
  .link:hover {
    color: var(--text);
  }
  .hint {
    margin: 0 0 16px;
    color: var(--dim);
    font-size: 0.82rem;
    line-height: 1.45;
  }
  .choices {
    display: grid;
    gap: 10px;
  }
  .choice {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 14px;
    border: 1px solid var(--line);
    border-radius: 12px;
    cursor: pointer;
    min-height: 72px;
  }
  .choice:hover {
    border-color: var(--line-strong);
  }
  .choice.on {
    border-color: #ffffff;
  }
  .choice input {
    position: absolute;
    opacity: 0;
    pointer-events: none;
  }
  .mark {
    width: 16px;
    height: 16px;
    flex: none;
    border-radius: 50%;
    border: 1px solid #4a4a4a;
  }
  .choice.on .mark {
    border-color: #ffffff;
    background: #ffffff;
    box-shadow: inset 0 0 0 4px #000;
  }
  .body {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .name {
    font-family: var(--preview);
    font-size: 1.15rem;
    font-weight: 600;
    letter-spacing: -0.01em;
  }
  .sample {
    font-family: var(--preview);
    font-variant-numeric: tabular-nums;
    font-size: 0.8rem;
    color: var(--muted);
    margin-top: 2px;
  }
  .choice input:focus-visible + .mark {
    outline: 2px solid #fff;
    outline-offset: 3px;
  }
  .key {
    display: grid;
    gap: 6px;
    font-size: 0.8rem;
    color: var(--muted);
  }
  .key input {
    height: 42px;
    border-radius: 10px;
    border: 1px solid var(--line-strong);
    background: #000;
    color: var(--text);
    font: inherit;
    padding: 0 12px;
  }
  .key input:focus-visible {
    outline: 2px solid #fff;
    outline-offset: 2px;
  }
  .actions {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 18px;
    flex-wrap: wrap;
  }
  button[type='submit'],
  .actions button:not(.link) {
    height: 42px;
    padding: 0 22px;
    border: 0;
    border-radius: 10px;
    background: #ffffff;
    color: #000;
    font: inherit;
    font-weight: 600;
    cursor: pointer;
  }
  button[type='submit']:hover:not(:disabled),
  .actions button:not(.link):hover:not(:disabled) {
    background: #e2e2e2;
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .msg {
    font-size: 0.82rem;
  }
  .msg.ok {
    color: var(--soft);
  }
  .msg.err {
    color: #ff9d9d;
  }
</style>
