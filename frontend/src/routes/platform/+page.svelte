<script>
  import { onMount } from 'svelte';
  import { api, ago } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  import { ROLE_LABEL } from '$lib/session.svelte.js';
  import PermPicker from '$lib/PermPicker.svelte';

  let data = $state(null);
  let users = $state([]);
  let invites = $state([]);
  let catalog = $state({});
  let rolePerms = $state({});
  let edit = $state(null);
  let add = $state({ email: '', name: '', mode: 'team', team_name: '', team_id: null, role: 'member', custom: false, perms: [] });
  let sent = $state(null);
  const ROLES = ['viewer', 'member', 'admin', 'owner'];

  async function load() {
    data = await api.get('/api/platform/teams');
    users = await api.get('/api/platform/users');
    invites = await api.get('/api/platform/invites');
    const t = await api.get('/api/team'); catalog = t.perm_catalog; rolePerms = t.role_perms;
  }
  onMount(load);

  function openDirs(t) { edit = { id: t.id, name: t.name, deltaprints: t.source_dirs.deltaprints || '', inbox: t.source_dirs.inbox || '' }; }
  async function saveDirs() {
    await attempt(() => api.patch(`/api/platform/teams/${edit.id}`, { source_dirs: { deltaprints: edit.deltaprints, inbox: edit.inbox } }), 'Saved');
    edit = null; load();
  }
  function roleChanged() { add.perms = [...(rolePerms[add.role] || [])]; }
  async function addPerson() {
    const b = { email: add.email, name: add.name || null, mode: add.mode };
    if (add.mode === 'team') b.team_name = add.team_name;
    if (add.mode === 'join') { b.team_id = add.team_id; b.role = add.role; if (add.custom && add.role !== 'owner') b.perms = add.perms; }
    sent = await attempt(() => api.post('/api/platform/people', b));
    toast(sent.emailed ? `Invite emailed to ${add.email}` : 'Invite ready: send them the link below');
    add = { email: '', name: '', mode: add.mode, team_name: '', team_id: add.team_id, role: 'member', custom: false, perms: [] };
    load();
  }
  async function cancel(i) { await attempt(() => api.del(`/api/platform/invites/${i.id}`), 'Invite cancelled'); load(); }
  async function removePerson(u) {
    if (!confirm(`Remove ${u.name} from every team and turn off their hub sign-in?`)) return;
    await attempt(() => api.post(`/api/platform/users/${u.id}/remove`), 'Removed'); load();
  }
  async function copy(text) { try { await navigator.clipboard.writeText(text); toast('Copied'); } catch { toast('Select the text and copy it', 'bad'); } }
  const teamName = (id) => data?.teams.find((t) => t.id === id)?.name || `#${id}`;
  const canSubmit = $derived(add.email && (add.mode !== 'team' || add.team_name.trim()) && (add.mode !== 'join' || add.team_id));
</script>

<svelte:head><title>People &amp; teams · Artisan Hub</title></svelte:head>

