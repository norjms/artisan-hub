<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import { attempt } from '$lib/toast.svelte.js';
  let { m, onsaved } = $props();
  const p = m.product;
  let rows = $state(p.variants.map((v) => ({ ...v })));
  let catalog = $state([]);
  let saving = $state(false);
  onMount(async () => { try { catalog = (await api.get('/api/inventory/filament')).catalog; } catch {} });

  function add() { rows.push({ color_name: '', color_hex: '#888888', material: p.filaments[0]?.material || 'PLA', active: true, price: null, spoolman_filament_id: null, sku: '' }); }
  function pick(r, fid) {
    const f = catalog.find((c) => c.id === Number(fid));
    r.spoolman_filament_id = f ? f.id : null;
    if (f) { r.color_hex = f.color_hex || r.color_hex; r.material = f.material || r.material; if (!r.color_name) r.color_name = f.name; }
  }
  async function save() {
    saving = true;
    try {
      await attempt(() => api.put(`/api/products/${p.id}/variants`, rows.filter((r) => r.color_name.trim()).map((r) => ({
        id: r.id, sku: r.sku || null, color_name: r.color_name.trim(), color_hex: r.color_hex, material: r.material,
        spoolman_filament_id: r.spoolman_filament_id ? Number(r.spoolman_filament_id) : null,
        price: r.price === '' || r.price == null ? null : Number(r.price), active: r.active, image_id: r.image_id || null }))), 'Colors saved');
      onsaved?.();
    } finally { saving = false; }
  }
</script>

<div class="panel p-5">
  <div class="flex items-center justify-between mb-3">
    <div>
      <h3 class="font-semibold">Color options</h3>
      <p class="text-sm muted">Each active color becomes a Woo variation with its own SKU and stock, linked to a Spoolman filament so supplies are tracked per color. Leave empty to sell one fixed color.</p>
    </div>
    <button class="btn" onclick={add}>+ Add color</button>
  </div>
  <div class="scroll-x">
    <table class="grid">
      <thead><tr><th></th><th>Color name</th><th>Spoolman filament</th><th>Material</th><th>Price override</th><th>SKU</th><th>Active</th><th></th></tr></thead>
      <tbody>
        {#each rows as r, i}
          <tr>
            <td><input type="color" bind:value={r.color_hex} class="w-9 h-9 rounded cursor-pointer" /></td>
            <td><input class="input" bind:value={r.color_name} placeholder="Sage green" /></td>
            <td><select class="input" value={r.spoolman_filament_id || ''} onchange={(e) => pick(r, e.target.value)}>
                <option value="">Match by color</option>
                {#each catalog as c}<option value={c.id}>{c.vendor} {c.name} ({c.material})</option>{/each}
              </select></td>
            <td><input class="input w-24" bind:value={r.material} /></td>
            <td><input class="input w-28" type="number" step="0.01" bind:value={r.price} placeholder="same" /></td>
            <td class="text-xs muted">{r.sku || 'auto'}</td>
            <td><input type="checkbox" bind:checked={r.active} /></td>
            <td><button class="btn sm danger" onclick={() => rows.splice(i, 1)}>Remove</button></td>
          </tr>
        {:else}
          <tr><td colspan="8" class="muted text-center py-6">Single color product. Add colors to offer choices.</td></tr>
        {/each}
      </tbody>
    </table>
  </div>
  <div class="mt-4 flex justify-end"><button class="btn primary" onclick={save} disabled={saving}>{saving ? 'Saving…' : 'Save colors'}</button></div>
</div>
