<script>
  import ToolHeads from '$lib/ToolHeads.svelte';
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  import { can } from '$lib/session.svelte.js';
  import { ENGINE, MATERIALS } from '$lib/slicing.js';

  let st = $state(null);
  let found = $state([]);           // printers the team's farm software reports (hints only)
  let edit = $state(null);          // printer being set up or edited
  let step = $state(1);
  let q = $state('');
  let machines = $state([]);
  let searching = $state(false);
  let entry = $state(null);         // catalog summary of the chosen preset
  let procs = $state([]);
  let fils = $state([]);
  let ovText = $state('');
  let importName = $state('');
  let pendingIni = $state(null);    // a PrusaSlicer .ini waiting for an OrcaSlicer base printer
  let conv = $state(null);          // result of the last .ini conversion
  let keepGcode = $state(false);    // carry the PrusaSlicer G-code over (best effort)
  let t;

  const STEPS = ['Slicer', 'Printer preset', 'Tools', 'Details'];

  async function load() {
    st = await api.get('/api/slicer/status');
    try { found = await api.get('/api/slicer/discover'); } catch { found = []; }
  }
  onMount(load);

  function blank() {
    const engines = Object.keys(st?.engines || {});
    return { name: '', engine: engines[0] || 'orca', machine_preset: '', machine_config: null, process_preset: '', tools: [],
             filament_presets: {}, process_overrides: {}, machine_overrides: {}, use_model_settings: true, active: true,
             is_default: !st?.profiles?.length, dispatch: 'none', wt_printer: '', hourly_rate: null, bed_type: '', filament_slots: null, notes: '' };
  }
  async function start(p = null, hint = null) {
    edit = p ? { ...JSON.parse(JSON.stringify(p)), bed_type: p.bed_type || '' } : blank();
    edit.process_preset = edit.process_preset || ''; edit.wt_printer = edit.wt_printer || '';
    ovText = Object.entries(edit.process_overrides || {}).map(([k, v]) => `${k} = ${v}`).join('\n');
    importName = ''; pendingIni = null; conv = null; entry = null; machines = []; procs = []; fils = [];
    q = hint?.model || '';
    if (hint) { edit.name = hint.name || ''; if (hint.source === 'legacy_farm') { edit.dispatch = 'legacy_farm'; edit.wt_printer = hint.name; } }
    step = p ? 4 : 1;
    if (p) await loadPresets();
    if (q) search();
  }

  function search() {
    clearTimeout(t);
    t = setTimeout(async () => {
      searching = true;
      try { machines = await api.get(`/api/slicer/machines?engine=${edit.engine}&q=${encodeURIComponent(q)}`); }
      catch (e) { machines = []; toast(e.message, 'bad'); }
      finally { searching = false; }
    }, 250);
  }
  async function pick(name) {
    edit.machine_preset = name;
    if (pendingIni && edit.engine === 'orca') await convert();
    try {
      const d = await api.get(`/api/slicer/machine-defaults?engine=${edit.engine}&machine=${encodeURIComponent(name)}`);
      entry = d.entry;
      if (!edit.id || !edit.tools?.length || edit.tools.length !== d.tools.length) edit.tools = d.tools;
      if (conv?.tools && conv.tools !== edit.tools.length) {
        const t0 = edit.tools[0] || d.tools[0];
        edit.tools = Array.from({ length: conv.tools }, (_, i) => ({ ...t0, slot: i + 1, material: null, color: null, preset: null }));
      }
      if (!edit.name) edit.name = (d.entry.model || name).replace(/\s*\d\.\d+\s*(mm\s*)?nozzle$/i, '').slice(0, 80);
    } catch (e) { toast(e.message, 'bad'); }
    await loadPresets();
    step = 3;
  }
  async function convert() {
    conv = await attempt(() => api.post('/api/slicer/convert-ini', { ini: pendingIni, base: edit.machine_preset, keep_gcode: keepGcode }));
    if (conv) edit.machine_config = conv.config;
  }
  async function importFile(ev) {
    const f = ev.target.files?.[0];
    if (!f) return;
    if (f.size > 500000) { toast('That file is too large for a printer preset', 'bad'); return; }
    const text = await f.text();
    importName = f.name;
    let base = null;
    if (edit.engine === 'orca' && /\.ini$/i.test(f.name)) {
      pendingIni = text; edit.machine_config = null;
      // search by the preset's own name without the nozzle ("Prusa CORE One INDX 8T HF0.4 nozzle" -> "CORE One INDX 8T")
      const id = text.match(/^\s*printer_settings_id\s*=\s*(.+?)\s*$/m)?.[1] || text.match(/^\s*printer_model\s*=\s*(.+?)\s*$/m)?.[1]?.replaceAll('_', ' ') || '';
      q = id.replace(/^(Original\s+)?Prusa\s+/i, '').replace(/\s*(HF)?\s*\d+(\.\d+)?\s*(mm\s*)?nozzle\s*$/i, '').trim(); search();
      toast('PrusaSlicer preset loaded. Pick the OrcaSlicer printer it is closest to; the hub converts it on top of that one.');
      return;
    }
    edit.machine_config = text;
    if (edit.engine === 'orca') { try { const j = JSON.parse(edit.machine_config); base = j.inherits || j.name; } catch { toast('An OrcaSlicer preset is a JSON file (or a PrusaSlicer .ini to convert)', 'bad'); edit.machine_config = null; return; } }
    else { const m = edit.machine_config.match(/^\s*inherits\s*=\s*(.+?)\s*$/m); base = m ? m[1].split(';')[0] : null; }
    if (base) { q = base; search(); toast(`Builds on ${base}. Pick it below (or the closest match) so the hub knows its tools and presets.`); }
    else toast('Imported. Pick the closest catalog printer below as its base.');
  }
  async function loadPresets() {
    procs = []; fils = [];
    if (!edit?.machine_preset) return;
    const qs = `engine=${edit.engine}&machine=${encodeURIComponent(edit.machine_preset)}`;
    try { procs = await api.get(`/api/slicer/presets/process?${qs}`); } catch { procs = []; }
    try { fils = await api.get(`/api/slicer/presets/filament?${qs}${edit.process_preset ? `&process=${encodeURIComponent(edit.process_preset)}` : ''}`); } catch { fils = []; }
  }
  const filsFor = (mat) => fils.filter((f) => !mat || (f.filament_type || '').toUpperCase() === mat);

  function addTool() { edit.tools = [...edit.tools, { slot: edit.tools.length + 1, nozzle: edit.tools[0]?.nozzle || 0.4, flow: edit.tools[0]?.flow || 'standard', material: null, color: null, preset: null }]; }
  function dropTool(i) { edit.tools = edit.tools.filter((_, k) => k !== i).map((x, k) => ({ ...x, slot: k + 1 })); }
  function fillAll(field, v) { edit.tools = edit.tools.map((x) => ({ ...x, [field]: v })); }

  function hasOv(k, v) { return parseOv(ovText)[k] === v; }
  function setOv(pairs, on) {
    const o = parseOv(ovText);
    for (const [k, v] of Object.entries(pairs)) { if (on) o[k] = v; else delete o[k]; }
    ovText = Object.entries(o).map(([k, v]) => `${k} = ${v}`).join('\n');
  }
  function parseOv(text) {
    const o = {};
    for (const line of text.split('\n')) {
      const m = line.match(/^\s*([a-z0-9_]+)\s*[=:]\s*(.+?)\s*$/i);
      if (m) o[m[1]] = m[2];
    }
    return o;
  }
  async function save() {
    const body = { ...edit, process_overrides: parseOv(ovText) };
    for (const k of Object.keys(body.filament_presets || {})) if (!body.filament_presets[k]) delete body.filament_presets[k];
    if (!body.machine_config) delete body.machine_config;
    if (body.hourly_rate === '' || body.hourly_rate === undefined) body.hourly_rate = null;
    if (body.filament_slots === '' || body.filament_slots === undefined) body.filament_slots = null;
    else if (body.filament_slots !== null) body.filament_slots = Number(body.filament_slots);
    else if (body.hourly_rate !== null) body.hourly_rate = Number(body.hourly_rate);
    delete body.imported; delete body.engine_label; delete body.id;
    const r = edit.id ? await attempt(() => api.patch(`/api/printer-profiles/${edit.id}`, body), 'Saved')
                      : await attempt(() => api.post('/api/printer-profiles', body), 'Printer added');
    if (r) { edit = null; load(); }
  }
  async function remove(p) {
    if (!confirm(`Remove ${p.name}? Its slice results go with it; the files stay on each model.`)) return;
    await attempt(() => api.del(`/api/printer-profiles/${p.id}`), 'Removed');
    load();
  }
  const known = $derived(new Set((st?.profiles || []).map((p) => (p.wt_printer || p.name).toLowerCase())));
