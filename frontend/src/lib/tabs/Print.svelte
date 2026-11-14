<script>
  import { onMount } from 'svelte';
  import { api, fmtTime, fmtMoney, ago } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  import { can } from '$lib/session.svelte.js';
  import { stateLabel, stateCls, ORIENT, ENGINE, KEYS, waitJob } from '$lib/slicing.js';
  let { m, onsaved } = $props();

  let sl = $state(null);
  let err = $state('');
  let open = $state({});          // profile_id -> advanced options open
  let opt = $state({});           // profile_id -> {orientation, process, walls, infill, supports, copies}
  let procs = $state({});         // profile_id -> process presets
  let busy = $state({});
  let printing = $state({});      // profile_id -> {qty, start} while its Print panel is open
  let filePick = $state({});      // profile_id -> sliced file chosen to print there
  let pickers = {};               // profile_id -> hidden file input for "Upload sliced file"
  let poll;

  const colorOf = (slot) => m.product?.filaments?.find((b) => b.slot === slot)?.color_hex || '#bbb';
  const extOf = (n) => (n.toLowerCase().endsWith('.gcode.3mf') ? '.gcode.3mf' : '.' + n.split('.').pop());
  const fileById = (id) => m.files_all.find((f) => f.id === id);
  const working = $derived(sl?.printers?.some((p) => p.state === 'queued' || p.state === 'slicing'));
  const slicedFiles = $derived(m.files_all.filter((f) => f.kind === '3mf_sliced' || f.kind === 'gcode'));
  const names = (ids) => (ids || []).map((id) => fileById(id)?.filename).filter(Boolean).join(', ');

  async function load() {
    try {
      sl = await api.get(`/api/models/${m.id}/slicing`);
      err = '';
      for (const p of sl.printers) {
        if (!opt[p.profile_id]) opt[p.profile_id] = { orientation: orientKey(p.orientation), process: '', walls: '', infill: '', supports: '', copies: 1 };
      }
    } catch (e) { err = e.message; }
  }
  onMount(() => {
    load();
    poll = setInterval(() => { if (working) { load(); } }, 3000);
    return () => clearInterval(poll);
  });

  function orientKey(o) {
    if (!o || !o.mode) return 'as_is';
    if (o.mode === 'face') return 'face:' + (o.normal || []).join(',');
    return o.mode;
  }
  function orientValue(k) {
    if (k.startsWith('face:')) return { mode: 'face', normal: k.slice(5).split(',').map(Number) };
    return { mode: k };
  }

  async function toggleAdvanced(p) {
    open[p.profile_id] = !open[p.profile_id];
    if (open[p.profile_id] && !procs[p.profile_id]) {
      const prof = (await api.get('/api/slicer/status')).profiles.find((x) => x.id === p.profile_id);
      try {
        procs[p.profile_id] = await api.get(`/api/slicer/presets/process?engine=${prof.engine}&machine=${encodeURIComponent(prof.machine_preset)}`);
      } catch { procs[p.profile_id] = []; }
    }
  }

  async function slice(p, approve = false) {
    const o = opt[p.profile_id];
    const keys = KEYS[p.engine] || KEYS.orca;
    const ov = {};
    if (o.walls) ov[keys.walls] = String(o.walls);
    if (o.infill) ov[keys.infill] = String(o.infill).endsWith('%') ? o.infill : `${o.infill}%`;
    if (o.supports) ov[keys.supports] = o.supports === 'yes' ? '1' : '0';
    busy[p.profile_id] = true;
    try {
      const r = await attempt(() => api.post(`/api/models/${m.id}/slice`, {
        profile_id: p.profile_id, process: o.process || null, process_overrides: Object.keys(ov).length ? ov : null,
        copies: Number(o.copies) || 1, orientation: orientValue(o.orientation), approve
      }));
      await load();
      const j = await waitJob(api, r.job);
      if (j?.state === 'done') {
        const x = j.result || {};
        toast(x.state === 'review' && !x.approved ? `Sliced for ${x.printer}: ${fmtTime(x.time_s)}, ${x.grams} g. Review it below.` : `Sliced and approved for ${x.printer}`);
      } else if (j?.state === 'failed') toast(`Slicing failed: ${(j.error || '').split('\n')[0]}`, 'bad', 12000);
      await load(); onsaved?.();
    } finally { busy[p.profile_id] = false; }
  }
  async function sliceAll() {
    const ids = sl.printers.map((p) => p.profile_id);
    await attempt(() => api.post(`/api/models/${m.id}/slice`, { profile_ids: ids }), `Queued for ${ids.length} printer(s)`);
    load();
  }
  function openPrint(p) {
    printing[p.profile_id] = printing[p.profile_id] ? null : { qty: 1, start: false };
  }
  async function printNow(p) {
    const o = printing[p.profile_id] || { qty: 1, start: false };
    const n = Math.max(1, Math.min(50, Math.round(Number(o.qty) || 1)));
    busy[p.profile_id] = true;
    try {
      const r = await attempt(() => api.post(`/api/models/${m.id}/print`, { profile_id: p.profile_id, quantity: n, start_now: !!o.start }));
      printing[p.profile_id] = null;
      if (r) {
        const jobs = `${r.jobs.length} job${r.jobs.length === 1 ? '' : 's'}`;
        toast(r.started?.length ? `Sent to ${r.printer}: ${jobs}. ${o.start ? 'Starting now.' : `It prints next on ${r.printer}.`}`
          : `Sent to ${r.printer}: ${jobs}. It starts when ${r.printer} is free.`, 'ok', 7000);
        for (const x of r.notes || []) toast(x, 'warn', 10000);
      }
    } finally { busy[p.profile_id] = false; }
  }
  async function useFile(p) {
    const fid = Number(filePick[p.profile_id]);
    if (!fid) return;
    const r = await attempt(() => api.post(`/api/models/${m.id}/printers/${p.profile_id}/use-file`, { file_id: fid, lock: true }));
    filePick[p.profile_id] = '';
    if (r) {
      toast(`${p.printer} now prints ${r.file} (locked)${r.production ? '. It is the production file.' : ''}`, 'ok', 6000);
      for (const w of r.warnings || []) toast(w, 'warn', 9000);
    }
    await load(); onsaved?.();
  }
  async function uploadFor(p, e) {
    const file = e.currentTarget.files?.[0];
    e.currentTarget.value = '';
    if (!file) return;
    busy[p.profile_id] = true;
    try {
      const r = await attempt(() => api.upload(`/api/models/${m.id}/printers/${p.profile_id}/upload`, [], { file, lock: 'true' }));
      if (r) {
        toast(`${p.printer} now prints ${r.file} (locked)${r.production ? '. It is the production file.' : ''}`, 'ok', 6000);
        for (const w of r.warnings || []) toast(w, 'warn', 9000);
      }
      await load(); onsaved?.();
    } finally { busy[p.profile_id] = false; }
  }
  async function setLock(p, v) {
    await attempt(() => api.post(`/api/slice-results/${p.id}/lock`, { locked: v }),
      v ? `Locked: ${p.printer} keeps this file until you change it` : `Unlocked: ${p.printer} is re-sliced when its inputs change`);
    await load();
  }
  async function removeSlice(p) {
    const what = p.source === 'file' ? 'The file stays in the model\'s Files.' : 'Its sliced files are deleted.';
    if (!confirm(`Remove the slice for ${p.printer}? ${what}${p.is_default && p.has_approved ? ' Store orders will have no production file until you slice or choose one.' : ''}`)) return;
    await attempt(() => api.del(`/api/slice-results/${p.id}`), `Removed the slice for ${p.printer}`);
    printing[p.profile_id] = null;
    await load(); onsaved?.();
  }
  async function approve(p) {
    const r = await attempt(() => api.post(`/api/slice-results/${p.id}/approve`), `Approved for ${p.printer}`);
    if (r?.production) toast('It is now the production file for store orders');
    await load(); onsaved?.();
  }
  async function reject(p) {
    if (!confirm(`Throw away this slice for ${p.printer}?${p.has_approved ? ' The approved version stays in use.' : ''}`)) return;
    await attempt(() => api.post(`/api/slice-results/${p.id}/reject`), 'Discarded');
    await load(); onsaved?.();
  }
  async function record(p, outcome) {
    const note = outcome === 'failed' ? prompt('What went wrong? (optional)') : '';
    if (note === null) return;
    await attempt(() => api.post(`/api/slice-results/${p.id}/prints`, { outcome, note: note || null }), outcome === 'ok' ? 'Recorded a good print' : 'Recorded a failed print');
    load();
  }
  async function unrecord(x) {
    await attempt(() => api.del(`/api/prints/${x.id}`), 'Removed');
    load();
  }
  async function inspect() {
    const r = await attempt(() => api.post(`/api/models/${m.id}/inspect`), 'Inspecting the model…');
    await waitJob(api, r.job);
    load();
  }
