<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  import FilamentRequest from '$lib/FilamentRequest.svelte';
  let { m, onsaved } = $props();
  const p = m.product;
  let fil = $state(p.filaments.map((x) => ({ ...x })));
  let parts = $state(p.parts.map((x) => ({ ...x })));
  let inv = $state([]);
  let catalog = $state([]);
  let search = $state('');
  let saving = $state(false);
  let showNew = $state(false);
  let sets = $state(null);         // each printer's approved slice: what it prints and how many it can make
  let np = $state({ name: '', category: 'Hardware', supplier: '', link: '', pack_qty: 1, initial_stock: 0, supplier_sku: '' });
  const hasColors = p.color_mode === 'options' && p.variants.some((v) => v.active);

  onMount(async () => {
    api.get(`/api/models/${m.id}/filament-sets`).then((r) => (sets = r)).catch(() => {});
    inv = await api.get('/api/inventory/parts');
    try { catalog = (await api.get('/api/inventory/filament')).catalog; } catch {}
  });
  const matches = $derived(search.length < 2 ? [] : inv.filter((x) => (x.name + ' ' + (x.ipn || '')).toLowerCase().includes(search.toLowerCase())).slice(0, 8));

  function addFil() { fil.push({ slot: fil.length + 1, label: '', follows_variant: false, material: 'PLA', color_name: '', color_hex: '#888888', spoolman_filament_id: null, grams: 0, waste_pct: 5 }); }
  function addPart(x) { parts.push({ inventree_part_id: x.id, name: x.name, qty: 1, variant_id: null, note: '' }); search = ''; }
  function pickFil(r, fid) {
    const f = catalog.find((c) => c.id === Number(fid));
    r.spoolman_filament_id = f ? f.id : null;
    if (f) { r.color_hex = f.color_hex || r.color_hex; r.material = f.material || r.material; r.color_name = r.color_name || f.name; }
  }
  async function estimate() {
    if (fil.some((f) => f.grams > 0) && !confirm('Replace the current grams with an estimate from the model geometry?')) return;
    await attempt(() => api.post(`/api/models/${m.id}/estimate-filament?overwrite=true`), 'Measuring the model files…');
    setTimeout(() => onsaved?.(), 6000);
  }
  async function createPart() {
    const r = await attempt(() => api.post('/api/inventory/parts', { ...np, pack_qty: Number(np.pack_qty) || 1, initial_stock: Number(np.initial_stock) || 0 }), 'Part created in InvenTree');
    inv = await api.get('/api/inventory/parts');
    parts.push({ inventree_part_id: r.id, name: r.name, qty: 1, variant_id: null, note: '' });
    showNew = false; np = { name: '', category: 'Hardware', supplier: '', link: '', pack_qty: 1, initial_stock: 0, supplier_sku: '' };
  }
  async function save() {
    saving = true;
    try {
      await attempt(() => api.put(`/api/products/${p.id}/bom`, {
        filaments: fil.map((r, i) => ({ ...r, slot: i + 1, grams: Number(r.grams) || 0, waste_pct: Number(r.waste_pct) || 0,
          spoolman_filament_id: r.spoolman_filament_id ? Number(r.spoolman_filament_id) : null })),
        parts: parts.map((r) => ({ ...r, qty: Number(r.qty) || 0, variant_id: r.variant_id ? Number(r.variant_id) : null }))
      }), 'Materials saved, store stock will update');
      onsaved?.();
    } finally { saving = false; }
  }
  const stockOf = (id) => inv.find((x) => x.id === id);
</script>

