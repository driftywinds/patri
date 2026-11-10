<script>
  import { onMount } from 'svelte';
  import Search from './lib/Search.svelte';
  import Train from './lib/Train.svelte';
  import Admin from './lib/Admin.svelte';
  import Marquee from './lib/Marquee.svelte';
  import { search } from './lib/api.js';

  const admin = location.pathname.replace(/\/+$/, '') === '/admin';

  let page = $state('home');
  let no = $state('');
  let results = $state([]);
  let busy = $state(false);
  let error = $state('');
  let searched = $state(false);
  let last = $state({ q: '', by: 'name' });

  function route() {
    const m = location.hash.match(/^#\/t\/(\d{1,5})$/);
    page = m ? 'train' : 'home';
    no = m ? m[1] : '';
    scrollTo({ top: 0 });
  }

  onMount(() => {
    if (admin) return;
    if (!location.hash) location.hash = '#/';
    route();
    addEventListener('hashchange', route);
    return () => removeEventListener('hashchange', route);
  });

  async function runSearch({ q, by }) {
    // Leaving a train page is a navigation: the results live on the home view.
    if (location.hash !== '#/') location.hash = '#/';
    last = { q, by };
    busy = true;
    error = '';
    results = [];
    try {
      results = (await search(q, by)).results;
      searched = true;
    } catch (e) {
      error = e.message;
    } finally {
      busy = false;
    }
  }
</script>

{#if admin}
  <main class="wrap"><Admin /></main>
{:else}
  <header class="bar">
    <a class="brand" href="#/" aria-label="patri home">
      <img src="/logo-trans.svg" alt="" width="26" height="26" />
      <span>patri</span>
    </a>
    <Search onsubmit={runSearch} busy={busy} />
  </header>

{#if page === 'train'}
  <main class="wrap"><Train {no} /></main>
{:else}
  <main class="wrap">
    {#if busy}
      <p class="state">Searching NTES…</p>
    {:else if error}
      <div class="card error">
        <p>{error}</p>
        <button class="ghost" onclick={() => runSearch(last)}>Try again</button>
      </div>
    {:else if results.length}
      <p class="count">{results.length} trains for “{last.q}”</p>
      <ul class="hits">
        {#each results as r (r.no)}
          <li>
            <a class="hit" href="#/t/{r.no}">
              <span class="hit-no">{r.no}</span>
              <span class="hit-main">
                <span class="hit-name"><Marquee text={r.name} /></span>
                <span class="hit-route">{r.source_name} → {r.dest_name}</span>
              </span>
              {#if r.type}<span class="badge">{r.type}</span>{/if}
            </a>
          </li>
        {/each}
      </ul>
    {:else if searched}
      <p class="state">No trains matched. Try a shorter name or a 5-digit number.</p>
    {:else}
      <section class="intro">
        <h1>Where's my train?</h1>
        <p>
          Live running status, the full route, and the expected time at every stop —
          straight from NTES.
        </p>
        <ul>
          <li><b>Name</b> — <code>rajdhani</code>, <code>shatabdi</code></li>
          <li><b>Number</b> — <code>12951</code></li>
        </ul>
      </section>
    {/if}
  </main>
{/if}
{/if}

<style>
  .bar {
    position: sticky;
    top: 0;
    z-index: 10;
    display: flex;
    gap: 12px;
    align-items: center;
    /* keep the header on the same column as the page content */
    padding: 10px max(16px, calc((100% - 728px) / 2));
    background: rgba(0, 0, 0, 0.86);
    backdrop-filter: blur(10px);
    border-bottom: 1px solid var(--line);
  }
  .bar :global(form) {
    flex: 1 1 auto;
    min-width: 0;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 7px;
    font-weight: 700;
    font-size: 1.15rem;
    letter-spacing: -0.02em;
    padding: 0 2px;
    color: var(--text);
  }
  .brand img {
    display: block;
    width: 26px;
    height: 26px;
  }
  .state,
  .count {
    color: var(--muted);
    font-size: 0.95rem;
  }
  .count {
    margin: 4px 0 10px;
  }
  .hits {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    /* implicit auto tracks refuse to shrink below nowrap text; pin them */
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
  }
  .hit {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 14px;
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 14px;
    min-height: 64px;
  }
  .hit:hover {
    border-color: var(--line-strong);
    background: var(--raised);
  }
  .hit-no {
    font-variant-numeric: tabular-nums;
    font-weight: 600;
    color: var(--text);
    min-width: 3.6rem;
    letter-spacing: 0.01em;
  }
  .hit-main {
    display: flex;
    flex-direction: column;
    min-width: 0;
    flex: 1;
  }
  .hit-name {
    display: block;
    font-weight: 500;
    min-width: 0;
  }
  .hit-route {
    color: var(--muted);
    font-size: 0.8rem;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .badge {
    font-size: 0.66rem;
    letter-spacing: 0.06em;
    color: var(--dim);
    border: 1px solid var(--line-strong);
    border-radius: 6px;
    padding: 2px 6px;
  }
  .card {
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 16px;
  }
  .error p {
    margin: 0 0 12px;
    color: var(--soft);
  }
  .ghost {
    border: 1px solid var(--line-strong);
    background: transparent;
    color: var(--text);
    border-radius: 10px;
    padding: 10px 16px;
    font: inherit;
    min-height: 44px;
    cursor: pointer;
  }
  .ghost:hover {
    background: var(--raised);
  }
  .intro {
    padding: 32px 4px;
    max-width: 34rem;
  }
  .intro h1 {
    font-size: 1.7rem;
    letter-spacing: -0.025em;
    margin: 0 0 8px;
  }
  .intro p {
    color: var(--muted);
    margin: 0 0 20px;
  }
  .intro ul {
    margin: 0;
    padding: 0;
    list-style: none;
    display: grid;
    gap: 6px;
    color: var(--muted);
    font-size: 0.95rem;
  }
  @media (max-width: 560px) {
    .bar {
      flex-wrap: wrap;
    }
    .bar :global(form) {
      flex: 1 1 100%;
      order: 2;
    }
  }
</style>
