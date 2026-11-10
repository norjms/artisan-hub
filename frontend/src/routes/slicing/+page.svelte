<script>
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { api, fmtTime, fmtMoney } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  import { can } from '$lib/session.svelte.js';
  import { stateLabel, stateCls, ENGINE } from '$lib/slicing.js';

  let view = $state(page.url.searchParams.get('view') || 'review');
  let g = $state(null);
  let pr = $state(null);
  let q = $state('');
  let filter = $state('');
  let offset = $state(0);
  const LIMIT = 100;
  let selected = $state(new Set());
  let t;

  async function loadGrid() {
    g = await api.get(`/api/slicing/grid?q=${encodeURIComponent(q)}&state=${filter}&limit=${LIMIT}&offset=${offset}`);
  }
  async function loadProblems() { pr = await api.get('/api/slicing/problems'); }
  // ---- review queue ----
  let rv = $state(null);
  let rvPrinter = $state('');
  let rvProblems = $state('');
  let rvOffset = $state(0);
  let rvSel = $state(new Set());
  let focus = $state(0);
  let rvBusy = $state(false);
  const RV_LIMIT = 48;
  async function loadReview() {
    rv = await api.get(`/api/slicing/review?${rvPrinter ? `profile_id=${rvPrinter}&` : ''}problems=${rvProblems}&limit=${RV_LIMIT}&offset=${rvOffset}`);
    rvSel = new Set([...rvSel].filter((id) => rv.items.some((x) => x.id === id)));
    focus = Math.min(focus, Math.max(0, rv.items.length - 1));
  }
  function rvToggle(id) { const s = new Set(rvSel); s.has(id) ? s.delete(id) : s.add(id); rvSel = s; }
  async function rvApprove(ids) {
    if (!ids.length) return;
    rvBusy = true;
    try {
      const r = await attempt(() => api.post('/api/slice-results/bulk-approve', { ids }));
      toast(`Approved ${r.approved}${r.skipped.length ? `, ${r.skipped.length} skipped` : ''}`);
      rvSel = new Set(); await loadReview(); loadProblems();
    } finally { rvBusy = false; }
  }
  async function rvReject(ids) {
    if (!ids.length || !confirm(`Discard ${ids.length} slice(s)? Approved versions stay in use.`)) return;
    rvBusy = true;
    try {
      const r = await attempt(() => api.post('/api/slice-results/bulk-reject', { ids }));
      toast(`Discarded ${r.discarded}`);
      rvSel = new Set(); await loadReview(); loadProblems();
    } finally { rvBusy = false; }
  }
  function onKey(e) {
    if (view !== 'review' || !rv?.items?.length || ['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target?.tagName)) return;
    const it = rv.items[focus];
    if (e.key === 'j' || e.key === 'ArrowRight') { focus = Math.min(rv.items.length - 1, focus + 1); e.preventDefault(); }
    else if (e.key === 'k' || e.key === 'ArrowLeft') { focus = Math.max(0, focus - 1); e.preventDefault(); }
    else if (e.key === 'x' && it) rvToggle(it.id);
    else if (e.key === 'a' && it && can('slice')) rvApprove([it.id]);
    else if (e.key === 'd' && it && can('slice')) rvReject([it.id]);
    else return;
    document.getElementById(`rv-${rv.items[focus]?.id}`)?.scrollIntoView({ block: 'nearest' });
  }
  async function sliceLibrary() {
    const d = g?.printers?.find((p) => p.is_default) || g?.printers?.[0];
    if (!d) return;
    if (!confirm(`Slice every model that has no slice yet for ${d.name}? They run in the background at low priority, two at a time, and each one waits for review.`)) return;
    const r = await attempt(() => api.post('/api/slicing/slice-all', { profile_id: d.id }));
    toast(`Queued ${r.queued} model(s)${r.nothing_to_slice ? `; ${r.nothing_to_slice} have no model file` : ''}`);
    loadGrid();
  }
  const load = () => (view === 'grid' ? loadGrid() : view === 'review' ? loadReview() : loadProblems());
  onMount(() => {
    load(); loadProblems(); if (view !== 'review') api.get('/api/slicing/review?limit=1').then((r) => (rv = r)).catch(() => {});
    window.addEventListener('keydown', onKey);
    const t = setInterval(() => { if (view === 'review' && rv?.queued) loadReview(); }, 20000);
    return () => { window.removeEventListener('keydown', onKey); clearInterval(t); };
  });
  function search() { clearTimeout(t); t = setTimeout(() => { offset = 0; loadGrid(); }, 300); }

  async function reslice(states, profile_id = null) {
    const r = await attempt(() => api.post('/api/slicing/reslice', { states, profile_id }));
    toast(r.queued ? `Queued ${r.queued} slice(s)` : 'Nothing to re-slice');
    load(); loadProblems();
  }
  async function checkStale() {
    await attempt(() => api.post('/api/slicing/check-stale'), 'Checking what changed…');
    setTimeout(() => { load(); loadProblems(); }, 4000);
  }
  async function sliceSelected(pid) {
    const ids = [...selected];
    const r = await attempt(() => api.post('/api/models/bulk-slice', { model_ids: ids, profile_ids: pid ? [pid] : g.printers.map((p) => p.id) }));
    toast(`Queued ${r.queued} slice(s)`);
    selected = new Set(); loadGrid();
  }
  async function approve(rid) {
    await attempt(() => api.post(`/api/slice-results/${rid}/approve`), 'Approved');
    load(); loadProblems();
  }
  function toggle(id) { const s = new Set(selected); s.has(id) ? s.delete(id) : s.add(id); selected = s; }
  const KIND = { failed: ['Failed', 'bad'], stale: ['Out of date', 'warn'], problem: ['Problem', 'warn'], review: ['Needs review', 'warn'], unsliced: ['Not sliced', ''] };
</script>

<h1 class="text-2xl font-bold mb-1">Slicing</h1>
<p class="muted text-sm mb-4 max-w-3xl">Every model on every printer. A new slice waits for review; once approved it is the file that printer uses.
  A slice goes out of date when its files, the printer, the colours or the slicer version change.</p>

<div class="border-b line flex gap-1 mb-4">
  {#each [['review', `Review${rv ? ` (${rv.total})` : ''}`], ['grid', 'All models'], ['problems', `Problems${pr ? ` (${pr.items.length})` : ''}`]] as [k, l]}
    <button class="px-4 py-2.5 text-sm font-semibold border-b-2 {view === k ? 'border-brand-600' : 'border-transparent muted'}" onclick={() => { view = k; load(); }}>{l}</button>
  {/each}
</div>

{#if view === 'review'}
  <div class="flex flex-wrap items-center gap-2 mb-3 text-sm">
    <select class="input w-auto" bind:value={rvPrinter} onchange={() => { rvOffset = 0; loadReview(); }}>
      <option value="">Every printer</option>{#each rv?.printers || [] as p}<option value={p.id}>{p.name}</option>{/each}
    </select>
    <select class="input w-auto" bind:value={rvProblems} onchange={() => { rvOffset = 0; loadReview(); }}>
      <option value="">With or without problems</option><option value="none">No problems</option><option value="some">With problems</option>
    </select>
    {#if rv}<span class="muted">{rv.total} waiting{rv.queued ? ` · ${rv.queued} still slicing` : ''}</span>{/if}
    {#if can('slice') && rv?.items?.length}
      <span class="ml-auto flex flex-wrap gap-2">
        {#if rvSel.size}
          <button class="btn sm primary" disabled={rvBusy} onclick={() => rvApprove([...rvSel])}>Approve {rvSel.size} selected</button>
          <button class="btn sm danger" disabled={rvBusy} onclick={() => rvReject([...rvSel])}>Discard {rvSel.size}</button>
          <button class="btn sm" onclick={() => (rvSel = new Set())}>Clear</button>
        {:else}
          <button class="btn sm" onclick={() => (rvSel = new Set(rv.items.map((x) => x.id)))}>Select this page</button>
          <button class="btn sm primary" disabled={rvBusy || !rv.items.some((x) => !x.problems.length)}
            onclick={() => rvApprove(rv.items.filter((x) => !x.problems.length).map((x) => x.id))}>Approve all on this page without problems</button>
        {/if}
      </span>
    {/if}
  </div>
  <p class="text-xs muted mb-3">Keys: j / k or arrows move, a approves, d discards, x selects.</p>
  {#if rv}
    <div class="grid sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-3">
      {#each rv.items as x, i (x.id)}
        <div id="rv-{x.id}" class="panel p-3 text-sm {focus === i ? 'ring-2 ring-brand-600' : ''} {rvSel.has(x.id) ? 'bg-brand-50 dark:bg-brand-100' : ''}"
          role="presentation" onclick={() => (focus = i)}>
          <div class="relative">
            {#if x.previews[0]}<img src={x.previews[0]} alt="Slice preview of {x.code}" class="w-full aspect-[4/3] object-contain rounded-lg bg-black/5" loading="lazy" />
            {:else}<div class="w-full aspect-[4/3] rounded-lg bg-black/5 grid place-items-center muted text-xs">no preview</div>{/if}
            {#if x.photo}<img src={x.photo} alt="Photo of {x.code}" class="absolute right-2 bottom-2 w-16 h-16 object-cover rounded-md border-2 border-white shadow" loading="lazy" />{/if}
            <label class="absolute left-2 top-2 bg-white/90 dark:bg-black/60 rounded px-1.5 py-0.5"><input type="checkbox" checked={rvSel.has(x.id)} onchange={() => rvToggle(x.id)} aria-label="Select {x.code}" /></label>
          </div>
          <div class="mt-2 flex items-start gap-2">
            <a class="font-semibold hover:underline min-w-0" href="/models/{x.model_id}?tab=print"><span class="block truncate">{x.code} {x.name}</span></a>
            <span class="chip ml-auto shrink-0">{x.printer}</span>
          </div>
          <div class="flex flex-wrap gap-x-3 gap-y-0.5 text-xs mt-1">
            <span><b>{fmtTime(x.time_s)}</b></span><span><b>{x.per_unit_g} g</b>/unit</span>
            {#if x.cost}<span><b>{fmtMoney(x.cost.total)}</b></span>{/if}
            {#if (x.plates || 1) > 1 || (x.copies || 1) > 1}<span class="muted">{x.plates} plate(s) · {x.copies} cop{x.copies === 1 ? 'y' : 'ies'}</span>{/if}
            {#if x.tools.length > 1}<span class="muted">{x.tools.length} colours{x.tower_g ? ` · tower ${x.tower_g} g` : ''}</span>{/if}
          </div>
          {#if x.replaces}<div class="text-xs mt-1" style="color:var(--warn)">Replaces approved: {fmtTime(x.replaces.time_s)}, {x.replaces.per_unit_g} g</div>{/if}
          <div class="text-[11px] muted truncate" title={x.process}>{x.process}</div>
          {#each x.problems as p}<div class="text-xs mt-1" style="color:var(--warn)">{p.msg}</div>{/each}
          {#each x.warnings.slice(0, 2) as w}<div class="text-[11px] muted mt-0.5 truncate" title={w}>{w}</div>{/each}
          {#if can('slice')}
            <div class="flex gap-2 mt-2">
              <button class="btn sm primary" disabled={rvBusy} onclick={() => rvApprove([x.id])}>Approve</button>
              <button class="btn sm danger" disabled={rvBusy} onclick={() => rvReject([x.id])}>Discard</button>
              <a class="btn sm ml-auto" href="/models/{x.model_id}?tab=print">Open</a>
            </div>
          {/if}
        </div>
      {:else}
        <div class="panel p-6 text-sm muted sm:col-span-2 xl:col-span-3 2xl:col-span-4 text-center">Nothing waiting for review.{rv.queued ? ` ${rv.queued} still slicing.` : ''}</div>
      {/each}
    </div>
    {#if rv.total > RV_LIMIT}
      <div class="flex items-center gap-2 mt-3 text-sm">
        <span class="muted">{rvOffset + 1} to {Math.min(rvOffset + RV_LIMIT, rv.total)} of {rv.total}</span>
        <span class="ml-auto flex gap-2">
          <button class="btn sm" disabled={rvOffset === 0} onclick={() => { rvOffset = Math.max(0, rvOffset - RV_LIMIT); loadReview(); }}>Previous</button>
          <button class="btn sm" disabled={rvOffset + RV_LIMIT >= rv.total} onclick={() => { rvOffset += RV_LIMIT; loadReview(); }}>Next</button>
        </span>
      </div>
    {/if}
  {/if}
{:else if view === 'grid'}
  <div class="flex flex-wrap items-center gap-2 mb-3">
    <input class="input w-64" placeholder="Search code or name" bind:value={q} oninput={search} />
    <select class="input w-auto" bind:value={filter} onchange={() => { offset = 0; loadGrid(); }}>
      <option value="">Any state</option>
      {#each ['none', 'review', 'approved', 'stale', 'failed', 'queued'] as s}<option value={s}>{stateLabel(s)}{g?.counts?.[s] ? ` (${g.counts[s]})` : ''}</option>{/each}
    </select>
    {#if can('slice')}
      <span class="ml-auto flex flex-wrap gap-2">
        <button class="btn sm primary" onclick={sliceLibrary}>Slice the whole library</button>
        <button class="btn sm" onclick={checkStale}>Check what changed</button>
        <button class="btn sm" onclick={() => reslice(['stale', 'failed'])}>Re-slice out of date and failed</button>
      </span>
    {/if}
  </div>
  {#if selected.size && can('slice')}
    <div class="panel p-3 mb-3 flex flex-wrap items-center gap-2 text-sm">
      <b>{selected.size} selected</b>
      <button class="btn sm primary" onclick={() => sliceSelected(null)}>Slice for every printer</button>
      {#each g.printers as p}<button class="btn sm" onclick={() => sliceSelected(p.id)}>Slice for {p.name}</button>{/each}
      <button class="btn sm" onclick={() => (selected = new Set())}>Clear</button>
    </div>
  {/if}
  {#if g}
    {#if !g.printers.length}
      <div class="panel p-5 text-sm">No printers yet. Add one on the <a class="underline" href="/printers">Printers</a> page.</div>
    {:else}
      <div class="panel scroll-x">
        <table class="grid text-sm">
          <thead><tr><th></th><th>Model</th><th>Colours</th>
            {#each g.printers as p}<th class="whitespace-nowrap">{p.name}<div class="text-[10px] muted font-normal">{ENGINE[p.engine] || p.engine} · {p.tools}T{p.is_default ? ' · default' : ''}</div></th>{/each}
          </tr></thead>
          <tbody>
            {#each g.rows as r (r.id)}
              <tr>
                <td><input type="checkbox" checked={selected.has(r.id)} onchange={() => toggle(r.id)} aria-label="Select {r.code}" /></td>
                <td><a class="hover:underline block max-w-[240px]" href="/models/{r.id}?tab=print" title={r.name}><b class="block truncate">{r.code}</b><span class="muted block truncate text-xs">{r.name}</span></a></td>
                <td>{r.colors_needed}</td>
                {#each r.cells as c}
                  <td class="whitespace-nowrap">
                    <span class="chip {stateCls(c.state)}" title={c.error || ''}>{stateLabel(c.state)}</span>
                    {#if c.problems}<span class="chip warn" title="See the Print tab">!</span>{/if}
                    {#if c.time_s}<div class="text-[11px] muted">{fmtTime(c.time_s)} · {c.grams} g{c.cost != null ? ` · ${fmtMoney(c.cost)}` : ''}</div>{/if}
                    {#if c.state === 'review' && can('slice')}<button class="text-[11px] underline" onclick={() => approve(c.id)}>approve</button>{/if}
                  </td>
                {/each}
              </tr>
            {:else}<tr><td colspan={3 + g.printers.length} class="muted text-center py-6">No models match.</td></tr>{/each}
          </tbody>
        </table>
      </div>
      <div class="flex items-center gap-2 mt-3 text-sm">
        <span class="muted">{g.total} model(s)</span>
        <span class="ml-auto flex gap-2">
          <button class="btn sm" disabled={offset === 0} onclick={() => { offset = Math.max(0, offset - LIMIT); loadGrid(); }}>Previous</button>
          <button class="btn sm" disabled={offset + LIMIT >= g.total} onclick={() => { offset += LIMIT; loadGrid(); }}>Next</button>
        </span>
      </div>
    {/if}
  {/if}
{:else if pr}
  <div class="flex flex-wrap gap-2 mb-3 text-sm">
    {#each Object.entries(pr.counts).filter(([, n]) => n) as [k, n]}<span class="chip {KIND[k]?.[1] || ''}">{KIND[k]?.[0] || k}: {n}</span>{/each}
    {#if can('slice') && (pr.counts.stale || pr.counts.failed)}<button class="btn sm ml-auto" onclick={() => reslice(['stale', 'failed'])}>Re-slice out of date and failed</button>{/if}
  </div>
  <div class="panel scroll-x">
    <table class="grid text-sm">
      <thead><tr><th>What</th><th>Model</th><th>Printer</th><th>Details</th><th></th></tr></thead>
      <tbody>
        {#each pr.items as x}
          <tr>
            <td><span class="chip {KIND[x.kind]?.[1] || ''}">{KIND[x.kind]?.[0] || x.kind}</span></td>
            <td><a class="hover:underline" href="/models/{x.model_id}?tab=print"><b>{x.code}</b> <span class="muted">{x.name}</span></a></td>
            <td>{x.printer}</td>
            <td class="text-xs max-w-[420px] break-words">{x.message}{#each x.details.filter((d) => d !== x.message) as d}<div class="muted">{d}</div>{/each}</td>
            <td class="whitespace-nowrap">
              {#if x.kind === 'review' && can('slice')}<button class="btn sm" onclick={() => approve(x.result_id)}>Approve</button>{/if}
              <a class="btn sm" href="/models/{x.model_id}?tab=print">Open</a>
            </td>
          </tr>
        {:else}<tr><td colspan="5" class="muted text-center py-6">Nothing needs attention.</td></tr>{/each}
      </tbody>
    </table>
  </div>
{/if}
