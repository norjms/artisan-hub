<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  import { can } from '$lib/session.svelte.js';

  // Filament on the farm: what each printer slot holds and which library spool it is, so every print's grams are
  // taken off the right spool. A slot the printer reports loaded but with no spool linked is used untracked.
  let { printers = [] } = $props();
  let loaded = $state({});         // printer name -> engine "loaded" answer
  let spools = $state([]);
  let source = $state('');
  let q = $state('');
  let picking = $state(null);      // {printer, slot}
  let busy = $state(false);
  let err = $state({});

  const M = '/api/mobile/v1';
  async function load() {
    const s = await api.get(`${M}/spools`).catch((e) => ({ spools: [], error: e.message }));
    spools = s.spools || []; source = s.source || '';
    const out = {};
    await Promise.all(printers.map(async (p) => {
      try { out[p.name] = await api.get(`${M}/printers/${encodeURIComponent(p.name)}/loaded`); }
      catch (e) { err[p.name] = e.message; }
    }));
    loaded = out;
  }
  onMount(load);

  const hex = (c) => (c ? '#' + String(c).replace('#', '').slice(0, 6) : 'transparent');
  // The library spool in this slot: by where each spool says it is installed, else by the slot's own assignment.
  const spoolOf = (printer, slot) =>
    spools.find((s) => s.assigned && s.assigned.printer === printer && Number(s.assigned.ams_id) === Number(slot.ams_id)
                       && Number(s.assigned.tray_id) === Number(slot.tray_id))
    || (slot.assignment && spools.find((s) => s.id === (slot.assignment.spool_id ?? slot.assignment.id))) || null;
  const near = (a, b) => {
    if (!a || !b) return 99;
    const p = (h) => [0, 2, 4].map((i) => parseInt(h.replace('#', '').slice(i, i + 2), 16));
    const [x, y] = [p(a), p(b)];
    return Math.sqrt(x.reduce((t, v, i) => t + (v - y[i]) ** 2, 0)) / 4.42;   // 0 same .. 100 opposite
  };
  // Library spools that fit what the printer says is in the slot, best first.
  function matches(slot) {
    const mat = (slot.live?.tray_type || '').toUpperCase();
    const col = slot.live?.tray_color ? hex(slot.live.tray_color) : null;
    return spools.filter((s) => !s.archived && !s.assigned && (!mat || (s.material || '').toUpperCase() === mat))
      .map((s) => ({ s, d: col ? near(col, s.color_hex) : 50 }))
      .sort((a, b) => a.d - b.d).map((x) => x.s);
  }
  async function link(printer, slot, spool) {
    busy = true;
    try {
      const body = { printer, ams_id: slot.ams_id, tray_id: slot.tray_id };
      if (slot.extruders?.length > 1 && slot.extruder_index != null) body.extruder_index = slot.extruder_index;
      await attempt(() => api.post(`${M}/spools/${spool.id}/assign`, body), `${spool.code} linked to ${printer} ${slot.label}`);
      picking = null; await load();
    } finally { busy = false; }
  }
  async function unlink(printer, slot, spool) {
    busy = true;
    try { await attempt(() => api.post(`${M}/spools/${spool.id}/unassign`), `${spool.code} taken out of ${printer} ${slot.label}`); await load(); }
    finally { busy = false; }
  }
  const untracked = $derived(Object.entries(loaded).flatMap(([name, l]) => (l.slots || []).filter((x) => x.live?.tray_type && !spoolOf(name, x))).length);
  const shown = $derived(spools.filter((s) => !s.archived && (!q || `${s.code} ${s.vendor} ${s.name} ${s.material} ${s.color_name || ''}`.toLowerCase().includes(q.toLowerCase()))));
</script>

