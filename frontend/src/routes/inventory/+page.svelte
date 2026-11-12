<script>
  import Glyph from '$lib/Glyph.svelte';
  import { farmName, FarmName } from '$lib/nav.svelte.js';
  import Page from '$lib/Page.svelte';
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  let tab = $state('parts');
  let parts = $state([]);
  let fil = $state(null);
  let q = $state('');
  let onlyUsed = $state(false);
  let ss = $state(null);
  async function load() {
    parts = await api.get('/api/inventory/parts');
    fil = await api.get('/api/inventory/filament');
    ss = await api.get('/api/spools/sync').catch(() => null);
  }
  async function syncSpools() {
    const r = await attempt(() => api.post('/api/spools/sync', {}));
    const n = (k) => (r[k] || []).length;
    toast(`Synced: ${n('to_spoolman')} use recorded in Spoolman, ${n('to_legacy_farm')} weights sent to ${farmName()}, ${n('created_in_spoolman') + n('created_in_legacy_farm')} new spools linked`);
    setTimeout(load, 3000);
  }
  onMount(load);
  async function adjust(p, delta) {
    await attempt(() => api.post(`/api/inventory/parts/${p.id}/adjust`, { delta }), `${delta > 0 ? 'Added' : 'Used'} ${Math.abs(delta)}`);
    load();
  }
  async function refresh() {
    await api.post('/api/actions/refresh-parts'); await api.post('/api/actions/refresh-spools');
    setTimeout(load, 4000);
  }
  const shown = $derived(parts.filter((p) => (!onlyUsed || p.used_by.length) && (p.name + ' ' + (p.ipn || '') + ' ' + (p.category || '')).toLowerCase().includes(q.toLowerCase())));
</script>

