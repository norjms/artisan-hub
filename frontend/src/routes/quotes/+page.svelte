<script>
  import { farmName, FarmName } from '$lib/nav.svelte.js';
  import Page from '$lib/Page.svelte';
  import { onMount } from 'svelte';
  import { api, fmtMoney, fmtTime, fmtBytes, ago } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  import { can } from '$lib/session.svelte.js';

  let view = $state('review');
  let d = $state(null);
  let cfg = $state(null);
  let store = $state(null);
  let refusing = $state(null);
  let reason = $state('');
  async function load() { d = await api.get('/api/quotes'); }
  async function loadCfg() { cfg = await api.get('/api/quotes/settings'); store = await api.get('/api/quotes/store'); }
  onMount(() => { load(); loadCfg(); });

  async function approve(q) {
    if (!confirm(`Approve and send ${q.quantity} x ${q.filename} to the farm?`)) return;
    await attempt(() => api.post(`/api/quotes/${q.id}/approve`), `Approved: slicing and sending to ${farmName()}`); load();
  }
  async function refuse(q) {
    await attempt(() => api.post(`/api/quotes/${q.id}/refuse`, { reason }), 'Refused: the customer gets a note; refund the order in WooCommerce');
    refusing = null; reason = ''; load();
  }
  async function save() {
    cfg = await attempt(() => api.put('/api/quotes/settings', cfg), 'Saved');
  }
  async function openStore(open) {
    const msg = open ? 'Open instant quotes on the store? This publishes the hidden "Custom 3D print" product so quoted prints can be bought. The quote page on the site still needs publishing in WordPress.'
                     : 'Close instant quotes? The product goes back to draft and new quotes are refused.';
    if (!confirm(msg)) return;
    store = await attempt(() => api.post('/api/quotes/store', { open }), open ? 'Instant quotes are open' : 'Instant quotes are closed');
    loadCfg();
  }
  const addMat = () => (cfg.materials = [...cfg.materials, { key: '', label: '', material: 'PLA', price_per_kg: 20, enabled: true, note: '', colours: [] }]);
  const stCls = { ordered: 'warn', approved: 'ok', refused: 'bad', failed: 'bad', ready: '', slicing: '', expired: '' };
</script>

