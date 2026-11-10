<script>
  import { train as fetchTrain } from './api.js';

  let { no } = $props();

  let data = $state(null);
  let loading = $state(true);
  let error = $state('');
  let seq = 0;

  async function load(n, force = false) {
    const mine = ++seq;
    loading = true;
    error = '';
    try {
      const res = await fetchTrain(n, force);
      if (mine !== seq) return;
      data = res;
    } catch (e) {
      if (mine !== seq) return;
      error = e.message;
    } finally {
      if (mine === seq) loading = false;
    }
  }

  $effect(() => {
    load(no);
  });

  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  let st = $derived(data?.status ?? null);

  let tone = $derived.by(() => {
    if (!st) return 'muted';
    if (st.message || st.state !== 'running') return 'muted';
    const d = st.delay ?? 0;
    return d > 4 ? 'late' : d > 0 ? 'warn' : 'ok';
  });

  let headline = $derived.by(() => {
    if (!st) return '';
    if (st.message) return st.message;
    if (st.state === 'finished') return `Reached ${title(data.train.dest_name || data.train.dest)}`;
    if (st.state === 'yet_to_start') return 'Yet to start';
    const d = st.delay ?? 0;
    if (d > 0) return `Running ${dur(d)} late`;
    if (d < 0) return `${dur(-d)} early`;
    return 'Running on time';
  });

  let subline = $derived.by(() => {
    if (!st || !data) return '';
    const bits = [];
    if (st.state === 'yet_to_start') {
      const t = data.stops[0]?.dep?.sch;
      if (t) bits.push(`departs ${t.t}`);
    } else if (st.last_station) {
      bits.push(`last reported ${title(st.last_station)}${st.last_update ? ` · ${st.last_update}` : ''}`);
    }
    bits.push(`fetched ${ago(data.fetched_at)}`);
    return bits.join(' · ');
  });

  let nextIndex = $derived(data ? data.stops.findIndex((s) => !s.departed) : -1);

  let runLabel = $derived.by(() => {
    if (!data) return '';
    const [y, m, d] = data.train.run_date.split('-');
    return `${Number(d)} ${MON[Number(m) - 1]} ${y}`;
  });

  function title(s) {
    return String(s || '')
      .toLowerCase()
      .replace(/\b[a-z]/g, (c) => c.toUpperCase());
  }
  function dur(m) {
    return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60 ? `${m % 60} min` : ''}`.trim() : `${m} min`;
  }
  function ago(ts) {
    const mins = Math.round((Date.now() / 1000 - ts) / 60);
    return mins < 1 ? 'just now' : `${mins} min ago`;
  }
  function times(stop, which) {
    const a = stop.arr[which];
    const d = stop.dep[which];
    if (!a && !d) return '';
    if (a && d && a.t !== d.t) return `${a.t}–${d.t}`;
    return (d || a).t;
  }
  function dayTag(stop) {
    const v = stop.dep.sch || stop.arr.sch || stop.dep.exp || stop.arr.exp;
    return v && v.d ? `day ${v.d + 1}` : '';
  }
  function differs(stop) {
    const e = times(stop, 'exp');
    return e && e !== times(stop, 'sch');
  }
  function delta(stop) {
    return stop.dep.delay ?? stop.arr.delay;
  }
  function chip(stop) {
    const m = delta(stop);
    if (m == null) return '';
    // "on time" is only worth saying for stops the train already served.
    if (m === 0 && !stop.arrived && !stop.departed) return '';
    return m === 0 ? 'on time' : m > 0 ? `+${m}m` : `${m}m`;
  }
  function chipClass(stop) {
    const m = delta(stop);
    return m == null ? '' : m > 0 ? 'late' : m < 0 ? 'early' : 'zero';
  }
</script>

<svelte:head>
  <title>{data ? `${data.train.no} ${data.train.name} · patri` : 'patri'}</title>
</svelte:head>

{#if !data && loading}
  <p class="state">Fetching from NTES…</p>
{:else if !data && error}
  <div class="card">
    <p class="err">{error}</p>
    <button class="ghost" onclick={() => load(no, true)}>Try again</button>
  </div>
{:else if data}
  {#if error}
    <p class="flash">{error} — showing the last result.</p>
  {/if}

  <section class="banner {tone}">
    <strong>{headline}</strong>
    <p>{subline}</p>
  </section>

  <div class="head">
    <div>
      <h1>{data.train.name}</h1>
      <p class="route">
        #{data.train.no} · {data.train.source} → {data.train.dest}{#if data.train.distance}
          · {data.train.distance} km
        {/if}
      </p>
      <p class="runof">run of {runLabel}</p>
    </div>
    <button class="ghost" onclick={() => load(no, true)} disabled={loading}>
      {loading ? '…' : 'Refresh'}
    </button>
  </div>

  {#if data.status.note && data.status.state === 'running'}
    <p class="note">{data.status.note}</p>
  {/if}

  <ol class="stops">
    {#each data.stops as s, i (s.seq)}
      <li class="stop {i === nextIndex ? 'next' : ''} {s.departed ? 'passed' : ''}">
        <span class="rail" aria-hidden="true"><span class="dot"></span></span>
        <div class="row">
          <div class="times">
            <span class="sch">{times(s, 'sch')}</span>
            {#if differs(s)}
              <span class="exp">
                <span class="lbl">{s.departed ? 'act' : 'exp'}</span>
                {times(s, 'exp')}
              </span>
            {/if}
          </div>
          <div class="meta">
            <span class="stn">{s.name}</span>
            <span class="sub">
              {s.code}{#if s.pf} · PF {s.pf}{/if}{#if s.dist != null} · {s.dist} km{/if}{#if dayTag(s)}
                · {dayTag(s)}
              {/if}
            </span>
          </div>
          {#if chip(s)}
            <span class="delta {chipClass(s)}">{chip(s)}</span>
          {/if}
        </div>
      </li>
    {/each}
  </ol>
{/if}

<style>
  .state {
    color: var(--muted);
    padding: 24px 0;
  }
  .card {
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 16px;
  }
  .err {
    margin: 0 0 12px;
    color: var(--soft);
  }
  .flash {
    margin: 0 0 12px;
    padding: 8px 12px;
    border-radius: 10px;
    background: var(--raised);
    border: 1px solid var(--line);
    color: var(--soft);
    font-size: 0.85rem;
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
    white-space: nowrap;
  }
  .ghost:hover:not(:disabled) {
    background: var(--raised);
  }
  .ghost:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .banner {
    border-radius: 14px;
    padding: 12px 14px;
    margin-bottom: 16px;
    background: var(--card);
    border: 1px solid var(--line);
    border-left: 3px solid var(--line-strong);
  }
  .banner strong {
    display: block;
    font-size: 1.05rem;
    letter-spacing: -0.015em;
  }
  .banner p {
    margin: 2px 0 0;
    font-size: 0.84rem;
    color: var(--muted);
  }
  .banner.late {
    border-left-color: #ffffff;
    background: #0d0d0d;
  }
  .banner.late strong {
    color: #ffffff;
  }
  .banner.warn {
    border-left-color: #b9b9b9;
  }
  .banner.warn strong {
    color: var(--soft);
  }
  .banner.ok {
    border-left-color: #6f6f6f;
  }
  .banner.ok strong {
    color: var(--soft);
    font-weight: 500;
  }
  .banner.muted {
    border-left-color: #2a2a2a;
  }
  .banner.muted strong {
    color: var(--muted);
    font-weight: 500;
  }
  .head {
    display: flex;
    gap: 12px;
    align-items: flex-start;
    justify-content: space-between;
  }
  h1 {
    font-size: 1.32rem;
    margin: 0;
    letter-spacing: -0.02em;
  }
  .route {
    margin: 4px 0 0;
    color: var(--muted);
    font-size: 0.9rem;
  }
  .runof {
    margin: 2px 0 0;
    color: var(--dim);
    font-size: 0.76rem;
  }
  .note {
    margin: 14px 0 0;
    padding: 10px 12px;
    border-left: 2px solid var(--line-strong);
    color: var(--muted);
    font-size: 0.8rem;
  }
  .stops {
    list-style: none;
    margin: 16px 0 0;
    padding: 0;
  }
  .stop {
    display: grid;
    grid-template-columns: 26px 1fr;
  }
  .rail {
    position: relative;
  }
  .rail::before {
    content: '';
    position: absolute;
    left: 50%;
    top: 0;
    bottom: 0;
    width: 1px;
    background: var(--line);
    transform: translateX(-50%);
  }
  .stop:first-child .rail::before {
    top: 22px;
  }
  .stop:last-child .rail::before {
    bottom: auto;
    height: 22px;
  }
  .dot {
    position: absolute;
    left: 50%;
    top: 17px;
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: var(--bg);
    border: 2px solid #3a3a3a;
    transform: translateX(-50%);
  }
  .stop.passed .dot {
    background: #6f6f6f;
    border-color: #6f6f6f;
  }
  .stop.next .dot {
    background: #ffffff;
    border-color: #ffffff;
    box-shadow: 0 0 0 4px rgba(255, 255, 255, 0.14);
  }
  .row {
    display: grid;
    grid-template-columns: 108px minmax(0, 1fr) auto;
    gap: 12px;
    align-items: center;
    padding: 12px 0;
    border-bottom: 1px solid var(--line);
    min-height: 60px;
  }
  .stop:last-child .row {
    border-bottom: 0;
  }
  .times {
    display: flex;
    flex-direction: column;
    font-variant-numeric: tabular-nums;
    line-height: 1.35;
    min-width: 0;
    overflow: hidden;
  }
  .sch {
    font-weight: 600;
    font-size: 0.95rem;
    white-space: nowrap;
    color: var(--text);
  }
  .exp {
    font-size: 0.72rem;
    color: var(--soft);
    white-space: nowrap;
  }
  .stop.passed .exp {
    color: var(--muted);
  }
  .lbl {
    color: var(--dim);
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-right: 3px;
  }
  .meta {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .stn {
    font-weight: 500;
    color: #ededed;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .sub {
    color: var(--dim);
    font-size: 0.76rem;
    font-variant-numeric: tabular-nums;
  }
  .stop.passed .stn,
  .stop.passed .sch {
    color: var(--muted);
    font-weight: 500;
  }
  .delta {
    font-size: 0.76rem;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 999px;
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
    border: 1px solid var(--line-strong);
    color: var(--soft);
  }
  .delta.late {
    background: #ffffff;
    border-color: #ffffff;
    color: #000000;
  }
  .delta.early {
    background: transparent;
    color: var(--soft);
  }
  .delta.zero {
    background: transparent;
    border-color: var(--line);
    color: var(--dim);
    font-weight: 500;
  }
  @media (max-width: 420px) {
    .row {
      grid-template-columns: 98px minmax(0, 1fr) auto;
      gap: 8px;
    }
    .sch {
      font-size: 0.88rem;
    }
    .exp {
      font-size: 0.66rem;
    }
    h1 {
      font-size: 1.15rem;
    }
  }
</style>
