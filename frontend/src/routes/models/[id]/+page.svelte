<script>
  import Page from '$lib/Page.svelte';
  import Glyph from '$lib/Glyph.svelte';
  import { farmName, FarmName } from '$lib/nav.svelte.js';
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { api, fmtMoney, fmtTime, ago } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  import Gallery from '$lib/Gallery.svelte';
  import Listing from '$lib/tabs/Listing.svelte';
  import Colors from '$lib/tabs/Colors.svelte';
  import Bom from '$lib/tabs/Bom.svelte';
  import Files from '$lib/tabs/Files.svelte';
  import Print from '$lib/tabs/Print.svelte';
  import Photos from '$lib/tabs/Photos.svelte';
  import License from '$lib/tabs/License.svelte';
  import CreateOrder from '$lib/CreateOrder.svelte';
  import { can } from '$lib/session.svelte.js';
  import { licenseChip } from '$lib/license.js';

  let m = $state(null);
  let ordering = $state(false);
  let tab = $state(page.url.searchParams.get('tab') || 'overview');
  let showArchive = $state(false);
  let busyLive = $state(false);
  let poll;

  const id = $derived(page.params.id);
  const gallery = $derived(m ? m.images_all.filter((i) => showArchive || (i.role !== 'archive')) : []);
  const archiveCount = $derived(m ? m.images_all.filter((i) => i.role === 'archive').length : 0);
  const p = $derived(m?.product);
  const lc = $derived(licenseChip(m?.license?.code));
  const av = $derived(m?.availability);
  const pending = $derived(m?.jobs?.some((j) => j.state === 'queued' || j.state === 'running'));

  async function load() {
    m = await api.get(`/api/models/${id}`);
  }
  onMount(() => {
    poll = setInterval(() => { if (pending) load(); }, 3000);
    return () => clearInterval(poll);
  });
  $effect(() => { id; load(); });

  async function toggleLive() {
    busyLive = true;
    try {
      await attempt(() => api.post(`/api/products/${p.id}/live`, { live: !p.live }), p.live ? 'Hiding on the store…' : 'Going live…');
      setTimeout(load, 1500);
    } finally { busyLive = false; }
  }
  async function push() {
    await attempt(() => api.post(`/api/products/${p.id}/push`), 'Pushing to WooCommerce…');
    setTimeout(load, 1500);
  }
  let delInfo = $state(null);
  let delCode = $state('');
  async function archiveIt() {
    await attempt(() => api.del(`/api/models/${m.id}`), 'Archived. Find it under Models > Archived.');
    load();
  }
  async function restoreIt() { await attempt(() => api.post(`/api/models/${m.id}/restore`), 'Restored'); load(); }
  async function askDelete() { delCode = ''; delInfo = await attempt(() => api.get(`/api/models/${m.id}/delete-check`)); }
  async function doDelete() {
    await attempt(() => api.post(`/api/models/${m.id}/purge`, { confirm: delCode }), 'Deleting everywhere');
    goto('/');
  }
  async function fav() {
    m = await api.patch(`/api/models/${m.id}`, { favorite: !m.favorite });
  }
  const unitsLabel = (a) => (a ? (a.has_bom === false ? 'No materials set' : `${a.units ?? 0} buildable`) : '');
</script>

