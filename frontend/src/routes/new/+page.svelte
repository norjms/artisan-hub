<script>
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import { attempt } from '$lib/toast.svelte.js';
  import Dropzone from '$lib/Dropzone.svelte';
  import SourceImport from '$lib/SourceImport.svelte';
  import { page } from '$app/state';
  const linkIn = page.url.searchParams.get('url') || '';
  let f = $state({ name: '', category: '', designer: '', source: 'own', source_url: '', summary: '', price: '', filament: '', grams: '', license: 'OWN' });
  $effect(() => { if (f.source === 'deltaprints') f.license = 'COMMERCIAL'; else if (f.source === 'own') f.license = 'OWN'; });
  let files = $state([]);
  let cats = $state([]);
  let busy = $state(false);
  onMount(async () => { cats = await api.get('/api/categories'); });
  async function create() {
    busy = true;
    try {
      const fd = new FormData();
      const meta = { ...f, price: f.price === '' ? null : Number(f.price), grams: f.grams === '' ? null : Number(f.grams) };
      for (const k of Object.keys(meta)) if (meta[k] === '' || meta[k] == null) delete meta[k];
      fd.append('meta', JSON.stringify(meta));
      for (const x of files) fd.append('files', x);
      const r = await attempt(() => fetch('/api/v1/ingest', { method: 'POST', body: fd, credentials: 'same-origin' })
        .then(async (res) => { const j = await res.json(); if (!res.ok) throw new Error(j.detail || res.statusText); return j; }), 'Model created');
      goto(`/models/${r.id}`);
    } finally { busy = false; }
  }
</script>

<h1 class="text-2xl font-bold mb-1">Add a model</h1>
<p class="muted text-sm mb-5">Drop everything you have: photos, STL/3MF/G-code. Print time and grams are read from sliced files. You can also drop a folder into your team's inbox folder or call <code>POST /api/v1/ingest</code>.</p>
<div class="mb-5 max-w-3xl"><SourceImport initial={linkIn} auto={!!linkIn} /></div>
<h2 class="font-semibold mb-3">Or add one by hand</h2>
<div class="grid lg:grid-cols-2 gap-5">
  <div class="panel p-5 space-y-3">
    <div><label class="lbl" for="n">Name</label><input id="n" class="input" bind:value={f.name} placeholder="Ribbed planter, small" /></div>
    <div class="grid grid-cols-2 gap-3">
      <div><label class="lbl" for="c">Category</label><input id="c" class="input" list="cats" bind:value={f.category} />
        <datalist id="cats">{#each cats as c}<option value={c.name}></option>{/each}</datalist></div>
      <div><label class="lbl" for="d">Designer</label><input id="d" class="input" bind:value={f.designer} placeholder="Your studio" /></div>
    </div>
    <div class="grid grid-cols-2 gap-3">
      <div><label class="lbl" for="s">Source</label><select id="s" class="input" bind:value={f.source}><option value="own">My design</option><option value="deltaprints">Deltaprints</option><option value="other">Other designer</option><option value="makerworld">MakerWorld</option><option value="printables">Printables</option><option value="thingiverse">Thingiverse</option></select></div>
      <div><label class="lbl" for="u">Source link</label><input id="u" class="input" bind:value={f.source_url} /></div>
    </div>
    <div><label class="lbl" for="su">Summary</label><textarea id="su" class="input" rows="3" bind:value={f.summary}></textarea></div>
    <div class="grid grid-cols-3 gap-3">
      <div><label class="lbl" for="p">Price</label><input id="p" type="number" step="0.01" class="input" bind:value={f.price} /></div>
      <div><label class="lbl" for="fi">Filament</label><input id="fi" class="input" bind:value={f.filament} placeholder="Black PLA" /></div>
      <div><label class="lbl" for="g">Grams</label><input id="g" type="number" class="input" bind:value={f.grams} placeholder="auto" /></div>
    </div>
    <div><label class="lbl" for="lic">License</label>
      <select id="lic" class="input" bind:value={f.license}>
        <option value="OWN">Own design</option><option value="COMMERCIAL">Commercial license (purchased or membership)</option>
        <option value="CC0">Public Domain (CC0)</option><option value="BY">CC BY</option><option value="BY-SA">CC BY-SA</option><option value="BY-ND">CC BY-ND</option>
        <option value="BY-NC">CC BY-NC (no selling)</option><option value="BY-NC-SA">CC BY-NC-SA (no selling)</option><option value="BY-NC-ND">CC BY-NC-ND (no selling)</option>
        <option value="SDFL">MakerWorld Standard (no selling)</option><option value="UNKNOWN">Unknown</option>
      </select>
      <p class="text-xs muted mt-1">Attach purchase receipts or permission emails on the model's License tab afterwards.</p></div>
  </div>
  <div class="panel p-5">
    <Dropzone onfiles={(fs) => (files = [...files, ...fs])} label="Drop photos and model files" />
    <ul class="mt-3 text-sm space-y-1 max-h-72 overflow-auto">
      {#each files as x, i}<li class="flex justify-between"><span class="truncate">{x.name}</span><button class="muted" onclick={() => files.splice(i, 1)}>remove</button></li>{/each}
    </ul>
    <button class="btn primary w-full mt-4" onclick={create} disabled={busy || !f.name}>{busy ? 'Creating…' : 'Create model'}</button>
  </div>
</div>
