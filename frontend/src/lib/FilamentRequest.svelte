<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import { attempt } from '$lib/toast.svelte.js';
  import { can } from '$lib/session.svelte.js';
  let { m, onsaved } = $props();

  let f = $state(null);
  let cat = $state(null);
  let editing = $state(null);   // slot being changed by hand
  let pick = $state('');

  async function load() { f = await api.get(`/api/models/${m.id}/filaments`); }
  onMount(load);

  async function match() {
    f = await attempt(() => api.post(`/api/models/${m.id}/filaments/match`),
      f?.production?.source === 'file' ? `Filament read from the production file on ${f.production.printer}` : f?.brand ? `Matched to ${f.brand}` : 'Read the requested filament');
    onsaved?.();
  }
  async function startEdit(slot) {
    editing = slot; pick = '';
    if (!cat) cat = await api.get('/api/filaments/catalog');
  }
  async function choose(slot) {
    if (!pick) return;
    const [family, name] = pick.split('|');
    await attempt(() => api.put(`/api/models/${m.id}/filaments/${slot}`, { family, name }), 'Changed');
    editing = null; await load(); onsaved?.();
  }
  const SRC = { note: 'colour code in the notes', 'product photo': 'read from the product photos (approved)', measured: 'measured from a printed swatch (FilamentColors.xyz, CC BY 4.0)', SpoolmanDB: "the brand's published colour (SpoolmanDB)", 'project file': "the designer's project file",
    'Bambu colour of the same name': 'the Bambu Lab colour of the same name', 'colour name': 'the colour name',
    'approximate (colour name)': 'approximated from the colour name' };
  const closeness = (d) => (d == null ? '' : d < 3 ? 'same colour' : d < 6 ? 'very close' : d < 10 ? 'close' : 'nearest available');
  const families = $derived(cat ? [...new Set(cat.map((c) => c.family))] : []);
</script>

{#if f}
  <div class="panel p-5 mb-5 text-sm">
    <div class="flex flex-wrap items-center gap-2 mb-2">
      <h3 class="font-semibold">Filament</h3>
      {#if f.brand}<span class="chip ok">printing with {f.brand}</span>{/if}
      {#if can('models')}<button class="btn sm ml-auto" onclick={match}>{f.request ? 'Match again' : 'Read requested filament'}</button>{/if}
    </div>
    {#if f.request?.note}
      <p><span class="muted">Designer's recommendation:</span> <b>{f.request.note}</b>{#if f.request.link} · <a class="underline" target="_blank" rel="noopener" href={f.request.link}>link</a>{/if}</p>
    {/if}
    {#if f.request?.project_file}<p class="muted text-xs">Project file: {f.request.project_file}</p>{/if}
    {#if f.production?.source === 'file'}<p class="text-xs mt-1" style="color:var(--warn)">The file chosen for {f.production.printer} ({f.production.file}) prints its own colours, so they are the materials list below, not the match here. Match again re-reads that file; to use these colours, slice for {f.production.printer} instead of using the file.</p>{/if}
    {#if f.request?.slots?.length}
      <div class="scroll-x mt-2">
        <table class="grid">
          <thead><tr><th>Colour</th><th>Requested</th><th>Colour code</th><th>{f.brand || 'Bambu Lab'} match</th><th></th></tr></thead>
          <tbody>
            {#each f.request.slots as s}
              <tr>
                <td>{s.slot}</td>
                <td>
                  <div class="flex items-center gap-2"><span class="swatch" style="background:{s.hex || '#ccc'}"></span>
                    <span>{[s.brand_label, s.material, s.finish?.length ? s.finish.join(' ') : '', s.name].filter(Boolean).join(' ') || 'not recorded'}</span></div>
                  {#if s.project_preset}<div class="text-[11px] muted">project preset: {s.project_preset}</div>{/if}
                </td>
                <td class="text-xs"><code>{s.hex || '?'}</code><div class="muted">{SRC[s.hex_source] || ''}{#if s.catalog_url}: <a href={s.catalog_url} target="_blank" rel="noopener">{s.catalog_match}</a>{:else}{s.catalog_match ? `: ${s.catalog_match}` : ''}{/if}{#if s.td != null} · TD {s.td}{/if}{#if s.photo_accents?.length}<div>also in the photos: {s.photo_accents.map((a) => a.name).join(', ')}</div>{/if}</div></td>
                <td>
                  {#if s.bambu}
                    <div class="flex items-center gap-2"><span class="swatch" style="background:{s.bambu.hex}"></span>
                      <b>Bambu {s.bambu.family} {s.bambu.name}</b></div>
                    <div class="text-[11px] muted">code {s.bambu.code} · {s.bambu.hex}{#if s.bambu.measured_hex} (measured {#if s.bambu.measured_url}<a href={s.bambu.measured_url} target="_blank" rel="noopener">{s.bambu.measured_hex}</a>{:else}{s.bambu.measured_hex}{/if}){/if} · {closeness(s.bambu.delta_e)}{s.bambu.delta_e != null ? ` (difference ${s.bambu.delta_e})` : ''}{s.bambu.why?.startsWith('chosen') ? ` · ${s.bambu.why}` : ''}</div>
                  {:else}<span class="muted">no match</span>{/if}
                  {#if editing === s.slot}
                    <div class="flex gap-2 mt-1">
                      <select class="input text-xs" bind:value={pick}>
                        <option value="">Pick a Bambu Lab colour</option>
                        {#each families as fam}<optgroup label={fam}>{#each cat.filter((c) => c.family === fam) as c}<option value={`${c.family}|${c.name}`}>{c.name} ({c.hex})</option>{/each}</optgroup>{/each}
                      </select>
                      <button class="btn sm primary" onclick={() => choose(s.slot)} disabled={!pick}>Use</button>
                      <button class="btn sm" onclick={() => (editing = null)}>Cancel</button>
                    </div>
                  {/if}
                </td>
                <td>{#if can('models') && editing !== s.slot && f.production?.source !== 'file'}<button class="btn sm" onclick={() => startEdit(s.slot)}>Change</button>{/if}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      <p class="text-xs muted mt-2">The designer's request is kept as documentation. The {f.brand || 'Bambu Lab'} match goes into the materials list below and is what the slicer uses (its own filament preset and colour).</p>
    {:else if f.request}
      <p class="muted">No filament is recorded for this model.</p>
    {/if}
  </div>
{/if}
