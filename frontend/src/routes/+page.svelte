<script>
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { api, fmtMoney } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  import { licenseChip } from '$lib/license.js';
  import SourceImport from '$lib/SourceImport.svelte';
  let byLink = $state(false);

  let items = $state([]);
  let total = $state(0);
  let pg = $state(1);
  let loading = $state(false);
  let done = $state(false);
  let cats = $state([]);
  let stats = $state(null);
  const sp = page.url.searchParams;
  let q = $state(sp.get('q') || '');
  let category = $state(sp.get('category') || '');
  let view = $state(sp.get('view') || 'all');
  let sort = $state(sp.get('sort') || 'recent');
  let size = $state(Number(globalThis.localStorage?.getItem('hub.size') || 260));
  let selecting = $state(false);
  let selected = $state(new Set());
  let sentinel;
  let timer;

  function params(p) {
    const u = new URLSearchParams({ page: String(p), per: '60', sort });
    if (q) u.set('q', q);
    if (category) u.set('category', category);
    if (view === 'live') u.set('live', 'true');
    if (view === 'hidden') u.set('live', 'false');
    if (view === 'supplies') { u.set('live', 'true'); u.set('attention', 'supplies'); }
    if (view === 'archived') u.set('status', 'archived');
    return u;
  }
  async function load(reset = false) {
    if (loading) return;
    if (reset) { pg = 1; done = false; }
    if (done) return;
    loading = true;
    try {
      const r = await api.get('/api/models?' + params(pg));
      items = reset ? r.items : [...items, ...r.items];
      total = r.total;
      done = items.length >= r.total || r.items.length === 0;
      pg += 1;
    } finally { loading = false; }
  }
  function refilter() {
    clearTimeout(timer);
    timer = setTimeout(() => {
      const u = new URLSearchParams();
      if (q) u.set('q', q); if (category) u.set('category', category);
      if (view !== 'all') u.set('view', view); if (sort !== 'recent') u.set('sort', sort);
      history.replaceState(history.state, '', u.toString() ? '?' + u : '/');
      load(true);
    }, 200);
  }
  onMount(() => {
    load(true);
    api.get('/api/categories').then((c) => (cats = c));
    api.get('/api/stats').then((s) => (stats = s));
    const io = new IntersectionObserver((e) => { if (e[0].isIntersecting) load(); }, { rootMargin: '800px' });
    io.observe(sentinel);
    return () => io.disconnect();
  });
  $effect(() => { try { localStorage.setItem('hub.size', String(size)); } catch {} });

  function toggleSel(id) {
    const s = new Set(selected); s.has(id) ? s.delete(id) : s.add(id); selected = s;
  }
  async function bulk(live) {
    const r = await attempt(() => api.post('/api/products/bulk-live', { model_ids: [...selected], live }));
    toast(`${live ? 'Going live' : 'Hiding'}: ${r.queued.length} queued${r.refused.length ? `, ${r.refused.length} need attention` : ''}`);
    if (r.refused.length) toast(r.refused.slice(0, 3).map((x) => `${x.code}: ${x.problems.join(', ')}`).join(' | '), 'bad', 9000);
    selected = new Set(); selecting = false; setTimeout(() => load(true), 1500);
  }
  async function bulkModels(action) {
    let confirm = null;
    if (action === 'delete') {
      confirm = prompt(`Permanently delete ${selected.size} model(s)? This removes their photos, files, store drafts, DAM photos and Watchtower files, and stops them being re-imported. License records are kept. Type DELETE to confirm.`);
      if (confirm == null) return;
    }
    const r = await attempt(() => api.post('/api/models/bulk', { model_ids: [...selected], action, confirm }));
    toast(`${action === 'delete' ? 'Deleting' : action === 'archive' ? 'Archived' : 'Restored'}: ${r.done.length}${r.refused.length ? `, ${r.refused.length} skipped` : ''}`);
    if (r.refused.length) toast(r.refused.slice(0, 3).map((x) => `${x.code}: ${x.problem}`).join(' | '), 'bad', 10000);
    selected = new Set(); selecting = false; setTimeout(() => load(true), action === 'delete' ? 2500 : 300);
  }
  let profiles = $state([]);
  async function bulkSlice() {
    if (!profiles.length) { try { profiles = (await api.get('/api/slicer/status')).profiles.filter((p) => p.active); } catch {} }
    if (!profiles.length) { toast('Add a printer on the Printers page first', 'bad'); return; }
    const names = profiles.map((p, i) => `${i + 1}. ${p.name}${p.is_default ? ' (default)' : ''}`).join('\n');
    const pick = prompt(`Slice ${selected.size} model(s) for which printer?\n${names}`, '1');
    const prof = profiles[Number(pick) - 1];
    if (!prof) return;
    const r = await attempt(() => api.post('/api/models/bulk-slice', { model_ids: [...selected], profile_id: prof.id, make_production: false }));
    toast(`${r.queued} model(s) queued for slicing on ${prof.name}; results land on each model's Files tab`);
    selected = new Set(); selecting = false;
  }
  async function bulkPush() {
    const r = await attempt(() => api.post('/api/products/bulk-push', { model_ids: [...selected], live: false }));
    toast(`${r.queued} product(s) queued to push as drafts`);
    selected = new Set(); selecting = false;
  }
