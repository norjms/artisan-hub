<script>
  import { goto } from '$app/navigation';
  import { api, fmtTime, fmtBytes } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  import { licenseChip } from '$lib/license.js';

  import { onMount } from 'svelte';
  let { initial = '', auto = false, compact = false } = $props();
  onMount(() => { if (auto && initial) preview(); });
  let url = $state(initial);
  let pv = $state(null);
  let prof = $state(null);
  let busy = $state(false);
  let status = $state('');
  let acceptNonSell = $state(false);
  let plan = $state(null);
  let category = $state('');
  let newCategory = $state('');

  export async function preview(u) {
    if (u) url = u;
    if (!url.trim()) return;
    busy = true; pv = null; status = ''; acceptNonSell = false;
    try {
      pv = await attempt(() => api.get(`/api/sources/preview?url=${encodeURIComponent(url.trim())}`));
      try {
        plan = await api.get(`/api/sources/plan?url=${encodeURIComponent(url.trim())}`);
        category = plan.suggested_category || '';
      } catch { plan = null; }
      const sel = pv.profiles.find((p) => p.profileId && p.profileId === pv.selected_profile) || pv.profiles.find((p) => p.default) || pv.profiles[0];
      prof = sel ? String(sel.id) : null;
    } finally { busy = false; }
  }

  async function doImport() {
    busy = true;
    try {
      const cat = category === '__new' ? newCategory.trim() : category;
      const r = await attempt(() => api.post('/api/sources/import', { url: url.trim(), profile_id: prof, download_files: true, ...(cat ? { category: cat } : {}) }));
      status = 'Importing photos, description, settings, filament, supplies, files and the license record…';
      for (let i = 0; i < 300; i++) {
        await new Promise((res) => setTimeout(res, 2000));
        const j = await api.get(`/api/jobs/${r.job}`);
        if (j.state === 'done') {
          const w = (j.result?.warnings || []).filter((x) => !(j.result?.files_in_browser && x.includes('need your signed-in browser')));
          toast(`Imported ${j.result.code}: ${j.result.images} photos, ${j.result.files} file(s)` + (w.length ? ` (${w.length} note${w.length > 1 ? 's' : ''})` : ''));
          if (j.result.files_in_browser) toast(`Files: open ${pv.platform_label} signed in and use the add-on's Send files, or drop them on the Files tab`, 'warn', 12000);
          for (const x of w) toast(x, 'bad', 9000);
          goto(`/models/${j.result.model_id}`);
          return;
        }
        if (j.state === 'failed' || (j.error && j.attempts >= j.max_attempts)) { toast(`Import failed: ${j.error}`, 'bad', 10000); status = ''; return; }
        if (j.error) status = `Attempt ${j.attempts} hit a problem (${j.error.split('\n')[0].slice(0, 120)}); retrying automatically…`;
      }
      status = 'Still working; check Sync health.';
    } finally { busy = false; }
  }
  const lc = $derived(pv ? licenseChip(pv.license) : null);
  const accountNote = $derived(pv && !pv.logged_in ? (
    pv.platform === 'makerworld' ? 'Model files need your MakerWorld account' :
    pv.platform === 'creality' ? 'Model files need your Creality Cloud account' :
    pv.platform === 'thingiverse' ? 'Thingiverse needs an App Token' :
    pv.platform === 'cults3d' ? 'Cults3D needs an API key for pasted links' : '') : '');
</script>

