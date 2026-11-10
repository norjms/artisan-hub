<script>
  import { onMount } from 'svelte';
  import { api, fmtBytes, fmtTime } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  import { can } from '$lib/session.svelte.js';
  import Dropzone from '$lib/Dropzone.svelte';
  let { m, onsaved } = $props();
  let busy = $state(false);
  let sl = $state(null);           // the model's slicing state: which printer prints which file
  let pick = $state({});           // file id -> printer chosen in its "Print on" menu
  let replaceFor = $state(null);   // the file a replacement is being picked for
  let picker;
  const kinds = { stl: 'STL', '3mf_project': '3MF project', '3mf_sliced': 'Sliced 3MF', gcode: 'G-code', step: 'STEP', f3d: 'Fusion 360', obj: 'OBJ', other: 'Other' };
  const sliced = (f) => f.kind === '3mf_sliced' || f.kind === 'gcode';

  // file id -> the printers using it: approved (prints it now) or waiting for review
  const usedBy = $derived.by(() => {
    const out = {};
    for (const p of sl?.printers || []) {
      for (const id of p.approved_file_ids || []) (out[id] ||= []).push({ ...p, approved: true });
      for (const id of p.file_ids || []) if (!(p.approved_file_ids || []).includes(id)) (out[id] ||= []).push({ ...p, approved: false });
    }
    return out;
  });

  async function loadSlicing() {
    try { sl = await api.get(`/api/models/${m.id}/slicing`); } catch { sl = null; }
  }
  onMount(loadSlicing);
  async function done() { await loadSlicing(); onsaved?.(); }

  async function upload(files) {
    busy = true;
    try { await attempt(() => api.upload(`/api/models/${m.id}/files`, files), 'Uploaded'); await done(); } finally { busy = false; }
  }
  async function setPrint(f, v) {
    await attempt(() => api.patch(`/api/files/${f.id}`, { is_print_file: v }), v ? 'Marked for production. Watchtower will get it.' : 'Unmarked');
    await done();
  }
  async function useOn(f) {
    const pid = Number(pick[f.id]);
    pick[f.id] = '';
    if (!pid) return;
    const p = sl.printers.find((x) => x.profile_id === pid);
    const r = await attempt(() => api.post(`/api/models/${m.id}/printers/${pid}/use-file`, { file_id: f.id, lock: true }));
    if (r) {
      toast(`${p.printer} now prints ${f.filename} (locked)${r.production ? '. It is the production file.' : ''}`, 'ok', 6000);
      for (const w of r.warnings || []) toast(w, 'warn', 9000);
    }
    await done();
  }
  async function del(f) {
    const users = (usedBy[f.id] || []).map((p) => p.printer);
    const msg = `Delete ${f.filename} for good? It goes from the library${f.watchtower_uuid ? ' and Watchtower' : ''}.`
      + (users.length ? `\n\n${users.join(', ')} ${users.length === 1 ? 'prints' : 'print'} it now and will have nothing approved.` : '')
      + (f.is_print_file ? '\n\nIt is the production file: store orders will have nothing to print until another file is chosen.' : '');
    if (!confirm(msg)) return;
    await attempt(() => api.del(`/api/files/${f.id}`), `Deleted ${f.filename}`);
    await done();
  }
  function askReplace(f) { replaceFor = f; picker.value = ''; picker.click(); }
  async function replace(e) {
    const file = e.currentTarget.files?.[0];
    const f = replaceFor;
    replaceFor = null;
    if (!file || !f) return;
    busy = true;
    try {
      const fd = new FormData();
      fd.append('file', file);
      const r = await attempt(() => fetch(`/api/files/${f.id}/replace`, { method: 'POST', body: fd, credentials: 'same-origin' })
        .then(async (x) => { const j = await x.json().catch(() => ({})); if (!x.ok) throw new Error(j.detail || x.statusText); return j; }));
      if (r) {
        toast(`Replaced ${f.filename}${r.printers?.length ? `; ${r.printers.join(', ')} now ${r.printers.length === 1 ? 'prints' : 'print'} ${r.file} (locked)` : ''}`, 'ok', 7000);
        for (const w of r.warnings || []) toast(w, 'warn', 9000);
      }
      await done();
    } finally { busy = false; }
  }
