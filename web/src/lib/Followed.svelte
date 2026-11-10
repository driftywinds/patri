<script>
  import { onMount } from 'svelte';
  import { following, toggleFollow } from './follow.js';
  import { train as fetchTrain } from './api.js';

  let items = $state([]);
  let shown = $state(false);

  onMount(async () => {
    items = following().map((t) => ({ ...t, status: null }));
    shown = items.length > 0;
    await Promise.all(
      items.map(async (item) => {
        try {
          const data = await fetchTrain(item.no);
          item.status = data.status;
          if (data.train?.name) item.name = data.train.name;
        } catch {
          item.status = { state: 'error' };
        }
      })
    );
  });

  function chip(status) {
    if (!status) return '…';
    if (status.state === 'error') return '—';
    if (status.state === 'finished') return 'reached';
    if (status.state === 'yet_to_start') return 'yet to start';
    if (status.state === 'not_running') return 'not running';
    const d = status.delay ?? 0;
    return d === 0 ? 'on time' : d > 0 ? `+${d}m` : `${d}m`;
  }

  function remove(event, no) {
    event.preventDefault();
    event.stopPropagation();
    toggleFollow({ no });
    items = items.filter((t) => t.no !== no);
    shown = items.length > 0;
  }
</script>

{#if shown}
  <section class="followed">
    <h2>Following</h2>
    <ul>
      {#each items as item (item.no)}
        <li>
          <a href="#/t/{item.no}">
            <span class="no">{item.no}</span>
            <span class="name">{item.name}</span>
            <span class="chip" class:late={item.status?.delay > 0}>{chip(item.status)}</span>
          </a>
          <button aria-label="Stop following {item.no}" onclick={(e) => remove(e, item.no)}>×</button>
        </li>
      {/each}
    </ul>
  </section>
{/if}

<style>
  .followed {
    margin: 6px 0 26px;
  }
  h2 {
    font-size: 0.66rem;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: var(--dim);
    font-weight: 600;
    margin: 0 0 10px;
  }
  ul {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 6px;
  }
  li {
    display: flex;
    align-items: center;
    gap: 4px;
  }
  a {
    flex: 1;
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
    padding: 10px 12px;
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 12px;
    min-height: 48px;
  }
  a:hover {
    border-color: var(--line-strong);
  }
  .no {
    font-variant-numeric: tabular-nums;
    font-weight: 600;
    min-width: 3.4rem;
  }
  .name {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.9rem;
  }
  .chip {
    font-size: 0.72rem;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 999px;
    border: 1px solid var(--line-strong);
    color: var(--soft);
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }
  .chip.late {
    background: #fff;
    border-color: #fff;
    color: #000;
  }
  button {
    border: 1px solid var(--line);
    background: transparent;
    color: var(--dim);
    border-radius: 10px;
    width: 32px;
    height: 32px;
    font-size: 1rem;
    line-height: 1;
    cursor: pointer;
  }
  button:hover {
    color: var(--text);
    border-color: var(--line-strong);
  }
</style>
