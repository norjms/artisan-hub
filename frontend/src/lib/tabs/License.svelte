<script>
  import { onMount } from 'svelte';
  import { api, ago } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  let { m, onsaved } = $props();
  const L = m.license;
  let code = $state(L.code);
  let note = $state(L.note || '');
  let override = $state(L.credit_override || '');
  let designerId = $state(m.designer_id ?? '');
  let designers = $state([]);
  let ev = $state([]);
  let file = $state(null);
  let fnote = $state('');
  let busy = $state(false);
  let newD = $state(null);
  const dz = $derived(designers.find((d) => d.id === Number(designerId)));

  async function load() {
    [designers, ev] = await Promise.all([api.get('/api/designers/full'), api.get(`/api/models/${m.id}/evidence`)]);
  }
  onMount(load);

  async function save() {
    busy = true;
    try {
      if (String(designerId) !== String(m.designer_id ?? '')) {
        await attempt(() => api.patch(`/api/models/${m.id}`, { designer_id: designerId === '' ? null : Number(designerId) }));
      }
      await attempt(() => api.put(`/api/models/${m.id}/license`, { code, note, credit_override: override }), 'License saved');
      onsaved?.(); load();
    } finally { busy = false; }
  }
  async function saveDesigner(d) {
    await attempt(() => api.patch(`/api/designers/${d.id}`, { name: d.name, url: d.url || null, credit_template: d.credit_template || null,
      always_credit: d.always_credit, notes: d.notes || null }), 'Designer saved');
    onsaved?.();
  }
  async function addDesigner() {
    const d = await attempt(() => api.post('/api/designers', newD));
    newD = null; await load(); designerId = d.id;
  }
  async function upload() {
    const fd = new FormData();
    fd.append('file', file); fd.append('note', fnote); fd.append('code', code);
    await attempt(() => fetch(`/api/models/${m.id}/evidence`, { method: 'POST', body: fd, credentials: 'same-origin' })
      .then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail || r.statusText); }), 'Document saved');
    file = null; fnote = ''; load();
  }
  async function snapshot() {
    await attempt(() => api.post(`/api/models/${m.id}/evidence/snapshot`), 'Taking a fresh snapshot; it appears here in a few seconds');
    setTimeout(load, 8000); setTimeout(load, 25000);
  }
  const opt = $derived(L.options.find((o) => o.code === code));
</script>

