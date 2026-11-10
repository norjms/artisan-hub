<script>
  // A small "Copy" button for a field: puts its text on the clipboard.
  import { toast } from '$lib/toast.svelte.js';
  let { text = '', label = 'Copy' } = $props();
  let done = $state(false);
  async function copy() {
    const v = text == null ? '' : String(text);
    try {
      await navigator.clipboard.writeText(v);
    } catch {
      const t = document.createElement('textarea');
      t.value = v;
      document.body.appendChild(t);
      t.select();
      document.execCommand('copy');
      t.remove();
    }
    done = true;
    toast?.('Copied');
    setTimeout(() => (done = false), 1200);
  }
</script>

<button type="button" class="text-xs muted hover:underline disabled:opacity-40" onclick={copy} disabled={!text} title="Copy to clipboard">
  {done ? 'Copied' : label}
</button>
