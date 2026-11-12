<script>
  import Page from '$lib/Page.svelte';
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';

  let ov = $state(null);
  let designers = $state([]);
  let adding = $state(null);     // designer id we're adding a grant for
  let form = $state({});
  let editing = $state(null);
  let pickDesigner = $state('');

  const blank = () => ({ name: '', kind: 'membership', starts_on: '', expires_on: '', auto_renews: true, renew_url: '', covers_all: true,
    model_codes: '', sales_need_active: true, terms: '' });
  async function load() {
    [ov, designers] = await Promise.all([api.get('/api/licenses'), api.get('/api/designers/full')]);
  }
  onMount(load);
  const payload = (f) => ({ ...f, starts_on: f.starts_on || null, expires_on: f.expires_on || null, renew_url: f.renew_url || null,
    terms: f.terms || null, model_codes: typeof f.model_codes === 'string' ? f.model_codes.split(/[\s,]+/).filter(Boolean) : f.model_codes });
  async function save(did) {
    if (editing) await attempt(() => api.patch(`/api/grants/${editing}`, payload(form)), 'Saved');
    else await attempt(() => api.post(`/api/designers/${did}/grants`, payload(form)), 'License recorded');
    adding = null; editing = null; load();
  }
  function startAdd(did) { adding = did; editing = null; form = blank(); }
  function startEdit(did, g) {
    adding = did; editing = g.id;
    form = { ...g, starts_on: g.starts_on || '', expires_on: g.expires_on || '', renew_url: g.renew_url || '', terms: g.terms || '', model_codes: (g.model_codes || []).join(', ') };
  }
  async function remove(g) { if (confirm(`Remove the record of '${g.name}'?`)) { await api.del(`/api/grants/${g.id}`); load(); } }
  async function upload(g, e) {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append('file', f);
    await attempt(() => fetch(`/api/grants/${g.id}/document`, { method: 'POST', body: fd, credentials: 'same-origin' })
      .then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail || r.statusText); }), 'Document attached');
    load();
  }
  const chip = { active: 'ok', expiring: 'warn', expired: 'bad', not_started: 'warn', none: '' };
  const label = (g) => g.state === 'active' ? (g.expires_on ? `active until ${g.expires_on}` : 'active, no end date')
    : g.state === 'expiring' ? `ends in ${g.days_left} day${g.days_left === 1 ? '' : 's'}` : g.state === 'expired' ? `ended ${g.expires_on}` : 'starts later';
  const others = $derived(designers.filter((d) => !ov?.designers.some((x) => x.designer_id === d.id)));
</script>

<Page title="Licenses" description="Commercial licenses you hold to sell prints: memberships (valid while you pay), one-time purchases, or written permission. Models marked Commercial license for a designer can be pushed and stay live only while one of that designer's licenses is in force. You get an email 14 days before a membership ends and again when it has ended.">

