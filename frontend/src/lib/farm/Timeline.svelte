<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  import { can } from '$lib/session.svelte.js';

  let tl = $state(null);
  async function load() { tl = await api.get('/api/farm/timeline'); }
  onMount(() => { load(); const t = setInterval(load, 60000); return () => clearInterval(t); });

  const t0 = $derived(tl ? new Date(tl.now).getTime() : 0);
  const t1 = $derived(tl ? new Date(tl.until).getTime() : 1);
  const pos = (iso) => Math.max(0, Math.min(100, ((new Date(iso).getTime() - t0) / (t1 - t0)) * 100));
  const ticks = $derived.by(() => {
    if (!tl) return [];
    const span = (t1 - t0) / 3600000;
    const step = span <= 24 ? 3 : span <= 72 ? 12 : 24;
    const out = [];
    const start = new Date(t0); start.setMinutes(0, 0, 0);
    for (let t = start.getTime() + 3600000; t < t1; t += 3600000) {
      const d = new Date(t);
      if (d.getHours() % step === 0) out.push({ left: ((t - t0) / (t1 - t0)) * 100, label: step >= 24 || d.getHours() === 0 ? d.toLocaleDateString([], { weekday: 'short', day: 'numeric' }) : d.toLocaleTimeString([], { hour: 'numeric' }) });
    }
    return out;
  });
  const palette = ['#ea580c', '#2563eb', '#16a34a', '#9333ea', '#db2777', '#0891b2', '#ca8a04', '#4f46e5'];
  function colour(b) {
    if (!b.orders.length) return b.status === 'printing' ? '#64748b' : '#94a3b8';
    let h = 0; for (const c of b.orders[0]) h = (h * 31 + c.charCodeAt(0)) >>> 0;
    return palette[h % palette.length];
  }
  const fmt = (iso) => iso ? new Date(iso).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' }) : '';
  const stateCls = { late: 'bad', at_risk: 'warn', on_time: 'ok', unknown: '' };
  const stateLabel = { late: 'late', at_risk: 'at risk', on_time: 'on time', unknown: "can't tell" };

  async function setDue(o, v) {
    await attempt(() => api.put(`/api/orders/${o.id}/due`, { due_at: v || null }), v ? 'Due date set' : 'Back to the default due date');
    load();
  }
  async function prioritise(o, p = 75) {
    const r = await attempt(() => api.post(`/api/orders/${o.id}/prioritise`, { priority: p }));
    toast(r.errors.length ? `Raised ${r.raised.length}; ${r.errors.join('; ')}` : `Raised ${r.raised.length} job(s) to ${p === 100 ? 'urgent' : 'high'}`, r.errors.length ? 'bad' : 'ok', 7000);
    load();
  }
</script>

{#if !tl}<p class="muted">Loading...</p>{:else}
  <p class="text-sm muted mb-3">An estimate: printers free up when Legacy farm says their print ends, plus {Math.round(tl.turnaround_minutes)} minutes to clear the plate; waiting jobs go highest priority first, oldest first, to the printer that is free soonest and has an approved slice of the model. Legacy farm makes the real choice.</p>

  <div class="panel p-4 scroll-x mb-4">
    <h2 class="font-semibold mb-2">Orders in the farm</h2>
    <table class="grid">
      <thead><tr><th>Order</th><th>Due</th><th>Prints done by</th><th>State</th><th>Jobs left</th><th></th></tr></thead>
      <tbody>
        {#each tl.orders as o}
          <tr>
            <td>#{o.number} <span class="text-xs muted">{o.customer || ''}</span></td>
            <td>{#if can('farm')}<input class="input" style="width: 10rem" type="date" value={o.due_at ? o.due_at.slice(0, 10) : ''} onchange={(e) => setDue(o, e.target.value)} />{:else}{fmt(o.due_at)}{/if}
              {#if !o.due_set}<div class="text-xs muted">default</div>{/if}</td>
            <td class="text-sm">{o.projected ? fmt(o.projected) : ''}</td>
            <td><span class="chip {stateCls[o.state]}">{stateLabel[o.state]}</span>{#if o.stuck}<div class="text-xs muted">{o.stuck} job(s) can't be placed</div>{/if}</td>
            <td>{o.jobs_left} of {o.jobs}</td>
            <td>{#if can('farm') && o.jobs_left}<button class="btn sm" onclick={() => prioritise(o, o.state === 'late' ? 100 : 75)}>{o.state === 'late' ? 'Make urgent' : 'Prioritise'}</button>{/if}</td>
          </tr>
        {:else}<tr><td colspan="6" class="muted text-center py-4">No orders waiting on prints.</td></tr>{/each}
      </tbody>
    </table>
  </div>

  <div class="panel p-4 scroll-x">
    <div style="min-width: 720px">
      <div class="relative h-6 ml-28 text-xs muted">
        {#each ticks as t}<span class="absolute" style="left: {t.left}%; transform: translateX(-50%)">{t.label}</span>{/each}
      </div>
      {#each tl.lanes as l}
        <div class="flex items-center gap-2 py-1.5 border-t line">
          <div class="shrink-0 text-sm" style="width: 6.5rem">
            <div class="font-medium">{l.printer}</div>
            <div class="text-xs muted">{l.online ? (l.state || '') : 'offline'}</div>
          </div>
          <div class="relative flex-1 h-9 rounded" style="background: var(--panel-2)">
            {#each ticks as t}<div class="absolute top-0 bottom-0" style="left: {t.left}%; border-left: 1px dashed var(--line)"></div>{/each}
            {#each l.bars as b}
              <div class="absolute top-1 bottom-1 rounded text-[11px] text-white px-1 overflow-hidden whitespace-nowrap"
                style="left: {pos(b.start)}%; width: max(3px, {pos(b.end) - pos(b.start)}%); background: {colour(b)}; opacity: {b.estimated ? 1 : 0.6}"
                title={`#${b.job} ${b.name}\n${fmt(b.start)} to ${fmt(b.end)}${b.orders.length ? `\norder ${b.orders.join(', ')}` : ''}${b.estimated ? '' : '\nno time estimate: 2 h assumed'}`}>
                {b.orders.length ? `#${b.orders[0]} ` : ''}{b.name}
              </div>
            {/each}
          </div>
        </div>
      {/each}
    </div>
  </div>

  {#if tl.unscheduled.length}
    <div class="panel p-4 mt-4">
      <h2 class="font-semibold mb-2">Not on the timeline</h2>
      {#each tl.unscheduled as u}<div class="text-sm py-0.5">#{u.job} {u.name}{#if u.orders.length} <span class="muted">(order {u.orders.join(', ')})</span>{/if}: <span class="text-amber-600">{u.why}</span></div>{/each}
    </div>
  {/if}
{/if}
