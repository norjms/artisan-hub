<script>
  import { onMount } from 'svelte';
  import { api, ago } from '$lib/api.js';
  import { attempt } from '$lib/toast.svelte.js';
  let jobs = $state([]);
  let state = $state('');
  let open = $state(null);
  async function load() { jobs = await api.get('/api/jobs' + (state ? `?state=${state}` : '')); }
  onMount(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); });
  async function run(name, force = false) { await attempt(() => api.post(`/api/actions/${name}${force ? '?force=true' : ''}`), 'Queued'); load(); }
  async function retry(j) { await attempt(() => api.post(`/api/jobs/${j.id}/retry`), 'Retrying'); load(); }
  const actions = [['scan-library', 'Scan DeltaPrints library'], ['scan-inbox', 'Scan inbox'], ['refresh-parts', 'Refresh InvenTree'],
    ['refresh-spools', 'Refresh Spoolman'], ['sync-stock', 'Push stock to Woo'], ['poll-orders', 'Pull Woo orders'],
    ['manyfold-rescan', 'Rescan Manyfold'], ['manyfold-link', 'Link Manyfold models']];
</script>

<div class="flex items-end gap-3 mb-4">
  <div class="mr-auto"><h1 class="text-2xl font-bold">Sync health</h1><p class="muted text-sm">Every push, import and sync is a job. Failures retry automatically with backoff; you can retry them here.</p></div>
  <select class="input w-40" bind:value={state} onchange={load}><option value="">Recent</option><option value="failed">Failed</option><option value="queued">Queued</option><option value="running">Running</option><option value="done">Done</option></select>
</div>
<div class="panel p-4 mb-4 flex flex-wrap gap-2">
  {#each actions as [k, l]}<button class="btn sm" onclick={() => run(k)}>{l}</button>{/each}
  <button class="btn sm" onclick={() => run('scan-library', true)} title="Re-import every folder even if unchanged">Full re-import</button>
</div>
<div class="panel p-4 scroll-x">
  <table class="grid">
    <thead><tr><th>#</th><th>Job</th><th>Ref</th><th>State</th><th>Tries</th><th>When</th><th>By</th><th></th></tr></thead>
    <tbody>
      {#each jobs as j (j.id)}
        <tr class="cursor-pointer" onclick={() => (open = open === j.id ? null : j.id)}>
          <td class="muted text-xs">{j.id}</td><td class="font-medium">{j.kind}</td><td class="text-xs muted">{j.ref}</td>
          <td><span class="chip {j.state === 'failed' ? 'bad' : j.state === 'done' ? 'ok' : j.state === 'running' ? 'warn' : ''}">{j.state}</span></td>
          <td>{j.attempts}</td><td class="text-xs muted">{ago(j.finished_at || j.created_at)}</td><td class="text-xs muted">{j.created_by || ''}</td>
          <td>{#if j.state === 'failed'}<button class="btn sm" onclick={(e) => { e.stopPropagation(); retry(j); }}>Retry</button>{/if}</td>
        </tr>
        {#if open === j.id}
          <tr><td colspan="8"><pre class="text-xs whitespace-pre-wrap max-h-80 overflow-auto">{j.error || JSON.stringify(j.result, null, 2)}</pre></td></tr>
        {/if}
      {/each}
    </tbody>
  </table>
</div>
