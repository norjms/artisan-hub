<script>
  import { onMount } from 'svelte';
  import { api, ago } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  import { session, loadMe, can, ROLE_LABEL, ROLE_HELP } from '$lib/session.svelte.js';
  import PermPicker from '$lib/PermPicker.svelte';

  let t = $state(null);
  let name = $state('');
  let inv = $state({ email: '', name: '', role: 'member', custom: false, perms: [] });
  let sent = $state(null);
  let editing = $state(null);      // { member, role, perms, custom }
  const ROLES = ['viewer', 'member', 'admin', 'owner'];
  const meId = $derived(session.me?.user?.user_id);
  const isOwner = $derived(t?.role === 'owner');
  const mine = $derived(t?.my_perms || []);
  // roles this person may hand out: never one that includes a permission they don't hold
  const grantable = $derived(t ? ROLES.filter((r) => (r === 'owner' ? isOwner : t.role_perms[r].every((x) => mine.includes(x)))) : []);

  async function load() { t = await api.get('/api/team'); name = t.name; }
  onMount(load);

  async function rename() { await attempt(() => api.patch('/api/team', { name }), 'Renamed'); await loadMe(); load(); }

  function openPerms(m) { editing = { member: m, role: m.role, perms: [...m.perms], custom: m.custom }; }
  function roleChanged() { editing.perms = [...t.role_perms[editing.role]]; }
  async function saveMember() {
    const e = editing;
    const body = { role: e.role, ...(e.custom ? { perms: e.perms } : { reset_perms: true }) };
    await attempt(() => api.patch(`/api/team/members/${e.member.user_id}`, body), `Saved ${e.member.name}`);
    editing = null; load();
  }
  async function remove(m) {
    if (!confirm(`Remove ${m.name} from ${t.name}? Their API keys and connected apps for this team stop working.`)) return;
    const r = await attempt(() => api.del(`/api/team/members/${m.user_id}`));
    toast(r.hub_access_removed ? `Removed. ${m.name} is in no other team, so their hub sign-in was turned off.` : 'Removed');
    load();
  }
  function invRole() { inv.perms = [...t.role_perms[inv.role]]; }
  async function invite() {
    const body = { email: inv.email, name: inv.name || null, role: inv.role, ...(inv.custom && inv.role !== 'owner' ? { perms: inv.perms } : {}) };
    sent = await attempt(() => api.post('/api/team/invites', body));
    toast(sent.emailed ? `Invite emailed to ${inv.email}` : 'Invite ready: send them the link below');
    inv = { email: '', name: '', role: 'member', custom: false, perms: [] };
    load();
  }
  async function revoke(i) { await attempt(() => api.del(`/api/team/invites/${i.id}`), 'Invite cancelled'); load(); }
  async function leave() {
    if (!confirm(`Leave ${t.name}? You lose access to its models and settings.`)) return;
    await attempt(() => api.post('/api/team/leave')); location.href = '/';
  }
  async function copy(text) { try { await navigator.clipboard.writeText(text); toast('Copied'); } catch { toast('Select the text and copy it', 'bad'); } }
  const permCount = (m) => `${m.perms.length} of ${Object.keys(t.perm_catalog).length}`;
</script>

<svelte:head><title>Team · Artisan Hub</title></svelte:head>