</script>

<input type="file" class="hidden" bind:this={picker} accept=".3mf,.gcode,.stl,.step,.stp,.f3d,.obj" onchange={replace} />

<div class="panel p-5">
  <p class="text-sm muted mb-3">Source files live in Manyfold. Slice and approve on the <b>Print</b> tab, or pick a sliced file here and choose which printer prints it: it is approved and locked to that printer at once (re-slicing won't replace it). The file the default printer prints is the production file.</p>
  <div class="scroll-x">
    <table class="grid">
      <thead><tr><th>File</th><th>Type</th><th>Slicer / printer</th><th>Plates</th><th>Time</th><th>Filament</th><th>Size</th><th>Prints on</th><th>Production</th><th></th></tr></thead>
      <tbody>
        {#each m.files_all as f (f.id)}
          <tr>
            <td class="max-w-[280px] truncate" title={f.filename}>{f.filename}{#if f.settings?.geometry?.bbox_mm}<div class="text-xs muted">{f.settings.geometry.bbox_mm.join(' x ')} mm{f.settings.geometry.est_grams ? ` · about ${Math.round(f.settings.geometry.est_grams)} g` : ''}</div>{/if}</td>
            <td><span class="chip {sliced(f) ? 'ok' : ''}">{kinds[f.kind] || f.kind}</span></td>
            <td class="text-xs">{f.slicer || ''}<div class="muted">{f.printer || ''}</div></td>
            <td>{f.plates || ''}</td>
            <td>{fmtTime(f.print_seconds)}</td>
            <td class="text-xs">{#each f.filament || [] as fl}<span class="inline-flex items-center gap-1 mr-2"><span class="swatch" style="background:{fl.color || '#ccc'}"></span>{fl.grams ? `${fl.grams}g` : fl.type}</span>{/each}</td>
            <td class="text-xs muted">{fmtBytes(f.bytes)}</td>
            <td class="text-xs min-w-[11rem]">
              {#each usedBy[f.id] || [] as p}
                <span class="chip {p.approved ? 'ok' : ''} mr-1 mb-1" title={p.approved ? (p.locked ? 'Approved and locked: re-slicing will not replace it' : 'Approved') : 'Waiting for review on the Print tab'}>{p.printer}{p.approved ? (p.locked ? ' · locked' : '') : ' · review'}</span>
              {/each}
              {#if sliced(f) && can('slice') && sl?.printers?.length}
                <select class="input mt-1 text-xs" aria-label="Print {f.filename} on a printer" bind:value={pick[f.id]} onchange={() => useOn(f)}>
                  <option value="">Print on…</option>
                  {#each sl.printers as p}
                    {@const mine = (usedBy[f.id] || []).some((u) => u.profile_id === p.profile_id && u.approved)}
                    <option value={p.profile_id}>{p.printer}{p.is_default ? ' (default)' : ''}{mine ? (p.locked ? ' ✓' : ' ✓ (lock it)') : p.has_approved ? ' (replaces its file)' : ''}</option>
                  {/each}
                </select>
              {/if}
            </td>
            <td>
              {#if sliced(f)}
                <button class="toggle {f.is_print_file ? 'on' : ''}" aria-label="Production file" onclick={() => setPrint(f, !f.is_print_file)}></button>
                {#if f.watchtower_uuid}<div class="text-[10px] muted mt-1">in Watchtower</div>{/if}
              {/if}
            </td>
            <td class="whitespace-nowrap">
              <a class="btn sm" href={f.download}>Download</a>
              {#if can('write')}
                <button class="btn sm" onclick={() => askReplace(f)} disabled={busy} title="Upload a new version; it takes this file's place everywhere">Replace</button>
                <button class="btn sm danger" onclick={() => del(f)} disabled={busy}>Delete</button>
              {/if}
            </td>
          </tr>
        {:else}<tr><td colspan="10" class="muted text-center py-6">No files yet.</td></tr>{/each}
      </tbody>
    </table>
  </div>
  <div class="mt-4"><Dropzone onfiles={upload} {busy} label="Drop STL, 3MF (project or sliced), G-code, STEP or F3D files" /></div>
</div>
