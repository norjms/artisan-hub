<script>
  import '../app.css';
  import { page } from '$app/state';
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import { toasts } from '$lib/toast.svelte.js';
  import { session, loadMe, switchTeam, ROLE_LABEL } from '$lib/session.svelte.js';
  import { theme, initTheme, toggleMode } from '$lib/theme.svelte.js';

  let { children } = $props();
  const me = $derived(session.me);
  const bare = $derived(page.url.pathname.startsWith('/invite/') || page.url.pathname === '/setup');
  let stats = $state(null);
  let menu = $state(false);
  let newTeam = $state('');
  let userMenu = $state(false);

  const nav = [
    { href: '/', label: 'Models', icon: 'M4 5h16v14H4z M4 15l4-4 4 4 3-3 5 5' },
    { href: '/new', label: 'Add model', icon: 'M12 5v14 M5 12h14' },
    { href: '/find', label: 'Find models', icon: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M3 12h18 M12 3a14 14 0 0 1 0 18 M12 3a14 14 0 0 0 0 18' },
    { href: '/printers', label: 'Printers', icon: 'M6 9V3h12v6 M6 18H4a1 1 0 0 1-1-1v-6a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v6a1 1 0 0 1-1 1h-2 M6 14h12v7H6z' },
    { href: '/farm', label: 'Farm', icon: 'M3 21h18 M5 21V8l7-5 7 5v13 M9 21v-6h6v6' },
    { href: '/quotes', label: 'Quotes', icon: 'M4 4h16v12H5.2L4 17.2z M8 8h8 M8 12h5' },
    { href: '/reports', label: 'Reports', icon: 'M4 20V10 M10 20V4 M16 20v-7 M22 20H2' },
    { href: '/slicing', label: 'Slicing', icon: 'M12 3 3 8l9 5 9-5z M3 12l9 5 9-5 M3 16l9 5 9-5' },
    { href: '/licenses', label: 'Licenses', icon: 'M7 3h7l5 5v13H7z M14 3v5h5 M10 13h6 M10 17h6' },
    { href: '/inventory', label: 'Inventory', icon: 'M3 7l9-4 9 4-9 4z M3 7v10l9 4 9-4V7 M12 11v10' },
    { href: '/planner', label: 'Shopping list', icon: 'M6 6h15l-2 9H8z M6 6 5 3H2 M9 20a1 1 0 1 0 0-2 1 1 0 0 0 0 2 M18 20a1 1 0 1 0 0-2 1 1 0 0 0 0 2' },
    { href: '/orders', label: 'Orders', icon: 'M5 4h14v16H5z M9 8h6 M9 12h6 M9 16h4' },
    { href: '/sync', label: 'Sync health', icon: 'M4 12a8 8 0 0 1 14-5l2-2v6h-6l2-2a5 5 0 0 0-9 3 M20 12a8 8 0 0 1-14 5l-2 2v-6h6l-2 2a5 5 0 0 0 9-3' },
    { href: '/team', label: 'Team', icon: 'M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M2 21v-1a6 6 0 0 1 12 0v1 M16 3.5a4 4 0 0 1 0 7 M22 21v-1a6 6 0 0 0-4-5.6' },
    { href: '/connections', label: 'Connections', icon: 'M9 15l6-6 M11 6l1-1a4 4 0 0 1 6 6l-1 1 M13 18l-1 1a4 4 0 0 1-6-6l1-1', perm: 'connections' },
    { href: '/add-on', label: 'Browser add-on', icon: 'M10 3h4v3a2 2 0 1 0 4 0V3h3v7h-3a2 2 0 1 0 0 4h3v7h-7v-3a2 2 0 1 0-4 0v3H3v-7h3a2 2 0 1 0 0-4H3V3z' },
    { href: '/settings', label: 'Settings', icon: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6 M19 12l2-1-2-4-2 1-2-2V4h-4v2L9 7 7 6 5 10l2 1v2l-2 1 2 4 2-1 2 2v2h4v-2l2-2 2 1 2-4-2-1z' }
  ];

  async function refresh() {
    if (bare) return;
    try { stats = await api.get('/api/stats'); } catch {}
  }
  onMount(async () => {
    initTheme();
    if (!bare) { await loadMe(); initTheme(session.me?.prefs); }
    refresh();
    const t = setInterval(refresh, 30000);
    return () => clearInterval(t);
  });
  const active = (href) => (href === '/' ? page.url.pathname === '/' || page.url.pathname.startsWith('/models') : page.url.pathname.startsWith(href));
</script>

{#if bare}
  <main class="p-4 md:p-6">{@render children()}</main>
{:else if session.noTeam}
  <main class="min-h-[80vh] grid place-items-center p-4">
    <div class="panel p-6 w-full max-w-md">
      <h1 class="text-xl font-semibold mb-2">You're not in a team yet</h1>
      <p class="muted mb-4">Open the invite link you were sent to join a team. If you think you should have access, ask the person who invited you.</p>
      <a class="text-sm muted hover:underline mt-4 inline-block" href="/auth/logout">Sign out</a>
    </div>
  </main>
{:else if theme.skin === 'legacy_farm'}
<div class="min-h-screen">
  <div class="wt-top">
    <div class="wt-header">
      <div class="flex items-center gap-2 flex-1 min-w-0">
        <button class="wt-icon-btn" onclick={toggleMode} aria-label="Toggle dark mode" title={theme.dark ? 'Light mode' : 'Dark mode'}>
          {#if theme.dark}
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"/></svg>
          {:else}
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/></svg>
          {/if}
        </button>
        {#if me?.teams?.length > 1}
          <label class="sr-only" for="team-switch-top">Team</label>
          <select id="team-switch-top" class="input text-sm" style="width: auto; max-width: 12rem; padding: .3rem .5rem" value={me.team.id} onchange={(e) => switchTeam(+e.currentTarget.value)}>
            {#each me.teams as t}<option value={t.id}>{t.name}</option>{/each}
          </select>
        {/if}
      </div>
      <a href="/" class="wt-brand">
        <img src="/favicon.svg" alt="" />
        <span class="wt-brand-title">Artisan Hub</span>
        {#if me?.team?.name}<span class="wt-brand-tag hidden sm:inline">{me.team.name}</span>{/if}
      </a>
      <div class="flex items-center justify-end gap-2 flex-1 min-w-0">
        {#if me?.user}
          <div class="relative">
            <button class="wt-icon-btn" style="width: auto; padding: 0 .6rem; gap: .4rem" onclick={() => (userMenu = !userMenu)} aria-label="Account menu">
              <span class="inline-flex items-center justify-center w-6 h-6 rounded-full text-xs font-bold" style="background: var(--accent); color: var(--accent-text)">{(me.user.name || me.user.username || '?').slice(0, 1).toUpperCase()}</span>
              <span class="hidden md:inline text-sm truncate" style="max-width: 10rem">{me.user.name || me.user.username}</span>
            </button>
            {#if userMenu}
              <div class="panel wt-menu" role="menu">
                <div class="px-2 py-1 text-xs muted">{ROLE_LABEL[me.team?.role] || 'API key'} in {me.team?.name}</div>
                {#if me?.user?.platform_admin}<a href="/platform" onclick={() => (userMenu = false)}>People &amp; teams</a>{/if}
                <a href="/settings#appearance" onclick={() => (userMenu = false)}>Appearance</a>
                {#if me?.links && Object.values(me.links).some(Boolean)}
                  <div class="px-2 pt-2 pb-1 text-[11px] uppercase tracking-wider muted font-semibold">Systems</div>
                  {#each [['woo', 'WooCommerce'], ['legacy_farm', 'Legacy farm'], ['dam', 'DAM'], ['manyfold', 'Manyfold'], ['inventree', 'InvenTree'], ['spoolman', 'Spoolman']] as [k, l]}
                    {#if me.links[k]}<a target="_blank" rel="noopener" href={me.links[k]}>{l}</a>{/if}
                  {/each}
                {/if}
                <div class="border-t line mt-1 pt-1"><a href="/auth/logout">Sign out</a></div>
              </div>
            {/if}
          </div>
        {/if}
      </div>
    </div>
    <nav class="wt-tabs" aria-label="Sections">
      {#each nav.filter((n) => !n.perm || me?.perms?.includes(n.perm)) as n}
        <a href={n.href} class="wt-tab {active(n.href) ? 'active' : ''}" aria-current={active(n.href) ? 'page' : undefined}>
          {n.label}
          {#if n.href === '/farm' && stats?.plate_checks}<span class="wt-badge warn">{stats.plate_checks}</span>{/if}
          {#if n.href === '/quotes' && stats?.quotes_waiting}<span class="wt-badge warn">{stats.quotes_waiting}</span>{/if}
          {#if n.href === '/sync' && stats?.jobs_failed}<span class="wt-badge">{stats.jobs_failed}</span>{/if}
          {#if n.href === '/licenses' && stats?.licenses_attention}<span class="wt-badge warn">{stats.licenses_attention}</span>{/if}
          {#if n.href === '/orders' && stats?.open_orders}<span class="wt-badge" style="background: var(--accent); color: var(--accent-text)">{stats.open_orders}</span>{/if}
          {#if n.href === '/planner' && stats?.live_out_of_supplies?.length}<span class="wt-badge">{stats.live_out_of_supplies.length}</span>{/if}
        </a>
      {/each}
    </nav>
  </div>
  <main class="min-w-0 p-4 md:p-6 max-w-[1800px] mx-auto" onclick={() => (userMenu = false)} role="presentation">
    {#if me?.team?.role === 'viewer'}
      <div class="panel px-4 py-2 mb-4 text-sm muted">You have view access to {me.team.name}. Ask an admin if you need to make changes.</div>
    {/if}
    {@render children()}
  </main>
</div>
{:else}
<div class="min-h-screen md:flex">
  <aside class="ophq-side md:w-60 md:fixed md:inset-y-0 md:flex md:flex-col border-b md:border-b-0 md:border-r line" style={theme.skin === 'openprinthq' ? '' : 'background:var(--panel)'}>
    <div class="flex items-center justify-between px-4 h-14">
      <a href="/" class="flex items-center gap-2 text-lg {theme.skin === 'openprinthq' ? 'ophq-wm' : 'font-bold'}">
        <img src="/favicon.svg" alt="" class="w-7 h-7" />
        {#if theme.skin === 'openprinthq'}<span>Artisan<b>Hub</b></span>{:else}Artisan Hub{/if}
      </a>
      <button class="md:hidden btn sm" onclick={() => (menu = !menu)} aria-label="Menu">☰</button>
    </div>
    {#if me?.team}
      <div class="px-4 pb-3">
        {#if me.teams?.length > 1}
          <label class="sr-only" for="team-switch">Team</label>
          <select id="team-switch" class="input w-full text-sm" value={me.team.id} onchange={(e) => switchTeam(+e.currentTarget.value)}>
            {#each me.teams as t}<option value={t.id}>{t.name}</option>{/each}
          </select>
        {:else}
          <div class="text-sm font-semibold truncate">{me.team.name}</div>
        {/if}
        <div class="text-xs muted mt-1">{ROLE_LABEL[me.team.role] || 'API key'}{me.team.role === 'viewer' ? ' · read only' : ''}</div>
      </div>
    {/if}
    <nav class="{menu ? 'block' : 'hidden'} md:block px-2 pb-3 md:flex-1">
      {#each nav.filter((n) => !n.perm || me?.perms?.includes(n.perm)) as n}
        <a href={n.href} onclick={() => (menu = false)}
          class="navlink flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium mb-0.5 {active(n.href) ? (theme.skin === 'openprinthq' ? 'active' : 'active bg-brand-600 text-white') : 'hover:bg-black/5 dark:hover:bg-white/5'}">
          <svg viewBox="0 0 24 24" class="w-4.5 h-4.5" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d={n.icon} /></svg>
          <span class="flex-1">{n.label}</span>
          {#if n.href === '/farm' && stats?.plate_checks}<span class="chip warn">{stats.plate_checks}</span>{/if}
          {#if n.href === '/quotes' && stats?.quotes_waiting}<span class="chip warn">{stats.quotes_waiting}</span>{/if}
          {#if n.href === '/sync' && stats?.jobs_failed}<span class="chip bad">{stats.jobs_failed}</span>{/if}
          {#if n.href === '/licenses' && stats?.licenses_attention}<span class="chip warn">{stats.licenses_attention}</span>{/if}
          {#if n.href === '/orders' && stats?.open_orders}<span class="chip">{stats.open_orders}</span>{/if}
          {#if n.href === '/planner' && stats?.live_out_of_supplies?.length}<span class="chip bad">{stats.live_out_of_supplies.length}</span>{/if}
        </a>
      {/each}
      {#if me?.user?.platform_admin}
        <a href="/platform" onclick={() => (menu = false)} class="flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium mb-0.5 {active('/platform') ? 'bg-brand-600 text-white' : 'hover:bg-black/5 dark:hover:bg-white/5'}">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h16 M4 12h16 M4 18h16 M8 6v12" /></svg>
          <span class="flex-1">People &amp; teams</span>
        </a>
      {/if}
      {#if me?.links && Object.values(me.links).some(Boolean)}
        <div class="mt-6 px-3 text-[11px] uppercase tracking-wider muted font-semibold">Systems</div>
        <div class="px-3 mt-1 grid grid-cols-2 gap-x-2 gap-y-1 text-sm">
          {#if me.links.woo}<a class="hover:underline" target="_blank" rel="noopener" href={me.links.woo}>WooCommerce</a>{/if}
          {#if me.links.legacy_farm}<a class="hover:underline" target="_blank" rel="noopener" href={me.links.legacy_farm}>Legacy farm</a>{/if}
          {#if me.links.dam}<a class="hover:underline" target="_blank" rel="noopener" href={me.links.dam}>DAM</a>{/if}
          {#if me.links.manyfold}<a class="hover:underline" target="_blank" rel="noopener" href={me.links.manyfold}>Manyfold</a>{/if}
          {#if me.links.inventree}<a class="hover:underline" target="_blank" rel="noopener" href={me.links.inventree}>InvenTree</a>{/if}
          {#if me.links.spoolman}<a class="hover:underline" target="_blank" rel="noopener" href={me.links.spoolman}>Spoolman</a>{/if}
        </div>
      {/if}
    </nav>
    {#if me?.user}
      <div class="hidden md:flex items-center justify-between gap-2 px-4 py-3 border-t line text-sm">
        <span class="truncate flex-1">{me.user.name || me.user.username}</span>
        <button class="btn sm" onclick={toggleMode} aria-label="Toggle dark mode">{theme.dark ? 'Light' : 'Dark'}</button>
        <a href="/auth/logout" class="muted hover:underline">Sign out</a>
      </div>
    {/if}
  </aside>
  <main class="md:ml-60 flex-1 min-w-0 p-4 md:p-6 max-w-[1800px]">
    {#if me?.team?.role === 'viewer'}
      <div class="panel px-4 py-2 mb-4 text-sm muted">You have view access to {me.team.name}. Ask an admin if you need to make changes.</div>
    {/if}
    {@render children()}
  </main>
</div>
{/if}

<div class="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
  {#each toasts.items as t (t.id)}
    <div class="panel px-4 py-3 shadow-lg text-sm" style="border-left: 4px solid {t.kind === 'bad' ? 'var(--bad)' : t.kind === 'warn' ? 'var(--warn)' : 'var(--ok)'}">
      {t.message}
    </div>
  {/each}
</div>