{#if data}
<div class="max-w-5xl space-y-5">
  <div>
    <h1 class="text-2xl font-bold mb-1">People &amp; teams</h1>
    <p class="muted text-sm">Only the hub owner sees this page. Adding someone here gives them a hub login (an Authentik account, or the hub-users group if they already have one). Team owners can then invite people into their own team.</p>
  </div>

  <div class="panel p-5">
    <h3 class="font-semibold mb-3">Add a person</h3>
    <form class="space-y-3" onsubmit={(e) => { e.preventDefault(); addPerson(); }}>
      <div class="flex gap-2 flex-wrap">
        <input class="input flex-1 min-w-[14rem]" type="email" required placeholder="name@example.com" bind:value={add.email} aria-label="Email" />
        <input class="input flex-1 min-w-[10rem]" placeholder="Name" bind:value={add.name} aria-label="Name" />
      </div>
      <fieldset class="grid gap-2 sm:grid-cols-3">
        <legend class="sr-only">How to add them</legend>
        {#each [['team', 'As a team', 'They own a new team and can invite their own people.'], ['individual', 'As an individual', 'Their own private workspace.'], ['join', 'Into an existing team', 'They join a team with the role you choose.']] as [v, label, help]}
          <label class="panel p-3 cursor-pointer text-sm {add.mode === v ? 'ring-2 ring-brand-600' : ''}">
            <input type="radio" name="mode" value={v} bind:group={add.mode} class="mr-1" /> <b>{label}</b>
            <div class="muted mt-1">{help}</div>
          </label>
        {/each}
      </fieldset>
      {#if add.mode === 'team'}
        <input class="input w-full" required placeholder="Team name" bind:value={add.team_name} maxlength="120" aria-label="Team name" />
      {:else if add.mode === 'join'}
        <div class="flex gap-2 flex-wrap">
          <select class="input flex-1" bind:value={add.team_id} required aria-label="Team">
            <option value={null} disabled>Choose a team</option>
            {#each data.teams as t}<option value={t.id}>{t.name}</option>{/each}
          </select>
          <select class="input" bind:value={add.role} onchange={roleChanged} aria-label="Role">{#each ROLES as r}<option value={r}>{ROLE_LABEL[r]}</option>{/each}</select>
        </div>
        {#if add.role !== 'owner'}
          <label class="flex items-center gap-2 text-sm"><input type="checkbox" bind:checked={add.custom} onchange={roleChanged} /> Choose permissions instead of the role's defaults</label>
          {#if add.custom}<PermPicker {catalog} bind:value={add.perms} allowed={Object.keys(catalog)} />{/if}
        {/if}
      {/if}
      <button class="btn primary" disabled={!canSubmit}>Add and create invite</button>
    </form>
    {#if sent}
      <div class="panel p-3 mt-3 text-sm space-y-2" style="border-color:var(--ok)">
        <div><b>Send them this link</b> <button class="btn sm ml-1" onclick={() => copy(sent.link)}>Copy</button><div class="break-all"><code>{sent.link}</code></div></div>
        {#if sent.account === 'invited'}<div class="muted">No Authentik account yet: the link lets them create one (it's tied to their email), then join.</div>
        {:else if sent.account === 'existing'}<div class="muted">They already had an Authentik account; it was added to hub-users.</div>
        {:else if sent.note}<div style="color:var(--bad)">{sent.note}</div>{/if}
      </div>
    {/if}
  </div>

  {#if invites.length}
  <div class="panel p-0 scroll-x"><table class="grid w-full"><thead><tr><th>Waiting to accept</th><th>Into</th><th>Role</th><th>Sent</th><th></th></tr></thead><tbody>
    {#each invites as i}
      <tr><td>{i.name || i.email}<div class="text-xs muted">{i.email}{i.needs_account ? ' · no login yet' : ''}</div></td>
        <td class="text-sm">{i.kind === 'new_team' ? 'New team: ' : ''}{i.team}</td><td class="text-sm">{ROLE_LABEL[i.role]}</td>
        <td class="text-xs muted">{i.expired ? 'expired' : ago(i.created_at)}</td>
        <td><button class="btn sm" onclick={() => cancel(i)}>Cancel</button></td></tr>
    {/each}
  </tbody></table></div>
  {/if}

  <div class="panel p-0 scroll-x"><table class="grid w-full"><thead><tr><th>Team</th><th>Members</th><th>Models</th><th>Server folders</th><th>Created</th><th></th></tr></thead><tbody>
    {#each data.teams as t}
      <tr><td>{t.name}{#if t.personal} <span class="chip">individual</span>{/if}<div class="text-xs muted">{t.slug}</div></td>
        <td>{t.members}</td><td>{t.models}</td>
        <td class="text-xs">{#if t.source_dirs.deltaprints}<div>DeltaPrints: {t.source_dirs.deltaprints}</div>{/if}{#if t.source_dirs.inbox}<div>Inbox: {t.source_dirs.inbox}</div>{/if}{#if !t.source_dirs.deltaprints && !t.source_dirs.inbox}<span class="muted">none</span>{/if}</td>
        <td class="text-xs muted">{ago(t.created_at)}</td>
        <td><button class="btn sm" onclick={() => openDirs(t)}>Folders</button></td></tr>
    {/each}
  </tbody></table></div>

  <div class="panel p-0 scroll-x"><table class="grid w-full"><thead><tr><th>Person</th><th>Teams</th><th>Last sign-in</th><th></th></tr></thead><tbody>
    {#each users as u}
      <tr><td>{u.name}{#if u.platform_admin} <span class="chip">hub owner</span>{/if}<div class="text-xs muted">{u.email || ''}</div></td>
        <td class="text-sm">{u.teams.map((m) => `${teamName(m.team_id)} (${ROLE_LABEL[m.role]})`).join(', ') || 'none'}</td>
        <td class="text-xs muted">{u.last_login_at ? ago(u.last_login_at) : 'never'}</td>
        <td>{#if !u.platform_admin}<button class="btn sm danger" onclick={() => removePerson(u)}>Remove from hub</button>{/if}</td></tr>
    {/each}
  </tbody></table></div>
</div>
{/if}

{#if edit}
  <div class="fixed inset-0 z-40 bg-black/40 flex items-center justify-center p-4" role="presentation" onclick={(e) => e.target === e.currentTarget && (edit = null)}>
    <div class="panel p-5 w-full max-w-lg" role="dialog" aria-modal="true" aria-label="Server folders">
      <h3 class="font-semibold text-lg mb-3">Server folders for {edit.name}</h3>
      <form class="space-y-3" onsubmit={(e) => { e.preventDefault(); saveDirs(); }}>
        <label class="block text-sm"><span class="font-medium">DeltaPrints library (scanned every 15 min)</span>
          <input class="input w-full mt-1" bind:value={edit.deltaprints} placeholder="{data.source_root}/..." /></label>
        <label class="block text-sm"><span class="font-medium">Drop box (imported every 2 min)</span>
          <input class="input w-full mt-1" bind:value={edit.inbox} placeholder="{data.source_root}/..." /></label>
        <div class="flex gap-2 justify-end"><button type="button" class="btn" onclick={() => (edit = null)}>Cancel</button><button class="btn primary">Save</button></div>
      </form>
    </div>
  </div>
{/if}
