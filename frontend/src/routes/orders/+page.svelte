<script>
  import { onMount } from 'svelte';
  import { api, fmtMoney, ago } from '$lib/api.js';
  let orders = $state([]);
  async function load() { orders = await api.get('/api/orders'); }
  onMount(load);
  async function poll() { await api.post('/api/actions/poll-orders'); setTimeout(load, 3000); }
</script>

<div class="flex items-end gap-3 mb-4">
  <div class="mr-auto"><h1 class="text-2xl font-bold">Orders</h1><p class="muted text-sm">From WooCommerce (webhook + 10 minute poll). Watchtower receives them through the shop bridge, or from the hub when the Farm page says so.</p></div>
  <button class="btn" onclick={poll}>Check Woo now</button>
</div>
<div class="panel p-4 scroll-x">
  <table class="grid">
    <thead><tr><th>Order</th><th>Placed</th><th>Customer</th><th>Status</th><th>Items</th><th class="text-right">Total</th><th>Printing</th><th>Parts</th></tr></thead>
    <tbody>
      {#each orders as o}
        <tr>
          <td><a class="font-medium underline" target="_blank" href={o.woo_url}>#{o.number}</a>{#if o.is_test} <span class="chip warn">test</span>{/if}</td>
          <td class="text-xs muted">{ago(o.placed_at)}</td>
          <td>{o.customer || ''}</td>
          <td><span class="chip {o.status === 'completed' ? 'ok' : o.status === 'processing' ? 'warn' : ''}">{o.status}</span></td>
          <td class="text-sm">{#each o.lines as l}<div>{#if l.model_id}<a class="hover:underline" href={`/models/${l.model_id}`}>{l.qty}× {l.name}</a>{:else}<span>{l.qty}× {l.name}</span> <span class="chip bad">unknown SKU {l.sku}</span>{/if}</div>{/each}</td>
          <td class="text-right">{fmtMoney(o.total)}</td>
          <td class="text-sm">{#if o.print}{#if o.print.printed_at}<span class="chip ok">printed</span>{:else if o.print.jobs}{#each Object.entries(o.print.by_status) as [k, n]}<span class="chip {k === 'completed' ? 'ok' : k === 'failed' ? 'bad' : ''}">{n} {k.replace('_', ' ')}</span> {/each}{:else if o.print.errors.length}<span class="chip bad" title={o.print.errors.join('; ')}>not sent</span>{:else}<span class="muted text-xs">not sent</span>{/if}{#if o.print.errors.length && o.print.jobs}<div class="text-xs text-red-600">{o.print.errors.join('; ')}</div>{/if}{/if}</td>
          <td>{#if o.consumed}<span class="chip ok">deducted</span>{:else if o.status === 'completed'}<span class="chip warn">pending</span>{:else}<span class="chip">reserved</span>{/if}</td>
        </tr>
      {:else}<tr><td colspan="8" class="muted text-center py-6">No orders yet.</td></tr>{/each}
    </tbody>
  </table>
</div>