<Page title={m?.name || 'Model'} crumb={m?.code || null} description={m ? `${m.code}, ${m.category || 'Uncategorized'}${m.designer ? `, by ${m.designer}` : ''}` : ''}>
{#snippet actions()}
  {#if m}
    {#if can('models')}<button class="btn" onclick={() => (tab = 'listing')}>Edit</button>{/if}
    <button class="btn" onclick={fav} aria-pressed={!!m.favorite} title="Favorite"><Glyph name="star" fill={!!m.favorite} />{m.favorite ? 'Favorite' : 'Add to favorites'}</button>
  {/if}
{/snippet}
{#if !m}
  <div class="muted">Loading...</div>
{:else}

  <div class="grid grid-cols-1 xl:grid-cols-[minmax(0,1.6fr)_minmax(340px,1fr)] gap-5">
    <div><Gallery images={gallery} {archiveCount} bind:showArchive /></div>

    <div class="flex flex-col gap-4">
      <div class="panel p-5">
        <div class="flex items-start gap-3">
          <div class="flex-1 min-w-0">
            <div class="text-xs muted font-semibold tracking-wide">{m.code}{#if m.designer} · by {#if m.designer_info?.url}<a class="hover:underline" target="_blank" href={m.designer_info.url}>{m.designer}</a>{:else}{m.designer}{/if}{/if}</div>
            <div class="ah-card-title mt-1">{m.name}</div>
          </div>
        </div>
        <div class="flex flex-wrap gap-1.5 mt-3">
          {#if p?.live}<span class="chip live">Live on the store</span>{:else if p?.woo_product_id}<span class="chip">Draft on store</span>{:else}<span class="chip">Not on store</span>{/if}
          <button class="chip {m.license?.problems?.length ? (lc.sell ? 'warn' : 'bad') : 'ok'}" onclick={() => (tab = 'license')} title={m.license?.problems?.join('; ') || m.license?.label}>{lc.label}{m.license?.problems?.length ? (lc.sell ? ': needs designer' : ': no selling') : ''}</button>
          {#if m.license?.ip_flags?.length}<span class="chip bad" title="A design license can't grant rights to someone else's brand or character">IP check: {m.license.ip_flags.join(', ')}</span>{/if}
          {#if m.license?.needs_credit}<span class="chip" title={m.license.credit_line}>Credit on listing</span>{/if}
          {#if m.print_ready}<span class="chip ok">Print-ready file</span>{:else}<span class="chip warn">Needs sliced file</span>{/if}
          {#if pending}<span class="chip">Syncing</span>{/if}
          {#if m.status === 'archived'}<span class="chip warn">Archived</span>{/if}
        </div>

        {#if p}
          <div class="mt-5 flex items-center gap-4">
            <div class="flex-1">
              <div class="ah-big">{p.price ? fmtMoney(p.price) : 'No price'}</div>
              <div class="text-xs muted">SKU {p.sku}</div>
            </div>
            <div class="text-right">
              <div class="text-xs muted mb-1 font-semibold">Live on store</div>
              <button class="toggle {p.live ? 'on' : ''} {busyLive ? 'busy' : ''}" onclick={toggleLive} aria-label="Toggle live" disabled={busyLive}></button>
            </div>
          </div>
          <div class="mt-4 flex flex-wrap gap-2">
            <button class="btn primary" onclick={push}>{p.woo_product_id ? 'Update store listing' : 'Push to store (draft)'}</button>
            {#if can('store')}<button class="btn" onclick={() => (ordering = true)}>Create order</button>{/if}
            {#if p.woo_admin_url}<a class="btn" target="_blank" href={p.woo_admin_url}>Edit in WooCommerce</a>{/if}
            {#if p.live && p.woo_view_url}<a class="btn" target="_blank" href={p.woo_view_url}>View</a>{/if}
          </div>
          {#if p.woo_error}<div class="mt-3 text-sm" style="color:var(--bad)">Store: {p.woo_error}</div>{/if}
          {#if p.farm_error}<div class="mt-1 text-sm" style="color:var(--warn)">{FarmName()}: {p.farm_error}</div>{/if}
          {#if p.woo_synced_at}<div class="mt-2 text-xs muted">Last pushed {ago(p.woo_synced_at)} · stock on store {JSON.stringify(p.woo_stock_pushed)}</div>{/if}
        {/if}
      </div>

      {#if av}
        <div class="panel p-5">
          <div class="flex items-center justify-between mb-2">
            <div class="font-semibold">Supplies</div>
            <span class="chip {av.units === 0 ? 'bad' : av.units < 3 ? 'warn' : 'ok'}">{unitsLabel(av)}</span>
          </div>
          {#if av.mode === 'options'}
            <div class="space-y-1 text-sm">
              {#each p.variants.filter((v) => v.active) as v}
                {@const r = av.variants[v.id]}
                <div class="flex items-center gap-2">
                  <span class="swatch" style="background:{v.color_hex || '#ccc'}"></span>
                  <span class="flex-1">{v.color_name}</span>
                  <span class="chip {r?.units === 0 ? 'bad' : 'ok'}">{r?.units ?? 0}</span>
                  {#if r?.limiting && r.units === 0}<span class="text-xs muted">short: {r.limiting}</span>{/if}
                </div>
              {/each}
            </div>
          {:else if av.lines?.length}
            <table class="grid">
              <tbody>
                {#each av.lines as ln}
                  <tr><td>{ln.label}</td><td class="muted">{ln.per_unit}{ln.kind === 'filament' ? ' g' : ''}/unit</td>
                    <td class="text-right">{ln.available}{ln.kind === 'filament' ? ' g' : ''} avail</td>
                    <td class="text-right"><span class="chip {ln.units === 0 ? 'bad' : 'ok'}">{ln.units}</span></td></tr>
                {/each}
              </tbody>
            </table>
          {:else}
            <p class="text-sm muted">Add filament and parts on the Materials tab so the store never takes an order you can't fill.</p>
          {/if}
        </div>
      {/if}

      <div class="panel p-4 grid grid-cols-2 gap-2 text-sm">
        {#if m.models_url}<a class="btn sm" href={m.models_url}>3D view</a>{:else if m.manyfold_url}<a class="btn sm" target="_blank" href={m.manyfold_url}>Manyfold 3D view</a>{/if}
        {#if m.dam_url}<a class="btn sm" target="_blank" href={m.dam_url}>Photos in DAM</a>{/if}
        {#if m.source_url}<a class="btn sm" target="_blank" href={m.source_url}>{m.makerworld_id ? 'MakerWorld page' : ({ printables: 'Printables page', thingiverse: 'Thingiverse page', creality: 'Creality Cloud page' }[m.source_platform] || 'Designer page')}</a>{/if}
        {#if m.patreon_url}<a class="btn sm" target="_blank" href={m.patreon_url}>Patreon</a>{/if}
      </div>

      <div class="panel p-4">
        {#if !delInfo}
          <div class="flex gap-2 text-sm">
            {#if m.status === 'archived'}<button class="btn sm flex-1" onclick={restoreIt}>Restore from archive</button>
            {:else}<button class="btn sm flex-1" onclick={archiveIt} title="Hide from the hub; keeps everything and can be undone">Archive</button>{/if}
            <button class="btn sm danger flex-1" onclick={askDelete}>Delete permanently…</button>
          </div>
        {:else}
          <div class="text-sm space-y-2">
            <div class="font-semibold" style="color:var(--bad)">Delete {m.code} everywhere?</div>
            {#if delInfo.blockers.length}
              {#each delInfo.blockers as b}<p>Can't delete: {b}.</p>{/each}
              <button class="btn sm" onclick={() => (delInfo = null)}>OK</button>
            {:else}
              <ul class="list-disc pl-5 muted">
                <li>{delInfo.will_remove.photos} photo(s) and {delInfo.will_remove.files} model file(s) in the hub and library{delInfo.will_remove.library_folder ? '' : ''}</li>
                {#if delInfo.will_remove.woo_draft}<li>the WooCommerce draft</li>{/if}
                {#if delInfo.will_remove.dam_photos}<li>{delInfo.will_remove.dam_photos} DAM photo(s){delInfo.will_remove.dam_collection ? ' and its collection' : ''}</li>{/if}
                {#if delInfo.will_remove.farm_files}<li>{delInfo.will_remove.farm_files} file(s) in {farmName()}</li>{/if}
                <li>Manyfold drops it on its next scan</li>
                <li>License records are kept in an archive folder</li>
                <li>The library scanner won't re-import it (undo in Settings)</li>
              </ul>
              <p class="muted">Original files in your DeltaPrints folder are not touched.</p>
              <input class="input" placeholder="Type {m.code} to confirm" bind:value={delCode} />
              <div class="flex gap-2">
                <button class="btn sm danger flex-1" disabled={delCode.trim().toUpperCase() !== m.code.toUpperCase()} onclick={doDelete}>Delete</button>
                <button class="btn sm flex-1" onclick={() => (delInfo = null)}>Cancel</button>
              </div>
            {/if}
          </div>
        {/if}
      </div>
    </div>
  </div>

  <div class="ah-tabs mt-2" role="tablist" aria-label="Model">
    {#each [['overview', 'Overview'], ['listing', 'Edit details'], ['colors', 'Colours'], ['bom', 'Materials'], ['print', 'Print'], ['files', `Files (${m.files_all.length})`], ['photos', `Photos (${m.images_all.length})`], ['license', 'License and credit'], ['history', 'History']] as [k, l]}
      <button role="tab" aria-selected={tab === k} onclick={() => (tab = k)}>{l}</button>
    {/each}
  </div>

  <div class="mt-5">
    {#if tab === 'overview'}
      <div class="grid lg:grid-cols-3 gap-5">
        <div class="lg:col-span-2 panel p-5">
          <div class="flex items-center justify-between mb-2"><h3 class="font-semibold">Description</h3>
            {#if can('models')}<button class="btn sm" onclick={() => (tab = 'listing')}>Edit</button>{/if}</div>
          <p class="whitespace-pre-line leading-relaxed">{m.description || m.summary || 'No description yet.'}</p>
          {#if m.guide_md}
            <details class="mt-4"><summary class="cursor-pointer font-semibold text-sm">Designer guide</summary>
              <pre class="whitespace-pre-wrap text-sm muted mt-2 font-sans">{m.guide_md}</pre></details>
          {/if}
        </div>
        <div class="panel p-5">
          <h3 class="font-semibold mb-2">Print settings</h3>
          <dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
            {#if m.model_dims}<dt class="muted">Size</dt><dd>{m.model_dims}</dd>{/if}
            {#each Object.entries(m.print_settings || {}) as [k, v]}
              <dt class="muted capitalize">{k.replaceAll('_', ' ')}</dt>
              <dd class="break-words">{#if String(v).startsWith('http')}<a class="underline" target="_blank" href={v}>link</a>{:else}{v}{/if}</dd>
            {/each}
          </dl>
          {#each m.files_all.filter((f) => f.print_seconds || f.filament?.length) as f}
            <div class="mt-4 text-sm border-t line pt-3">
              <div class="font-semibold truncate">{f.filename}</div>
              <div class="muted">{f.printer || f.slicer || ''} {f.plates ? `· ${f.plates} plate(s)` : ''} {f.print_seconds ? `· ${fmtTime(f.print_seconds)}` : ''}</div>
              <div class="flex flex-wrap gap-1 mt-1">
                {#each f.filament as fl}<span class="chip"><span class="swatch" style="background:{fl.color || '#ccc'}"></span>{fl.type} {fl.grams ? `${fl.grams}g` : ''}</span>{/each}
              </div>
            </div>
          {/each}
          <div class="mt-4 text-xs muted">Source: {m.source}{#if m.source_folder} · {m.source_folder}{/if}</div>
        </div>
      </div>
    {:else if tab === 'listing'}
      <Listing {m} onsaved={load} />
    {:else if tab === 'colors'}
      <Colors {m} onsaved={load} />
    {:else if tab === 'bom'}
      {#key m}<Bom {m} onsaved={load} />{/key}
    {:else if tab === 'print'}
      <Print {m} onsaved={load} />
    {:else if tab === 'files'}
      <Files {m} onsaved={load} />
    {:else if tab === 'photos'}
      <Photos {m} onsaved={load} />
    {:else if tab === 'license'}
      <License {m} onsaved={load} />
    {:else if tab === 'history'}
      <div class="grid lg:grid-cols-2 gap-5">
        <div class="panel p-5">
          <h3 class="font-semibold mb-3">History</h3>
          {#each m.events as e}
            <div class="text-sm py-1.5 border-b line"><span class="muted">{ago(e.at)}</span> · {e.message}{#if e.actor} <span class="muted">({e.actor})</span>{/if}</div>
          {:else}<p class="muted text-sm">Nothing yet.</p>{/each}
        </div>
        <div class="panel p-5">
          <h3 class="font-semibold mb-3">Sync jobs</h3>
          {#each m.jobs as j}
            <div class="text-sm py-1.5 border-b line">
              <span class="chip {j.state === 'failed' ? 'bad' : j.state === 'done' ? 'ok' : ''}">{j.state}</span> {j.kind} <span class="muted">{ago(j.at)}</span>
              {#if j.error}<div class="text-xs mt-1" style="color:var(--bad)">{j.error}</div>{/if}
            </div>
          {:else}<p class="muted text-sm">No jobs.</p>{/each}
        </div>
      </div>
    {/if}
  </div>
{/if}

{#if ordering && m}<CreateOrder model={m} onclose={() => (ordering = false)} />{/if}
</Page>
