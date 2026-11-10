<script>
  // Checkboxes for team permissions. "view" is always on. Permissions the editor doesn't hold are shown but locked.
  let { catalog, value = $bindable([]), allowed = [], disabled = false } = $props();
  function toggle(k, on) {
    const set = new Set(value);
    on ? set.add(k) : set.delete(k);
    set.add('view');
    value = Object.keys(catalog).filter((x) => set.has(x));
  }
</script>

<div class="grid gap-1.5 sm:grid-cols-2">
  {#each Object.entries(catalog) as [k, label]}
    {@const locked = disabled || k === 'view' || !allowed.includes(k)}
    <label class="flex items-start gap-2 text-sm {locked && !disabled ? 'opacity-60' : ''}" title={!allowed.includes(k) ? "You don't have this permission, so you can't give it" : ''}>
      <input type="checkbox" class="mt-0.5" checked={value.includes(k) || k === 'view'} disabled={locked} onchange={(e) => toggle(k, e.currentTarget.checked)} />
      <span>{label}</span>
    </label>
  {/each}
</div>
