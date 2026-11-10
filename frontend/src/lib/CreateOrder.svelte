<script>
  import { onMount } from 'svelte';
  import { api, fmtMoney } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';

  // "Print me one of these": a WooCommerce order for this model, with Woo's invoice and pay link (services/manual_orders.py).
  let { model, onclose } = $props();
  let f = $state({ first_name: '', last_name: '', email: '', phone: '', quantity: 1, total: '', fulfilment: 'pickup',
                   shipping_cost: 0, address_1: '', city: '', state: '', postcode: '', country: 'US', due_date: '',
                   note: '', send_invoice: true, print: 'paid' });
  let price = $state(null);
  let edited = $state(false);    // the person typed a total: stop following the suggestion
  let busy = $state(false);
  let done = $state(null);

  async function suggest() {
    price = await api.get(`/api/models/${model.id}/order-price?quantity=${Math.max(1, Number(f.quantity) || 1)}`).catch(() => null);
    if (!edited && price?.total != null) f.total = price.total;
  }
  onMount(suggest);

  async function submit() {
    busy = true;
    try {
      const body = {
        customer: { first_name: f.first_name, last_name: f.last_name, email: f.email, phone: f.phone },
        address: f.fulfilment === 'ship' ? { address_1: f.address_1, city: f.city, state: f.state, postcode: f.postcode, country: f.country } : null,
        quantity: Number(f.quantity) || 1, total: Number(f.total), fulfilment: f.fulfilment,
        shipping_cost: f.fulfilment === 'ship' ? Number(f.shipping_cost) || 0 : 0,
        due_date: f.due_date || null, note: f.note || null, send_invoice: f.send_invoice, print_now: f.print === 'now'
      };
      done = await attempt(() => api.post(`/api/models/${model.id}/order`, body));
      if (done) toast(`Order #${done.number} created${done.invoice_sent ? ', invoice sent' : ''}`, 'ok', 7000);
    } finally { busy = false; }
  }
  async function resend() {
    await attempt(() => api.post(`/api/orders/${done.order_id}/send-invoice`), 'Invoice sent');
  }
  async function copy() {
    try { await navigator.clipboard.writeText(done.payment_url); toast('Payment link copied'); } catch { toast(done.payment_url); }
  }
</script>

<div class="fixed inset-0 bg-black/40 z-40 grid place-items-center p-4" role="presentation" onclick={(e) => { if (e.target === e.currentTarget) onclose(); }}>
  <div class="panel p-5 w-full max-w-xl max-h-[92vh] overflow-auto text-sm flex flex-col gap-3">
    <h3 class="font-semibold text-lg">Create order: {model.name}</h3>
    {#if !done}
      <div class="grid grid-cols-2 gap-2">
        <input class="input" placeholder="First name" bind:value={f.first_name} />
        <input class="input" placeholder="Last name" bind:value={f.last_name} />
        <input class="input" type="email" placeholder="Email (for the invoice)" bind:value={f.email} />
        <input class="input" placeholder="Phone (optional)" bind:value={f.phone} />
      </div>
      <div class="grid grid-cols-2 gap-2 items-end">
        <label>Quantity <input class="input" type="number" min="1" bind:value={f.quantity} onchange={suggest} /></label>
        <label>Order total ({price?.currency || 'USD'}) <input class="input" type="number" min="0" step="0.01" bind:value={f.total} oninput={() => (edited = true)} /></label>
      </div>
      {#if price}<div class="text-xs muted">{price.source === 'none' ? price.detail : `Suggested ${fmtMoney(price.total)} from the ${price.source === 'store' ? 'store price' : 'slice'}: ${price.detail}`}
        {#if edited && price.total != null}<button class="underline ml-1" onclick={() => { edited = false; f.total = price.total; }}>use the suggestion</button>{/if}</div>{/if}
      <div class="flex gap-4">
        <label class="flex items-center gap-2"><input type="radio" bind:group={f.fulfilment} value="pickup" /> Pickup</label>
        <label class="flex items-center gap-2"><input type="radio" bind:group={f.fulfilment} value="ship" /> Ship</label>
      </div>
      {#if f.fulfilment === 'ship'}
        <div class="grid grid-cols-2 gap-2">
          <input class="input col-span-2" placeholder="Street address" bind:value={f.address_1} />
          <input class="input" placeholder="City" bind:value={f.city} />
          <input class="input" placeholder="State" bind:value={f.state} />
          <input class="input" placeholder="ZIP" bind:value={f.postcode} />
          <input class="input" placeholder="Country (US)" bind:value={f.country} />
          <label class="col-span-2">Shipping charged <input class="input" type="number" min="0" step="0.01" bind:value={f.shipping_cost} /></label>
        </div>
      {/if}
      <label>Due date (optional) <input class="input" type="date" bind:value={f.due_date} /></label>
      <input class="input" placeholder="Note (shows on the order)" bind:value={f.note} />
      <label class="flex items-center gap-2"><input type="checkbox" bind:checked={f.send_invoice} /> Email the invoice now (Woo's invoice, with a Pay link)</label>
      <div class="flex flex-col gap-1">
        <span class="text-xs muted font-semibold">Send it to the farm</span>
        <label class="flex items-center gap-2"><input type="radio" bind:group={f.print} value="paid" /> When it's paid</label>
        <label class="flex items-center gap-2"><input type="radio" bind:group={f.print} value="now" /> Now, before payment</label>
      </div>
      {#if !model.product}<div class="text-xs text-amber-600">This model has no product SKU yet, so the farm can't be sent its file automatically.</div>{/if}
      <div class="flex gap-2 justify-end">
        <button class="btn" onclick={onclose}>Cancel</button>
        <button class="btn primary" disabled={busy || f.total === '' || (!f.first_name && !f.last_name)} onclick={submit}>Create order</button>
      </div>
    {:else}
      <div>Order <b>#{done.number}</b> created in WooCommerce for {fmtMoney(Number(done.total))}.</div>
      {#if done.invoice_sent}<div class="text-sm">The invoice was emailed to {f.email}.</div>{/if}
      {#if done.invoice_error}<div class="text-sm text-red-600">The invoice wasn't sent: {done.invoice_error}</div>{/if}
      {#if done.print}
        {#if done.print.skipped}<div class="text-sm text-amber-600">Not sent to the farm: {done.print.skipped}</div>
        {:else}<div class="text-sm">Sent to the farm: {done.print.jobs} job(s).{#if done.print.problems?.length} <span class="text-amber-600">{done.print.problems.join('; ')}</span>{/if}</div>{/if}
      {:else}<div class="text-sm muted">It goes to the farm when it's paid.</div>{/if}
      <div class="flex flex-wrap gap-2">
        {#if done.payment_url}<button class="btn" onclick={copy}>Copy payment link</button>{/if}
        {#if f.email}<button class="btn" onclick={resend}>{done.invoice_sent ? 'Send the invoice again' : 'Send the invoice'}</button>{/if}
        <a class="btn" href="/orders">Orders</a>
        <button class="btn primary ml-auto" onclick={onclose}>Done</button>
      </div>
    {/if}
  </div>
</div>