<Page title="Inventory" description={`Parts from InvenTree, filament from Spoolman (kept in step with ${farmName()}). Refreshed every 5 minutes.`}>
{#snippet actions()}
<button class="btn" onclick={refresh}>Refresh now</button>
{/snippet}
<div class="ah-tabs" role="tablist" aria-label="Inventory">
  {#each [['parts', 'Parts'], ['filament', 'Filament'], ['needs', 'Filament for live products']] as [k, l]}
    <button role="tab" aria-selected={tab === k} onclick={() => (tab = k)}>{l}</button>
  {/each}
</div>

{#if tab === 'parts'}
  <div class="panel p-4">
    <div class="flex gap-3 mb-3 items-center">
      <input class="input w-72" placeholder="Search parts" bind:value={q} />
      <label class="text-sm flex items-center gap-2"><input type="checkbox" bind:checked={onlyUsed} /> Used by products</label>
      {#if fil}<a class="btn sm ml-auto" target="_blank" href={parts[0]?.url?.split('/web/')[0] + '/web/part/'}>Open InvenTree</a>{/if}
    </div>
    <div class="scroll-x"><table class="grid">
      <thead><tr><th>Part</th><th>Category</th><th class="text-right">In stock</th><th class="text-right">Open orders</th><th class="text-right">Available</th><th class="text-right">On order</th><th>Used by</th><th>Supplier</th><th>Adjust</th></tr></thead>
      <tbody>
        {#each shown as p}
          <tr>
            <td><a class="font-medium hover:underline" target="_blank" href={p.url}>{p.name}</a>{#if p.ipn}<div class="text-xs muted">{p.ipn}</div>{/if}</td>
            <td class="text-xs muted">{p.category}</td>
            <td class="text-right">{p.in_stock}</td>
            <td class="text-right">{p.open_order_demand || ''}</td>
            <td class="text-right"><span class="chip {p.available <= 0 ? 'bad' : p.available <= p.minimum_stock ? 'warn' : 'ok'}">{p.available}</span></td>
            <td class="text-right">{p.on_order || ''}</td>
            <td class="text-xs">{#each p.used_by as u}<a class="chip mr-1 {u.live ? 'ok' : ''}" href={`/models/${u.model_id}`}>{u.sku} ×{u.qty}</a>{/each}</td>
            <td class="text-xs">{#each p.suppliers as s}{#if s.link}<a class="underline" target="_blank" href={s.link}>{s.name || 'link'}</a>{:else}{s.name}{/if}{/each}</td>
            <td class="whitespace-nowrap">
              <button class="btn sm" onclick={() => adjust(p, -1)} aria-label="Take one away"><Glyph name="minus" size={14} />1</button>
              <button class="btn sm" onclick={() => adjust(p, 1)}>+1</button>
              <button class="btn sm" onclick={() => { const n = Number(prompt('Add how many? (negative to remove)')); if (n) adjust(p, n); }}>±</button>
            </td>
          </tr>
        {:else}<tr><td colspan="9" class="muted text-center py-6">No parts yet. Add them from a model's Materials tab or in InvenTree.</td></tr>{/each}
      </tbody>
    </table></div>
  </div>
{:else if tab === 'filament' && fil}
  <div class="panel p-4">
    <div class="flex flex-wrap items-center gap-2 mb-3">
      {#if ss}
        <span class="text-sm muted mr-auto">Spoolman and {farmName()} sync every 5 minutes: {farmName() === 'Legacy farm' ? "Legacy farm's" : "the farm's"} print use is recorded in Spoolman, Spoolman's weights go to {farmName()}, new spools are added on the other side.
          {#if ss.last}Last: {new Date(ss.last.at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}, {ss.links.filter((l) => l.state === 'ok').length} spools linked{#if ss.links.some((l) => l.state === 'broken')}, <span class="text-red-600">{ss.links.filter((l) => l.state === 'broken').length} broken</span>{/if}.{/if}
          {#if !ss.enabled}<span class="chip warn">off</span>{/if}</span>
        <button class="btn sm" onclick={syncSpools}>Sync spools now</button>
      {/if}
      <a class="btn sm {ss ? '' : 'ml-auto'}" target="_blank" href={fil.spoolman_url}>Open Spoolman</a>
    </div>
    <div class="grid gap-3" style="grid-template-columns: repeat(auto-fill, minmax(220px, 1fr))">
      {#each fil.spools as s}
        <div class="border line rounded-xl p-3 flex gap-3 items-center">
          <span class="w-10 h-10 rounded-full border line flex-none" style="background:{s.color_hex || '#ccc'}"></span>
          <div class="min-w-0">
            <div class="font-medium truncate">{s.name}</div>
            <div class="text-xs muted">{s.vendor} · {s.material}</div>
            <div class="text-sm font-semibold">{s.remaining_g} g</div>
          </div>
        </div>
      {:else}<p class="muted">No spools in Spoolman.</p>{/each}
    </div>
  </div>
{:else if tab === 'needs' && fil}
  <div class="panel p-4">
    <p class="text-sm muted mb-3">What you should keep on hand to cover open orders plus each live product's buffer.</p>
    <table class="grid">
      <thead><tr><th>Filament</th><th class="text-right">Needed</th><th class="text-right">On hand</th><th class="text-right">Short</th><th>Used by</th></tr></thead>
      <tbody>
        {#each fil.live_needs as n}
          <tr>
            <td><span class="inline-flex items-center gap-2"><span class="swatch" style="background:{n.color_hex || '#ccc'}"></span>{n.label}</span></td>
            <td class="text-right">{n.required_g} g</td><td class="text-right">{n.on_hand_g} g</td>
            <td class="text-right">{#if n.short_g}<span class="chip bad">{n.short_g} g</span>{:else}<span class="chip ok">ok</span>{/if}</td>
            <td class="text-xs">{n.used_by.join(', ')}</td>
          </tr>
        {:else}<tr><td colspan="5" class="muted text-center py-6">No live products yet.</td></tr>{/each}
      </tbody>
    </table>
  </div>
{/if}
</Page>
