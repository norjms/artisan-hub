<script>
  import Glyph from '$lib/Glyph.svelte';
  import { api } from '$lib/api.js';
  import { attempt } from '$lib/toast.svelte.js';
  import Dropzone from '$lib/Dropzone.svelte';
  let { m, onsaved } = $props();
  let busy = $state(false);
  let list = $state([...m.images_all]);
  async function upload(files) {
    busy = true;
    try { await attempt(() => api.upload(`/api/models/${m.id}/images`, files, { role: 'gallery' }), 'Photos added, sending to the DAM'); onsaved?.(); }
    finally { busy = false; }
  }
  async function patch(im, body) {
    await attempt(() => api.patch(`/api/images/${im.id}`, body));
    onsaved?.();
  }
  async function move(i, d) {
    const j = i + d;
    if (j < 0 || j >= list.length) return;
    [list[i], list[j]] = [list[j], list[i]];
    await api.post(`/api/models/${m.id}/images/order`, list.map((x) => x.id));
  }
  async function remove(im) {
    if (!confirm('Remove this photo from the hub? The DAM copy is kept.')) return;
    await attempt(() => api.del(`/api/images/${im.id}`), 'Removed');
    list = list.filter((x) => x.id !== im.id);
    onsaved?.();
  }
</script>

<div class="panel p-5">
  <div class="mb-4"><Dropzone onfiles={upload} {busy} accept="image/*" label="Drop product photos (JPG, PNG, HEIC)" /></div>
  <p class="text-sm muted mb-3">Hero is the store's main image. Gallery photos with "store" on go to WooCommerce (first 12). Archive photos stay in the hub and DAM only.</p>
  <div class="grid gap-3" style="grid-template-columns: repeat(auto-fill, minmax(180px, 1fr))">
    {#each list as im, i (im.id)}
      <div class="border line rounded-xl overflow-hidden">
        <div class="relative aspect-square bg-ah-panel">
          <img src={im.thumb} alt="" loading="lazy" class="w-full h-full object-cover" />
          {#if im.role === 'hero'}<span class="chip live absolute top-2 left-2">HERO</span>{/if}
          {#if im.dam_ref}<span class="chip absolute top-2 right-2" title="ResourceSpace #{im.dam_ref}">DAM</span>{:else if im.dam_error}<span class="chip bad absolute top-2 right-2" title={im.dam_error}>DAM error</span>{/if}
        </div>
        <div class="p-2 space-y-1.5 text-xs">
          <select class="input py-1 text-xs" value={im.role} onchange={(e) => patch(im, { role: e.target.value })}>
            <option value="hero">Hero</option><option value="gallery">Gallery</option><option value="detail">Detail</option>
            <option value="cutout">Cutout</option><option value="archive">Archive</option>
          </select>
          <div class="flex items-center gap-1">
            <label class="flex items-center gap-1 mr-auto"><input type="checkbox" checked={im.for_web} onchange={(e) => patch(im, { for_web: e.target.checked })} /> store</label>
            <button class="btn sm px-2" onclick={() => move(i, -1)} aria-label="Earlier"><Glyph name="arrow-left" /></button>
            <button class="btn sm px-2" onclick={() => move(i, 1)} aria-label="Later"><Glyph name="arrow-right" /></button>
            <button class="btn sm px-2 danger" onclick={() => remove(im)} aria-label="Remove"><Glyph name="x" /></button>
          </div>
          <div class="muted truncate" title={im.filename}>{im.width}×{im.height}</div>
        </div>
      </div>
    {/each}
  </div>
</div>
