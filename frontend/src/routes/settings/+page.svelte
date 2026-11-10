<script>
  import { onMount } from 'svelte';
  import { api, ago } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  import { theme, setTheme, SKINS, MODES } from '$lib/theme.svelte.js';
  let s = $state(null);
  let keys = $state([]);
  let conns = $state([]);
  const origin = typeof location !== "undefined" ? location.origin : "";
  let newKey = $state('');
  let created = $state('');
  let createdNote = $state('');
  let mw = $state(null);
  let mwf = $state({ email: '', password: '', code: '', tfa_key: null });
  let mwStep = $state('creds');
  let mwMsg = $state('');
  let mwBusy = $state(false);
  async function load() {
    s = await api.get('/api/settings'); mw = await api.get('/api/makerworld/status');
    try { keys = (await api.get('/api/mobile/v1/keys')).keys; conns = await api.get('/api/oauth/connections'); } catch { keys = []; conns = []; }
  }
  async function mwLogin() {
    mwBusy = true;
    try {
      const body = { email: mwf.email, password: mwStep === 'creds' ? mwf.password : null, code: mwStep !== 'creds' ? mwf.code : null, tfa_key: mwf.tfa_key };
      if (mwStep === 'code') body.password = null;
      const r = await attempt(() => api.post('/api/makerworld/login', body));
      if (r.status === 'connected') { toast(`Connected${r.name ? ' as ' + r.name : ''}`); mwf = { email: '', password: '', code: '', tfa_key: null }; mwStep = 'creds'; mwMsg = ''; load(); }
      else if (r.status === 'code_sent') { mwStep = 'code'; mwMsg = r.message; }
      else if (r.status === 'tfa') { mwStep = 'tfa'; mwf.tfa_key = r.tfa_key; mwMsg = r.message; }
    } finally { mwBusy = false; }
  }
  async function mwLogout() { if (confirm('Disconnect MakerWorld?')) { await api.del('/api/makerworld/login'); load(); } }
  let srcs = $state(null);
  let tvTok = $state('');
  let cr = $state({ email: '', password: '' });
  async function loadSrcs() { srcs = await api.get('/api/sources/status'); }
  async function tvSave() { await attempt(() => api.post('/api/sources/thingiverse/login', { token: tvTok }), 'Thingiverse connected'); tvTok = ''; loadSrcs(); }
  async function crSave() { const r = await attempt(() => api.post('/api/sources/creality/login', cr), 'Creality Cloud connected'); cr = { email: '', password: '' }; loadSrcs(); }
  let cu = $state({ email: '', token: '' });
  async function cuSave() { await attempt(() => api.post('/api/sources/cults3d/login', cu), 'Cults3D connected'); cu = { email: '', token: '' }; loadSrcs(); }
  async function srcOut(p) { if (confirm('Disconnect?')) { await api.del(`/api/sources/${p}/login`); loadSrcs(); } }
  let mp = $state({ mat: '', price: '' });
  const tzs = (() => { try { return Intl.supportedValuesOf('timeZone'); } catch { return ['UTC', 'America/Chicago', 'America/New_York', 'America/Denver', 'America/Los_Angeles', 'Europe/London']; } })();
  let bl = $state(null);
  async function loadBl() { bl = await api.get('/api/import-blocklist'); }
  async function unblock(code) { bl = await attempt(() => api.post('/api/import-blocklist/remove', { code }), `${code} can be imported again`); }
  async function recheck() { await attempt(() => api.post('/api/actions/license-recheck'), 'Checking every MakerWorld model license; you get an email if any changed'); }
  onMount(() => { load(); loadBl(); loadSrcs(); });
  async function matchAll() { await attempt(() => api.post('/api/filaments/match-all'), 'Matching every model in the background'); }
  async function save() { s = await attempt(() => api.put('/api/settings', s), 'Saved'); }
  async function mk() { const r = await attempt(() => api.post('/api/keys', { name: newKey || 'client' })); created = r.key; createdNote = ''; newKey = ''; load(); }
  // Connect a phone by QR code (one-time, 10 minutes; the phone confirms before a key is made)
  let pair = $state(null);
  let pairAccess = $state('act');
  let pairDays = $state('90');
  let pairLeft = $state(0);
  let pairTimer = null;
  function stopTimer() { if (pairTimer) clearInterval(pairTimer); pairTimer = null; }
  async function startPair() {
    stopTimer();
    try {
      pair = await attempt(() => api.post('/api/mobile/v1/pairings', { access: pairAccess, days: pairDays === 'never' ? null : Number(pairDays) }));
    } catch { return; }
    tickPair();
    pairTimer = setInterval(tickPair, 2000);
  }
  async function tickPair() {
    if (!pair) return;
    pairLeft = Math.max(0, Math.round((new Date(pair.expires_at) - Date.now()) / 1000));
    try { const s = await api.get(`/api/mobile/v1/pairings/${pair.id}`); pair = { ...pair, ...s }; } catch {}
    if (pair && pair.state !== 'waiting') {
      stopTimer();
      if (pair.state === 'connected') { toast(`${pair.device} is connected`); load(); }
    }
  }
  async function cancelPair() {
    stopTimer();
    if (pair && pair.state === 'waiting') { try { await api.del(`/api/mobile/v1/pairings/${pair.id}`); } catch {} }
    pair = null;
  }
  $effect(() => () => stopTimer());
  async function mkPhone() {
    const r = await attempt(() => api.post('/api/mobile/v1/keys', { name: newKey || 'Phone', scopes: ['read', 'write'], expires_in_days: 90, bind_instance: true, note: 'Artisan Hub phone app' }));
    created = r.key; createdNote = 'Paste it into the Artisan Hub app with this address: ' + origin + '. It works for 90 days and only on the first phone that uses it.'; newKey = ''; load();
  }
  async function copyPairLink() { try { await navigator.clipboard.writeText(pair.url); toast('Link copied; open it on the phone'); } catch { toast('Could not copy the link'); } }
  async function copyKey() { try { await navigator.clipboard.writeText(created); toast('Key copied'); } catch { toast('Select the key and copy it'); } }
  async function disconnect(c) { if (confirm(`Disconnect ${c.app}? It will need to sign in again.`)) { await attempt(() => api.del(`/api/oauth/connections/${c.grant}`), 'Disconnected'); load(); } }
  async function revoke(k) { if (confirm(`Revoke ${k.name}?`)) { await api.del(`/api/keys/${k.id}`); load(); } }