<div class="grid lg:grid-cols-2 gap-5">
  <div class="space-y-5">
    <div class="panel p-5 space-y-3">
      <h3 class="font-semibold">License</h3>
      <div>
        <label class="lbl" for="lc">License for this design</label>
        <select id="lc" class="input" bind:value={code}>
          {#each L.options as o}<option value={o.code}>{o.label}{o.sell ? '' : ' (no selling)'}</option>{/each}
        </select>
      </div>
      {#if opt && !opt.sell}<p class="text-sm" style="color:var(--bad)">This license does not allow selling prints. The store push is blocked until you record a commercial license (choose "Commercial license" and attach the agreement below).</p>{/if}
      {#if code === 'BY-ND'}<p class="text-sm muted">No derivatives: sell prints of the file as published. Don't remix, rescale with changes, or re-share modified files.</p>{/if}
      {#if code === 'BY-SA'}<p class="text-sm muted">Share-alike: if you remix this model, your remix files must be released under CC BY-SA too. Selling prints is fine.</p>{/if}
      <div><label class="lbl" for="ln">Note (where the license came from, membership, permission email…)</label><input id="ln" class="input" bind:value={note} /></div>
      {#if L.checked_at}<p class="text-xs muted">Last checked on MakerWorld {ago(L.checked_at)}. The hub re-checks weekly and emails you if the designer changes it.</p>{/if}
      {#if L.commercial}
        <div class="text-sm p-3 rounded-lg border line">
          {#if !L.commercial.recorded}No commercial license recorded for {m.designer || 'this designer'} yet. <a class="underline" href="/licenses">Record it</a> so you're reminded before it lapses.
          {:else if L.commercial.active}Covered by <b>{L.commercial.grant.name}</b>{L.commercial.grant.expires_on ? ` until ${L.commercial.grant.expires_on}` : ''}. <a class="underline" href="/licenses">Manage</a>
          {:else}<span style="color:var(--bad)"><b>{L.commercial.grant.name}</b> ended {L.commercial.grant.expires_on}.</span> <a class="underline" href="/licenses">Renew or update</a>{/if}
        </div>
      {/if}
      {#each L.problems as pr}<p class="text-sm" style="color:var(--warn)">{pr}</p>{/each}
    </div>

    <div class="panel p-5 space-y-3">
      <h3 class="font-semibold">Designer and credit</h3>
      <div class="flex gap-2 items-end">
        <div class="flex-1"><label class="lbl" for="dz">Designer</label>
          <select id="dz" class="input" bind:value={designerId}>
            <option value="">(none)</option>
            {#each designers as d}<option value={d.id}>{d.name}{d.platform ? ` · ${d.platform}` : ''}</option>{/each}
          </select></div>
        <button class="btn" onclick={() => (newD = { name: '', url: '', always_credit: false })}>New</button>
      </div>
      {#if newD}
        <div class="p-3 rounded-lg border line space-y-2">
          <input class="input" placeholder="Designer name" bind:value={newD.name} />
          <input class="input" placeholder="Profile link (optional)" bind:value={newD.url} />
          <div class="flex gap-2"><button class="btn primary sm" onclick={addDesigner} disabled={!newD.name}>Add designer</button><button class="btn sm" onclick={() => (newD = null)}>Cancel</button></div>
        </div>
      {/if}
      {#if dz}
        <div class="p-3 rounded-lg border line space-y-2 text-sm">
          <div class="grid grid-cols-2 gap-2">
            <input class="input" bind:value={dz.name} aria-label="name" />
            <input class="input" placeholder="Profile link" bind:value={dz.url} aria-label="profile" />
          </div>
          <input class="input" placeholder="Credit template, e.g. Design by {'{designer}'}{'{profile}'}. {'{license_part}'}" bind:value={dz.credit_template} aria-label="template" />
          <label class="flex items-center gap-2"><input type="checkbox" bind:checked={dz.always_credit} /> Always credit this designer (even when the license doesn't require it)</label>
          <div class="flex justify-between items-center"><span class="muted">{dz.models} model(s)</span><button class="btn sm" onclick={() => saveDesigner(dz)}>Save designer</button></div>
        </div>
      {/if}
      <div><label class="lbl" for="co">Custom credit text for this model (optional)</label><input id="co" class="input" bind:value={override} placeholder="Leave blank to use the designer template" /></div>
      <div class="text-sm">
        <div class="lbl">Shown on the store listing</div>
        {#if L.credit_line}<div class="p-3 rounded-lg border line italic">{L.credit_line}</div>{:else}<div class="muted">No credit line (not required).</div>{/if}
      </div>
      <button class="btn primary w-full" onclick={save} disabled={busy}>{busy ? 'Saving…' : 'Save license and credit'}</button>
    </div>
  </div>

  <div class="panel p-5">
    <div class="flex items-center justify-between mb-3">
      <h3 class="font-semibold">License records</h3>
      {#if m.makerworld_id || ['printables', 'thingiverse', 'creality'].includes(m.source_platform)}<button class="btn sm" onclick={snapshot}>Snapshot now</button>{/if}
    </div>
    <p class="text-sm muted mb-3">Each model-site import saves the raw license data and page exactly as served (with SHA-256 fingerprints), a PDF record, and an Internet Archive copy that proves the date independently.</p>
    {#each ev as r}
      <div class="py-3 border-b line text-sm">
        <div class="flex items-center gap-2 flex-wrap">
          <span class="chip">{r.kind.endsWith('_snapshot') ? ({ makerworld: 'MakerWorld', printables: 'Printables', thingiverse: 'Thingiverse', creality: 'Creality Cloud' }[r.kind.replace('_snapshot', '')] || 'Site') + ' snapshot' : r.kind === 'upload' ? 'Document' : 'Note'}</span>
          <b>{r.license}</b><span class="muted">{new Date(r.captured_at).toLocaleString()}</span>
        </div>
        {#if r.note}<div class="mt-1">{r.note}</div>{/if}
        <div class="flex flex-wrap gap-2 mt-2">
          {#if r.files.pdf}<a class="btn sm" target="_blank" href={r.files.pdf}>PDF</a>{/if}
          {#if r.files.json}<a class="btn sm" target="_blank" href={r.files.json}>Raw data</a>{/if}
          {#if r.files.html}<a class="btn sm" target="_blank" href={r.files.html}>Page source</a>{/if}
          {#if r.wayback_url}<a class="btn sm" target="_blank" href={r.wayback_url}>Internet Archive</a>{/if}
        </div>
        {#if r.sha256}<div class="text-[11px] muted mt-1 break-all">sha256 {r.sha256}</div>{/if}
      </div>
    {:else}<p class="text-sm muted">No records yet.</p>{/each}
    <div class="mt-4 space-y-2">
      <div class="lbl">Attach a license document (purchase receipt, membership terms, permission email as PDF)</div>
      <input type="file" class="text-sm" onchange={(e) => (file = e.target.files[0])} />
      <input class="input" placeholder="Note" bind:value={fnote} />
      <button class="btn sm" onclick={upload} disabled={!file}>Upload</button>
    </div>
  </div>
</div>
