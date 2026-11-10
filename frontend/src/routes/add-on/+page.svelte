<script>
  import { onMount } from 'svelte';
  import { api, ago } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  const origin = typeof location !== 'undefined' ? location.origin : '';
  let keys = $state([]);
  let sites = $state([]);
  let created = $state('');
  let name = $state('Browser add-on');
  let err = $state('');
  async function load() {
    try { keys = (await api.get('/api/clip/v1/keys')).keys; err = ''; } catch (e) { keys = []; err = e.message || String(e); }
    try { sites = (await api.get('/api/clip/v1/sites')).sites; } catch { sites = []; }
  }
  async function make() { const r = await attempt(() => api.post('/api/clip/v1/keys', { name: name || 'Browser add-on' })); created = r.key; load(); }
  async function revoke(k) { if (confirm(`Revoke ${k.name}? The add-on using it stops working.`)) { await api.del(`/api/clip/v1/keys/${k.id}`); load(); } }
  async function copy(t) { try { await navigator.clipboard.writeText(t); toast('Copied'); } catch { toast('Select it and copy it'); } }
  onMount(load);
</script>

<h1 class="text-2xl font-bold mb-1">Browser add-on</h1>
<p class="muted text-sm mb-5 max-w-3xl">The Artisan Hub add-on for Firefox, Chrome and Edge puts <b>Add to hub</b> on model pages and in the toolbar. For sites that only let a signed-in browser download (Thangs, MyMiniFactory, Cults3D, paid models anywhere), it sends the files from your browser too. Licenses and the designer's credit always come with the model.</p>

<div class="grid lg:grid-cols-2 gap-5">
  <div class="panel p-5">
    <h3 class="font-semibold mb-2">1. Install it</h3>
    <ul class="text-sm list-disc pl-5 space-y-1">
      <li><b>Firefox:</b> the signed file from the hub team (<code>artisan-hub-clipper-firefox.xpi</code>), or about:debugging &gt; This Firefox &gt; Load Temporary Add-on.</li>
      <li><b>Chrome and Edge:</b> unzip <code>artisan-hub-clipper-chromium.zip</code>, open chrome://extensions (edge://extensions), turn on Developer mode and choose Load unpacked.</li>
      <li>Store listings come later.</li>
    </ul>
    <h3 class="font-semibold mt-4 mb-2">2. Make your key</h3>
    {#if err}<p class="text-sm" style="color:var(--bad)">{err}</p>{/if}
    <div class="flex gap-2 mb-3"><input class="input" placeholder="Name (Firefox on the desk PC)" bind:value={name} /><button class="btn primary" onclick={make}>Make add-on key</button></div>
    {#if created}
      <div class="panel p-3 mb-3 text-sm break-all" style="border-color:var(--ok)">
        <b>Copy now, shown once:</b> <button class="btn sm" onclick={() => copy(created)}>Copy key</button><br /><code>{created}</code>
        <div class="mt-2">Hub address: <code>{origin}</code> <button class="btn sm" onclick={() => copy(origin)}>Copy</button></div>
      </div>
    {/if}
    <h3 class="font-semibold mt-4 mb-2">3. Paste both into the add-on</h3>
    <p class="text-sm muted">Open the add-on's settings (right-click its toolbar button, Options / Extension options), paste the hub address and the key, and press Test. The add-on asks to reach this hub's address once.</p>
    <p class="text-sm muted mt-2">The key is yours: it can only add models by link and send their files, it works for a year, and it stops as soon as you lose the right to add models or leave the team.</p>
  </div>
  <div class="panel p-5">
    <h3 class="font-semibold mb-2">Your add-on keys</h3>
    {#if keys.length}<table class="grid"><tbody>
      {#each keys as k}<tr><td>{k.name}</td><td class="text-xs muted">ahk_{k.prefix}_…</td><td class="text-xs muted">{k.last_used_at ? 'used ' + ago(k.last_used_at) : 'never used'}{k.expires_at ? ' · expires ' + new Date(k.expires_at).toLocaleDateString() : ''}</td>
        <td><button class="btn sm danger" onclick={() => revoke(k)}>Revoke</button></td></tr>{/each}
    </tbody></table>{:else}<p class="text-sm muted">None yet.</p>{/if}
    <h3 class="font-semibold mt-5 mb-2">What works where</h3>
    <table class="grid text-sm"><tbody>
      {#each sites as s}<tr><td class="font-medium">{s.label}</td><td>{s.files === 'hub' ? 'Hub downloads files' : 'Files from your browser'}</td><td class="text-xs muted">{s.needs || ''}{s.needs && s.notes ? '. ' : ''}{s.notes}</td></tr>{/each}
    </tbody></table>
  </div>
</div>