</script>

{#if err}
  <div class="panel p-5 text-sm">{err}</div>
{:else if sl}
  <div class="panel p-4 mb-4 flex flex-wrap items-center gap-3 text-sm">
    <span><b>{sl.colors_needed}</b> colour{sl.colors_needed === 1 ? '' : 's'}</span>
    {#if sl.size_mm}<span class="muted">{sl.size_mm.map((v) => Math.round(v)).join(' x ')} mm</span>{/if}
    <span class="muted">Slices {sl.sources.map((s) => s.filename).join(', ') || 'no model file yet'}</span>
    <span class="ml-auto flex gap-2">
      {#if can('slice')}
        <button class="btn sm" onclick={inspect} title="Count painted colours and find the flat faces it can rest on">{sl.inspected_at ? 'Inspect again' : 'Inspect model'}</button>
        {#if sl.printers.length > 1}<button class="btn sm" onclick={sliceAll}>Slice for every printer</button>{/if}
      {/if}
    </span>
  </div>

  {#if !sl.printers.length}
    <div class="panel p-5 text-sm">No printers yet. Add one on the <a class="underline" href="/slicing?tab=printers">Printers</a> tab of Slicing.</div>
  {/if}

  <div class="grid xl:grid-cols-2 gap-4">
    {#each sl.printers as p (p.profile_id)}
      {@const st = p.stats || {}}
      <div class="panel p-5 text-sm">
        <div class="flex flex-wrap items-center gap-2">
          <b class="text-base">{p.printer}</b>
          {#if p.is_default}<span class="chip ok" title="Its approved file is the one store orders print">default</span>{/if}
          <span class="muted text-xs">{ENGINE[p.engine] || p.engine}{p.engine_version ? ` ${p.engine_version}` : ''} · {p.tools} tool{p.tools === 1 ? '' : 's'}</span>
          <span class="chip {stateCls(p.state)} ml-auto">{stateLabel(p.state)}</span>
          {#if p.locked}<span class="chip" title="Kept exactly as approved: re-slicing and auto-approve won't replace it">locked</span>{/if}
          {#if p.source === 'file'}<span class="chip" title="A sliced file chosen by hand">chosen file</span>{/if}
          {#if p.has_approved && can('farm')}
            <button class="btn primary sm" onclick={() => openPrint(p)} disabled={!sl.can_print_now}
              title={sl.can_print_now ? `Print the approved file on ${p.printer}` : 'Connect a print farm on the Connections page to print from here'}>Print</button>
          {/if}
        </div>

        {#if printing[p.profile_id]}
          <div class="mt-3 p-3 rounded-lg border line flex flex-wrap items-end gap-3">
            <div><label class="lbl" for="q{p.profile_id}">How many</label>
              <input id="q{p.profile_id}" class="input w-24" type="number" min="1" max="50" bind:value={printing[p.profile_id].qty} /></div>
            <label class="flex items-center gap-2 text-sm pb-2"><input type="checkbox" bind:checked={printing[p.profile_id].start} /> The bed on {p.printer} is clear: start now</label>
            <span class="ml-auto flex gap-2">
              <button class="btn" onclick={() => (printing[p.profile_id] = null)}>Cancel</button>
              <button class="btn primary" onclick={() => printNow(p)} disabled={busy[p.profile_id]}>Send to {p.printer}</button>
            </span>
            <div class="w-full muted text-xs">Prints {names(p.approved_file_ids) || 'the approved file'} on {p.printer}, ahead of the rest of the queue.</div>
          </div>
        {/if}

        {#if p.previews?.length}
          <div class="flex gap-2 mt-3 scroll-x">
            {#each p.previews as src}<img {src} alt="Plate preview for {p.printer}" class="h-40 rounded-lg border line bg-ah-panel" loading="lazy" />{/each}
          </div>
        {/if}

        {#if st.time_s || st.grams}
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-3">
            <div><div class="muted text-xs">Print time</div><b>{fmtTime(st.time_s)}</b></div>
            <div><div class="muted text-xs">Per unit</div><b>{st.per_unit_g ?? st.grams} g</b></div>
            <div><div class="muted text-xs">Plates / copies</div><b>{st.plates || 1} / {st.copies || 1}</b></div>
            <div><div class="muted text-xs">Layer</div><b>{st.layer_height || (st.process || '').match(/([\d.]+)mm/)?.[1] || '?'} mm</b></div>
          </div>
          {#if st.tools?.length}
            <div class="flex flex-wrap gap-1.5 mt-2">
              {#each st.tools as t}
                <span class="chip" title={t.preset}><span class="swatch" style="background:{t.color_hex || colorOf(t.slot)}"></span>T{t.tool}: {t.per_unit_g ?? t.grams} g</span>
              {/each}
              {#if st.tower_g}<span class="chip" title="Prime / wipe tower">tower {st.tower_g} g</span>{/if}
            </div>
          {/if}
          {#if st.process}<div class="muted text-xs mt-1">{st.process}</div>{/if}
          {#if p.cost}
            <div class="mt-2 text-sm" title={`Filament from ${p.cost.prices.join(', ')}; printer time at ${fmtMoney(p.cost.rate)}/h`}>
              Cost per unit <b>{fmtMoney(p.cost.total)}</b> <span class="muted text-xs">filament {fmtMoney(p.cost.material)} + printer time {fmtMoney(p.cost.machine)}</span>
              {#if p.candidate_differs && p.approved_cost}<span class="muted text-xs"> · approved: {fmtMoney(p.approved_cost.total)}</span>{/if}
            </div>
          {/if}
        {/if}

        {#if p.prints && (p.has_approved || p.prints.recent.length)}
          <div class="mt-3 border-t line pt-2">
            <div class="flex flex-wrap items-center gap-2 text-xs">
              <b class="text-sm">Real prints</b>
              {#if p.prints.verified}<span class="chip ok" title="Printed successfully at least once in this version">verified</span>{/if}
              <span class="muted">{p.prints.ok} ok · {p.prints.failed} failed{p.prints.avg_duration_s ? ` · took ${fmtTime(p.prints.avg_duration_s)} on average (estimate ${fmtTime(st.time_s)})` : ''}</span>
              {#if can('slice') && p.has_approved}
                <span class="ml-auto flex gap-1.5">
                  <button class="btn sm" onclick={() => record(p, 'ok')}>Printed fine</button>
                  <button class="btn sm danger" onclick={() => record(p, 'failed')}>Print failed</button>
                </span>
              {/if}
            </div>
            {#each p.prints.recent.slice(0, 4) as x}
              <div class="text-xs mt-1 {x.current ? '' : 'muted'}">
                <span class="chip {x.outcome === 'ok' ? 'ok' : x.outcome === 'failed' ? 'bad' : ''}">{x.outcome}</span>
                {ago(x.at)}{x.printer ? ` on ${x.printer}` : ''}{x.duration_s ? `, ${fmtTime(x.duration_s)}` : ''}{x.source === 'farm' ? ' (the farm)' : x.by ? ` (${x.by})` : ''}{x.note ? `: ${x.note}` : ''}{x.current ? '' : ' · older version'}
                {#if can('slice') && x.source === 'manual'}<button class="underline muted ml-1" onclick={() => unrecord(x)}>remove</button>{/if}
              </div>
            {/each}
          </div>
        {/if}

        {#if p.candidate_differs && (p.state === 'review' || p.state === 'stale')}
          <div class="mt-3 text-xs" style="color:var(--warn)">The approved slice stays in use until you approve this one{p.approved_stats?.time_s ? ` (approved: ${fmtTime(p.approved_stats.time_s)}, ${p.approved_stats.per_unit_g} g)` : ''}.</div>
        {/if}
        {#if p.error}<div class="mt-3 text-xs break-words" style="color:var(--bad)">{p.error}</div>{/if}
        {#each p.problems || [] as pr}<div class="mt-2 text-xs" style="color:var(--warn)">{pr.msg}</div>{/each}
        {#each st.warnings || [] as w}<div class="mt-1 text-xs muted">{w}</div>{/each}

        {#if can('slice')}
          <div class="flex flex-wrap items-end gap-2 mt-4">
            <div class="min-w-[12rem]"><label class="lbl" for="or{p.profile_id}">On the bed</label>
              <select id="or{p.profile_id}" class="input" bind:value={opt[p.profile_id].orientation}>
                {#each ORIENT as [k, l]}<option value={k} disabled={p.engine !== 'orca' && k !== 'as_is'}>{l}</option>{/each}
                {#if p.engine === 'orca'}
                  {#each sl.faces as f, i}<option value={'face:' + f.normal.join(',')}>Face {i + 1} down ({Math.round(f.area_mm2)} mm²)</option>{/each}
                {/if}
              </select></div>
            <button class="btn primary" onclick={() => slice(p)} disabled={busy[p.profile_id] || p.state === 'queued' || p.state === 'slicing'}>
              {p.state === 'none' ? 'Slice' : 'Re-slice'}</button>
            {#if p.state === 'review' || (p.state === 'stale' && p.candidate_differs)}
              <button class="btn" onclick={() => approve(p)}>Approve</button>
              <button class="btn danger" onclick={() => reject(p)}>Discard</button>
            {/if}
            {#if p.has_approved}
              <button class="btn sm" onclick={() => setLock(p, !p.locked)}
                title={p.locked ? 'Let re-slicing replace it again when the model or printer changes' : 'Keep this file exactly as approved'}>{p.locked ? 'Unlock' : 'Lock'}</button>
            {/if}
            {#if p.state !== 'none' && p.id}<button class="btn sm danger" onclick={() => removeSlice(p)}>Remove</button>{/if}
            <button class="btn sm ml-auto" onclick={() => toggleAdvanced(p)}>{open[p.profile_id] ? 'Fewer options' : 'More options'}</button>
          </div>
          <div class="flex flex-wrap items-end gap-2 mt-3">
            <div class="min-w-[14rem] flex-1"><label class="lbl" for="uf{p.profile_id}">Or print a file sliced elsewhere</label>
              <select id="uf{p.profile_id}" class="input" bind:value={filePick[p.profile_id]} onchange={() => useFile(p)}>
                <option value="">{slicedFiles.length ? 'Choose one of the model\'s sliced files…' : 'No sliced files yet: upload one'}</option>
                {#each slicedFiles as f}<option value={f.id}>{f.filename}{f.printer ? ` (${f.printer})` : ''}{(p.approved_file_ids || []).includes(f.id) ? ' (approved)' : ''}</option>{/each}
              </select></div>
            <button class="btn" onclick={() => pickers[p.profile_id]?.click()} disabled={busy[p.profile_id]}>Upload sliced file…</button>
            <input type="file" class="hidden" accept=".3mf,.gcode" bind:this={pickers[p.profile_id]} onchange={(e) => uploadFor(p, e)} />
          </div>
          {#if open[p.profile_id]}
            <div class="grid sm:grid-cols-3 gap-2 mt-3">
              <div class="sm:col-span-3"><label class="lbl" for="pr{p.profile_id}">Process</label>
                <select id="pr{p.profile_id}" class="input" bind:value={opt[p.profile_id].process}>
                  <option value="">Automatic (designer's layer height or the printer's default)</option>
                  {#each procs[p.profile_id] || [] as pr}<option value={pr.name}>{pr.name}</option>{/each}
                </select></div>
              <div><label class="lbl" for="w{p.profile_id}">Walls</label><input id="w{p.profile_id}" class="input" type="number" min="1" max="12" bind:value={opt[p.profile_id].walls} placeholder="preset" /></div>
              <div><label class="lbl" for="i{p.profile_id}">Infill %</label><input id="i{p.profile_id}" class="input" bind:value={opt[p.profile_id].infill} placeholder="preset" /></div>
              <div><label class="lbl" for="s{p.profile_id}">Supports</label><select id="s{p.profile_id}" class="input" bind:value={opt[p.profile_id].supports}><option value="">preset</option><option value="yes">yes</option><option value="no">no</option></select></div>
              <div><label class="lbl" for="c{p.profile_id}">Copies on the plate</label><input id="c{p.profile_id}" class="input" type="number" min="1" max="50" bind:value={opt[p.profile_id].copies} /></div>
            </div>
          {/if}
        {/if}

        {#if p.file_ids?.length}
          <div class="flex flex-wrap gap-2 mt-3 text-xs">
            {#each p.file_ids as fid}
              {@const f = fileById(fid)}
              {#if f}<a class="btn sm" href={f.download} title={f.filename}>Download {extOf(f.filename)}{p.file_ids.length > 1 ? ` (${p.file_ids.indexOf(fid) + 1})` : ''}</a>{/if}
            {/each}
          </div>
        {/if}
        {#if p.sliced_at}<div class="muted text-xs mt-2">Sliced {ago(p.sliced_at)}{p.requested_by ? ` by ${p.requested_by}` : ''}{p.approved_at ? ` · approved ${ago(p.approved_at)}${p.approved_by ? ` by ${p.approved_by}` : ''}` : ''}</div>{/if}
      </div>
    {/each}
  </div>
{:else}
  <div class="panel p-5 text-sm muted">Loading…</div>
{/if}
