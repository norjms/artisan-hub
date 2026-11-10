<script>
  let { onfiles, label = 'Drop files here or click to choose', accept = '', busy = false } = $props();
  let over = $state(false);
  let input;
  function drop(e) {
    e.preventDefault(); over = false;
    const fs = [...(e.dataTransfer?.files || [])];
    if (fs.length) onfiles(fs);
  }
</script>

<button type="button" class="w-full border-2 border-dashed rounded-xl p-6 text-center text-sm transition {over ? 'border-brand-500 bg-brand-50' : 'line'}"
  ondragover={(e) => { e.preventDefault(); over = true; }} ondragleave={() => (over = false)} ondrop={drop}
  onclick={() => input.click()} disabled={busy}>
  <span class="muted">{busy ? 'Uploading…' : label}</span>
  <input bind:this={input} type="file" multiple {accept} class="hidden" onchange={(e) => { const fs = [...e.target.files]; e.target.value = ''; if (fs.length) onfiles(fs); }} />
</button>