<FilamentRequest {m} {onsaved} />
{#if sets?.sets?.length}
  <div class="panel p-5 mb-5 text-sm">
    <h3 class="font-semibold">Filament by printer</h3>
    <p class="muted text-xs mb-2">What each printer's approved slice prints, and how many it could make from the spools on hand. The production set (the default printer's slice) is the filament per unit below and sets the store stock.</p>
    <div class="scroll-x">
      <table class="grid">
        <thead><tr><th>Printer</th><th>Prints with</th><th>Per unit</th><th>Can make</th></tr></thead>
        <tbody>
          {#each sets.sets as s (s.profile_id)}
            <tr>
              <td class="min-w-[10rem]"><b>{s.printer}</b>
                {#if s.production}<span class="chip ok">production</span>{/if}{#if s.source === 'file'}<span class="chip">chosen file</span>{/if}{#if s.locked}<span class="chip">locked</span>{/if}
                <div class="text-[11px] muted truncate max-w-[18rem]" title={s.files.join(', ')}>{s.files.join(', ')}</div></td>
              <td>
                {#each s.set as x}
                  <div class="flex items-center gap-1.5"><span class="swatch" style="background:{x.color_hex || '#ccc'}"></span>{x.color_name || x.color_hex || 'any colour'} <span class="muted">{x.material} · {x.per_unit_g} g</span></div>
                {/each}
                {#if s.differs}<div class="text-xs mt-1" style="color:var(--warn)">different colours from production</div>{/if}
              </td>
              <td>{s.grams} g</td>
              <td><b>{s.units ?? '?'}</b>{#if s.units === 0 && s.limiting}<div class="text-[11px] muted">short of {s.limiting}</div>{/if}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  </div>
{/if}
<div class="grid lg:grid-cols-2 gap-5">
  <div class="panel p-5">
    <div class="flex items-center justify-between mb-3">
      <div><h3 class="font-semibold">Filament per unit</h3><p class="text-sm muted">{#if sets?.production}Set by the production slice on {sets.production.printer}{sets.production.source === 'file' ? ' (a chosen file: its colours too)' : ''}; a new production slice or file updates it.{:else}Grams come from the sliced file when there is one.{/if}{#if m.print_settings?.filament_estimate}{" "}Current figure: {m.print_settings.filament_estimate}.{/if}</p>
        {#if m.files_all.some((f) => /\.(stl|3mf)$/i.test(f.filename))}<button class="btn sm mt-1" onclick={estimate} title="Measure the STL/3MF volume and estimate grams at 15% infill">Estimate from model files</button>{/if}</div>
      <button class="btn sm" onclick={addFil}>+ Filament</button>
    </div>
    {#each fil as r, i}
      <div class="border line rounded-xl p-3 mb-3 space-y-2">
        <div class="flex gap-2 items-center">
          <input class="input w-28" bind:value={r.label} placeholder="Body" />
          {#if hasColors}
            <label class="text-sm flex items-center gap-1.5"><input type="checkbox" bind:checked={r.follows_variant} /> uses the chosen color</label>
          {/if}
          <button class="btn sm danger ml-auto" onclick={() => fil.splice(i, 1)}>Remove</button>
        </div>
        {#if !r.follows_variant}
          <div class="flex gap-2 items-center">
            <input type="color" bind:value={r.color_hex} class="w-9 h-9 rounded" />
            <input class="input" bind:value={r.color_name} placeholder="Color" />
            <input class="input w-24" bind:value={r.material} />
          </div>
          <select class="input" value={r.spoolman_filament_id || ''} onchange={(e) => pickFil(r, e.target.value)}>
            <option value="">Match any spool by material + color</option>
            {#each catalog as c}<option value={c.id}>{c.vendor} {c.name} ({c.material})</option>{/each}
          </select>
        {/if}
        <div class="flex gap-2 items-center text-sm">
          <input class="input w-28" type="number" step="0.1" bind:value={r.grams} /> g
          <input class="input w-20 ml-3" type="number" bind:value={r.waste_pct} /> % waste
        </div>
      </div>
    {:else}<p class="muted text-sm">No filament set.</p>{/each}
  </div>

  <div class="panel p-5">
    <div class="flex items-center justify-between mb-3">
      <div><h3 class="font-semibold">Parts per unit</h3><p class="text-sm muted">Magnets, screws, inserts, boxes, labels. Stock lives in InvenTree.</p></div>
      <button class="btn sm" onclick={() => (showNew = !showNew)}>{showNew ? 'Cancel' : 'New part'}</button>
    </div>
    {#if showNew}
      <div class="border line rounded-xl p-3 mb-3 grid grid-cols-2 gap-2">
        <input class="input col-span-2" placeholder="Name, e.g. Neodymium magnet 6x2mm" bind:value={np.name} />
        <input class="input" placeholder="Category (Hardware/Magnets)" bind:value={np.category} />
        <input class="input" placeholder="Supplier (Amazon)" bind:value={np.supplier} />
        <input class="input col-span-2" placeholder="Buy link" bind:value={np.link} />
        <input class="input" type="number" placeholder="Pack size" bind:value={np.pack_qty} />
        <input class="input" type="number" placeholder="On hand now" bind:value={np.initial_stock} />
        <button class="btn primary col-span-2" onclick={createPart} disabled={!np.name}>Create in InvenTree and add</button>
      </div>
    {/if}
    <div class="relative mb-3">
      <input class="input" placeholder="Search parts to add…" bind:value={search} />
      {#if matches.length}
        <div class="absolute z-10 mt-1 w-full panel shadow-lg max-h-72 overflow-auto">
          {#each matches as x}
            <button class="w-full text-left px-3 py-2 hover:bg-black/5 text-sm flex justify-between" onclick={() => addPart(x)}>
              <span>{x.name}</span><span class="muted">{x.in_stock} in stock</span>
            </button>
          {/each}
        </div>
      {/if}
    </div>
    <table class="grid">
      <thead><tr><th>Part</th><th>Qty</th>{#if hasColors}<th>Only for</th>{/if}<th>In stock</th><th></th></tr></thead>
      <tbody>
        {#each parts as r, i}
          <tr>
            <td>{r.name}</td>
            <td><input class="input w-20" type="number" step="0.5" bind:value={r.qty} /></td>
            {#if hasColors}<td><select class="input w-32" bind:value={r.variant_id}><option value={null}>All colors</option>
              {#each p.variants as v}<option value={v.id}>{v.color_name}</option>{/each}</select></td>{/if}
            <td>{stockOf(r.inventree_part_id)?.available ?? '?'}</td>
            <td><button class="btn sm danger" onclick={() => parts.splice(i, 1)}>Remove</button></td>
          </tr>
        {:else}<tr><td colspan="5" class="muted text-center py-4">No parts.</td></tr>{/each}
      </tbody>
    </table>
  </div>
</div>
<div class="mt-4 flex justify-end"><button class="btn primary" onclick={save} disabled={saving}>{saving ? 'Saving…' : 'Save materials'}</button></div>
