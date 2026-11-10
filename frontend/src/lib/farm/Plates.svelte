<script>
  import { onMount } from 'svelte';
  import { api, ago } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  import { can } from '$lib/session.svelte.js';

  // Build plates: what is on each printer, the catalog, and plate reslices waiting for a look.
  // Swapping a plate here updates the farm's plate check and the Bambu marker detection (services/plates.py).
  let cat = $state(null);
  let printers = $state([]);
  let review = $state([]);
  let pick = $state({});          // profile_id -> plate id chosen in the dropdown
  let edit = $state(null);        // plate being added or edited
  let busy = $state(false);

  async function load() {
    const [c, p, r] = await Promise.all([api.get('/api/plates'), api.get('/api/printer-profiles/plates'),
                                         api.get('/api/plate-slices?state=review')]);
    cat = c; printers = p.printers; review = r.plate_slices;
    pick = Object.fromEntries(printers.map((x) => [x.profile_id, x.installed_plate ? String(x.installed_plate.id) : '']));
  }
  onMount(load);
  const makers = $derived(cat ? [...new Set(cat.plates.map((p) => p.maker || 'Other'))] : []);

  async function swap(row) {
    const id = pick[row.profile_id];
    const plate = cat.plates.find((p) => String(p.id) === id);
    if (!confirm(plate ? `Record ${plate.name} as fitted on ${row.printer}?` : `Clear the plate recorded on ${row.printer}?`)) return;
    busy = true;
    try {
      const r = await attempt(() => api.put(`/api/printer-profiles/${row.profile_id}/plate`, { plate_id: id ? Number(id) : null }));
      toast(r.errors?.length ? `${row.printer}: ${r.errors.join('; ')}` : `${row.printer}: ${plate ? plate.name : 'no plate'} recorded`, r.errors?.length ? 'bad' : 'ok', 7000);
      await load();
    } finally { busy = false; }
  }

  function openPlate(p) {
    edit = p ? { id: p.id, maker: p.maker || '', name: p.name, slicer_type: p.slicer_type, bambu_marker: p.bambu_marker, materials: p.materials.join(', '), notes: p.notes || '' }
             : { maker: '', name: '', slicer_type: cat.slicer_types[0], bambu_marker: false, materials: '', notes: '' };
  }
  async function savePlate() {
    const body = { maker: edit.maker, name: edit.name, slicer_type: edit.slicer_type, bambu_marker: edit.bambu_marker, notes: edit.notes,
                   materials: edit.materials.split(/[,\s]+/).map((m) => m.trim()).filter(Boolean) };
    if (edit.id) await attempt(() => api.patch(`/api/plates/${edit.id}`, body), 'Plate saved');
    else await attempt(() => api.post('/api/plates', body), 'Plate added');
    edit = null; load();
  }
  async function delPlate(p) {
    if (!confirm(`Remove ${p.name} from the catalog?`)) return;
    await attempt(() => api.del(`/api/plates/${p.id}`), 'Plate removed'); load();
  }
  async function approve(s) {
    await attempt(() => api.post(`/api/plate-slices/${s.id}/approve`), `Approved: the waiting job on ${s.printer} is swapped within a minute`);
    load();
  }
</script>

