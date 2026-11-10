<script>
  import { onMount } from 'svelte';
  import { api, fmtMoney } from '$lib/api.js';

  let days = $state(30);
  let view = $state('farm');
  let f = $state(null);
  let p = $state(null);
  let fu = $state(null);
  let measuring = $state(false);
  let onlyPriced = $state(true);
  let onlyLive = $state(false);
  let q = $state('');
  async function load() {
    [f, p, fu] = await Promise.all([api.get(`/api/reports/farm?days=${days}`), api.get(`/api/reports/profit?days=${days}`),
      api.get(`/api/reports/filament?days=${days}`)]);
  }
  onMount(load);
  const maxReason = $derived(f ? Math.max(1, ...f.reasons.map((r) => r.count)) : 1);
  const rows = $derived(p ? p.rows.filter((r) => (!onlyPriced || r.price != null) && (!onlyLive || r.live) &&
    (r.sku + ' ' + r.title).toLowerCase().includes(q.toLowerCase())) : []);
  const num = (v, d = 1) => (v == null ? '' : Number(v).toLocaleString(undefined, { maximumFractionDigits: d }));
  const kg = (g) => (g == null ? '' : g >= 1000 ? `${num(g / 1000, 2)} kg` : `${num(g, 0)} g`);
  const SOURCES = { printer: ['Reported by the printer', 'ok'], weighed: ['Corrected by a weigh-in', 'ok'],
    progress: ['Estimate x how far it got', 'warn'], estimate: ["Slicer's estimate", ''], none: ['Unknown', 'bad'] };
  async function measure() {
    measuring = true;
    try { await api.post('/api/farm/filament/measure', { days: Math.min(days, 120) }); await load(); } finally { measuring = false; }
  }
</script>

<div class="flex flex-wrap items-end gap-3 mb-4">
  <div class="mr-auto">
    <h1 class="text-2xl font-bold">Reports</h1>
    <p class="muted text-sm">What the farm printed, what failed and what it cost, and what each product earns.</p>
  </div>
  <label class="text-sm">Last
    <select class="input inline-block" style="width: auto" bind:value={days} onchange={load}>
      <option value={7}>7 days</option><option value={30}>30 days</option><option value={90}>90 days</option><option value={365}>365 days</option>
    </select></label>
</div>