<Page title="Instant quotes" description="Customers upload their own model on the store and get a price straight away. Paid quotes wait here for you before they print.">
{#snippet actions()}
{#if store}<span class="chip {store.product?.status === 'publish' && cfg?.enabled ? 'ok' : ''}">{store.product?.status === 'publish' && cfg?.enabled ? 'open on the store' : 'closed'}</span>{/if}
{/snippet}

<div class="ah-tabs" role="tablist">
  {#each [['review', `To review${d?.waiting.length ? ` (${d.waiting.length})` : ''}`], ['all', 'All quotes'], ['settings', 'Settings']] as [k, l]}
    <button role="tab" aria-selected={view === k} onclick={() => (view = k)}>{l}</button>
  {/each}
</div>

{#if !d}<p class="muted">Loading...</p>
{:else if view === 'review'}
  <div class="grid gap-4" style="grid-template-columns: repeat(auto-fill, minmax(320px, 1fr))">
    {#each d.waiting as q (q.id)}
      <div class="panel p-4 flex flex-col gap-2">
        {#if q.previews[0]}<img src={q.previews[0]} alt="Sliced preview" class="w-full rounded-lg" style="background: var(--panel-2); max-height: 220px; object-fit: contain" />{/if}
        <div class="font-semibold break-all">{q.filename}</div>
        <div class="text-sm">Order <b>#{q.order?.number}</b> {q.order?.customer || ''}</div>
        <div class="text-sm">{q.quantity} x {q.material} {q.colour || ''} <span class="muted">on {q.printer}</span></div>
        <div class="text-xs muted">{q.size_mm ? `${q.size_mm.join(' x ')} mm, ` : ''}{q.breakdown.grams} g each, {fmtTime(q.time_s)} each, {fmtBytes(q.bytes)}</div>
        <div class="text-sm">Charged <b>{fmtMoney(q.total)}</b> <span class="text-xs muted">(cost {fmtMoney((q.breakdown.material_cost || 0) + (q.breakdown.machine_cost || 0))} each)</span></div>
        <a class="text-xs underline" href={`/api/quotes/${q.id}/model`}>Download the customer's file</a>
        {#if can('farm')}
          {#if refusing === q.id}
            <textarea class="input" rows="3" placeholder="Why it can't be printed (the customer sees this)" bind:value={reason}></textarea>
            <div class="flex gap-2"><button class="btn danger" onclick={() => refuse(q)}>Refuse</button><button class="btn" onclick={() => (refusing = null)}>Cancel</button></div>
          {:else}
            <div class="flex gap-2"><button class="btn primary" onclick={() => approve(q)}>Approve and print</button><button class="btn danger" onclick={() => { refusing = q.id; reason = ''; }}>Refuse...</button></div>
          {/if}
        {/if}
      </div>
    {:else}<div class="panel p-6 muted text-center" style="grid-column: 1 / -1">No paid quotes waiting.</div>{/each}
  </div>
  <p class="text-xs muted mt-3">Approving slices the model again in the customer's material and colour, uploads it to {farmName()} and queues the pieces on their order. Refusing puts the order on hold with a note to the customer; refund it in WooCommerce.</p>

{:else if view === 'all'}
  <div class="panel p-4 scroll-x">
    <table class="grid">
      <thead><tr><th>Quote</th><th>Status</th><th>Model</th><th>Choice</th><th class="text-right">Total</th><th>Order</th><th>When</th></tr></thead>
      <tbody>
        {#each d.quotes as q}
          <tr>
            <td>#{q.id}</td>
            <td><span class="chip {stCls[q.status] || ''}">{q.status}</span>{#if q.error}<div class="text-xs muted">{q.error}</div>{/if}</td>
            <td class="text-sm break-all">{q.filename}<div class="text-xs muted">{q.size_mm ? q.size_mm.join(' x ') + ' mm' : ''} {q.printer || ''}</div></td>
            <td class="text-sm">{q.material ? `${q.quantity} x ${q.material} ${q.colour || ''}` : ''}</td>
            <td class="text-right">{fmtMoney(q.total)}</td>
            <td>{q.order ? `#${q.order.number}` : ''}</td>
            <td class="text-xs muted">{ago(q.created_at)}</td>
          </tr>
        {:else}<tr><td colspan="7" class="muted text-center py-6">No quotes yet.</td></tr>{/each}
      </tbody>
    </table>
  </div>

{:else if cfg}
  {@const edit = can('settings')}
  <div class="grid gap-4" style="grid-template-columns: repeat(auto-fit, minmax(320px, 1fr))">
    <div class="panel p-4 flex flex-col gap-3">
      <h2 class="font-semibold">Price</h2>
      <p class="text-sm muted">Order price = (filament + machine time) x markup x pieces + setup fee, never below the minimum. Filament uses each material's price per kg below; machine time uses the quote printer's hourly rate.</p>
      <div class="grid grid-cols-3 gap-3">
        <label><span class="lbl">Markup (x)</span><input class="input" type="number" step="0.1" min="1" disabled={!edit} bind:value={cfg.markup} /></label>
        <label><span class="lbl">Setup fee</span><input class="input" type="number" step="0.5" min="0" disabled={!edit} bind:value={cfg.setup_fee} /></label>
        <label><span class="lbl">Minimum order</span><input class="input" type="number" step="0.5" min="0" disabled={!edit} bind:value={cfg.minimum} /></label>
        <label><span class="lbl">Largest file (MB)</span><input class="input" type="number" min="1" max="200" disabled={!edit} bind:value={cfg.max_mb} /></label>
        <label><span class="lbl">Quote valid (days)</span><input class="input" type="number" min="1" max="60" disabled={!edit} bind:value={cfg.valid_days} /></label>
        <label><span class="lbl">Most pieces</span><input class="input" type="number" min="1" max="500" disabled={!edit} bind:value={cfg.max_quantity} /></label>
      </div>
    </div>
    <div class="panel p-4 flex flex-col gap-3">
      <h2 class="font-semibold">On the store</h2>
      {#if store?.product}
        <div class="text-sm">Product: <b>{store.product.name}</b> <span class="chip {store.product.status === 'publish' ? 'ok' : ''}">{store.product.status}</span> (hidden from the catalogue; bought only through a quote)</div>
      {:else}
        <div class="text-sm muted">The hidden "Custom 3D print" product is created as a draft the first time you open quotes.{#if store?.error} {store.error}{/if}</div>
      {/if}
      <p class="text-sm muted">The WordPress connector adds a draft page "Custom 3D prints" with the quote form ([artisan_quote] shortcode). Publish it in WordPress when you're ready.</p>
      {#if can('store')}
        <div class="flex gap-2">
          <button class="btn primary" disabled={cfg.enabled && store?.product?.status === 'publish'} onclick={() => openStore(true)}>Open on the store</button>
          <button class="btn" disabled={!cfg.enabled} onclick={() => openStore(false)}>Close</button>
        </div>
      {/if}
    </div>
  </div>
  <div class="panel p-4 mt-4">
    <div class="flex items-center mb-2"><h2 class="font-semibold mr-auto">Materials and colours offered</h2>{#if edit}<button class="btn sm" onclick={addMat}>Add a material</button>{/if}</div>
    {#each cfg.materials as m, i}
      <div class="border-t line py-3 flex flex-col gap-2">
        <div class="flex flex-wrap gap-2 items-end">
          <label style="width: 9rem"><span class="lbl">Name shown</span><input class="input" disabled={!edit} bind:value={m.label} /></label>
          <label style="width: 7rem"><span class="lbl">Material</span><input class="input" disabled={!edit} bind:value={m.material} /></label>
          <label style="width: 7rem"><span class="lbl">Price / kg</span><input class="input" type="number" step="0.5" disabled={!edit} bind:value={m.price_per_kg} /></label>
          <label class="flex-1" style="min-width: 12rem"><span class="lbl">What it's good for</span><input class="input" disabled={!edit} bind:value={m.note} /></label>
          <label class="text-sm flex items-center gap-2 pb-2"><input type="checkbox" disabled={!edit} bind:checked={m.enabled} /> Offered</label>
          {#if edit}<button class="btn sm danger" onclick={() => (cfg.materials = cfg.materials.filter((_, j) => j !== i))}>Remove</button>{/if}
        </div>
        <div class="flex flex-wrap gap-2 items-center">
          {#each m.colours as col, k}
            <span class="chip"><span class="swatch" style="background: {col.hex || 'transparent'}"></span>
              <input class="bg-transparent" style="width: 7rem" disabled={!edit} bind:value={col.name} />
              <input type="color" disabled={!edit} value={col.hex || '#000000'} oninput={(e) => (col.hex = e.target.value.toUpperCase())} />
              {#if edit}<button aria-label="Remove colour" onclick={() => (m.colours = m.colours.filter((_, j) => j !== k))}>x</button>{/if}</span>
          {/each}
          {#if edit}<button class="btn sm" onclick={() => (m.colours = [...m.colours, { name: 'New colour', hex: '#808080' }])}>Add colour</button>{/if}
        </div>
      </div>
    {/each}
    {#if edit}<div class="mt-3"><button class="btn primary" onclick={save}>Save</button></div>{/if}
    <p class="text-xs muted mt-2">The model is sliced once in the first offered material; other materials scale its grams by density. Colours tied to a Spoolman filament show as out of stock when there isn't enough left.</p>
  </div>
{/if}
</Page>
