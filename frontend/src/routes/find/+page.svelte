<script>
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { api } from '$lib/api.js';
  import { attempt } from '$lib/toast.svelte.js';
  import { licenseChip } from '$lib/license.js';
  import SourceImport from '$lib/SourceImport.svelte';

  const sp = page.url.searchParams;
  let platform = $state(sp.get('platform') || 'makerworld');
  let q = $state(sp.get('q') || '');
  let sort = $state(sp.get('sort') || 'downloads');
  let lic = $state(sp.get('lic') || 'sellable');
  let items = $state([]);
  let total = $state(0);
  let nextOffset = $state(0);
  let busy = $state(false);
  let done = $state(false);
  let picked = $state(null);
  let importer = $state();
  let sentinel = $state();
  let accts = $state(null);

  const PLATFORMS = [['makerworld', 'MakerWorld'], ['printables', 'Printables'], ['thingiverse', 'Thingiverse'], ['creality', 'Creality Cloud']];
  const LICS = {
    makerworld: [['CC0', 'CC0'], ['BY', 'CC BY'], ['BY-SA', 'CC BY-SA'], ['BY-ND', 'CC BY-ND']],
    printables: [['CC0', 'CC0'], ['BY', 'CC BY'], ['BY-SA', 'CC BY-SA'], ['BY-ND', 'CC BY-ND'], ['PR-COMMERCIAL', 'Commercial Use'], ['PR-COMMERCIAL-ND', 'Commercial, no derivatives'], ['GPL', 'GPL'], ['BSD', 'BSD']],
    thingiverse: [['CC0', 'Public domain'], ['BY', 'CC BY'], ['BY-SA', 'CC BY-SA'], ['BY-ND', 'CC BY-ND'], ['GPL', 'GPL'], ['BSD', 'BSD']],
    creality: [['CC0', 'CC0'], ['BY', 'CC BY'], ['BY-SA', 'CC BY-SA'], ['BY-ND', 'CC BY-ND']]
  };

  async function search(reset = true) {
    if (busy) return;
    busy = true;
    try {
      const off = reset ? 0 : nextOffset;
      const r = await attempt(() => api.get(`/api/sources/search?platform=${platform}&q=${encodeURIComponent(q)}&sort=${sort}&licenses=${lic}&offset=${off}&limit=40`));
      items = reset ? r.items : [...items, ...r.items.filter((x) => !items.some((y) => y.id === x.id))];
      total = r.total;
      nextOffset = r.next_offset ?? off + 40;
      done = (r.total != null && nextOffset >= r.total) || (r.items.length === 0 && nextOffset > off + 200);
      if (reset) goto(`?platform=${platform}&q=${encodeURIComponent(q)}&sort=${sort}&lic=${lic}`, { replaceState: true, keepFocus: true, noScroll: true });
    } catch { done = true; }
    finally { busy = false; }
  }
  function switchTo(p) { platform = p; lic = 'sellable'; picked = null; items = []; search(); }
  onMount(() => {
    api.get('/api/sources/status').then((s) => (accts = s)).catch(() => {});
    search();
    const io = new IntersectionObserver((e) => { if (e[0].isIntersecting && items.length && !done) search(false); }, { rootMargin: '600px' });
    if (sentinel) io.observe(sentinel);
    return () => io.disconnect();
  });
  async function pick(it) {
    picked = it;
    await Promise.resolve();
    importer?.preview(it.url);
  }
  const fmtN = (n) => (n == null ? '' : n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : n);
  const needToken = $derived(platform === 'thingiverse' && accts && !accts.thingiverse?.connected);
</script>

<div class="flex flex-wrap items-end gap-3 mb-3">
  <div class="flex-1 min-w-[220px]">
    <h1 class="text-2xl font-bold">Find models</h1>
    <p class="muted text-sm">Only licenses that allow selling prints. {total ? `${Number(total).toLocaleString()} matches` : ''}</p>
  </div>
