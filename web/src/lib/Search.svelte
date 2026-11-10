<script>
  let { onsubmit, busy = false } = $props();
  let q = $state('');

  function submit(event) {
    event.preventDefault();
    const value = q.trim();
    if (value.length >= 2) onsubmit({ q: value });
  }
</script>

<form class="search" onsubmit={submit} role="search">
  <input
    bind:value={q}
    type="search"
    placeholder="Train number or name"
    aria-label="Train number or name"
    autocomplete="off"
    autocapitalize="characters"
    enterkeyhint="search"
  />
  <button type="submit" disabled={busy}>{busy ? 'Searching…' : 'Search'}</button>
</form>

<style>
  .search {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 8px;
  }
  input {
    height: 44px;
    min-width: 0;
    border-radius: 12px;
    border: 1px solid var(--line);
    background: var(--card);
    color: var(--text);
    font: inherit;
    padding: 0 12px;
  }
  input::placeholder {
    color: var(--dim);
  }
  input:focus-visible {
    outline: none;
    border-color: #6f6f6f;
    box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.08);
  }
  input::-webkit-search-cancel-button {
    filter: invert(0.6);
  }
  button {
    height: 44px;
    padding: 0 20px;
    border: 0;
    border-radius: 12px;
    background: var(--text);
    color: var(--accent-ink);
    font: inherit;
    font-weight: 600;
    letter-spacing: -0.01em;
    cursor: pointer;
  }
  button:hover:not(:disabled) {
    background: #e2e2e2;
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  @media (max-width: 560px) {
    .search {
      grid-template-columns: 1fr;
    }
  }
</style>
