<script>
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  onMount(() => goto('/find?platform=makerworld', { replaceState: true }));
</script>
