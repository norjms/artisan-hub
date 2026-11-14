<script>
  import Page from '$lib/Page.svelte';
  import { onMount } from 'svelte';
  import { api, fmtMoney, ago } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  import { can } from '$lib/session.svelte.js';
  let orders = $state([]);
  let connected = $state([]);
  let shipping = $state(null);      // { order, company, number, url }
  const POLL = { woo: 'poll-orders', shopify: 'poll-shopify-orders', etsy: 'poll-etsy-orders' };
  const LABEL = { woo: 'WooCommerce', shopify: 'Shopify', etsy: 'Etsy' };
  async function load() { orders = await api.get('/api/orders'); }
  onMount(async () => {
    load();
    try { connected = (await api.get('/api/stores')).connected; } catch { connected = ['woo']; }
  });
  async function poll() {
    for (const c of connected) await api.post(`/api/actions/${POLL[c]}`);
    toast(`Checking ${connected.map((c) => LABEL[c]).join(', ')}…`);
    setTimeout(load, 4000);
  }
  async function ship() {
    const { order, ...tracking } = shipping;
    const r = await attempt(() => api.post(`/api/orders/${order.id}/ship`, tracking), `Marked shipped on ${order.store}`);
    if (r) { shipping = null; load(); }
  }
  const storeNames = $derived(connected.map((c) => LABEL[c]).join(', ') || 'your stores');
</script>

<Page title="Orders" description={`From ${storeNames} (webhooks and a 10 minute check). The farm receives them from the hub when the Farm overview says so. Stock is one pool across every store.`}>
{#snippet actions()}<button class="btn" onclick={poll} disabled={!connected.length}>Check stores now</button>{/snippet}
<section class="ah-glass ah-card ah-scroll" style="flex:1">
  <h2 class="ah-card-title">{orders.length} {orders.length === 1 ? 'order' : 'orders'}</h2>
  <table class="grid">
    <thead><tr><th>Order</th><th>Store</th><th>Placed</th><th>Customer</th><th>Status</th><th>Items</th><th class="text-right">Total</th><th>Printing</th><th>Parts</th><th></th></tr></thead>
    <tbody>
      {#each orders as o}
        <tr>
          <td>{#if o.store_url}<a class="font-medium underline" target="_blank" href={o.store_url}>#{o.number}</a>{:else}<span class="font-medium">#{o.number}</span>{/if}{#if o.is_test} <span class="chip warn">test</span>{/if}</td>
          <td class="text-sm">{o.store}</td>
          <td class="text-xs muted">{ago(o.placed_at)}</td>
          <td>{o.customer || ''}</td>
          <td><span class="chip {o.status === 'completed' ? 'ok' : o.status === 'processing' ? 'warn' : ''}">{o.status}</span></td>
          <td class="text-sm">{#each o.lines as l}<div>{#if l.model_id}<a class="hover:underline" href={`/models/${l.model_id}`}>{l.qty} x {l.name}</a>{:else}<span>{l.qty} x {l.name}</span> <span class="chip bad">Unknown SKU {l.sku || '(none)'}</span>{/if}</div>{/each}</td>
          <td class="text-right">{fmtMoney(o.total)}</td>
          <td class="text-sm">{#if o.print}{#if o.print.printed_at}<span class="chip ok">printed</span>{:else if o.print.jobs}{#each Object.entries(o.print.by_status) as [k, n]}<span class="chip {k === 'completed' ? 'ok' : k === 'failed' ? 'bad' : ''}">{n} {k.replace('_', ' ')}</span> {/each}{:else if o.print.errors.length}<span class="chip bad" title={o.print.errors.join('; ')}>not sent</span>{:else}<span class="muted text-xs">not sent</span>{/if}{#if o.print.errors.length && o.print.jobs}<div class="text-xs text-red-600">{o.print.errors.join('; ')}</div>{/if}{/if}</td>
          <td>{#if o.consumed}<span class="chip ok">deducted</span>{:else if o.status === 'completed'}<span class="chip warn">pending</span>{:else}<span class="chip">reserved</span>{/if}</td>
          <td>{#if can('store') && ['processing', 'on-hold'].includes(o.status)}<button class="btn sm" onclick={() => (shipping = { order: o, company: '', number: '', url: '' })}>Mark shipped</button>{/if}</td>
        </tr>
      {:else}<tr><td colspan="10" class="muted text-center py-6">No orders yet.</td></tr>{/each}
    </tbody>
  </table>
</section>
</Page>

{#if shipping}
  <div class="fixed inset-0 z-40 bg-black/40 flex items-center justify-center p-4" role="presentation" onclick={(e) => e.target === e.currentTarget && (shipping = null)}>
    <div class="panel p-5 w-full max-w-md" role="dialog" aria-modal="true" aria-label="Mark order shipped">
      <h3 class="font-semibold text-lg mb-1">Ship order #{shipping.order.number}</h3>
      <p class="text-sm muted mb-4">Marks it shipped on {shipping.order.store}{shipping.order.channel === 'woo' ? ' (completed)' : ''} and tells the customer. Tracking is optional, but Etsy asks for it on US orders over $10.</p>
      <form class="space-y-3" onsubmit={(e) => { e.preventDefault(); ship(); }}>
        <label class="block text-sm"><span class="font-medium">Carrier</span><input class="input w-full mt-1" bind:value={shipping.company} placeholder="USPS, UPS, FedEx…" /></label>
        <label class="block text-sm"><span class="font-medium">Tracking number</span><input class="input w-full mt-1" bind:value={shipping.number} /></label>
        {#if shipping.order.channel === 'shopify'}<label class="block text-sm"><span class="font-medium">Tracking link (optional)</span><input class="input w-full mt-1" bind:value={shipping.url} placeholder="https://" /></label>{/if}
        <div class="flex gap-2 justify-end pt-2">
          <button type="button" class="btn" onclick={() => (shipping = null)}>Cancel</button>
          <button type="submit" class="btn primary">Mark shipped</button>
        </div>
      </form>
    </div>
  </div>
{/if}