</script>

<div class="flex flex-wrap items-end gap-3 mb-4">
  <div class="mr-auto">
    <h1 class="text-2xl font-bold">Models</h1>
    <p class="muted text-sm">{total} models{#if stats} · {stats.live} live on the store · {stats.on_woo} listed{/if}</p>
  </div>
  {#if stats?.live_out_of_supplies?.length}
    <button class="chip bad" onclick={() => { view = 'supplies'; refilter(); }}>⚠ {stats.live_out_of_supplies.length} live product(s) out of supplies</button>
  {/if}
  <a href="/get-app" class="btn" data-sveltekit-reload title="Install the Artisan Hub app on an Android phone">📱 Get the app</a>
  <button class="btn" onclick={() => (byLink = true)} title="Paste a model link from Printables, Thingiverse, MakerWorld, Thangs, MyMiniFactory, Cults3D or any model file">🔗 Add by link</button>
  <a href="/new" class="btn primary">+ Add model</a>
</div>

{#if byLink}
  <div class="fixed inset-0 z-50 flex items-start justify-center p-4 overflow-auto" style="background:rgba(0,0,0,.45)" role="presentation" onclick={(e) => { if (e.target === e.currentTarget) byLink = false; }} onkeydown={(e) => { if (e.key === 'Escape') byLink = false; }}>
    <div class="w-full max-w-2xl mt-10 relative" role="dialog" aria-modal="true" aria-label="Add a model by link">
      <button class="btn sm absolute right-3 top-3 z-10" onclick={() => (byLink = false)} aria-label="Close">✕</button>
      <SourceImport />
    </div>
  </div>
{/if}

<div class="panel p-3 mb-4 flex flex-wrap gap-2 items-center sticky top-2 z-20 shadow-sm">
  <input class="input w-64" placeholder="Search name, code, SKU…" bind:value={q} oninput={refilter} />
  <select class="input w-48" bind:value={category} onchange={refilter}>
    <option value="">All categories</option>
    {#each cats as c}<option value={c.id}>{c.name} ({c.count})</option>{/each}
  </select>
  <div class="flex rounded-lg border line overflow-hidden text-sm">
    {#each [['all', 'All'], ['live', 'Live'], ['hidden', 'Not live'], ['supplies', 'Out of supplies'], ['archived', 'Archived']] as [v, l]}
      <button class="px-3 py-1.5 {view === v ? 'bg-brand-600 text-white' : ''}" onclick={() => { view = v; refilter(); }}>{l}</button>
    {/each}
  </div>
  <select class="input w-40" bind:value={sort} onchange={refilter}>
    <option value="recent">Recently added</option><option value="published">Designer date</option>
    <option value="name">Name</option><option value="code">Code</option>
  </select>
  <div class="flex items-center gap-2 ml-auto text-sm muted">
    <span>Size</span><input type="range" min="160" max="460" step="20" bind:value={size} />
    <button class="btn sm" onclick={() => { selecting = !selecting; selected = new Set(); }}>{selecting ? 'Cancel' : 'Select'}</button>
  </div>
</div>

{#if selecting && selected.size}
  <div class="panel p-3 mb-4 flex flex-wrap gap-2 items-center sticky top-20 z-20 shadow">
    <b>{selected.size} selected</b>
    <button class="btn sm" onclick={bulkPush}>Push to store (draft)</button>
    <button class="btn sm" onclick={bulkSlice}>Slice for printer…</button>
    <button class="btn sm primary" onclick={() => bulk(true)}>Go live</button>
    <button class="btn sm" onclick={() => bulk(false)}>Hide</button>
    <span class="mx-1 muted">|</span>
    {#if view === 'archived'}<button class="btn sm" onclick={() => bulkModels('restore')}>Restore</button>
    {:else}<button class="btn sm" onclick={() => bulkModels('archive')}>Archive</button>{/if}
    <button class="btn sm danger" onclick={() => bulkModels('delete')}>Delete…</button>
  </div>
{/if}

<div class="grid gap-4" style="grid-template-columns: repeat(auto-fill, minmax({size}px, 1fr))">
  {#each items as m (m.id)}
    <a href={selecting ? undefined : `/models/${m.id}`} onclick={(e) => { if (selecting) { e.preventDefault(); toggleSel(m.id); } }}
       class="group panel overflow-hidden block relative transition hover:shadow-lg {selected.has(m.id) ? 'ring-2 ring-brand-500' : ''}">
      <div class="aspect-[4/5] bg-black/5 overflow-hidden">
        {#if m.thumb}
          <img src={size > 300 ? m.large : m.thumb} alt={m.name} loading="lazy" class="w-full h-full object-cover transition duration-300 group-hover:scale-[1.03]" />
        {:else}
          <div class="w-full h-full grid place-items-center muted text-sm">No photo yet</div>
        {/if}
      </div>
      <div class="absolute top-2 left-2 flex gap-1">
        {#if m.live}<span class="chip live">LIVE</span>{:else if m.on_woo}<span class="chip">Draft</span>{/if}
        {#if m.live && m.units === 0}<span class="chip bad">No supplies</span>{/if}
      </div>
      {#if selecting}<div class="absolute top-2 right-2 w-6 h-6 rounded-md border-2 border-white bg-black/30 grid place-items-center text-white">{selected.has(m.id) ? '✓' : ''}</div>{/if}
      <div class="p-3">
        <div class="flex items-start gap-2">
          <div class="flex-1 min-w-0">
            <div class="font-semibold leading-snug line-clamp-2">{m.name}</div>
            <div class="text-xs muted mt-0.5">{m.code} · {m.category || 'Uncategorized'}</div>
          </div>
          {#if m.price}<div class="font-semibold text-sm">{fmtMoney(m.price)}</div>{/if}
        </div>
        <div class="flex flex-wrap gap-1 mt-2">
          <span class="chip">{m.images} photos</span>
          {#if m.units != null}<span class="chip {m.units === 0 ? 'bad' : m.units < 3 ? 'warn' : 'ok'}">can build {m.units}</span>{/if}
          {#if m.print_ready}<span class="chip ok">print-ready</span>{/if}
          {#if !m.license_ok}<span class="chip {licenseChip(m.license_code).cls}">{licenseChip(m.license_code).label}</span>{:else if m.source_platform}<span class="chip">{({ makerworld: 'MakerWorld', printables: 'Printables', thingiverse: 'Thingiverse', creality: 'Creality' })[m.source_platform]}</span>{/if}
        </div>
      </div>
    </a>
  {/each}
</div>
<div bind:this={sentinel} class="h-10 grid place-items-center muted text-sm mt-4">{loading ? 'Loading…' : done && items.length ? 'That is everything.' : ''}</div>
{#if !loading && !items.length}
  <div class="panel p-10 text-center">
    <div class="text-lg font-semibold mb-1">No models yet</div>
    <p class="muted mb-4">Import runs automatically from the folders assigned to your team, or add one now.</p>
    <a href="/new" class="btn primary">Add a model</a>
  </div>
{/if}
