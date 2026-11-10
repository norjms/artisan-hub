<script>
  // The farm's alarm feed: what every printer is reporting right now (OpenPrintHQ, capability "alarms").
  import { onMount } from 'svelte';
  import { api, ago } from '$lib/api.js';
  import { attempt } from '$lib/toast.svelte.js';
  import { can } from '$lib/session.svelte.js';

  let { onchange = () => {} } = $props();
  let rows = $state(null);
  let history = $state(false);
  let printer = $state('');
  let err = $state('');

  async function load() {
    err = '';
    try {
      const qs = new URLSearchParams({ state: history ? 'all' : 'active' });
      if (printer) qs.set('printer', printer);
      rows = (await api.get(`/api/farm/alarms?${qs}`)).alarms;
    } catch (e) { err = e.message || String(e); rows = []; }
  }
  onMount(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  });
  const printers = $derived([...new Set((rows || []).map((a) => a.printer_name).filter(Boolean))].sort());
  const sevCls = (s) => (s === 'fatal' || s === 'serious' ? 'bad' : 'warn');
  async function ack(a) {
    await attempt(() => api.post(`/api/farm/alarms/${a.id}/ack`), 'Acknowledged');
    await load(); onchange();
  }
  async function ackPrinter(name) {
    await attempt(() => api.post(`/api/farm/printers/${encodeURIComponent(name)}/alarms/ack`), `Alarms on ${name} acknowledged`);
    await load(); onchange();
  }
</script>

<div class="flex flex-wrap items-center gap-3 mb-3 text-sm">
  <label class="flex items-center gap-2"><input type="checkbox" bind:checked={history} onchange={load} /> Include cleared</label>
  <select class="input" style="width: 12rem" bind:value={printer} onchange={load}>
    <option value="">Every printer</option>
    {#each printers as n}<option>{n}</option>{/each}
  </select>
  {#if printer && can('farm')}<button class="btn sm" onclick={() => ackPrinter(printer)}>Acknowledge all on {printer}</button>{/if}
  <span class="muted text-xs ml-auto">An alarm clears by itself when the printer stops reporting it. Acknowledging marks it seen.</span>
</div>
{#if err}<div class="panel p-4 text-sm text-red-600">{err}</div>
{:else if rows === null}<div class="muted text-sm">Loading...</div>
{:else if !rows.length}<div class="panel p-6 text-center muted">{history ? 'No alarms recorded.' : 'No printer is reporting a problem.'}</div>
{:else}
  <div class="panel overflow-x-auto">
    <table class="w-full text-sm">
      <thead><tr><th>Severity</th><th>Printer</th><th>Alarm</th><th>Since</th><th></th></tr></thead>
      <tbody>
        {#each rows as a (a.id)}
          <tr class={a.active ? '' : 'muted'}>
            <td><span class="chip {a.active ? sevCls(a.severity) : ''}">{a.severity}</span></td>
            <td>{a.printer_name || `printer ${a.printer_id}`}</td>
            <td>{a.message}<div class="text-xs muted font-mono">{a.source} {a.code}</div></td>
            <td class="text-xs">{ago(a.first_seen)}{#if !a.active}<div class="muted">cleared {ago(a.cleared_at)}</div>{/if}</td>
            <td class="text-right">
              {#if a.acknowledged_at}<span class="text-xs muted">seen by {a.acknowledged_by || 'someone'}</span>
              {:else if can('farm')}<button class="btn sm" onclick={() => ack(a)}>Acknowledge</button>{/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{/if}
