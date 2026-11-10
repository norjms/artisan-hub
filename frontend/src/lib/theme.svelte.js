import { api } from './api.js';

// The person's look: a skin (watchtower or classic) and a mode (light, dark or system). Saved on their hub
// account so it follows them between devices, and in the browser so the first paint is already right.
export const SKINS = { watchtower: 'Watchtower', openprinthq: 'OpenPrintHQ', classic: 'Classic' };
export const MODES = { system: 'Match my device', light: 'Light', dark: 'Dark' };
export const theme = $state({ skin: 'watchtower', mode: 'system', dark: false });

function store(k, v) { try { localStorage.setItem(k, v); } catch {} }
function systemDark() { return typeof matchMedia !== 'undefined' && matchMedia('(prefers-color-scheme: dark)').matches; }

export function apply(skin = theme.skin, mode = theme.mode) {
  theme.skin = SKINS[skin] ? skin : 'watchtower';
  theme.mode = MODES[mode] ? mode : 'system';
  theme.dark = theme.mode === 'dark' || (theme.mode === 'system' && systemDark());
  const el = document.documentElement;
  el.dataset.skin = theme.skin;
  el.dataset.theme = theme.dark ? 'dark' : 'light';
  store('hub.skin', theme.skin);
  store('hub.mode', theme.mode);
  shareLook();
}

// Hand the look to the supporting apps on the same domain (parts, DAM, sign-in): their shared theme reads this cookie.
function shareLook() {
  try {
    const host = location.hostname, i = host.indexOf('.');
    const domain = i > 0 && host.split('.').length > 2 ? host.slice(i) : '';
    document.cookie = `ah_look=${theme.skin}.${theme.mode}; path=/; max-age=31536000; SameSite=Lax` +
      (domain ? `; domain=${domain}` : '') + (location.protocol === 'https:' ? '; Secure' : '');
  } catch {}
}

export function initTheme(prefs) {
  let skin = 'watchtower', mode = 'system';
  try { skin = localStorage.getItem('hub.skin') || skin; mode = localStorage.getItem('hub.mode') || mode; } catch {}
  if (prefs?.skin) skin = prefs.skin;
  if (prefs?.mode) mode = prefs.mode;
  apply(skin, mode);
  if (typeof matchMedia !== 'undefined') {
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (theme.mode === 'system') apply(); });
  }
}

export async function setTheme(skin, mode) {
  apply(skin ?? theme.skin, mode ?? theme.mode);
  try { await api.put('/api/me/prefs', { skin: theme.skin, mode: theme.mode }); } catch {}
}

// The header's moon/sun button: flips between light and dark (leaving "match my device").
export function toggleMode() { setTheme(theme.skin, theme.dark ? 'light' : 'dark'); }
