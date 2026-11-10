<script>
  import { onMount } from 'svelte';
  import { api, ago } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  import { can } from '$lib/session.svelte.js';

  let m = $state(null);
  let parts = $state([]);
  let newTask = $state(null);
  let plan = $state(null);       // the plan being added or edited
  let partQ = $state('');
  async function load() { m = await api.get('/api/maintenance'); }
  async function loadParts() { if (!parts.length) parts = await api.get('/api/inventory/parts').catch(() => []); }
  onMount(load);
  const printers = $derived(m ? Object.keys(m.hours) : []);
  const partHits = $derived(partQ.length < 2 ? [] : parts.filter((p) => (p.name + ' ' + (p.ipn || '')).toLowerCase().includes(partQ.toLowerCase())).slice(0, 8));

  function addPart(target, p) {
    if (!target.parts.some((x) => x.part_id === p.id)) target.parts = [...target.parts, { part_id: p.id, qty: 1, name: p.name }];
    partQ = '';
  }
  function openTask() { loadParts(); newTask = { printer: printers[0] || '', title: '', parts: [], notes: '', out_of_service: false }; }
  async function service(t, on) {
    await attempt(() => api.post(`/api/maintenance/tasks/${t.id}/service`, { out_of_service: on }), on ? `${t.printer} is out of service in Watchtower` : `${t.printer} is back in service`);
    load();
  }
  async function syncWt() {
    const r = await attempt(() => api.post('/api/maintenance/sync'));
    const w = r.watchtower || {};
    toast(w.error ? `Watchtower: ${w.error}` : `Watchtower: ${w.schedules ?? 0} schedules${w.added ? `, ${w.added} new` : ''}${w.closed_by_watchtower ? `, ${w.closed_by_watchtower} done there` : ''}; ${r.opened.length} task(s) opened`, w.error ? 'bad' : 'ok', 7000);
    load();
  }
  async function saveTask() {
    await attempt(() => api.post('/api/maintenance/tasks', newTask), 'Task added');
    newTask = null; load();
  }
  function openPlan(p) {
    loadParts();
    plan = p ? { id: p.id, printer: p.printer, title: p.title, every_hours: p.every_hours, every_days: p.every_days, parts: [...p.parts], notes: p.notes || '', active: p.active, watchtower: p.watchtower }
             : { printer: printers[0] || '', title: '', every_hours: 100, every_days: null, parts: [], notes: '', active: true };
  }
  async function savePlan() {
    const body = { ...plan, every_hours: plan.every_hours || null, every_days: plan.every_days || null };
    if (plan.id) await attempt(() => api.put(`/api/maintenance/plans/${plan.id}`, body), 'Plan saved');
    else await attempt(() => api.post('/api/maintenance/plans', body), 'Plan added');
    plan = null; load();
  }
  async function delPlan(p) {
    if (!confirm(`Remove the plan "${p.title}" on ${p.printer}? Its past tasks stay in the history.`)) return;
    await attempt(() => api.del(`/api/maintenance/plans/${p.id}`), 'Plan removed'); load();
  }
  async function done(t) {
    const list = t.parts.map((p) => `${p.qty} x ${p.name || p.part_id}`).join(', ');
    if (t.parts.length && !confirm(`Mark done and take ${list} out of InvenTree stock?`)) return;
    const r = await attempt(() => api.post(`/api/maintenance/tasks/${t.id}/done`, {}));
    toast(r.parts_error ? `Done. Parts: ${r.parts_error}` : 'Done', r.parts_error ? 'bad' : 'ok', 7000); load();
  }
  async function dismiss(t) {
    if (!confirm('Dismiss this task without doing it? No parts are taken.')) return;
    await attempt(() => api.post(`/api/maintenance/tasks/${t.id}/dismiss`)); load();
  }
  const srcCls = { alarm: 'bad', plan: 'warn', manual: '' };
</script>