</script>

<h1 class="text-2xl font-bold mb-1">Printers</h1>
<p class="muted text-sm mb-5 max-w-3xl">Each printer is sliced with OrcaSlicer or PrusaSlicer, starting from that slicer's preset for the
  printer (or one you import), then each model's recommended layer height, walls, infill and supports, then your own overrides.</p>

{#if st}
  {#if !st.configured}
    <div class="panel p-5 text-sm">No slicer service is set up. The hub operator sets a shared one (HUB_SLICER_URL), or your team can
      connect its own on <a class="underline" href="/connections">Connections</a>.</div>
  {:else}
    <div class="panel p-4 mb-4 flex flex-wrap items-center gap-3 text-sm">
      {#if st.health?.ok}
        <span class="chip ok">Slicer online</span>
        <span class="muted">{Object.entries(st.engines || {}).map(([k, v]) => `${ENGINE[k] || k} ${v}`).join(' · ')} · {st.health.busy}/{st.health.slots} busy · {st.source === 'team' ? "your team's slicer" : 'shared slicer'}</span>
      {:else}<span class="chip bad">Slicer offline</span><span class="muted">{st.health?.error}</span>{/if}
      {#if can('slice')}<button class="btn sm primary ml-auto" onclick={() => start()}>Add printer</button>{/if}
    </div>

    {#if found.filter((f) => !known.has((f.name || '').toLowerCase())).length && can('slice')}
      <div class="panel p-4 mb-4 text-sm">
        <div class="font-semibold mb-2">Printers your print farm reports</div>
        <div class="flex flex-wrap gap-2">
          {#each found.filter((f) => !known.has((f.name || '').toLowerCase())) as f}
            <button class="btn sm" onclick={() => start(null, f)}>Set up {f.name}{f.model ? ` (${f.model})` : ''}</button>
          {/each}
        </div>
      </div>
    {/if}

    <div class="grid lg:grid-cols-2 gap-4">
      {#each st.profiles as p (p.id)}
        <div class="panel p-5 text-sm">
          <div class="flex flex-wrap items-center gap-2">
            <b class="text-base">{p.name}</b>{#if p.is_default}<span class="chip ok">default</span>{/if}{#if !p.active}<span class="chip">off</span>{/if}
            <span class="chip">{ENGINE[p.engine] || p.engine}</span>
            {#if can('slice')}<span class="ml-auto flex gap-1.5"><button class="btn sm" onclick={() => start(p)}>Edit</button><button class="btn sm danger" onclick={() => remove(p)}>Remove</button></span>{/if}
          </div>
          <div class="muted mt-1">{p.machine_preset}{p.imported ? ' · imported preset' : ''}</div>
          <div class="mt-2"><ToolHeads prof={p} /></div>
          <div class="mt-2">Process: {p.process_preset || "by the designer's layer height, else the printer's default"}{p.bed_type ? ` · plate: ${p.bed_type}` : ''}</div>
          {#if Object.keys(p.process_overrides).length}<div class="mt-1">Overrides: {Object.entries(p.process_overrides).map(([k, v]) => `${k}=${v}`).join(', ')}</div>{/if}
          <div class="mt-1 muted">{p.use_model_settings ? "Uses each model's recommended settings" : 'Ignores model recommendations'}{p.dispatch === 'legacy_farm' ? ` · approved files go to the farm${p.wt_printer ? ` (${p.wt_printer})` : ''}` : ''}{p.hourly_rate != null ? ` · ${p.hourly_rate}/h` : ''}</div>
        </div>
      {:else}<p class="muted">No printers yet. Add one to start slicing.</p>{/each}
    </div>
  {/if}
{/if}

{#if edit}
  <div class="fixed inset-0 bg-black/40 z-40 grid place-items-center p-4" role="presentation" onclick={(e) => { if (e.target === e.currentTarget) edit = null; }}>
    <div class="panel p-5 w-full max-w-3xl max-h-[92vh] overflow-auto text-sm">
      <h3 class="font-semibold text-lg mb-3">{edit.id ? `Edit ${edit.name}` : 'Add printer'}</h3>
      <div class="flex flex-wrap gap-1 mb-4">
        {#each STEPS as s, i}
          <button class="chip {step === i + 1 ? 'ok' : ''}" disabled={!edit.machine_preset && i + 1 > 2} onclick={() => (step = i + 1)}>{i + 1}. {s}</button>
        {/each}
      </div>

      {#if step === 1}
        <p class="mb-3">Which slicer should the hub use for this printer?</p>
        <div class="grid sm:grid-cols-2 gap-3">
          {#each Object.entries(st.engines || {}) as [k, v]}
            <label class="panel p-4 cursor-pointer flex gap-3 {edit.engine === k ? 'ring-2 ring-brand-600' : ''}">
              <input type="radio" bind:group={edit.engine} value={k} onchange={() => { edit.machine_preset = ''; edit.machine_config = null; machines = []; }} />
              <div><b>{ENGINE[k] || k}</b> <span class="muted">{v}</span>
                <div class="muted text-xs mt-1">{k === 'prusa' ? "Prusa's own profiles, including toolchangers like the CORE One INDX." : 'Bambu, Prusa, Voron, Creality, Qidi and many more.'}</div></div>
            </label>
          {/each}
        </div>
        <div class="flex gap-2 mt-4"><button class="btn primary" onclick={() => { step = 2; search(); }}>Next</button><button class="btn" onclick={() => (edit = null)}>Cancel</button></div>

      {:else if step === 2}
        <label class="lbl" for="ms">Find your printer in {ENGINE[edit.engine]}'s catalog</label>
        <input id="ms" class="input" bind:value={q} oninput={search} placeholder="CORE One INDX, H2D, MK4S, Voron 2.4 350…" />
        <div class="mt-2 max-h-72 overflow-auto border line rounded-lg">
          {#each machines as mc}
            <button class="block w-full text-left px-3 py-2 hover:bg-black/5 dark:hover:bg-white/5 {edit.machine_preset === mc.name ? 'font-semibold' : ''}" onclick={() => pick(mc.name)}>
              {mc.name}<span class="muted text-xs"> · {mc.vendor || ''}{(mc.tools || mc.extruders) > 1 ? ` · ${mc.tools || mc.extruders} tools` : ''}</span></button>
          {:else}<div class="muted p-3">{searching ? 'Searching…' : 'Type to search.'}</div>{/each}
        </div>
        <div class="mt-4 panel p-3">
          <div class="font-semibold">Or import your own preset</div>
          <p class="muted text-xs mt-1">{edit.engine === 'orca' ? 'In OrcaSlicer, export the printer preset (a .json file), or use a PrusaSlicer export (.ini) and the hub converts it.' : 'In PrusaSlicer, File > Export > Export Config (a .ini file).'}
            Use this for a printer the catalog doesn't have yet. Then pick the catalog printer it builds on, so the hub knows its tools and presets.</p>
          <input type="file" accept={edit.engine === 'orca' ? '.json,.ini' : '.ini'} class="mt-2" onchange={importFile} />
          {#if pendingIni}<label class="flex items-center gap-2 text-xs mt-2"><input type="checkbox" bind:checked={keepGcode} />
            Also carry over the start, end and tool-change G-code (best effort; leave off to use the OrcaSlicer printer's G-code)</label>{/if}
          {#if importName || edit.imported}<div class="text-xs mt-1" style="color:var(--ok)">Imported {importName || 'preset'}{edit.machine_preset ? `, building on ${edit.machine_preset}` : ''}</div>{/if}
        </div>
        <div class="flex gap-2 mt-4"><button class="btn" onclick={() => (step = 1)}>Back</button>{#if edit.machine_preset}<button class="btn primary" onclick={() => (step = 3)}>Next</button>{/if}</div>

      {:else if step === 3}
        {#if conv}
          <div class="panel p-3 mb-3 text-xs">
            <b>Converted {importName}</b> on top of {conv.base}: {conv.mapped.length} printer settings carried over{conv.ignored ? `, ${conv.ignored} print or filament settings left out` : ''}.
            {#if conv.unmapped.length}<div class="muted mt-1">Not carried over: {conv.unmapped.join(', ')}</div>{/if}
            {#each conv.warnings as w}<div class="mt-1" style="color:var(--warn)">{w}</div>{/each}
            <label class="flex items-center gap-2 mt-2"><input type="checkbox" bind:checked={keepGcode} onchange={convert} /> Carry over the PrusaSlicer G-code (best effort)</label>
          </div>
        {/if}
        <p class="mb-2">{edit.machine_preset}{entry ? ` has ${entry.tools || entry.extruders || 1} tool(s).` : ''} Set what each tool usually has loaded; the hub picks
          filament presets from it and warns when a model needs colours or materials the printer doesn't have.</p>
        <div class="flex flex-wrap gap-2 mb-2 text-xs">
          <span class="muted">Set all:</span>
          {#each ['PLA', 'PETG'] as mt}<button class="btn sm" onclick={() => fillAll('material', mt)}>{mt}</button>{/each}
          <button class="btn sm" onclick={() => fillAll('flow', 'hf')}>High flow</button>
          <button class="btn sm" onclick={() => fillAll('flow', 'standard')}>Standard flow</button>
        </div>
        <div class="scroll-x">
          <table class="grid">
            <thead><tr><th>Tool</th><th>Nozzle</th><th>Flow</th><th>Material</th><th>Colour</th><th>Filament preset</th><th></th></tr></thead>
            <tbody>
              {#each edit.tools as tl, i}
                <tr>
                  <td>T{tl.slot}</td>
                  <td><input class="input w-20" type="number" step="0.05" min="0.1" max="2" bind:value={tl.nozzle} /></td>
                  <td><select class="input w-28" bind:value={tl.flow}><option value="standard">Standard</option><option value="hf">High flow</option></select></td>
                  <td><select class="input w-28" bind:value={tl.material}><option value={null}>Any</option>{#each MATERIALS as mt}<option value={mt}>{mt}</option>{/each}</select></td>
                  <td><input type="color" value={tl.color || '#cccccc'} oninput={(e) => (tl.color = e.target.value)} aria-label="Tool {tl.slot} colour" />
                    {#if tl.color}<button class="text-xs underline ml-1" onclick={() => (tl.color = null)}>clear</button>{/if}</td>
                  <td><select class="input" bind:value={tl.preset}><option value={null}>Automatic</option>{#each filsFor(tl.material) as f}<option value={f.name}>{f.name}</option>{/each}</select></td>
                  <td>{#if edit.tools.length > 1}<button class="btn sm" onclick={() => dropTool(i)} aria-label="Remove tool {tl.slot}">x</button>{/if}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        <button class="btn sm mt-2" onclick={addTool}>Add a tool</button>
        <div class="flex gap-2 mt-4"><button class="btn" onclick={() => (step = 2)}>Back</button><button class="btn primary" onclick={() => (step = 4)}>Next</button></div>

      {:else}
        <div class="grid sm:grid-cols-2 gap-3">
          <div><label class="lbl" for="pn">Name</label><input id="pn" class="input" bind:value={edit.name} /></div>
          <div><label class="lbl" for="pp">Default process</label>
            <select id="pp" class="input" bind:value={edit.process_preset} onchange={loadPresets}>
              <option value="">By the designer's layer height, else the printer's default</option>
              {#each procs as pr}<option value={pr.name}>{pr.name}</option>{/each}
            </select></div>
        </div>
        <div class="mt-3"><label class="lbl" for="po">Process overrides for this printer (one per line, {ENGINE[edit.engine]} setting = value)</label>
          <textarea id="po" class="input font-mono text-xs" rows="3" bind:value={ovText} placeholder={edit.engine === 'orca' ? 'brim_type = no_brim\nseam_position = back' : 'brim_width = 0\nseam_position = rear'}></textarea></div>
        <div class="flex flex-col gap-2 mt-3">
          {#if edit.engine === 'orca'}
            <label class="flex items-center gap-2"><input type="checkbox" checked={hasOv('wipe_tower_wall_type', 'rib')} onchange={(e) => setOv({ wipe_tower_wall_type: 'rib', wipe_tower_fillet_wall: '1' }, e.target.checked)} />
              Rib prime tower (OrcaSlicer 2.5: a sturdier tower for multi-colour prints)</label>
          {/if}
          <label class="flex items-center gap-2"><input type="checkbox" checked={hasOv(edit.engine === 'orca' ? 'sparse_infill_pattern' : 'fill_pattern', 'adaptivecubic')} onchange={(e) => setOv({ [edit.engine === 'orca' ? 'sparse_infill_pattern' : 'fill_pattern']: 'adaptivecubic' }, e.target.checked)} />
            Adaptive infill (adaptive cubic: dense near the surface, sparse inside; overrides the designer's pattern)</label>
        </div>
        <div class="grid sm:grid-cols-3 gap-3 mt-3">
          <div><label class="lbl" for="hr">Printer time per hour</label><input id="hr" class="input" type="number" min="0" step="0.05" bind:value={edit.hourly_rate} placeholder="team default" /></div>
          {#if edit.engine === 'orca'}<div><label class="lbl" for="bt">Build plate always fitted</label>
            <select id="bt" class="input" bind:value={edit.bed_type}><option value="">Printer default</option>
              {#each ['High Temp Plate', 'Textured PEI Plate', 'Cool Plate', 'Engineering Plate', 'Supertack Plate', 'Textured Cool Plate'] as b}<option value={b}>{b}</option>{/each}</select>
            <div class="text-[11px] muted">Used for every slice on this printer, including designer project files. Sets the bed temperature for each filament.</div></div>{/if}
          <div><label class="lbl" for="fs">Filament slots (including AMS)</label><input id="fs" class="input" type="number" min="1" max="64" step="1" bind:value={edit.filament_slots} placeholder="one per tool" />
            <div class="text-[11px] muted">How many filaments it can hold at once. Models needing more colours than this are flagged.</div></div>
          <div><label class="lbl" for="pd">Approved files go to</label>
            <select id="pd" class="input" bind:value={edit.dispatch}><option value="none">Nowhere (download them)</option><option value="legacy_farm">The print farm</option></select></div>
          {#if edit.dispatch === 'legacy_farm'}<div><label class="lbl" for="pw">Printer name on the farm</label><input id="pw" class="input" bind:value={edit.wt_printer} /></div>{/if}
        </div>
        <div class="flex flex-col gap-2 mt-3">
          <label class="flex items-center gap-2"><input type="checkbox" bind:checked={edit.use_model_settings} /> Apply each model's recommended walls, infill, supports and layer height</label>
          <label class="flex items-center gap-2"><input type="checkbox" bind:checked={edit.is_default} /> Default printer (its approved slice is the production file and sets the grams in Materials)</label>
          <label class="flex items-center gap-2"><input type="checkbox" bind:checked={edit.active} /> Active</label>
        </div>
        <div class="flex gap-2 mt-4">
          <button class="btn" onclick={() => (step = 3)}>Back</button>
          <button class="btn primary" onclick={save} disabled={!edit.name || !edit.machine_preset}>Save</button>
          <button class="btn" onclick={() => (edit = null)}>Cancel</button>
        </div>
      {/if}
    </div>
  </div>
{/if}
