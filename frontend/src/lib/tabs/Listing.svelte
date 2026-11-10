<script>
  import { api } from '$lib/api.js';
  import { attempt } from '$lib/toast.svelte.js';
  import CopyButton from '$lib/CopyButton.svelte';
  let { m, onsaved } = $props();
  const p = m.product || {};
  let f = $state({
    name: m.name, summary: m.summary || '', description: m.description || '', category: m.category || '',
    notes: m.notes || '', model_dims: m.model_dims || '',
    sku: p.sku, title: p.title, price: p.price ?? '', sale_price: p.sale_price ?? '', short_description: p.short_description || '',
    pdesc: p.description || '', weight_g: p.weight_g ?? '', dims_cm: p.dims_cm || '', target_buffer: p.target_buffer, stock_cap: p.stock_cap
  });
  let saving = $state(false);
  const num = (v) => (v === '' || v == null ? null : Number(v));
  async function save() {
    saving = true;
    try {
      await attempt(() => api.patch(`/api/models/${m.id}`, { name: f.name, summary: f.summary || null, description: f.description || null,
        category: f.category || null, notes: f.notes || null, model_dims: f.model_dims || null }));
      if (p.id) await attempt(() => api.patch(`/api/products/${p.id}`, { sku: f.sku, title: f.title, price: num(f.price), sale_price: num(f.sale_price),
        short_description: f.short_description || null, description: f.pdesc || null, weight_g: num(f.weight_g), dims_cm: f.dims_cm || null,
        target_buffer: Number(f.target_buffer), stock_cap: Number(f.stock_cap) }), p.woo_product_id ? 'Saved. Click "Update store listing" to send it to Woo.' : 'Saved');
      onsaved?.();
    } finally { saving = false; }
  }
</script>

<div class="grid lg:grid-cols-2 gap-5">
  <div class="panel p-5 space-y-3">
    <h3 class="font-semibold">On the store</h3>
    <div><div class="flex items-baseline justify-between gap-2"><label class="lbl" for="t">Product title</label><CopyButton text={f.title} /></div><input id="t" class="input" bind:value={f.title} /></div>
    <div class="grid grid-cols-3 gap-3">
      <div><div class="flex items-baseline justify-between gap-2"><label class="lbl" for="sku">SKU</label><CopyButton text={f.sku} /></div><input id="sku" class="input" bind:value={f.sku} /></div>
      <div><label class="lbl" for="pr">Price</label><input id="pr" class="input" type="number" step="0.01" bind:value={f.price} /></div>
      <div><label class="lbl" for="sp">Sale price</label><input id="sp" class="input" type="number" step="0.01" bind:value={f.sale_price} /></div>
    </div>
    <div><div class="flex items-baseline justify-between gap-2"><label class="lbl" for="sd">Short description</label><CopyButton text={f.short_description} /></div><textarea id="sd" class="input" rows="3" bind:value={f.short_description}></textarea></div>
    <div><div class="flex items-baseline justify-between gap-2"><label class="lbl" for="pd">Full description (blank = use model description)</label><CopyButton text={f.pdesc} /></div><textarea id="pd" class="input" rows="7" bind:value={f.pdesc}></textarea></div>
    <div class="grid grid-cols-2 gap-3">
      <div><label class="lbl" for="w">Shipping weight (g)</label><input id="w" class="input" type="number" bind:value={f.weight_g} /></div>
      <div><div class="flex items-baseline justify-between gap-2"><label class="lbl" for="d">Box size cm (LxWxH)</label><CopyButton text={f.dims_cm} /></div><input id="d" class="input" placeholder="15x12x10" bind:value={f.dims_cm} /></div>
    </div>
  </div>
  <div class="space-y-5">
    <div class="panel p-5 space-y-3">
      <h3 class="font-semibold">Model</h3>
      <div><div class="flex items-baseline justify-between gap-2"><label class="lbl" for="n">Name</label><CopyButton text={f.name} /></div><input id="n" class="input" bind:value={f.name} /></div>
      <div class="grid grid-cols-2 gap-3">
        <div><div class="flex items-baseline justify-between gap-2"><label class="lbl" for="c">Category</label><CopyButton text={f.category} /></div><input id="c" class="input" bind:value={f.category} /></div>
        <div><div class="flex items-baseline justify-between gap-2"><label class="lbl" for="md">Model size</label><CopyButton text={f.model_dims} /></div><input id="md" class="input" bind:value={f.model_dims} /></div>
      </div>
      <div><div class="flex items-baseline justify-between gap-2"><label class="lbl" for="s">Summary</label><CopyButton text={f.summary} /></div><textarea id="s" class="input" rows="2" bind:value={f.summary}></textarea></div>
      <div><div class="flex items-baseline justify-between gap-2"><label class="lbl" for="de">Description</label><CopyButton text={f.description} /></div><textarea id="de" class="input" rows="5" bind:value={f.description}></textarea></div>
      <p class="text-sm muted">License, designer and credit live on the <b>License &amp; credit</b> tab.{#if m.license?.credit_line} The listing description ends with: <i>{m.license.credit_line}</i>{/if}</p>
      <div><div class="flex items-baseline justify-between gap-2"><label class="lbl" for="no">Private notes</label><CopyButton text={f.notes} /></div><textarea id="no" class="input" rows="2" bind:value={f.notes}></textarea></div>
    </div>
    <div class="panel p-5 space-y-3">
      <h3 class="font-semibold">Supply guard</h3>
      <div class="grid grid-cols-2 gap-3">
        <div><label class="lbl" for="tb">Keep supplies for (units)</label><input id="tb" class="input" type="number" min="0" bind:value={f.target_buffer} /></div>
        <div><label class="lbl" for="sc">Max stock shown on store</label><input id="sc" class="input" type="number" min="1" bind:value={f.stock_cap} /></div>
      </div>
      <p class="text-xs muted">Store stock = units you can build from parts and filament on hand (minus open orders), capped at the max. The shopping list keeps enough for the buffer.</p>
    </div>
    <button class="btn primary w-full" onclick={save} disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
  </div>
</div>
