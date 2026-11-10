<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';

  // A printer's tools as the hardware really is. A tool switcher (H2C: one fixed left nozzle, a right extruder that
  // swaps hotends from a rack) shows each extruder with its mounted nozzle and the rack. A tool changer (Prusa XL,
  // Core One INDX, Snapmaker U1) shows "Tool changer" and one chip per tool. Anything else: one chip per tool.
  let { prof } = $props();
  let live = $state(null);
  let open = $state(false);
  const farmName = $derived(prof.wt_printer || prof.name);
  onMount(async () => {
    live = await api.get(`/api/mobile/v1/printers/${encodeURIComponent(farmName)}/nozzles`).catch(() => null);
  });
  const TYPE = { hardened_steel: 'HS', stainless_steel: 'SS', brass: 'Brass', tungsten_carbide: 'TC' };
  const short = (n) => [`${Number(n.diameter_mm ?? n.nozzle_diameter ?? 0) || '?'}`,
    TYPE[n.nozzle_type] || (n.nozzle_type && n.nozzle_type !== 'undefine' ? n.nozzle_type : ''),
    /high/i.test(n.volume_type || '') ? 'HF' : ''].filter(Boolean).join(' ');
  const rack = $derived(live?.rack || []);
  const groups = $derived.by(() => {
    const g = new Map();
    for (const n of rack) { const k = short(n); g.set(k, (g.get(k) || 0) + 1); }
    return [...g.entries()];
  });
  const extruders = $derived([...(live?.nozzles || [])].sort((a, b) => (a.position === 'left' ? -1 : 1) - (b.position === 'left' ? -1 : 1)));
  const changer = $derived(!rack.length && (prof.tools?.length || 0) > 2);
</script>

{#if rack.length}
  <div class="flex flex-col gap-1 mt-1 text-sm">
    {#each extruders as n}
      <div class="flex flex-wrap items-center gap-2">
        <span class="muted text-xs w-12 capitalize">{n.position || `T${n.extruder_index + 1}`}</span>
        <span class="chip {n.active ? 'ok' : ''}" title={n.active ? 'printing with this one' : ''}>{short(n)}{n.active ? ' ●' : ''}</span>
        {#if n.position === 'right' || (n.position !== 'left' && extruders.length === 1)}
          <span class="muted text-xs">rack:</span>
          {#each groups as [k, c]}<button class="chip" onclick={() => (open = !open)} title="Show each rack slot">{k}{c > 1 ? ` ×${c}` : ''}</button>{/each}
          <span class="muted text-xs">{rack.length + 1} nozzles</span>
        {:else}<span class="muted text-xs">fixed</span>{/if}
      </div>
    {/each}
    {#if open}
      <div class="text-xs muted grid gap-0.5 ml-14">
        {#each rack as n}<div>slot {n.id - 15}: {short(n)} · {n.serial_number || ''}{n.wear != null ? ` · wear ${n.wear}` : ''}</div>{/each}
      </div>
    {/if}
    <div class="text-xs muted">Tool switcher: slices as {extruders.length} extruders; the right one switches to any nozzle in its rack.</div>
  </div>
{:else}
  <div class="flex flex-wrap items-center gap-1.5 mt-1">
    {#if changer}<span class="text-xs muted mr-1">Tool changer · {prof.tools.length} tools</span>{/if}
    {#each prof.tools as tl}
      <span class="chip" title={tl.preset || ''}><span class="swatch" style="background:{tl.color || '#ccc'}"></span>T{tl.slot} {tl.nozzle}{tl.flow === 'hf' ? ' HF' : ''}{tl.material ? ` ${tl.material}` : ''}</span>
    {/each}
  </div>
{/if}
