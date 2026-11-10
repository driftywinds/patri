async function get(url) {
  const res = await fetch(url, { headers: { Accept: 'application/json' } });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `request failed (${res.status})`);
  return body;
}

export const search = (q) => get(`/api/search?q=${encodeURIComponent(q)}`);

export const train = (no, refresh = false) =>
  get(`/api/train/${encodeURIComponent(no)}${refresh ? '?refresh=1' : ''}`);
