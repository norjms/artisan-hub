import { api } from './api.js';

// Who is signed in, which team they are acting for, and what their role allows.
export const session = $state({ me: null, loaded: false, noTeam: false });

export async function loadMe() {
  try { session.me = await api.get('/api/me'); session.noTeam = false; }
  catch (e) { session.me = null; session.noTeam = e?.status === 403 && e?.message === 'no_team'; }
  session.loaded = true;
  return session.me;
}

const LEGACY = { read: 'view', write: 'models', admin: 'settings' };
export const can = (perm) => !!session.me?.perms?.includes(LEGACY[perm] || perm);

export async function switchTeam(team) {
  await api.post('/api/me/team', { team });
  location.href = '/';
}

export const ROLE_LABEL = { viewer: 'Viewer', member: 'Member', admin: 'Admin', owner: 'Owner' };
export const ROLE_HELP = {
  viewer: 'looks at everything, changes nothing',
  member: 'models, slicing and stock',
  admin: 'also the store, deleting, licenses, settings, connected services and API keys',
  owner: 'everything, including inviting people and setting their permissions'
};
