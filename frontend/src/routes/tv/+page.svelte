<script>
  // Connect a TV: the Apple TV or Android TV app shows a short code; approving it here gives that TV a view-only key.
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import { attempt } from '$lib/toast.svelte.js';
  let code = $state('');
  let name = $state('');
  let done = $state(null);

  function tidy(v) {
    const c = (v || '').toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 8);
    return c.length > 4 ? `${c.slice(0, 4)}-${c.slice(4)}` : c;
  }
  // The TV's QR code opens this page with ?code=XXXX-XXXX filled in.
  onMount(() => { const c = new URLSearchParams(location.search).get('code'); if (c) code = tidy(c); });

  async function approve() {
    const r = await attempt(() => api.post('/api/mobile/v1/tv/approve', { code, name: name.trim() || null }), 'TV connected');
    if (r) { done = r; code = ''; name = ''; }
  }
</script>

<svelte:head><title>Connect a TV · Artisan Hub</title></svelte:head>

<div class="max-w-lg mx-auto p-4">
  <h1 class="text-2xl font-bold mb-1">Connect a TV</h1>
  <p class="muted text-sm mb-4">Open Artisan Hub on the Apple TV or Android TV. It shows a code; type it here. The TV gets
    its own view-only key: it shows printers, alerts, the queue and today's orders, and cannot change anything. You can
    disconnect it any time under Settings, API keys.</p>
  {#if done}
    <div class="panel p-3 mb-4 text-sm">Connected <b>{done.name || done.device}</b>. The TV shows the wall in a few seconds.</div>
  {/if}
  <div class="panel p-4 space-y-3">
    <label class="block text-sm"><span class="font-medium">Code on the TV</span>
      <input class="input w-full mt-1 text-2xl tracking-widest font-mono uppercase" placeholder="XXXX-XXXX" maxlength="9"
             autocomplete="off" value={code} oninput={(e) => { code = tidy(e.currentTarget.value); e.currentTarget.value = code; }} /></label>
    <label class="block text-sm"><span class="font-medium">Name (optional)</span>
      <input class="input w-full mt-1" placeholder="Print room TV" bind:value={name} maxlength="120" /></label>
    <button class="btn primary w-full" disabled={code.replace('-', '').length !== 8} onclick={approve}>Connect this TV</button>
  </div>
</div>