{#if !cat}<p class="muted">Loading...</p>{:else}
  <div class="flex flex-wrap items-center gap-2 mb-3">
    <h2 class="font-semibold mr-auto">Fitted plates</h2>
  </div>
  <p class="text-sm muted mb-3">When a job was sliced for another plate, the hub reslices it for the plate recorded here
    (if that plate suits the job's filaments) and the job prints. Keep this current when you swap a plate.</p>
  <div class="panel p-4 scroll-x mb-6">
    <table class="grid">
      <thead><tr><th>Printer</th><th>Usually slices for</th><th>Fitted now</th><th></th></tr></thead>
      <tbody>
        {#each printers as row (row.profile_id)}
          <tr>
            <td class="font-semibold">{row.printer}{#if row.farm_printer !== row.printer} <span class="muted text-xs">({row.farm_printer})</span>{/if}</td>
            <td>{row.usual_plate || 'printer default'}</td>
            <td>
              {#if can('farm')}
                <select class="input" style="min-width: 12rem" bind:value={pick[row.profile_id]}>
                  <option value="">Not recorded</option>
                  {#each makers as mk}<optgroup label={mk}>{#each cat.plates.filter((p) => (p.maker || 'Other') === mk) as p}<option value={String(p.id)}>{p.name} ({p.slicer_type})</option>{/each}</optgroup>{/each}
                </select>
              {:else}{row.installed_plate ? row.installed_plate.name : 'Not recorded'}{/if}
              {#if row.installed_plate && row.bambu && !row.installed_plate.bambu_marker}<div class="text-xs muted">Bambu plate detection is off while it is fitted</div>{/if}
            </td>
            <td>{#if can('farm') && pick[row.profile_id] !== (row.installed_plate ? String(row.installed_plate.id) : '')}
              <button class="btn primary sm" disabled={busy} onclick={() => swap(row)}>Record swap</button>{/if}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>

  {#if review.length}
    <h2 class="font-semibold mb-2">Reslices waiting for a look</h2>
    <div class="grid gap-3 mb-6" style="grid-template-columns: repeat(auto-fill, minmax(280px, 1fr))">
      {#each review as s (s.id)}
        <div class="panel p-4 flex flex-col gap-2">
          <div class="flex items-start gap-2"><div class="font-semibold mr-auto">{s.printer}</div><span class="chip warn">{s.plate}</span></div>
          {#each s.problems as p}<div class="text-sm text-amber-600">{p.msg}</div>{/each}
          {#if s.stats?.time_s}<div class="text-xs muted">{Math.round(s.stats.time_s / 60)} min, {s.stats.grams} g</div>{/if}
          {#if s.sliced_at}<div class="text-xs muted">sliced {ago(s.sliced_at)}</div>{/if}
          {#if can('slice')}<div><button class="btn primary sm" onclick={() => approve(s)}>Approve and print</button></div>{/if}
        </div>
      {/each}
    </div>
  {/if}

  <div class="flex flex-wrap items-center gap-2 mb-2">
    <h2 class="font-semibold mr-auto">Plate catalog</h2>
    {#if can('slice')}<button class="btn sm" onclick={() => openPlate(null)}>Add a plate</button>{/if}
  </div>
  {#if edit}
    <div class="panel p-4 mb-4 flex flex-col gap-2">
      <div class="flex flex-wrap gap-2">
        <input class="input" style="width: 9rem" placeholder="Maker" bind:value={edit.maker} />
        <input class="input" style="flex: 1; min-width: 12rem" placeholder="Name (e.g. Darkmoon CFX)" bind:value={edit.name} />
        <label class="text-sm flex items-center gap-2">Slices as
          <select class="input" bind:value={edit.slicer_type}>{#each cat.slicer_types as t}<option value={t}>{t}</option>{/each}</select></label>
      </div>
      <input class="input" placeholder="Filaments it suits, e.g. PLA, PETG, ABS, PA (families; PA covers PA6, PA12, PAHT)" bind:value={edit.materials} />
      <label class="text-sm flex items-center gap-2"><input type="checkbox" bind:checked={edit.bambu_marker} /> Genuine Bambu plate (the printer can recognise it; detection stays on)</label>
      <input class="input" placeholder="Notes" bind:value={edit.notes} />
      <div class="flex gap-2"><button class="btn primary" onclick={savePlate}>Save plate</button><button class="btn" onclick={() => (edit = null)}>Cancel</button></div>
    </div>
  {/if}
  <div class="panel p-4 scroll-x mb-6">
    <table class="grid">
      <thead><tr><th>Maker</th><th>Plate</th><th>Slices as</th><th>Bambu marker</th><th>Suits</th><th>On</th><th></th></tr></thead>
      <tbody>
        {#each cat.plates as p (p.id)}
          <tr>
            <td class="muted text-xs whitespace-nowrap">{p.maker || ''}</td>
            <td class="font-semibold">{p.name}{#if p.notes}<div class="text-xs muted font-normal">{p.notes}</div>{/if}</td>
            <td>{p.slicer_type}</td>
            <td>{p.bambu_marker ? 'yes' : 'no'}</td>
            <td>{#if p.materials.length}<div class="flex flex-wrap gap-1">{#each p.materials as m}<span class="chip">{m}</span>{/each}</div>{:else}<span class="chip bad">not filled in: suits nothing</span>{/if}</td>
            <td>{p.installed_on?.join(', ') || ''}</td>
            <td class="whitespace-nowrap">{#if can('slice')}<button class="btn sm" onclick={() => openPlate(p)}>Edit</button>
              {#if !p.installed_on?.length}<button class="btn sm" onclick={() => delPlate(p)}>Remove</button>{/if}{/if}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{/if}