{#snippet partEditor(target)}
  <div class="flex flex-col gap-1">
    {#each target.parts as p, i}
      <div class="flex items-center gap-2 text-sm">
        <input class="input" style="width: 5rem" type="number" min="0.01" step="any" bind:value={p.qty} />
        <span class="flex-1">{p.name || `part ${p.part_id}`}</span>
        <button class="btn sm" onclick={() => (target.parts = target.parts.filter((_, j) => j !== i))}>Remove</button>
      </div>
    {/each}
    <input class="input" placeholder="Add a spare part from InvenTree (type to search)" bind:value={partQ} />
    {#each partHits as p}<button class="text-left text-sm hover:underline" onclick={() => addPart(target, p)}>{p.name} <span class="muted">{p.ipn || ''} ({p.in_stock} in stock)</span></button>{/each}
  </div>
{/snippet}

{#if !m}<p class="muted">Loading...</p>{:else}
  <div class="flex flex-wrap items-center gap-2 mb-3">
    <h2 class="font-semibold mr-auto">Open work orders</h2>
    {#each m.down as d}<span class="chip bad">{d} out of service</span>{/each}
    {#if can('farm') && m.linked}<button class="btn sm" onclick={syncWt}>Sync with Watchtower</button>{/if}
    {#if can('farm')}<button class="btn sm" onclick={openTask}>Add a task</button>{/if}
  </div>
  {#if newTask}
    <div class="panel p-4 mb-4 flex flex-col gap-2">
      <div class="flex gap-2">
        <input class="input" style="width: 10rem" list="farm-printers" placeholder="Printer" bind:value={newTask.printer} />
        <input class="input" placeholder="What needs doing (e.g. Swap the 0.4 nozzle)" bind:value={newTask.title} />
      </div>
      {@render partEditor(newTask)}
      <input class="input" placeholder="Notes" bind:value={newTask.notes} />
      {#if m.linked}<label class="text-sm flex items-center gap-2"><input type="checkbox" bind:checked={newTask.out_of_service} /> Take the printer out of service until this is done (Watchtower sends it no new jobs)</label>{/if}
      <div class="flex gap-2"><button class="btn primary" onclick={saveTask}>Add task</button><button class="btn" onclick={() => (newTask = null)}>Cancel</button></div>
    </div>
  {/if}
  <datalist id="farm-printers">{#each printers as p}<option value={p}></option>{/each}</datalist>
  <div class="grid gap-3 mb-6" style="grid-template-columns: repeat(auto-fill, minmax(280px, 1fr))">
    {#each m.open as t (t.id)}
      <div class="panel p-4 flex flex-col gap-2">
        <div class="flex items-start gap-2"><div class="font-semibold mr-auto">{t.printer}</div>{#if t.out_of_service}<span class="chip bad">out of service</span>{/if}<span class="chip {srcCls[t.source]}">{t.source}</span></div>
        <div>{t.title}</div>
        {#if t.parts.length}<div class="text-xs muted">Parts: {t.parts.map((p) => `${p.qty} x ${p.name || p.part_id}`).join(', ')}</div>{/if}
        {#if t.notes}<div class="text-xs muted">{t.notes}</div>{/if}
        <div class="text-xs muted">opened {ago(t.created_at)}</div>
        {#if t.wt_note}<div class="text-xs text-amber-600">{t.wt_note}</div>{/if}
        {#if can('farm')}<div class="flex flex-wrap gap-2"><button class="btn primary sm" onclick={() => done(t)}>Done</button><button class="btn sm" onclick={() => dismiss(t)}>Dismiss</button>
          {#if m.linked}<button class="btn sm" onclick={() => service(t, !t.out_of_service)}>{t.out_of_service ? 'Back in service' : 'Out of service'}</button>{/if}</div>{/if}
      </div>
    {:else}<div class="panel p-6 muted text-center" style="grid-column: 1 / -1">Nothing waiting.</div>{/each}
  </div>

  <div class="flex flex-wrap items-center gap-2 mb-2">
    <h2 class="font-semibold mr-auto">Recurring plans</h2>
    {#if can('farm')}<button class="btn sm" onclick={() => openPlan(null)}>Add a plan</button>{/if}
  </div>
  {#if plan}
    <div class="panel p-4 mb-4 flex flex-col gap-2">
      <div class="flex flex-wrap gap-2">
        <input class="input" style="width: 10rem" list="farm-printers" placeholder="Printer" disabled={plan.watchtower} bind:value={plan.printer} />
        <input class="input" style="flex: 1; min-width: 14rem" placeholder="Task (e.g. Clean and lube the rods)" disabled={plan.watchtower} bind:value={plan.title} />
      </div>
      <div class="flex flex-wrap gap-3 text-sm items-center">
        <label>Every <input class="input inline-block" style="width: 6rem" type="number" min="0" disabled={plan.watchtower} bind:value={plan.every_hours} /> print hours</label>
        <label>and/or every <input class="input inline-block" style="width: 6rem" type="number" min="0" disabled={plan.watchtower} bind:value={plan.every_days} /> days</label>
        {#if plan.watchtower}<span class="text-xs muted">From Watchtower's Maintenance tab: change the task and interval there. Spare parts, notes and on/off are kept here.</span>{/if}
        <label class="flex items-center gap-2"><input type="checkbox" bind:checked={plan.active} /> Active</label>
      </div>
      {@render partEditor(plan)}
      <input class="input" placeholder="Notes or a how-to link" bind:value={plan.notes} />
      <div class="flex gap-2"><button class="btn primary" onclick={savePlan}>Save plan</button><button class="btn" onclick={() => (plan = null)}>Cancel</button></div>
    </div>
  {/if}
  <div class="panel p-4 scroll-x mb-6">
    <table class="grid">
      <thead><tr><th>Printer</th><th>Task</th><th>Every</th><th>Since last</th><th>Parts</th><th></th></tr></thead>
      <tbody>
        {#each m.plans as p}
          <tr class={p.active ? '' : 'muted'}>
            <td>{p.printer}</td><td>{p.title}{#if p.watchtower} <span class="chip" title="Linked with Watchtower's Maintenance tab">Watchtower</span>{/if}</td>
            <td class="text-sm">{[p.every_hours && `${p.every_hours} print h`, p.every_days && `${p.every_days} days`].filter(Boolean).join(' or ')}</td>
            <td class="text-sm">{[p.hours_since != null && `${p.hours_since} h`, p.days_since != null && `${p.days_since} days`].filter(Boolean).join(', ') || 'never done'} {#if p.due}<span class="chip warn">due</span>{/if}</td>
            <td class="text-xs">{p.parts.map((x) => `${x.qty} x ${x.name || x.part_id}`).join(', ')}</td>
            <td>{#if can('farm')}<button class="btn sm" onclick={() => openPlan(p)}>Edit</button> {#if !p.watchtower}<button class="btn sm danger" onclick={() => delPlan(p)}>Remove</button>{/if}{/if}</td>
          </tr>
        {:else}<tr><td colspan="6" class="muted text-center py-4">No plans yet. A plan opens a task every so many print hours or days.</td></tr>{/each}
      </tbody>
    </table>
    <p class="text-xs muted mt-2">Plans marked Watchtower are the schedules on Watchtower's Maintenance tab: finishing one here completes it there, and one completed there closes the task here. Other tasks go into Watchtower's maintenance log. Print hours are Watchtower's count when it has one, else the jobs the hub has seen Watchtower finish. Last 30 days: {Object.entries(m.hours).map(([k, v]) => `${k} ${v} h`).join(', ') || 'none yet'}.</p>
  </div>

  {#if m.recent.length}
    <h2 class="font-semibold mb-2">History</h2>
    <div class="panel p-4 scroll-x">
      <table class="grid">
        <thead><tr><th>When</th><th>Printer</th><th>Task</th><th>Result</th><th>Parts used</th><th>By</th></tr></thead>
        <tbody>
          {#each m.recent as t}
            <tr><td class="text-xs muted">{ago(t.done_at)}</td><td>{t.printer}</td><td>{t.title}</td>
              <td><span class="chip {t.state === 'done' ? 'ok' : ''}">{t.state}</span></td>
              <td class="text-xs">{t.parts_used.map((p) => `${p.qty} x ${p.name || p.part_id}`).join(', ')}{#if t.parts_error}<div class="text-red-600">{t.parts_error}</div>{/if}</td>
              <td class="text-xs muted">{t.done_by}{#if t.wt_note}<div>{t.wt_note}</div>{/if}</td></tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
{/if}