<div class="panel p-5">
  <h3 class="font-semibold mb-1">Add a model by link</h3>
  {#if !compact}<p class="text-sm muted mb-3">Paste a link from MakerWorld, Printables, Thingiverse, Creality Cloud, Thangs, MyMiniFactory or Cults3D, or a direct STL/3MF/STEP/OBJ/ZIP link or any model page. The hub pulls photos, description, designer, print settings, filament, hardware, files and the license, and saves a dated license record. With the <a class="underline" href="/add-on">browser add-on</a> it is one click on the model page.</p>{/if}
  <form class="flex gap-2" onsubmit={(e) => { e.preventDefault(); preview(); }}>
    <input class="input" placeholder="https://www.printables.com/model/… or makerworld.com/en/models/…" bind:value={url} />
    <button class="btn" disabled={busy || !url}>{busy && !pv ? 'Looking…' : 'Preview'}</button>
  </form>

  {#if pv}
    <div class="mt-4 flex gap-4">
      {#if pv.cover}<img src={pv.cover} alt="" class="w-28 h-28 object-cover rounded-lg flex-none" referrerpolicy="no-referrer" />{/if}
      <div class="min-w-0 flex-1">
        <div class="text-xs muted font-semibold">{pv.platform_label}</div>
        <div class="font-semibold leading-tight">{pv.title}</div>
        <div class="text-sm muted">by {pv.creator} · {pv.photos} photos{pv.files?.length ? ` · ${pv.files.length} files` : ''}</div>
        <div class="flex flex-wrap gap-1.5 mt-2">
          <span class="chip {lc.cls}" title={pv.license_raw}>{pv.license_label}</span>
          {#if pv.needs_credit}<span class="chip">Credit required (added to listing automatically)</span>{/if}
          {#if pv.paid}<span class="chip warn">Paid model</span>{/if}
          {#if pv.direct_file}<span class="chip">Direct file link</span>{/if}
          {#if pv.from_browser}<span class="chip">Read from your browser</span>{/if}
          {#if pv.existing_model_id}<a class="chip" href="/models/{pv.existing_model_id}">Already imported: re-import updates it</a>{/if}
        </div>
      </div>
    </div>
    {#if pv.ip_flags?.length}
      <div class="mt-3 text-sm p-3 rounded-lg" style="background:color-mix(in srgb, var(--warn) 12%, transparent)">
        <b>Possible trademark or character:</b> {pv.ip_flags.join(', ')}. The designer's license covers their model, not someone else's brand. Selling prints of licensed characters or logos can get listings pulled or worse.
      </div>
    {/if}
    {#if !pv.sellable}
      <div class="mt-3 text-sm p-3 rounded-lg" style="background:color-mix(in srgb, var(--bad) 10%, transparent)">
        <b>This license does not allow selling prints.</b> You can still import it for personal use or if you hold a separate commercial license from the designer; it won't be pushed to the store until you record that.
        <label class="flex items-center gap-2 mt-2"><input type="checkbox" bind:checked={acceptNonSell} /> Import anyway</label>
      </div>
    {/if}
    {#if pv.profiles.length}
      <div class="mt-4">
        <div class="lbl">Print profile</div>
        <div class="space-y-1.5 max-h-64 overflow-auto">
          {#each pv.profiles as pr}
            <label class="flex items-start gap-2 text-sm p-2 rounded-lg border line cursor-pointer {prof === String(pr.id) ? 'border-brand-600' : ''}">
              <input type="radio" name="prof" value={String(pr.id)} bind:group={prof} class="mt-1" />
              <span class="flex-1 min-w-0">
                <span class="font-medium">{pr.title || 'Designer settings'}</span>{#if pr.default} <span class="chip">default</span>{/if}
                <span class="block muted">{pr.printer || ''} {pr.time_s ? `· ${fmtTime(pr.time_s)}` : ''} {pr.grams ? `· ${Math.round(pr.grams)} g` : ''}</span>
                <span class="flex flex-wrap gap-1 mt-1">
                  {#each pr.filaments || [] as f}<span class="chip"><span class="swatch" style="background:{f.color || '#ccc'}"></span>{f.type} {f.usedG ? `${Math.round(f.usedG)}g` : ''}</span>{/each}
                </span>
              </span>
            </label>
          {/each}
        </div>
      </div>
    {/if}
    {#if pv.files?.length && pv.platform !== 'makerworld'}
      <details class="mt-3 text-sm"><summary class="cursor-pointer lbl">Files ({pv.files.length})</summary>
        <ul class="pl-4 mt-1 muted">{#each pv.files as f}<li class="truncate">{f.name} {f.size ? `· ${fmtBytes(f.size)}` : ''}</li>{/each}</ul></details>
    {/if}
    {#if pv.supplies.length}
      <div class="mt-3 text-sm"><div class="lbl">Hardware found (added to Materials and InvenTree)</div>
        <ul class="list-disc pl-5">{#each pv.supplies as s}<li>{#if s.qty_known === false}{s.title} <span class="muted">({s.packs ? `designer lists ${s.packs} pack${s.packs > 1 ? 's' : ''}; ` : ''}you set the count per print)</span>{:else}{s.qty} × {s.title}{/if}</li>{/each}</ul></div>
    {/if}
    {#if pv.files_in_browser}
      <p class="mt-3 text-xs p-2 rounded-lg" style="background:color-mix(in srgb, var(--warn) 10%, transparent)"><b>Files come from your browser:</b> {pv.platform_label} only lets a signed-in browser download. Details, photos and the license import now; then use the add-on's <i>Send files</i> on the model page, or drop the files on the model's Files tab.{#if pv.download_page} <a class="underline" target="_blank" rel="noreferrer" href={pv.download_page}>Open the download page</a>{/if}</p>
    {/if}
    {#if accountNote}<p class="mt-3 text-xs" style="color:var(--warn)">{accountNote}: connect it in <a class="underline" href="/settings">Settings</a>. Photos, details and license still import without it.</p>{/if}
    {#if plan}
      <div class="mt-3 text-sm">
        <label class="lbl" for="imp-cat">Category</label>
        <div class="flex gap-2">
          <select id="imp-cat" class="input" bind:value={category}>
            <option value="">No category</option>
            {#each plan.categories as c}<option value={c}>{c}{c === plan.suggested_category ? ' (suggested)' : ''}</option>{/each}
            <option value="__new">New category…</option>
          </select>
          {#if category === '__new'}<input class="input" placeholder="New category name" bind:value={newCategory} />{/if}
        </div>
        {#if plan.source_category}<p class="text-xs muted mt-1">On {pv.platform_label}: {plan.source_category}. Your choice is remembered for next time.</p>{/if}
        {#if plan.not_english?.length || plan.files_not_english}
          <p class="text-xs mt-2" style="color:var(--warn)">Not in English ({[...(plan.not_english || []), plan.files_not_english ? `${plan.files_not_english} file name(s)` : null].filter(Boolean).join(', ')}): {plan.options?.translate ? 'the hub puts it into English with your AI connection after the import' : 'translation is off in Connected services'}. The original wording is kept in the model's notes.</p>
        {/if}
      </div>
    {/if}
    <button class="btn primary w-full mt-4" onclick={doImport} disabled={busy || (!pv.sellable && !acceptNonSell)}>{busy ? 'Importing…' : pv.existing_model_id ? 'Re-import' : 'Import model'}</button>
    {#if status}<p class="text-sm muted mt-2">{status}</p>{/if}
  {/if}
</div>
