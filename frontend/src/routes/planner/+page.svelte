<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  let include = $state('live');
  let plan = $state(null);
  let showAll = $state(false);
  async function load() { plan = await api.get(`/api/planner?include=${include}`); }
  onMount(load);
  async function pos() {
    const r = await attempt(() => api.post('/api/planner/purchase-orders'));
    toast(r.purchase_orders.length ? `Created purchase orders: ${r.purchase_orders.join(', ')}` : 'Nothing to order from suppliers with InvenTree links');
  }
  const fil = $derived(plan ? plan.filament.filter((x) => showAll || x.short_g > 0) : []);
  const parts = $derived(plan ? plan.parts.filter((x) => showAll || x.short > 0) : []);
</script>

<div class="flex flex-wrap items-end gap-3 mb-4">
  <div class="mr-auto"><h1 class="text-2xl font-bold">Shopping list</h1>
    <p class="muted text-sm">Covers open orders plus the supply buffer for each {include === 'live' ? 'live' : ''} product.</p></div>
  <select class="input w-56" bind:value={include} onchange={load}><option value="live">Live products</option><option value="all">All products (planning)</option></select>
  <label class="text-sm flex items-center gap-2"><input type="checkbox" bind:checked={showAll} /> Show covered items</label>
  <a class="btn" href={`/api/planner.csv?include=${include}`}>Download CSV</a>
  <button class="btn primary" onclick={pos}>Create purchase orders</button>
</div>

{#if plan}
  <div class="grid xl:grid-cols-2 gap-5">
    <div class="panel p-4">
      <h3 class="font-semibold mb-3">Filament</h3>
      <table class="grid">
        <thead><tr><th>Filament</th><th class="text-right">Need</th><th class="text-right">Have</th><th class="text-right">Buy</th><th>Where</th></tr></thead>
        <tbody>
          {#each fil as f}
            <tr>
              <td><span class="inline-flex items-center gap-2"><span class="swatch" style="background:{f.color_hex || '#ccc'}"></span>{f.label}</span><div class="text-xs muted">{f.used_by.join(', ')}</div></td>
              <td class="text-right">{f.required_g} g</td><td class="text-right">{f.on_hand_g} g</td>
              <td class="text-right">{#if f.buy_spools}<span class="chip bad">{f.buy_spools} spool{f.buy_spools > 1 ? 's' : ''}</span>{:else}<span class="chip ok">covered</span>{/if}</td>
              <td class="text-xs">{#if f.catalog}{f.catalog.vendor} {f.catalog.name}{#if f.catalog.url} <a class="underline" target="_blank" href={f.catalog.url}>buy</a>{/if}{:else}<span class="muted">not in Spoolman catalog</span>{/if}</td>
            </tr>
          {:else}<tr><td colspan="5" class="muted text-center py-6">Nothing to buy.</td></tr>{/each}
        </tbody>
      </table>
    </div>
    <div class="panel p-4">
      <h3 class="font-semibold mb-3">Parts and packaging</h3>
      <table class="grid">
        <thead><tr><th>Part</th><th class="text-right">Need</th><th class="text-right">Have</th><th class="text-right">On order</th><th class="text-right">Buy</th><th>Supplier</th></tr></thead>
        <tbody>
          {#each parts as p}
            <tr>
              <td>{p.name}<div class="text-xs muted">{p.used_by.join(', ')}</div></td>
              <td class="text-right">{p.required}</td><td class="text-right">{p.on_hand}</td><td class="text-right">{p.on_order || ''}</td>
              <td class="text-right">{#if p.buy_packs}<span class="chip bad">{p.buy_packs} pack{p.buy_packs > 1 ? 's' : ''}</span>{:else}<span class="chip ok">covered</span>{/if}</td>
              <td class="text-xs">{#if p.supplier}{#if p.supplier.link}<a class="underline" target="_blank" href={p.supplier.link}>{p.supplier.name || 'buy'}</a>{:else}{p.supplier.name}{/if}{:else}<span class="muted">no supplier</span>{/if}</td>
            </tr>
          {:else}<tr><td colspan="6" class="muted text-center py-6">Nothing to buy.</td></tr>{/each}
        </tbody>
      </table>
    </div>
  </div>
{/if}
