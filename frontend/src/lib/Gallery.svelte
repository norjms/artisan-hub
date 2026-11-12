<script>
  let { images = [], archiveCount = 0, showArchive = $bindable(false) } = $props();
  let idx = $state(0);
  let full = $state(false);
  let strip;
  const cur = $derived(images[Math.min(idx, images.length - 1)]);

  function go(d) {
    if (!images.length) return;
    idx = (idx + d + images.length) % images.length;
    const el = strip?.children?.[idx];
    el?.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
  }
  function key(e) {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
    if (e.key === 'ArrowRight') go(1);
    else if (e.key === 'ArrowLeft') go(-1);
    else if (e.key === 'Escape') full = false;
  }
  $effect(() => { images; if (idx >= images.length) idx = 0; });
</script>

<svelte:window onkeydown={key} />

<div class="panel overflow-hidden">
  <div class="relative bg-ah-panel aspect-[4/5] sm:aspect-[5/4] lg:aspect-[4/3] select-none">
    {#if cur}
      <button class="w-full h-full" onclick={() => (full = true)} aria-label="Open full screen">
        <img src={cur.large} alt="" class="w-full h-full object-contain" />
      </button>
      {#if images.length > 1}
        <button class="absolute left-2 top-1/2 -translate-y-1/2 w-10 h-10 rounded-full bg-black/45 text-white text-xl hover:bg-black/70" onclick={() => go(-1)} aria-label="Previous">‹</button>
        <button class="absolute right-2 top-1/2 -translate-y-1/2 w-10 h-10 rounded-full bg-black/45 text-white text-xl hover:bg-black/70" onclick={() => go(1)} aria-label="Next">›</button>
      {/if}
      <div class="absolute bottom-2 right-3 chip">{idx + 1} / {images.length}{#if cur.role !== 'gallery'} · {cur.role}{/if}</div>
    {:else}
      <div class="w-full h-full grid place-items-center muted">No photos yet</div>
    {/if}
  </div>
  <div class="flex items-center gap-2 p-2 border-t line">
    <div bind:this={strip} class="flex gap-2 scroll-x flex-1 py-1">
      {#each images as im, i (im.id)}
        <button class="flex-none w-20 h-20 rounded-lg overflow-hidden border-2 {i === idx ? 'border-ah-accent' : 'border-transparent opacity-80 hover:opacity-100'}"
                onclick={() => (idx = i)}>
          <img src={im.thumb} alt="" loading="lazy" class="w-full h-full object-cover" />
        </button>
      {/each}
    </div>
    {#if archiveCount}
      <button class="btn sm flex-none" onclick={() => { showArchive = !showArchive; idx = 0; }}>
        {showArchive ? 'Hide' : 'Show'} {archiveCount} more
      </button>
    {/if}
  </div>
</div>

{#if full && cur}
  <div class="fixed inset-0 z-50 bg-black/95 flex flex-col" role="dialog">
    <div class="flex items-center justify-between p-3 text-white/80 text-sm">
      <span>{idx + 1} / {images.length} · {cur.filename} · {cur.width}×{cur.height}</span>
      <div class="flex gap-2">
        <a class="btn sm" href={cur.original} target="_blank">Original</a>
        <button class="btn sm" onclick={() => (full = false)}>Close</button>
      </div>
    </div>
    <div class="flex-1 relative min-h-0">
      <img src={cur.large} alt="" class="absolute inset-0 w-full h-full object-contain" />
      <button class="absolute left-0 inset-y-0 w-1/4" onclick={() => go(-1)} aria-label="Previous"></button>
      <button class="absolute right-0 inset-y-0 w-1/4" onclick={() => go(1)} aria-label="Next"></button>
    </div>
    <div class="flex gap-2 scroll-x p-3">
      {#each images as im, i (im.id)}
        <button class="flex-none w-16 h-16 rounded overflow-hidden border-2 {i === idx ? 'border-ah-accent' : 'border-transparent opacity-60'}" onclick={() => (idx = i)}>
          <img src={im.thumb} alt="" loading="lazy" class="w-full h-full object-cover" />
        </button>
      {/each}
    </div>
  </div>
{/if}