<div class="flex border-b line mb-4">
  {#each [['farm', 'Failures and waste'], ['printers', 'Printers'], ['filament', 'Filament use'], ['profit', 'Profit']] as [k, l]}
    <button class="px-4 py-2.5 text-sm font-semibold border-b-2 {view === k ? 'border-brand-600' : 'border-transparent muted'}" onclick={() => (view = k)}>{l}</button>
  {/each}
</div>

{#if !f || !p}<p class="muted">Loading...</p>
{:else if view === 'farm'}
  <div class="grid gap-3 mb-4" style="grid-template-columns: repeat(auto-fit, minmax(150px, 1fr))">
    {#each [['Prints', f.totals.jobs], ['Success rate', f.totals.success_rate != null ? `${f.totals.success_rate}%` : 'n/a'],
            ['Print hours', num(f.totals.print_hours)], ['Filament', kg(f.totals.filament_g)], ['Failed', f.totals.failed],
            ['Filament wasted', kg(f.totals.waste_g)], ['Hours wasted', num(f.totals.waste_hours)], ['Cost of failures', fmtMoney(f.totals.waste_cost)]] as [l, v]}
      <div class="panel p-4"><div class="text-xs muted">{l}</div><div class="text-2xl font-bold">{v}</div></div>
    {/each}
  </div>
  <div class="grid gap-4" style="grid-template-columns: repeat(auto-fit, minmax(340px, 1fr))">
    <div class="panel p-4">
      <div class="flex items-center mb-3"><h2 class="font-semibold mr-auto">Why prints failed</h2><a class="btn sm" href={`/api/reports/reasons.csv?days=${days}`}>CSV</a></div>
      {#each f.reasons as r}
        <div class="mb-2">
          <div class="flex text-sm"><span class="mr-auto">{r.reason}</span><span class="font-semibold">{r.count}</span></div>
          <div class="h-2 rounded" style="background: var(--panel-2)"><div class="h-2 rounded" style="width: {(100 * r.count) / maxReason}%; background: #dc2626"></div></div>
          <div class="text-xs muted">{kg(r.waste_g)}, {num(r.waste_hours)} h</div>
        </div>
      {:else}<p class="muted text-sm">No failed prints in this period.</p>{/each}
      <p class="text-xs muted mt-2">Reasons come from plate checks; prints Watchtower stopped show its error.</p>
    </div>
    <div class="panel p-4 scroll-x">
      <div class="flex items-center mb-3"><h2 class="font-semibold mr-auto">Models that fail most</h2><a class="btn sm" href={`/api/reports/models.csv?days=${days}`}>CSV</a></div>
      <table class="grid">
        <thead><tr><th>Model</th><th>Prints</th><th>Failed</th><th>Wasted</th><th class="text-right">Cost</th></tr></thead>
        <tbody>
          {#each f.models as m}
            <tr><td class="text-sm"><a class="hover:underline" href={`/models/${m.model_id}`}>{m.code} {m.name}</a></td><td>{m.jobs}</td>
              <td>{m.failed} <span class="text-xs muted">({m.failure_rate}%)</span></td><td class="text-sm">{kg(m.waste_g)}</td><td class="text-right">{fmtMoney(m.waste_cost)}</td></tr>
          {:else}<tr><td colspan="5" class="muted text-center py-4">None.</td></tr>{/each}
        </tbody>
      </table>
    </div>
  </div>
  <p class="text-xs muted mt-3">Grams are Watchtower's estimate from the sliced file; a failed print counts the share it got through. Costs use the same filament prices and printer rates as slice costs.</p>

{:else if view === 'filament'}
  <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
    {#each [['Measured prints', fu.measured_jobs], ['Measured vs estimate', fu.measured_vs_estimate != null ? `${Math.round(fu.measured_vs_estimate * 100)}%` : 'n/a'],
            ['Printers that report use', fu.klipper_printers.length ? fu.klipper_printers.join(', ') : 'none'], ['Weigh-ins', fu.weigh_ins.length]] as [l, v]}
      <div class="panel p-3"><div class="text-xs muted uppercase tracking-wide">{l}</div><div class="text-xl font-semibold">{v}</div></div>
    {/each}
  </div>
  <div class="panel p-4 scroll-x mb-4">
    <div class="flex items-center mb-3"><h2 class="font-semibold mr-auto">Where each print's filament figure comes from</h2>
      <button class="btn sm" onclick={measure} disabled={measuring}>{measuring ? 'Measuring...' : 'Measure again'}</button></div>
    <table class="grid">
      <thead><tr><th>Source</th><th>Prints</th><th class="text-right">Filament</th><th class="text-right">Estimate for the same prints</th></tr></thead>
      <tbody>
        {#each Object.entries(fu.by_source) as [k, v]}
          <tr><td><span class="chip {SOURCES[k]?.[1] || ''}">{SOURCES[k]?.[0] || k}</span></td><td>{v.jobs}</td>
            <td class="text-right">{kg(v.used_g)}</td><td class="text-right muted">{kg(v.estimate_g)}</td></tr>
        {:else}<tr><td colspan="4" class="muted text-center py-4">No finished prints in this period.</td></tr>{/each}
      </tbody>
    </table>
    <p class="text-xs muted mt-2">Klipper printers report what they really extruded; other prints are corrected when their spool is weighed in Spoolman (the difference is spread over the prints on that spool since the last weigh-in). Failed and cancelled prints count the estimate times how far they got. Costs in Profit use the measured figures once a model has any.</p>
  </div>
  <div class="panel p-4 scroll-x">
    <h2 class="font-semibold mb-3">Recent weigh-ins</h2>
    <table class="grid">
      <thead><tr><th>Spoolman spool</th><th>When</th><th class="text-right">Recorded</th><th class="text-right">Scale says</th><th>Applied</th></tr></thead>
      <tbody>
        {#each fu.weigh_ins as w}
          <tr><td>#{w.spool}</td><td class="text-sm">{new Date(w.at).toLocaleString()}</td><td class="text-right">{kg(w.tracked_g)}</td>
            <td class="text-right">{kg(w.measured_g)}</td>
            <td class="text-sm">{w.factor != null ? `x${num(w.factor, 3)} on ${w.jobs} prints` : w.note}</td></tr>
        {:else}<tr><td colspan="5" class="muted text-center py-4">None yet. Weigh a spool and update its weight in Spoolman.</td></tr>{/each}
      </tbody>
    </table>
  </div>

{:else if view === 'printers'}
  <div class="panel p-4 scroll-x">
    <div class="flex items-center mb-3"><h2 class="font-semibold mr-auto">Printers, last {days} days</h2><a class="btn sm" href={`/api/reports/printers.csv?days=${days}`}>CSV</a></div>
    <table class="grid">
      <thead><tr><th>Printer</th><th>Prints</th><th>Success</th><th>Print hours</th><th style="min-width: 160px">Busy</th><th>Wasted</th><th class="text-right">Cost of failures</th></tr></thead>
      <tbody>
        {#each f.printers as r}
          <tr>
            <td class="font-medium">{r.printer}</td><td>{r.jobs}</td>
            <td><span class="chip {r.success_rate >= 90 ? 'ok' : r.success_rate >= 75 ? 'warn' : 'bad'}">{r.success_rate ?? ''}%</span></td>
            <td>{num(r.print_hours)}</td>
            <td><div class="flex items-center gap-2"><div class="h-2 flex-1 rounded" style="background: var(--panel-2)"><div class="h-2 rounded" style="width: {Math.min(100, r.utilisation)}%; background: #ea580c"></div></div><span class="text-xs">{r.utilisation}%</span></div></td>
            <td class="text-sm">{kg(r.waste_g)}, {num(r.waste_hours)} h</td><td class="text-right">{fmtMoney(r.waste_cost)}</td>
          </tr>
        {:else}<tr><td colspan="7" class="muted text-center py-4">No finished prints in this period.</td></tr>{/each}
      </tbody>
    </table>
    <p class="text-xs muted mt-2">Busy is print hours out of every hour in the period.</p>
  </div>

{:else}
  <div class="grid gap-3 mb-4" style="grid-template-columns: repeat(auto-fit, minmax(150px, 1fr))">
    {#each [['Units sold', p.totals.units], ['Revenue', fmtMoney(p.totals.revenue)], ['Profit', fmtMoney(p.totals.profit)],
            ['Products with a price', `${p.totals.priced} of ${p.totals.priced + p.totals.unpriced}`]] as [l, v]}
      <div class="panel p-4"><div class="text-xs muted">{l}</div><div class="text-2xl font-bold">{v}</div></div>
    {/each}
  </div>
  <div class="panel p-4 scroll-x">
    <div class="flex flex-wrap items-center gap-3 mb-3">
      <input class="input" style="max-width: 16rem" placeholder="Search SKU or name" bind:value={q} />
      <label class="text-sm flex items-center gap-2"><input type="checkbox" bind:checked={onlyPriced} /> Priced only</label>
      <label class="text-sm flex items-center gap-2"><input type="checkbox" bind:checked={onlyLive} /> Live only</label>
      <a class="btn sm ml-auto" href={`/api/reports/profit.csv?days=${days}`}>CSV</a>
    </div>
    <table class="grid">
      <thead><tr><th>SKU</th><th class="text-right">Price</th><th class="text-right">Filament</th><th class="text-right">Machine</th><th class="text-right">Parts</th>
        <th class="text-right">Cost</th><th class="text-right">Margin</th><th class="text-right">Print h</th><th class="text-right">Per printer-hour</th><th class="text-right">Sold</th><th class="text-right">Profit</th></tr></thead>
      <tbody>
        {#each rows.slice(0, 300) as r}
          <tr>
            <td class="text-sm"><a class="hover:underline" href={`/models/${r.model_id}`}>{r.sku}</a> {#if r.live}<span class="chip live">live</span>{/if}<div class="text-xs muted">{r.title}</div></td>
            <td class="text-right">{fmtMoney(r.price)}</td><td class="text-right" title={r.material_basis === 'estimate' ? "From the slicer's estimate" : `Slicer said ${fmtMoney(r.material_estimate)}; ${r.material_basis}`}>{fmtMoney(r.material_cost)}{#if r.material_basis !== 'estimate'}<div class="text-xs" style="color: var(--ok)">measured</div>{/if}</td><td class="text-right">{fmtMoney(r.machine_cost)}</td>
            <td class="text-right">{fmtMoney(r.parts_cost)}{#if r.parts_price_missing}<span title="A part has no supplier price in InvenTree" class="text-amber-600"> *</span>{/if}</td>
            <td class="text-right font-medium">{r.cost == null ? '' : fmtMoney(r.cost)}{#if r.cost == null}<span class="text-xs muted">no approved slice</span>{/if}</td>
            <td class="text-right {r.margin < 0 ? 'text-red-600' : ''}">{fmtMoney(r.margin)}{#if r.margin_pct != null}<div class="text-xs muted">{r.margin_pct}%</div>{/if}</td>
            <td class="text-right">{num(r.print_hours, 2)}</td><td class="text-right font-medium">{fmtMoney(r.per_printer_hour)}</td>
            <td class="text-right">{r.units_sold || ''}</td><td class="text-right">{r.profit ? fmtMoney(r.profit) : ''}</td>
          </tr>
        {:else}<tr><td colspan="11" class="muted text-center py-6">{onlyPriced ? 'No products have a price yet. Untick "Priced only" to see costs.' : 'Nothing matches.'}</td></tr>{/each}
      </tbody>
    </table>
    {#if rows.length > 300}<p class="text-xs muted mt-2">Showing 300 of {rows.length}; the CSV has them all.</p>{/if}
    <p class="text-xs muted mt-2">Cost per unit = filament and machine time from the {p.printer || 'default'} printer's approved slice, plus BOM parts at their cheapest InvenTree supplier price. Revenue is units sold (paid, not test) at list price.</p>
  </div>
{/if}
