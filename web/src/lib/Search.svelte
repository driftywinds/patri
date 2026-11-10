<script>
  let { onsubmit, busy = false } = $props();
  let q = $state('');
  let by = $state('name');

  function submit(event) {
    event.preventDefault();
    const value = q.trim();
    if (value.length >= 2) onsubmit({ q: value, by });
  }
</script>

<form class="search" onsubmit={submit} role="search">
  <div class="field">
    <input
      bind:value={q}
      type="search"
      placeholder="Train number or name"
      aria-label="Train number or name"
      autocomplete="off"
      autocapitalize="characters"
      enterkeyhint="search"
    />
    <select bind:value={by} aria-label="Search by">
      <option value="name">Name</option>
      <option value="number">Number</option>
    </select>
  </div>
  <button type="submit" disabled={busy}>{busy ? 'Searching…' : 'Search'}</button>
</form>

<style>
  .search {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 8px;
    align-items: stretch;
  }
  .field {
    display: flex;
    align-items: stretch;
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 12px;
    overflow: hidden;
  }
  .field:focus-within {
    border-color: #6f6f6f;
    box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.08);
  }
  input {
    flex: 1;
    min-width: 0;
    border: 0;
    background: transparent;
    color: var(--text);
    font: inherit;
    padding: 0 12px;
    height: 44px;
  }
  input::placeholder {
    color: var(--dim);
  }
  input:focus {
    outline: none;
  }
  input::-webkit-search-cancel-button {
    filter: invert(0.6);
  }
  select {
    border: 0;
    border-left: 1px solid var(--line);
    background: transparent;
    color: var(--muted);
    font: inherit;
    font-size: 0.88rem;
    padding: 0 8px;
    height: 44px;
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
