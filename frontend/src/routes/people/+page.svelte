<script>
  // People: everyone who can sign in to this hub and what they can do. Brings together what were the Team page and
  // the hub owner's People and teams page (/team and /platform redirect here; the owner's part is ?view=hub).
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { Icon } from 'artisanhub-ui';
  import { api, ago } from '$lib/api.js';
  import { attempt, toast } from '$lib/toast.svelte.js';
  import { session, loadMe, can, ROLE_LABEL, ROLE_HELP } from '$lib/session.svelte.js';
  import PermPicker from '$lib/PermPicker.svelte';
  import Page from '$lib/Page.svelte';

  const ROLES = ['viewer', 'member', 'admin', 'owner'];
  const owner = $derived(!!session.me?.user?.platform_admin);
  let view = $state(page.url.searchParams.get('view') === 'hub' ? 'hub' : 'team');
  function pick(v) { view = v; const u = new URL(location.href); if (v === 'hub') u.searchParams.set('view', 'hub'); else u.searchParams.delete('view'); history.replaceState(history.state, '', u); if (v === 'hub' && !data) loadHub(); }

  // ---- the team (what was /team)
  let t = $state(null);
  let name = $state('');
  let inv = $state({ email: '', name: '', role: 'member', custom: false, perms: [] });
  let sent = $state(null);
  let editing = $state(null);      // { member, role, perms, custom }
  const meId = $derived(session.me?.user?.user_id);
  const isOwner = $derived(t?.role === 'owner');
  const mine = $derived(t?.my_perms || []);
  // roles this person may hand out: never one that includes a permission they don't hold
  const grantable = $derived(t ? ROLES.filter((r) => (r === 'owner' ? isOwner : t.role_perms[r].every((x) => mine.includes(x)))) : []);

  async function load() { t = await api.get('/api/team'); name = t.name; }
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

  // ---- the whole hub, for its owner (what was /platform)
  let data = $state(null);
  let users = $state([]);
  let invites = $state([]);
  let edit = $state(null);
  let add = $state({ email: '', name: '', mode: 'team', team_name: '', team_id: null, role: 'member', custom: false, perms: [] });
  let hubSent = $state(null);
  async function loadHub() {
    data = await api.get('/api/platform/teams');
    users = await api.get('/api/platform/users');
    invites = await api.get('/api/platform/invites');
  }
  function openDirs(x) { edit = { id: x.id, name: x.name, deltaprints: x.source_dirs.deltaprints || '', inbox: x.source_dirs.inbox || '' }; }
  async function saveDirs() {
    await attempt(() => api.patch(`/api/platform/teams/${edit.id}`, { source_dirs: { deltaprints: edit.deltaprints, inbox: edit.inbox } }), 'Saved');
    edit = null; loadHub();
  }
  function addRoleChanged() { add.perms = [...(t?.role_perms?.[add.role] || [])]; }
  async function addPerson() {
    const b = { email: add.email, name: add.name || null, mode: add.mode };
    if (add.mode === 'team') b.team_name = add.team_name;
    if (add.mode === 'join') { b.team_id = add.team_id; b.role = add.role; if (add.custom && add.role !== 'owner') b.perms = add.perms; }
    hubSent = await attempt(() => api.post('/api/platform/people', b));
    toast(hubSent.emailed ? `Invite emailed to ${add.email}` : 'Invite ready: send them the link below');
    add = { email: '', name: '', mode: add.mode, team_name: '', team_id: add.team_id, role: 'member', custom: false, perms: [] };
    loadHub();
  }
  async function cancelHub(i) { await attempt(() => api.del(`/api/platform/invites/${i.id}`), 'Invite cancelled'); loadHub(); }
  async function removePerson(u) {
    if (!confirm(`Remove ${u.name} from every team and turn off their hub sign-in?`)) return;
    await attempt(() => api.post(`/api/platform/users/${u.id}/remove`), 'Removed'); loadHub();
  }
  // A one-time link for someone to choose a new password (logins in LLDAP), for the owner to pass on.
  let pwLink = $state(null);
  async function passwordLink(u) {
    const r = await attempt(() => api.post(`/api/account/password-link/${u.id}`));
    pwLink = { ...r, name: u.name };
  }
  const teamName = (id) => data?.teams.find((x) => x.id === id)?.name || `#${id}`;
  const canSubmit = $derived(add.email && (add.mode !== 'team' || add.team_name.trim()) && (add.mode !== 'join' || add.team_id));

  onMount(() => { load(); if (view === 'hub') loadHub(); });