{#if ov}
  {#if ov.commercial_without_designer}
    <div class="panel p-4 mb-4 text-sm" style="border-color:var(--warn)">{ov.commercial_without_designer} commercial model(s) have no designer set, so their license can't be tracked. Set the designer on each model's License tab.</div>
  {/if}
  <div class="space-y-4">
    {#each ov.designers as d (d.designer_id)}
      <div class="panel p-5">
        <div class="flex flex-wrap items-center gap-3">
          <div class="flex-1 min-w-0">
            <div class="font-semibold text-lg">{#if d.url}<a class="hover:underline" target="_blank" href={d.url}>{d.designer}</a>{:else}{d.designer}{/if}</div>
            <div class="text-sm muted">{d.models} commercial model(s) · {d.live} live on the store</div>
          </div>
          <span class="chip {chip[d.status]}">{d.status === 'none' ? 'No license recorded' : d.status === 'active' ? 'Covered' : d.status === 'expiring' ? 'Ending soon' : 'Ended'}</span>
          <button class="btn sm" onclick={() => startAdd(d.designer_id)}>Add license</button>
        </div>
        {#if d.status === 'none'}<p class="text-sm muted mt-2">Record the license (and its renewal date if it's a membership) so the hub can warn you before it lapses.</p>{/if}
        {#each d.grants as g (g.id)}
          <div class="mt-3 p-3 rounded-lg border line text-sm">
            <div class="flex flex-wrap items-center gap-2">
              <b>{g.name}</b><span class="chip">{g.kind}</span><span class="chip {chip[g.state]}">{label(g)}</span>
              {#if g.auto_renews}<span class="chip">auto-renews</span>{/if}
              {#if !g.covers_all}<span class="chip">only {g.model_codes.length} model(s)</span>{/if}
              {#if !g.sales_need_active}<span class="chip">right to sell continues after end date</span>{/if}
              <span class="ml-auto flex gap-1.5">
                {#if g.renew_url}<a class="btn sm" target="_blank" href={g.renew_url}>Renew</a>{/if}
                {#if g.document}<a class="btn sm" target="_blank" href={g.document}>Terms</a>{/if}
                <label class="btn sm cursor-pointer">Attach<input type="file" class="hidden" onchange={(e) => upload(g, e)} /></label>
                <button class="btn sm" onclick={() => startEdit(d.designer_id, g)}>Edit</button>
                <button class="btn sm danger" onclick={() => remove(g)}>Remove</button>
              </span>
            </div>
            {#if g.terms}<p class="muted mt-1 whitespace-pre-line">{g.terms}</p>{/if}
          </div>
        {/each}
        {#if adding === d.designer_id}
          <form class="mt-3 p-4 rounded-lg border line space-y-3 text-sm" onsubmit={(e) => { e.preventDefault(); save(d.designer_id); }}>
            <div class="grid sm:grid-cols-3 gap-3">
              <div class="sm:col-span-2"><label class="lbl" for="gn">Name</label><input id="gn" class="input" placeholder="Patreon commercial tier" bind:value={form.name} /></div>
              <div><label class="lbl" for="gk">Type</label><select id="gk" class="input" bind:value={form.kind}><option value="membership">Membership / subscription</option><option value="purchase">One-time purchase</option><option value="permission">Written permission</option></select></div>
            </div>
            <div class="grid sm:grid-cols-3 gap-3">
              <div><label class="lbl" for="gs">Started</label><input id="gs" type="date" class="input" bind:value={form.starts_on} /></div>
              <div><label class="lbl" for="ge">Ends / renews on</label><input id="ge" type="date" class="input" bind:value={form.expires_on} /></div>
              <div><label class="lbl" for="gu">Renewal link</label><input id="gu" class="input" placeholder="https://www.patreon.com/…" bind:value={form.renew_url} /></div>
            </div>
            <div class="flex flex-wrap gap-4">
              <label class="flex items-center gap-2"><input type="checkbox" bind:checked={form.auto_renews} /> Auto-renews</label>
              <label class="flex items-center gap-2"><input type="checkbox" bind:checked={form.sales_need_active} /> Selling prints requires it to be active</label>
              <label class="flex items-center gap-2"><input type="checkbox" bind:checked={form.covers_all} /> Covers all of this designer's models</label>
            </div>
            {#if !form.covers_all}<div><label class="lbl" for="gc">Model codes it covers</label><input id="gc" class="input" placeholder="M1185, M11247" bind:value={form.model_codes} /></div>{/if}
            <div><label class="lbl" for="gt">Terms (what it allows, limits such as max units)</label><textarea id="gt" class="input" rows="3" bind:value={form.terms}></textarea></div>
            <div class="flex gap-2"><button class="btn primary" disabled={!form.name}>{editing ? 'Save' : 'Record license'}</button><button type="button" class="btn" onclick={() => { adding = null; editing = null; }}>Cancel</button></div>
          </form>
        {/if}
      </div>
    {:else}<p class="muted">No commercial-license models yet.</p>{/each}
  </div>
  <div class="panel p-4 mt-5 flex flex-wrap items-end gap-2 text-sm">
    <div class="flex-1 min-w-[200px]"><label class="lbl" for="od">Record a license for another designer</label>
      <select id="od" class="input" bind:value={pickDesigner}><option value="">Choose a designer</option>{#each others as d}<option value={d.id}>{d.name}</option>{/each}</select></div>
    <button class="btn" disabled={!pickDesigner} onclick={() => { ov.designers = [...ov.designers, { designer_id: Number(pickDesigner), designer: others.find((x) => x.id === Number(pickDesigner)).name, models: 0, live: 0, grants: [], status: 'none' }]; startAdd(Number(pickDesigner)); pickDesigner = ''; }}>Add</button>
  </div>
{/if}
</Page>