{#if untracked}<div class="panel p-3 mb-3 text-sm text-amber-600">{untracked} loaded slot{untracked === 1 ? ' has' : 's have'} no spool linked, so what prints there isn't taken off any spool. Link them below.</div>{/if}

<div class="grid gap-3 mb-6" style="grid-template-columns: repeat(auto-fill, minmax(340px, 1fr))">
  {#each printers as p (p.name)}
    <div class="panel p-4 flex flex-col gap-2">
      <b>{p.name}</b>
      {#if err[p.name]}<div class="text-xs text-red-600">{err[p.name]}</div>{/if}
      {#each loaded[p.name]?.slots || [] as slot (`${slot.ams_id}-${slot.tray_id}`)}
        {@const sp = spoolOf(p.name, slot)}
        <div class="flex items-center gap-2 text-sm">
          <span class="swatch" style="background:{sp ? sp.color_hex : hex(slot.live?.tray_color)}"></span>
          <span class="w-28 shrink-0 muted text-xs">{slot.label}</span>
          {#if sp}
            <span class="flex-1">{sp.code} {sp.vendor || ''} {sp.name || sp.material} <span class="muted text-xs">{Math.round(sp.remaining_g ?? 0)} g left</span></span>
            {#if can('inventory')}<button class="btn sm" disabled={busy} onclick={() => unlink(p.name, slot, sp)}>Unlink</button>{/if}
          {:else if slot.live?.tray_type}
            <span class="flex-1 text-amber-600">{slot.live.tray_type} loaded, no spool linked</span>
            {#if can('inventory')}
              {@const m = matches(slot)}
              {#if m[0]}<button class="btn primary sm" disabled={busy} onclick={() => link(p.name, slot, m[0])} title="Best match by material and colour">Link {m[0].code}</button>{/if}
              <button class="btn sm" onclick={() => (picking = { printer: p.name, slot })}>Pick</button>
            {/if}
          {:else}
            <span class="flex-1 muted text-xs">empty</span>
            {#if can('inventory')}<button class="btn sm" onclick={() => (picking = { printer: p.name, slot })}>Load</button>{/if}
          {/if}
        </div>
      {:else}{#if !err[p.name]}<div class="text-xs muted">{loaded[p.name] ? 'No slots reported.' : 'Loading...'}</div>{/if}{/each}
    </div>
  {/each}
</div>

{#if picking}
  <div class="fixed inset-0 bg-black/40 z-40 grid place-items-center p-4" role="presentation" onclick={(e) => { if (e.target === e.currentTarget) picking = null; }}>
    <div class="panel p-5 w-full max-w-lg max-h-[85vh] overflow-auto text-sm flex flex-col gap-2">
      <h3 class="font-semibold">Spool for {picking.printer} {picking.slot.label}</h3>
      {#each [...matches(picking.slot), ...spools.filter((s) => !s.archived && !s.assigned && !matches(picking.slot).includes(s))] as s (s.id)}
        <button class="flex items-center gap-2 text-left hover:underline" disabled={busy} onclick={() => link(picking.printer, picking.slot, s)}>
          <span class="swatch" style="background:{s.color_hex}"></span>{s.code} {s.vendor || ''} {s.name || ''} {s.material}<span class="muted text-xs ml-auto">{Math.round(s.remaining_g ?? 0)} g</span>
        </button>
      {:else}<div class="muted">No free spools in the library.</div>{/each}
      <div><button class="btn" onclick={() => (picking = null)}>Cancel</button></div>
    </div>
  </div>
{/if}

<div class="flex flex-wrap items-center gap-2 mb-2">
  <h2 class="font-semibold mr-auto">Filament library{source === 'farm' ? '' : ' (Spoolman)'}</h2>
  <input class="input" style="width: 16rem" placeholder="Search spools" bind:value={q} />
  <a class="btn sm" href="/inventory">Inventory</a>
</div>
<div class="panel p-4 scroll-x mb-6">
  <table class="grid">
    <thead><tr><th>Spool</th><th>Filament</th><th>Left</th><th>Where</th></tr></thead>
    <tbody>
      {#each shown as s (s.id)}
        <tr>
          <td class="whitespace-nowrap"><span class="swatch" style="background:{s.color_hex}"></span> {s.code}</td>
          <td>{s.vendor || ''} {s.name || ''} <span class="muted text-xs">{s.material}{s.color_name ? ` · ${s.color_name}` : ''}</span></td>
          <td class="whitespace-nowrap">{Math.round(s.remaining_g ?? 0)} g{s.label_weight_g ? ` of ${Math.round(s.label_weight_g)}` : ''}</td>
          <td>{s.assigned ? `${s.assigned.printer} ${s.assigned.slot_label || ''}` : s.location || s.location_code || ''}</td>
        </tr>
      {:else}<tr><td colspan="4" class="muted text-center py-4">No spools.</td></tr>{/each}
    </tbody>
  </table>
</div>