</div>
<div class="flex gap-1 mb-3 scroll-x">
  {#each PLATFORMS as [k, l]}
    <button class="px-4 py-2 rounded-lg text-sm font-semibold whitespace-nowrap {platform === k ? 'bg-brand-600 text-white' : 'panel'}" onclick={() => switchTo(k)}>{l}</button>
  {/each}
</div>
{#if needToken}
  <div class="panel p-4 mb-4 text-sm">Thingiverse only works with an App Token. Create a free app at <a class="underline" target="_blank" href="https://www.thingiverse.com/apps/create">thingiverse.com/apps/create</a> (choose Web App), copy its App Token and paste it in <a class="underline" href="/settings">Settings</a>.</div>
{/if}
<form class="panel p-3 flex flex-wrap gap-2 mb-4" onsubmit={(e) => { e.preventDefault(); search(); }}>
  <input class="input flex-1 min-w-[200px]" placeholder="Search: planter, articulated dragon, cable organizer…" bind:value={q} />
  <select class="input w-auto" bind:value={lic} onchange={() => search()}>
    <option value="sellable">All that allow selling</option>
    {#each LICS[platform] as [v, l]}<option value={v}>{l}</option>{/each}
  </select>
  <select class="input w-auto" bind:value={sort} onchange={() => search()}>
    <option value="relevance">Best match</option><option value="downloads">Most downloaded</option>
    <option value="prints">Most printed</option><option value="likes">Most liked</option><option value="newest">Newest</option>
  </select>
  <button class="btn primary">Search</button>
</form>
{#if platform === 'thingiverse'}<p class="text-xs muted -mt-2 mb-3">Thingiverse can't filter by license, so the hub checks each result; pages load slower and may show fewer than 20.</p>{/if}

<div class="grid gap-5 {picked ? 'xl:grid-cols-[minmax(0,1fr)_420px]' : ''}">
  <div>
    <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-6 gap-3">
      {#each items as it (it.id)}
        {@const lc = licenseChip(it.license)}
        <button class="panel overflow-hidden text-left hover:shadow-lg transition {picked?.id === it.id ? 'ring-2 ring-brand-600' : ''}" onclick={() => pick(it)}>
          <div class="aspect-[3/4] bg-black/5 relative">
            {#if it.cover}<img src={it.cover} alt="" loading="lazy" class="w-full h-full object-cover" referrerpolicy="no-referrer" />{/if}
            {#if it.imported_model_id}<span class="chip live absolute top-2 left-2">In hub</span>{/if}
            {#if it.needs_parts}<span class="chip absolute top-2 right-2" style="background:var(--panel)">Needs hardware</span>{/if}
            {#if it.paid}<span class="chip warn absolute top-2 right-2" style="background:var(--panel)">Paid</span>{/if}
            {#if it.ip_flags?.length}<span class="chip bad absolute bottom-2 left-2" style="background:var(--panel)" title="Mentions {it.ip_flags.join(', ')}: a design license can't cover someone else's brand or character">IP risk</span>{/if}
          </div>
          <div class="p-2.5">
            <div class="text-sm font-semibold leading-snug line-clamp-2">{it.title}</div>
            <div class="text-xs muted truncate">{it.creator}</div>
            <div class="flex items-center gap-1.5 mt-1.5 text-xs">
              <span class="chip {lc.cls}" title={it.license_label}>{lc.label}</span>
              <span class="muted ml-auto">⬇ {fmtN(it.downloads)}</span>
            </div>
          </div>
        </button>
      {/each}
    </div>
    <div bind:this={sentinel} class="h-10 flex items-center justify-center text-sm muted mt-4">
      {#if busy}Loading…{:else if done && items.length}End of results{:else if !items.length}No results{/if}
    </div>
  </div>
  {#if picked}
    <div class="xl:sticky xl:top-4 self-start space-y-2">
      <div class="flex justify-between items-center">
        <a class="text-sm underline" target="_blank" href={picked.url}>Open on {PLATFORMS.find((p) => p[0] === platform)[1]}</a>
        <button class="btn sm" onclick={() => (picked = null)}>Close</button>
      </div>
      {#key picked.id}<SourceImport bind:this={importer} initial={picked.url} />{/key}
    </div>
  {/if}
</div>