</script>

<Page title="People" description="Everyone who can sign in to this hub, and what they can do.">
  {#snippet actions()}
    {#if owner}
      <div class="ah-seg" role="group" aria-label="Whose people">
        <button type="button" aria-pressed={view === 'team'} onclick={() => pick('team')}>{t?.name || 'This team'}</button>
        <button type="button" aria-pressed={view === 'hub'} onclick={() => pick('hub')}>Whole hub</button>
      </div>
    {/if}
    {#if view === 'team' && t?.role}<button class="btn sm ghost" onclick={leave}>Leave team</button>{/if}
  {/snippet}
  {#snippet widgets()}
    <section class="ah-glass ah-card">
      <div class="flex items-center gap-[10px]"><div class="ah-icon-tile"><Icon name="shield" /></div>
        <div style="flex:1;min-width:0"><h3 class="ah-widget-title">Roles</h3><div class="ah-muted">What each role can do</div></div></div>
      <div class="ah-table" role="table" aria-label="Roles" style="grid-template-columns:minmax(0,0.6fr) minmax(0,1.6fr)">
        <div role="row"><div role="columnheader">Role</div><div role="columnheader">Can</div></div>
        {#each [...ROLES].reverse() as r}<div role="row"><div role="cell">{ROLE_LABEL[r]}</div><div role="cell">{ROLE_HELP[r][0].toUpperCase() + ROLE_HELP[r].slice(1)}</div></div>{/each}
      </div>
    </section>
    {#if view === 'team' && t && can('team')}
      <section class="ah-glass ah-card">
        <h3 class="ah-widget-title">Team name</h3>
        <form class="flex gap-2" onsubmit={(e) => { e.preventDefault(); rename(); }}>
          <input class="input" bind:value={name} maxlength="120" aria-label="Team name" />
          <button class="btn primary" disabled={!name.trim() || name === t.name}>Save</button></form>
        <p class="ah-muted">Everyone here shares this team's models, printers, licenses, settings and connected services.</p>
      </section>
    {/if}
  {/snippet}

  {#if view === 'team'}
    {#if t}
      {#if can('invite')}
        <section class="ah-glass ah-card" style="flex:none">
          <h2 class="ah-card-title">Invite someone</h2>
          <form class="flex flex-col gap-3" onsubmit={(e) => { e.preventDefault(); invite(); }}>
            <div class="flex gap-3 items-end">
              <div class="ah-field" style="flex:2"><label for="ie">Email</label><input id="ie" class="input" type="email" required placeholder="name@example.com" bind:value={inv.email} /></div>
              <div class="ah-field" style="flex:1.2"><label for="in">Name (optional)</label><input id="in" class="input" bind:value={inv.name} /></div>
              <div class="ah-field" style="flex:1"><label for="ir">Role</label>
                <select id="ir" class="input" bind:value={inv.role} onchange={invRole}>{#each grantable as r}<option value={r}>{ROLE_LABEL[r]}</option>{/each}</select></div>
              <button class="btn primary" type="submit">Send invite</button>
            </div>
            {#if inv.role !== 'owner'}
              <label class="flex items-center gap-2"><input type="checkbox" bind:checked={inv.custom} onchange={invRole} /> Choose permissions instead of the role's defaults</label>
              {#if inv.custom}<PermPicker catalog={t.perm_catalog} bind:value={inv.perms} allowed={mine} />{/if}
            {/if}
          </form>
          <p class="ah-muted">They get a link. If they don't have a login yet, the link lets them create one first. Invites last 14 days.</p>
          {#if sent}
            <div class="ah-banner ah-banner--live" style="flex-direction:column;align-items:flex-start">
              <div><b>Send them this link</b> <button class="btn sm" onclick={() => copy(sent.link)}>Copy</button></div><code style="word-break:break-all">{sent.link}</code>
              {#if sent.account === 'invited'}<span>They don't have a login yet; the link walks them through creating one.</span>
              {:else if sent.account === 'existing'}<span>They already have a login and can now sign in to the hub.</span>
              {:else if sent.note}<span>{sent.note}</span>{/if}
            </div>
          {/if}
        </section>
      {/if}
      <section class="ah-glass ah-card" style="flex:1 0 auto;min-height:220px">
        <h2 class="ah-card-title">{t.members.length} {t.members.length === 1 ? 'person' : 'people'}</h2>
        <table class="grid"><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Permissions</th><th>Signed in</th><th></th></tr></thead><tbody>
          {#each t.members as m}
            <tr>
              <td>{m.name}{#if m.user_id === meId} <span class="chip">You</span>{/if}</td>
              <td class="ah-secondary">{m.email || ''}</td>
              <td><span class="chip {m.role === 'owner' ? 'live' : ''}">{ROLE_LABEL[m.role]}</span></td>
              <td>{m.role === 'owner' ? 'All' : permCount(m)}{#if m.custom} <span class="chip warn">Custom</span>{/if}</td>
              <td class="ah-muted">{m.last_login_at ? ago(m.last_login_at) : 'Never'}</td>
              <td style="white-space:nowrap;text-align:right">
                {#if can('people') && m.user_id !== meId && (isOwner || m.role !== 'owner')}
                  <button class="btn sm ghost" onclick={() => openPerms(m)}>Change role</button>
                  <button class="btn sm danger" onclick={() => remove(m)}>Remove</button>
                {/if}
              </td>
            </tr>
          {/each}
          {#each can('invite') ? t.invites || [] : [] as i}
            <tr><td>{i.name || i.email}</td><td class="ah-secondary">{i.email}</td><td><span class="chip warn">Invited</span></td>
              <td>{ROLE_LABEL[i.role]}{#if i.perms} <span class="chip warn">Custom</span>{/if}</td>
              <td class="ah-muted">{i.expired ? 'Invite expired' : 'Sent ' + ago(i.created_at)}{i.needs_account ? ', no login yet' : ''}</td>
              <td style="text-align:right"><button class="btn sm ghost" onclick={() => revoke(i)}>Cancel invite</button></td></tr>
          {/each}
        </tbody></table>
      </section>
    {:else}<p class="ah-muted">Loading...</p>{/if}

  {:else if data}
    <section class="ah-glass ah-card" style="flex:none">
      <h2 class="ah-card-title">Add a person</h2>
      <p class="ah-muted" style="margin-top:-6px">Only the hub owner sees this. Adding someone here gives them a hub sign-in: an account in the hub's user directory (LLDAP), used through the Authelia sign-in, or access for the account they already have. Team owners can then invite people into their own team.</p>
      <form class="flex flex-col gap-3" onsubmit={(e) => { e.preventDefault(); addPerson(); }}>
        <div class="flex gap-3">
          <input class="input" type="email" required placeholder="name@example.com" bind:value={add.email} aria-label="Email" />
          <input class="input" placeholder="Name" bind:value={add.name} aria-label="Name" />
        </div>
        <fieldset class="grid gap-2" style="grid-template-columns:repeat(3,minmax(0,1fr));border:0;padding:0;margin:0">
          <legend class="sr-only">How to add them</legend>
          {#each [['team', 'As a team', 'They own a new team and can invite their own people.'], ['individual', 'As an individual', 'Their own private workspace.'], ['join', 'Into an existing team', 'They join a team with the role you choose.']] as [v, label, help]}
            <label class="panel" style="padding:10px 14px;cursor:pointer;{add.mode === v ? 'border-color:var(--ah-accent)' : ''}">
              <input type="radio" name="mode" value={v} bind:group={add.mode} /> <b>{label}</b>
              <div class="ah-muted">{help}</div>
            </label>
          {/each}
        </fieldset>
        {#if add.mode === 'team'}
          <input class="input" required placeholder="Team name" bind:value={add.team_name} maxlength="120" aria-label="Team name" />
        {:else if add.mode === 'join'}
          <div class="flex gap-3">
            <select class="input" bind:value={add.team_id} required aria-label="Team">
              <option value={null} disabled>Choose a team</option>
              {#each data.teams as x}<option value={x.id}>{x.name}</option>{/each}
            </select>
            <select class="input" style="width:12rem" bind:value={add.role} onchange={addRoleChanged} aria-label="Role">{#each ROLES as r}<option value={r}>{ROLE_LABEL[r]}</option>{/each}</select>
          </div>
          {#if add.role !== 'owner' && t}
            <label class="flex items-center gap-2"><input type="checkbox" bind:checked={add.custom} onchange={addRoleChanged} /> Choose permissions instead of the role's defaults</label>
            {#if add.custom}<PermPicker catalog={t.perm_catalog} bind:value={add.perms} allowed={Object.keys(t.perm_catalog)} />{/if}
          {/if}
        {/if}
        <button class="btn primary" style="align-self:flex-start" disabled={!canSubmit}>Add and create invite</button>
      </form>
      {#if hubSent}
        <div class="ah-banner ah-banner--live" style="flex-direction:column;align-items:flex-start">
          <div><b>Send them this link</b> <button class="btn sm" onclick={() => copy(hubSent.link)}>Copy</button></div><code style="word-break:break-all">{hubSent.link}</code>
          {#if hubSent.account === 'invited'}<span>No sign-in yet: the link lets them choose a password (it is tied to their email), then join.</span>
          {:else if hubSent.account === 'existing'}<span>They already had a sign-in; it now gives them access to the hub.</span>
          {:else if hubSent.note}<span>{hubSent.note}</span>{/if}
        </div>
      {/if}
    </section>
    {#if invites.length}
      <section class="ah-glass ah-card" style="flex:none">
        <h2 class="ah-card-title">Waiting to accept</h2>
        <table class="grid"><thead><tr><th>Person</th><th>Into</th><th>Role</th><th>Sent</th><th></th></tr></thead><tbody>
          {#each invites as i}
            <tr><td>{i.name || i.email}<div class="ah-muted">{i.email}{i.needs_account ? ', no login yet' : ''}</div></td>
              <td>{i.kind === 'new_team' ? 'New team: ' : ''}{i.team}</td><td>{ROLE_LABEL[i.role]}</td>
              <td class="ah-muted">{i.expired ? 'Expired' : ago(i.created_at)}</td>
              <td style="text-align:right"><button class="btn sm ghost" onclick={() => cancelHub(i)}>Cancel invite</button></td></tr>
          {/each}
        </tbody></table>
      </section>
    {/if}
    <section class="ah-glass ah-card" style="flex:none">
      <h2 class="ah-card-title">Teams</h2>
      <table class="grid"><thead><tr><th>Team</th><th>Members</th><th>Models</th><th>Server folders</th><th>Created</th><th></th></tr></thead><tbody>
        {#each data.teams as x}
          <tr><td>{x.name}{#if x.personal} <span class="chip">Individual</span>{/if}<div class="ah-muted">{x.slug}</div></td>
            <td>{x.members}</td><td>{x.models}</td>
            <td>{#if x.source_dirs.deltaprints}<div>DeltaPrints: {x.source_dirs.deltaprints}</div>{/if}{#if x.source_dirs.inbox}<div>Inbox: {x.source_dirs.inbox}</div>{/if}{#if !x.source_dirs.deltaprints && !x.source_dirs.inbox}<span class="ah-muted">None</span>{/if}</td>
            <td class="ah-muted">{ago(x.created_at)}</td>
            <td style="text-align:right"><button class="btn sm ghost" onclick={() => openDirs(x)}>Set folders</button></td></tr>
        {/each}
      </tbody></table>
    </section>
    <section class="ah-glass ah-card" style="flex:none">
      <h2 class="ah-card-title">Everyone with a sign-in</h2>
      {#if pwLink}
        <div class="ah-banner ah-banner--live" style="flex-direction:column;align-items:flex-start">
          <div><b>Send {pwLink.name} this link</b> <button class="btn sm" onclick={() => copy(pwLink.link)}>Copy</button></div><code style="word-break:break-all">{pwLink.link}</code>
          <span>It lets them choose a new password for the login {pwLink.user}. It works once, for {pwLink.expires_in_minutes} minutes, and replaces any older link.</span>
        </div>
      {/if}
      <table class="grid"><thead><tr><th>Person</th><th>Teams</th><th>Last sign-in</th><th></th></tr></thead><tbody>
        {#each users as u}
          <tr><td>{u.name}{#if u.platform_admin} <span class="chip live">Hub owner</span>{/if}<div class="ah-muted">{u.email || ''}</div></td>
            <td>{u.teams.map((m) => `${teamName(m.team_id)} (${ROLE_LABEL[m.role]})`).join(', ') || 'None'}</td>
            <td class="ah-muted">{u.last_login_at ? ago(u.last_login_at) : 'Never'}</td>
            <td style="text-align:right;white-space:nowrap">{#if u.email}<button class="btn sm ghost" onclick={() => passwordLink(u)}>Make password link</button>{/if}
              {#if !u.platform_admin}<button class="btn sm danger" onclick={() => removePerson(u)}>Remove from hub</button>{/if}</td></tr>
        {/each}
      </tbody></table>
    </section>
  {:else}<p class="ah-muted">Loading...</p>{/if}
</Page>

{#if editing}
  <div class="ah-dialog-back" role="presentation" onclick={(e) => e.target === e.currentTarget && (editing = null)}>
    <div class="ah-dialog" role="dialog" aria-modal="true" aria-label="Role and permissions">
      <h3 class="ah-card-title">{editing.member.name}</h3>
      <div class="ah-field"><label for="er">Role</label>
        <select id="er" class="input" bind:value={editing.role} onchange={roleChanged}>
          {#each grantable.length ? Array.from(new Set([...grantable, editing.member.role])) : [editing.member.role] as r}<option value={r}>{ROLE_LABEL[r]}: {ROLE_HELP[r]}</option>{/each}
        </select></div>
      {#if editing.role === 'owner'}
        <p class="ah-muted">Owners can do everything.</p>
      {:else}
        <label class="flex items-center gap-2"><input type="checkbox" bind:checked={editing.custom} onchange={roleChanged} /> Choose permissions instead of the role's defaults</label>
        <PermPicker catalog={t.perm_catalog} bind:value={editing.perms} allowed={mine} disabled={!editing.custom} />
      {/if}
      <div class="flex gap-2 justify-end">
        <button class="btn" onclick={() => (editing = null)}>Cancel</button>
        <button class="btn primary" onclick={saveMember}>Save</button>
      </div>
    </div>
  </div>
{/if}

{#if edit}
  <div class="ah-dialog-back" role="presentation" onclick={(e) => e.target === e.currentTarget && (edit = null)}>
    <form class="ah-dialog" role="dialog" aria-modal="true" aria-label="Server folders" onsubmit={(e) => { e.preventDefault(); saveDirs(); }}>
      <h3 class="ah-card-title">Server folders for {edit.name}</h3>
      <div class="ah-field"><label for="dp">DeltaPrints library (scanned every 15 min)</label><input id="dp" class="input" bind:value={edit.deltaprints} placeholder="{data.source_root}/..." /></div>
      <div class="ah-field"><label for="ib">Drop box (imported every 2 min)</label><input id="ib" class="input" bind:value={edit.inbox} placeholder="{data.source_root}/..." /></div>
      <div class="flex gap-2 justify-end"><button type="button" class="btn" onclick={() => (edit = null)}>Cancel</button><button class="btn primary">Save</button></div>
    </form>
  </div>
{/if}