</script>

<h1 class="text-2xl font-bold mb-4">Settings</h1>
<div class="panel p-5 mb-5" id="appearance">
  <h3 class="font-semibold mb-1">Appearance</h3>
  <p class="text-sm muted mb-3">Just for you, on every device you sign in on.</p>
  <div class="flex flex-wrap gap-6">
    <div>
      <div class="lbl">Look</div>
      <div class="flex gap-2">
        {#each Object.entries(SKINS) as [k, l]}
          <button class="btn {theme.skin === k ? 'primary' : ''}" onclick={() => setTheme(k, null)}>{l}</button>
        {/each}
      </div>
      <p class="text-xs muted mt-1">Legacy farm matches Legacy farm's own screens (top tabs, IBM Plex type, blue accents). OpenPrintHQ matches the OpenPrintHQ app (navy, violet, Inter, sidebar). Classic is the hub's original look.</p>
    </div>
    <div>
      <div class="lbl">Light or dark</div>
      <div class="flex gap-2">
        {#each Object.entries(MODES) as [k, l]}
          <button class="btn {theme.mode === k ? 'primary' : ''}" onclick={() => setTheme(null, k)}>{l}</button>
        {/each}
      </div>
    </div>
  </div>
</div>
{#if s}
<div class="grid lg:grid-cols-2 gap-5">
  <div class="panel p-5 space-y-4">
    <h3 class="font-semibold">Automation</h3>
    <label class="flex items-center justify-between gap-4"><span><b>Auto-queue prints</b><div class="text-sm muted">Legacy farm starts print jobs as soon as a store order for a mapped SKU arrives.</div></span>
      <button class="toggle {s.wt_auto_queue ? 'on' : ''}" onclick={() => { s.wt_auto_queue = !s.wt_auto_queue; save(); }} aria-label="auto queue"></button></label>
    <label class="flex items-center justify-between gap-4"><span><b>Store 3MF / G-code files in Legacy farm</b><div class="text-sm muted">Every model's 3MF and G-code files are mirrored into Legacy farm's library under team name / category / model.</div></span>
      <button class="toggle {s.wt_mirror_library ? 'on' : ''}" onclick={() => { s.wt_mirror_library = !s.wt_mirror_library; save(); }} aria-label="mirror"></button></label>
    <label class="flex items-center justify-between gap-4"><span><b>Deduct parts when orders complete</b><div class="text-sm muted">Removes per-unit parts from InvenTree stock when Woo marks the order completed. Filament is tracked by Spoolman.</div></span>
      <button class="toggle {s.consume_parts_on_complete ? 'on' : ''}" onclick={() => { s.consume_parts_on_complete = !s.consume_parts_on_complete; save(); }} aria-label="consume"></button></label>
    <label class="flex items-center justify-between gap-4"><span><b>Hide products when a commercial license ends</b><div class="text-sm muted">When a membership you sell under passes its end date, its live products are hidden on the store automatically. Off: you get an email and decide.</div></span>
      <button class="toggle {s.hide_on_license_expiry ? 'on' : ''}" onclick={() => { s.hide_on_license_expiry = !s.hide_on_license_expiry; save(); }} aria-label="hide on expiry"></button></label>
  </div>
  <div class="panel p-5 space-y-4">
    <h3 class="font-semibold">Slicing</h3>
    <label class="flex items-center justify-between gap-4"><span><b>Pause slicing</b><div class="text-sm muted">Queued slices wait until you turn this off.</div></span>
      <button class="toggle {s.slice_paused ? 'on' : ''}" onclick={() => { s.slice_paused = !s.slice_paused; save(); }} aria-label="pause slicing"></button></label>
    <label class="block"><b>Filament brand</b><div class="text-sm muted mb-1">Replace each model's requested filament with this brand's closest colour (same finish when it has one). The designer's request is kept on the model.</div>
      <select class="input" bind:value={s.filament_brand} onchange={save}><option value={null}>Keep the requested filaments</option><option value="Bambu Lab">Bambu Lab</option></select></label>
    {#if s.filament_brand}<button class="btn sm" onclick={matchAll}>Match the whole library now</button>{/if}
    <label class="block"><b>Slice new models automatically</b><div class="text-sm muted mb-1">When a model is imported, slice it right away. The result still waits for review.</div>
      <select class="input" bind:value={s.slice_auto_new} onchange={save}>
        <option value="off">Off</option><option value="default">For the default printer</option><option value="all">For every active printer</option>
      </select></label>
    <label class="flex items-center justify-between gap-4"><span><b>Re-slice out-of-date slices overnight</b><div class="text-sm muted">After a slicer upgrade or a printer change, slices that changed are redone at night.</div></span>
      <button class="toggle {s.slice_nightly ? 'on' : ''}" onclick={() => { s.slice_nightly = !s.slice_nightly; save(); }} aria-label="nightly re-slice"></button></label>
    {#if s.slice_nightly}
      <div class="grid grid-cols-2 gap-3">
        <label><span class="lbl">Starts at (hour)</span><input class="input" type="number" min="0" max="23" bind:value={s.slice_night_hour} onchange={() => { s.slice_night_hour = Number(s.slice_night_hour); save(); }} /></label>
        <label><span class="lbl">Time zone</span><input class="input" list="tzs" bind:value={s.timezone} onchange={save} placeholder="America/Chicago" />
          <datalist id="tzs">{#each tzs as z}<option value={z}></option>{/each}</datalist></label>
      </div>
    {/if}
    <label class="flex items-center justify-between gap-4"><span><b>Approve slices that finish without problems</b><div class="text-sm muted">Skip review for any slice with no problems (missing tools or materials, failed prints). Slices with problems still wait for you.</div></span>
      <button class="toggle {s.slice_auto_approve_clean ? 'on' : ''}" onclick={() => { s.slice_auto_approve_clean = !s.slice_auto_approve_clean; save(); }} aria-label="auto approve clean slices"></button></label>
    <label class="flex items-center justify-between gap-4"><span><b>Approve re-slices that barely changed</b><div class="text-sm muted">Skip review when an approved slice is redone and print time and grams stay within {s.slice_auto_approve_pct}%.</div></span>
      <button class="toggle {s.slice_auto_approve ? 'on' : ''}" onclick={() => { s.slice_auto_approve = !s.slice_auto_approve; save(); }} aria-label="auto approve"></button></label>
    {#if s.slice_auto_approve}<label class="block"><span class="lbl">Tolerance %</span><input class="input w-32" type="number" min="0" max="50" bind:value={s.slice_auto_approve_pct} onchange={() => { s.slice_auto_approve_pct = Number(s.slice_auto_approve_pct); save(); }} /></label>{/if}
    <label class="flex items-center justify-between gap-4"><span><b>Go live only with an approved slice</b><div class="text-sm muted">A product can't go live on the store until the default printer has an approved print file.</div></span>
      <button class="toggle {s.require_approved_slice ? 'on' : ''}" onclick={() => { s.require_approved_slice = !s.require_approved_slice; save(); }} aria-label="require approved slice"></button></label>
    <div class="border-t line pt-3">
      <b>Cost per unit</b>
      <div class="text-sm muted mb-2">Filament price comes from Spoolman when the colour is linked to a Spoolman filament with a price, else from these prices. Printers can have their own hourly rate.</div>
      <div class="grid grid-cols-2 gap-3">
        <label><span class="lbl">Filament price per kg</span><input class="input" type="number" min="0" step="0.5" bind:value={s.default_material_price} onchange={() => { s.default_material_price = Number(s.default_material_price); save(); }} /></label>
        <label><span class="lbl">Printer time per hour</span><input class="input" type="number" min="0" step="0.05" bind:value={s.printer_hourly_rate} onchange={() => { s.printer_hourly_rate = Number(s.printer_hourly_rate); save(); }} /></label>
      </div>
      <div class="lbl mt-3">Price per kg by material (optional)</div>
      <div class="flex flex-wrap gap-2">
        {#each Object.entries(s.material_prices || {}) as [mat, v]}
          <span class="chip">{mat}: {v}<button class="ml-1 muted hover:underline" onclick={() => { const m = { ...s.material_prices }; delete m[mat]; s.material_prices = m; save(); }}>remove</button></span>
        {/each}
      </div>
      <form class="flex gap-2 mt-2" onsubmit={(e) => { e.preventDefault(); if (!mp.mat || mp.price === '') return; s.material_prices = { ...(s.material_prices || {}), [mp.mat.toUpperCase()]: Number(mp.price) }; mp = { mat: '', price: '' }; save(); }}>
        <input class="input w-32" placeholder="PETG" bind:value={mp.mat} /><input class="input w-32" type="number" min="0" step="0.5" placeholder="per kg" bind:value={mp.price} /><button class="btn">Add</button>
      </form>
    </div>
  </div>
  <div class="panel p-5 space-y-3">
    <h3 class="font-semibold">MakerWorld account</h3>
    <p class="text-sm muted">Needed to download model files (3MF) when importing. Browsing, photos, details and license records work without it. Uses your Bambu Lab login; the password is sent once to Bambu Lab and only the session token is stored.</p>
    {#if mw?.connected}
      <div class="flex items-center justify-between"><span class="chip ok">Connected{mw.name ? ` as ${mw.name}` : ''}</span><span class="text-xs muted">{mw.account} · {ago(mw.connected_at)}</span>
        <button class="btn sm danger" onclick={mwLogout}>Disconnect</button></div>
    {:else}
      <form class="space-y-2" onsubmit={(e) => { e.preventDefault(); mwLogin(); }}>
        <input class="input" type="email" placeholder="Bambu Lab email" bind:value={mwf.email} autocomplete="username" />
        {#if mwStep === 'creds'}<input class="input" type="password" placeholder="Password (leave blank to get an email code)" bind:value={mwf.password} autocomplete="current-password" />
        {:else}<input class="input" inputmode="numeric" placeholder={mwStep === 'tfa' ? 'Authenticator code' : 'Code from email'} bind:value={mwf.code} autocomplete="one-time-code" />{/if}
        {#if mwMsg}<p class="text-sm muted">{mwMsg}</p>{/if}
        <button class="btn primary" disabled={mwBusy || !mwf.email}>{mwStep === 'creds' ? 'Connect' : 'Verify'}</button>
      </form>
    {/if}
    <div class="border-t line pt-3 flex items-center justify-between gap-3">
      <span class="text-sm"><b>License watch</b><div class="muted">Weekly check that imported designs (all sites) still carry the license you recorded.</div></span>
      <button class="btn sm" onclick={recheck}>Check now</button>
    </div>
  </div>
  <div class="panel p-5 space-y-4">
    <h3 class="font-semibold">Other model sites</h3>
    <div class="text-sm"><b>Printables</b> <span class="chip ok">no account needed</span><div class="muted">Free models download directly.</div></div>
    <div class="text-sm border-t line pt-3"><b>Thangs and MyMiniFactory</b> <span class="chip ok">no account needed</span><div class="muted">Details, photos and license import from the link. Their downloads need you signed in, so the files come from your browser: the Artisan Hub add-on's <i>Send files</i>, or drop them on the model's Files tab.</div></div>
    <div class="text-sm space-y-2 border-t line pt-3">
      <div class="flex items-center justify-between"><b>Cults3D</b>
        {#if srcs?.cults3d?.connected}<span class="flex items-center gap-2"><span class="chip ok">API key{srcs.cults3d.account ? ` (${srcs.cults3d.account})` : ''}</span><button class="btn sm danger" onclick={() => srcOut('cults3d')}>Remove</button></span>{/if}</div>
      {#if !srcs?.cults3d?.connected}
        <p class="muted">Cults3D refuses servers, so without a key the browser add-on reads the model page you have open. With your Cults3D nick and an API key from <a class="underline" target="_blank" href="https://cults3d.com/en/api/keys">cults3d.com/en/api/keys</a> a pasted link works here and on the phone too. Files always come from your browser.</p>
        <form class="flex gap-2" onsubmit={(e) => { e.preventDefault(); cuSave(); }}><input class="input" placeholder="Cults3D nick" bind:value={cu.email} /><input class="input" placeholder="API key" bind:value={cu.token} /><button class="btn primary" disabled={!cu.email || !cu.token}>Save</button></form>
      {/if}
    </div>
    <div class="text-sm space-y-2 border-t line pt-3">
      <div class="flex items-center justify-between"><b>Thingiverse</b>
        {#if srcs?.thingiverse?.connected}<span class="flex items-center gap-2"><span class="chip ok">Connected</span><button class="btn sm danger" onclick={() => srcOut('thingiverse')}>Remove</button></span>{/if}</div>
      {#if !srcs?.thingiverse?.connected}
        <p class="muted">Thingiverse's API needs an App Token: create a free app at <a class="underline" target="_blank" href="https://www.thingiverse.com/apps/create">thingiverse.com/apps/create</a> (type Web App, any name, and this hub's address as the URL), then copy the App Token here.</p>
        <form class="flex gap-2" onsubmit={(e) => { e.preventDefault(); tvSave(); }}><input class="input" placeholder="App Token" bind:value={tvTok} /><button class="btn primary" disabled={!tvTok}>Save</button></form>
      {/if}
    </div>
    <div class="text-sm space-y-2 border-t line pt-3">
      <div class="flex items-center justify-between"><b>Creality Cloud</b>
        {#if srcs?.creality?.connected}<span class="flex items-center gap-2"><span class="chip ok">Connected{srcs.creality.name ? ` as ${srcs.creality.name}` : ''}</span><button class="btn sm danger" onclick={() => srcOut('creality')}>Disconnect</button></span>{/if}</div>
      {#if !srcs?.creality?.connected}
        <p class="muted">Needed only to download 3MF files. Password goes to Creality once; only the session token is stored.</p>
        <form class="space-y-2" onsubmit={(e) => { e.preventDefault(); crSave(); }}>
          <input class="input" type="email" placeholder="Creality Cloud email" bind:value={cr.email} autocomplete="username" />
          <input class="input" type="password" placeholder="Password" bind:value={cr.password} autocomplete="current-password" />
          <button class="btn primary" disabled={!cr.email || !cr.password}>Connect</button>
        </form>
      {/if}
    </div>
  </div>
  {#if bl?.codes?.length}
  <div class="panel p-5">
    <h3 class="font-semibold mb-1">Deleted models (won't be re-imported)</h3>
    <p class="text-sm muted mb-3">The library scanner skips these. Remove one to let it import again on the next scan. Importing a MakerWorld model by hand also clears it.</p>
    <div class="flex flex-wrap gap-2">{#each bl.codes as c}<span class="chip">{c} <button class="ml-1 muted hover:underline" onclick={() => unblock(c)}>allow</button></span>{/each}</div>
  </div>
  {/if}
  <div class="panel p-5">
    <h3 class="font-semibold mb-1">Claude connector</h3>
    <p class="text-sm muted mb-3">Add <code>{origin}/mcp</code> as a custom connector in Claude (Settings &gt; Connectors). You sign in with your hub login and approve it; Claude can then find models, slice them for your printers and pick production files.</p>
    {#if conns.length}<table class="grid"><tbody>
      {#each conns as c}<tr><td>{c.app}</td><td class="text-xs muted">{c.user} · {c.scopes.join(', ')}</td><td class="text-xs muted">connected {ago(c.connected_at)}{c.last_used_at ? ', used ' + ago(c.last_used_at) : ''}</td>
        <td><button class="btn sm danger" onclick={() => disconnect(c)}>Disconnect</button></td></tr>{/each}
    </tbody></table>{:else}<p class="text-sm muted">No apps connected.</p>{/if}
  </div>
  <div class="panel p-5" id="browser-add-on">
    <h3 class="font-semibold mb-1">Browser add-on</h3>
    <p class="text-sm muted">Add models from Printables, Thingiverse, MakerWorld, Creality Cloud, Thangs, MyMiniFactory and Cults3D with one click in Firefox, Chrome or Edge. Each person makes their own add-on key on the <a class="underline" href="/add-on">Browser add-on</a> page.</p>
  </div>
  <div class="panel p-5">
    <h3 class="font-semibold mb-1">API keys</h3>
    <p class="text-sm muted mb-3">For scripts, Claude, n8n and so on. Send <code>Authorization: Bearer ahk_…</code>. Docs: <a class="underline" href="/api/docs" target="_blank">/api/docs</a>. Quick add: <code>POST /api/v1/ingest</code>.</p>
    <div class="panel p-4 mb-4">
      <h4 class="font-semibold mb-1">Connect a phone <a class="btn sm ml-2 align-middle" href="/tv">Connect a TV</a></h4>
      <p class="text-sm muted mb-3">Scan the code with the phone's camera or the Artisan Hub app. If the app is not installed yet, the phone offers to install it. The phone shows what it is connecting to and asks you to confirm; only then is a key made, tied to that phone. The code works once, for 10 minutes. To install the app first, open <a class="underline" href="/get-app" data-sveltekit-reload>Get the app</a> on the phone.</p>
      {#if !pair}
        <div class="flex flex-wrap gap-2 items-center mb-2">
          <span class="text-sm muted" style="min-width:6rem">The phone can</span>
          {#each [['view', 'View only'], ['act', 'View and act'], ['all', 'Everything I can do']] as [val, label]}
            <button class="btn sm" class:primary={pairAccess === val} onclick={() => (pairAccess = val)}>{label}</button>
          {/each}
        </div>
        <div class="flex flex-wrap gap-2 items-center mb-3">
          <span class="text-sm muted" style="min-width:6rem">for</span>
          {#each [['30', '30 days'], ['90', '90 days'], ['365', '1 year'], ['never', 'No expiry']] as [val, label]}
            <button class="btn sm" class:primary={pairDays === val} onclick={() => (pairDays = val)}>{label}</button>
          {/each}
        </div>
        <button class="btn primary" onclick={startPair}>Show QR code</button>
      {:else}
        <div class="flex flex-wrap gap-6 items-center">
          <div class="pair-qr" class:faded={pair.state !== 'waiting'}>{@html pair.qr_svg}</div>
          <div class="text-sm" style="min-width:14rem">
            {#if pair.state === 'waiting'}
              <p><b>Waiting for a phone</b> · {Math.floor(pairLeft / 60)}:{String(pairLeft % 60).padStart(2, '0')} left</p>
              <p class="muted mt-1">{pair.access}, {pair.days ? `${pair.days} days` : 'no expiry'}</p>
            {:else if pair.state === 'connected'}
              <p style="color:var(--ok)"><b>{pair.device} is connected.</b></p>
              <p class="muted mt-1">Its key is in the list below, tied to that phone.</p>
            {:else}
              <p><b>This code {pair.state === 'expired' ? 'has expired' : 'was cancelled'}.</b></p>
            {/if}
            <div class="flex gap-2 mt-3">
              {#if pair.state === 'waiting'}
                <button class="btn" onclick={cancelPair}>Cancel</button>
                <button class="btn" onclick={copyPairLink} title="For a phone that cannot scan: send it this link">Copy link</button>
              {:else}
                <button class="btn primary" onclick={startPair}>New code</button>
                <button class="btn" onclick={() => (pair = null)}>Done</button>
              {/if}
            </div>
          </div>
        </div>
      {/if}
    </div>
    <div class="flex gap-2 mb-3"><input class="input" placeholder="Key name" bind:value={newKey} /><button class="btn primary" onclick={mk}>Create</button><button class="btn" onclick={mkPhone} title="Read and write, 90 days, tied to the first phone that uses it">Phone key (paste)</button></div>
    {#if created}<div class="panel p-3 mb-3 text-sm break-all" style="border-color:var(--ok)"><b>Copy now, shown once:</b> <button class="btn sm" onclick={copyKey}>Copy</button><br /><code>{created}</code>{#if createdNote}<div class="muted text-xs mt-2">{createdNote}</div>{/if}</div>{/if}
    <table class="grid"><tbody>
      {#each keys as k}<tr><td>{k.name}</td><td class="text-xs muted">ahk_{k.prefix}_…</td><td class="text-xs muted">{k.last_used_at ? 'used ' + ago(k.last_used_at) : 'never used'}{k.expires_at ? (k.expired ? ' · expired' : ' · expires ' + new Date(k.expires_at).toLocaleDateString()) : ''}{k.instance !== 'any' ? ' · ' + (k.instance === 'bound' ? 'tied to one device' : 'ties to first device') : ''}</td>
        <td>{#if k.revoked}<span class="chip">revoked</span>{:else}<button class="btn sm danger" onclick={() => revoke(k)}>Revoke</button>{/if}</td></tr>{/each}
    </tbody></table>
  </div>
</div>
{/if}

<style>
  .pair-qr { width: 15rem; max-width: 100%; background: #fff; border-radius: 10px; overflow: hidden; }
  .pair-qr :global(svg) { width: 100%; height: auto; display: block; shape-rendering: crispEdges; }
  .pair-qr.faded { opacity: 0.25; }
</style>