{#if t}
<div class="max-w-4xl space-y-5">
  <div class="flex flex-wrap items-end gap-3">
    <div class="flex-1 min-w-0">
      <h1 class="text-2xl font-bold">{t.name}</h1>
      <p class="muted text-sm">Everyone here shares this team's models, printers, licenses, settings and connected services. Other teams can't see any of it.</p>
    </div>
    {#if t.role}<button class="btn sm" onclick={leave}>Leave team</button>{/if}
  </div>

  {#if can('team')}
  <div class="panel p-5">
    <h3 class="font-semibold mb-2">Team name</h3>
    <div class="flex gap-2 flex-wrap"><input class="input flex-1 min-w-[12rem]" bind:value={name} maxlength="120" aria-label="Team name" />
      <button class="btn primary" disabled={!name.trim() || name === t.name} onclick={rename}>Save</button></div>
  </div>
  {/if}

  <div class="panel p-5">
    <h3 class="font-semibold mb-1">People</h3>
    <ul class="text-sm muted mb-3 space-y-0.5">{#each ROLES as r}<li><b>{ROLE_LABEL[r]}</b>: {ROLE_HELP[r]}</li>{/each}</ul>
    <div class="scroll-x"><table class="grid w-full"><thead><tr><th>Name</th><th>Role</th><th>Permissions</th><th>Last sign-in</th><th></th></tr></thead><tbody>
      {#each t.members as m}
        <tr>
          <td>{m.name}{#if m.user_id === meId} <span class="chip">you</span>{/if}<div class="text-xs muted">{m.email || ''}</div></td>
          <td>{ROLE_LABEL[m.role]}</td>
          <td class="text-sm">{m.role === 'owner' ? 'all' : permCount(m)}{#if m.custom} <span class="chip warn">custom</span>{/if}</td>
          <td class="text-xs muted">{m.last_login_at ? ago(m.last_login_at) : 'never'}</td>
          <td class="whitespace-nowrap">
            {#if can('people') && m.user_id !== meId && (isOwner || m.role !== 'owner')}
              <button class="btn sm" onclick={() => openPerms(m)}>Role &amp; permissions</button>
              <button class="btn sm danger" onclick={() => remove(m)}>Remove</button>
            {/if}
          </td>
        </tr>
      {/each}
    </tbody></table></div>
  </div>

  {#if can('invite')}
  <div class="panel p-5">
    <h3 class="font-semibold mb-1">Invite someone to {t.name}</h3>
    <p class="text-sm muted mb-3">They get a link. If they don't have a login yet, the link lets them create one first. Invites last 14 days.</p>
    <form class="space-y-3" onsubmit={(e) => { e.preventDefault(); invite(); }}>
      <div class="flex gap-2 flex-wrap">
        <input class="input flex-1 min-w-[14rem]" type="email" required placeholder="name@example.com" bind:value={inv.email} aria-label="Email" />
        <input class="input flex-1 min-w-[10rem]" placeholder="Name (optional)" bind:value={inv.name} aria-label="Name" />
        <select class="input" bind:value={inv.role} onchange={invRole} aria-label="Role">
          {#each grantable as r}<option value={r}>{ROLE_LABEL[r]}</option>{/each}
        </select>
      </div>
      {#if inv.role !== 'owner'}
        <label class="flex items-center gap-2 text-sm"><input type="checkbox" bind:checked={inv.custom} onchange={invRole} /> Choose permissions instead of the role's defaults</label>
        {#if inv.custom}<PermPicker catalog={t.perm_catalog} bind:value={inv.perms} allowed={mine} />{/if}
      {/if}
      <button class="btn primary" type="submit">Send invite</button>
    </form>
    {#if sent}
      <div class="panel p-3 mt-3 text-sm space-y-2" style="border-color:var(--ok)">
        <div><b>Send them this link</b> <button class="btn sm ml-1" onclick={() => copy(sent.link)}>Copy</button><div class="break-all"><code>{sent.link}</code></div></div>
        {#if sent.account === 'invited'}<div class="muted">They don't have a login yet; the link walks them through creating one.</div>
        {:else if sent.account === 'existing'}<div class="muted">They already have a login and can now sign in to the hub.</div>
        {:else if sent.note}<div style="color:var(--bad)">{sent.note}</div>{/if}
      </div>
    {/if}
    {#if t.invites?.length}
      <h4 class="font-semibold mt-4 mb-2 text-sm">Waiting to accept</h4>
      <table class="grid w-full"><tbody>
        {#each t.invites as i}<tr><td>{i.name || i.email}<div class="text-xs muted">{i.email}</div></td><td class="text-sm">{ROLE_LABEL[i.role]}{#if i.perms} <span class="chip warn">custom</span>{/if}</td>
          <td class="text-xs muted">{i.expired ? 'expired' : 'sent ' + ago(i.created_at)}{i.needs_account ? ', no login yet' : ''}</td>
          <td><button class="btn sm" onclick={() => revoke(i)}>Cancel</button></td></tr>{/each}
      </tbody></table>
    {/if}
  </div>
  {/if}
</div>
{/if}

{#if editing}
  <div class="fixed inset-0 z-40 bg-black/40 flex items-center justify-center p-4" role="presentation" onclick={(e) => e.target === e.currentTarget && (editing = null)}>
    <div class="panel p-5 w-full max-w-lg max-h-[90vh] overflow-y-auto" role="dialog" aria-modal="true" aria-label="Role and permissions">
      <h3 class="font-semibold text-lg mb-3">{editing.member.name}</h3>
      <label class="block text-sm mb-3"><span class="font-medium">Role</span>
        <select class="input w-full mt-1" bind:value={editing.role} onchange={roleChanged}>
          {#each grantable.length ? Array.from(new Set([...grantable, editing.member.role])) : [editing.member.role] as r}<option value={r}>{ROLE_LABEL[r]} · {ROLE_HELP[r]}</option>{/each}
        </select></label>
      {#if editing.role === 'owner'}
        <p class="text-sm muted">Owners can do everything.</p>
      {:else}
        <label class="flex items-center gap-2 text-sm mb-2"><input type="checkbox" bind:checked={editing.custom} onchange={roleChanged} /> Choose permissions instead of the role's defaults</label>
        <PermPicker catalog={t.perm_catalog} bind:value={editing.perms} allowed={mine} disabled={!editing.custom} />
      {/if}
      <div class="flex gap-2 justify-end pt-4">
        <button class="btn" onclick={() => (editing = null)}>Cancel</button>
        <button class="btn primary" onclick={saveMember}>Save</button>
      </div>
    </div>
  </div>
{/if}
