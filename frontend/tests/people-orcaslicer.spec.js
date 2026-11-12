// People, Whole hub, on an installation that runs OrcaSlicer in the browser: a switch per person, the hub's owners
// always on and fixed with a one-line note, at both desktop sizes with no page scroll and nothing cut off. The
// hub's answers about people and OrcaSlicer access are stood in for, the way an installation's hub gives them.
import { test, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';

const OUT = process.env.SHOTS_DIR || 'test-results/screens';
const TEAM = [{ id: 1, slug: 'artisan-prints', name: 'Artisan Prints', personal: false, members: 4, models: 212, source_dirs: {}, storage_prefix: '', created_at: '2026-10-01T10:00:00Z' }];
const USERS = [
  { id: 1, name: 'James Norman', email: 'admin@hub.example', platform_admin: true, teams: [{ team_id: 1, role: 'owner' }], last_login_at: '2026-11-12T09:00:00Z' },
  { id: 2, name: 'Riley Chen', email: 'riley@example.com', platform_admin: false, teams: [{ team_id: 1, role: 'admin' }], last_login_at: '2026-11-11T16:20:00Z' },
  { id: 3, name: 'Sam Ortiz', email: 'sam@example.com', platform_admin: false, teams: [{ team_id: 1, role: 'member' }], last_login_at: '2026-11-10T08:05:00Z' },
  { id: 4, name: 'Pat Lee', email: 'pat@example.com', platform_admin: false, teams: [{ team_id: 1, role: 'viewer' }], last_login_at: null }
];

function fits() {
  const vw = innerWidth, vh = innerHeight, se = document.scrollingElement;
  const clipped = [];
  for (const el of document.body.querySelectorAll('*')) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || el.closest('.sr-only, #svelte-announcer, [aria-hidden="true"], svg')) continue;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const hides = (v) => v === 'hidden' || v === 'clip';
    if (cs.textOverflow !== 'ellipsis' && ((hides(cs.overflowX) && el.scrollWidth > el.clientWidth + 2) || (hides(cs.overflowY) && el.scrollHeight > el.clientHeight + 2))) clipped.push(el.tagName + '.' + el.className);
    if (r.right > vw + 1 || r.bottom > vh + 1) {
      let p = el.parentElement, boxed = false;
      while (p && p !== document.body) { const ps = getComputedStyle(p); if (ps.overflowX !== 'visible' || ps.overflowY !== 'visible' || ps.position === 'fixed') { boxed = true; break; } p = p.parentElement; }
      if (!boxed && cs.position !== 'fixed') clipped.push('outside: ' + el.tagName + '.' + el.className);
    }
  }
  return { pageScroll: se.scrollHeight > vh + 1 || se.scrollWidth > vw + 1, clipped };
}

async function standIn(page, state) {
  await page.route('**/api/platform/teams', (r) => r.fulfill({ json: { teams: TEAM, source_root: '' } }));
  await page.route('**/api/platform/users', (r) => r.fulfill({ json: USERS }));
  await page.route('**/api/platform/invites', (r) => r.fulfill({ json: [] }));
  await page.route('**/api/platform/apps/orcaslicer', (r) => r.fulfill({ json: state.available
    ? { available: true, group: 'orcaslicer-users', owners: [1], allowed: [1, ...state.allowed], error: state.error || null }
    : { available: false } }));
  await page.route('**/api/platform/users/*/orcaslicer', async (r) => {
    const id = Number(r.request().url().split('/').at(-2));
    const { allowed } = r.request().postDataJSON();
    if (state.fail) return r.fulfill({ status: 502, json: { detail: "The hub's user directory (LLDAP) did not take the change: LLDAP did not answer: ConnectError" } });
    state.puts.push([id, allowed]);
    state.allowed = allowed ? [...new Set([...state.allowed, id])] : state.allowed.filter((x) => x !== id);
    return r.fulfill({ json: { user_id: id, allowed, changed: true } });
  });
}

const sw = (page, name) => page.getByRole('switch', { name: `OrcaSlicer in the browser for ${name}` });

test('the owner turns OrcaSlicer in the browser on and off per person', async ({ page }, info) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  const state = { available: true, allowed: [2], puts: [] };
  await standIn(page, state);
  await page.goto('/people?view=hub', { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  await expect(page.getByRole('columnheader', { name: 'OrcaSlicer in the browser' })).toBeVisible();
  // The hub's owner: always on, and the switch can't be changed.
  await expect(sw(page, 'James Norman')).toHaveAttribute('aria-checked', 'true');
  await expect(sw(page, 'James Norman')).toBeDisabled();
  await expect(page.getByText('Hub owners always have it')).toBeVisible();
  await expect(sw(page, 'Riley Chen')).toHaveAttribute('aria-checked', 'true');
  await expect(sw(page, 'Sam Ortiz')).toHaveAttribute('aria-checked', 'false');
  const dir = `${OUT}/${info.project.name}`;
  mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: `${dir}/people_hub_orcaslicer.png` });
  const r = await page.evaluate(fits);
  expect(r.pageScroll, 'the page scrolls').toBe(false);
  expect(r.clipped, 'cut off').toEqual([]);

  await sw(page, 'Sam Ortiz').click();
  await expect(sw(page, 'Sam Ortiz')).toHaveAttribute('aria-checked', 'true');
  await expect(page.getByText('Sam Ortiz can now open OrcaSlicer in the browser')).toBeVisible();
  await sw(page, 'Riley Chen').click();
  await expect(sw(page, 'Riley Chen')).toHaveAttribute('aria-checked', 'false');
  expect(state.puts).toEqual([[3, true], [2, false]]);

  // The user directory refuses: the error is said plainly and the switch goes back.
  state.fail = true;
  await sw(page, 'Pat Lee').click();
  await expect(page.getByText("The hub's user directory (LLDAP) did not take the change", { exact: false })).toBeVisible();
  await expect(sw(page, 'Pat Lee')).toHaveAttribute('aria-checked', 'false');
  await page.screenshot({ path: `${dir}/people_hub_orcaslicer_error.png` });
  expect((await page.evaluate(fits)).pageScroll).toBe(false);
  expect(errors).toEqual([]);
});

test('a hub without OrcaSlicer in the browser shows no switch', async ({ page }) => {
  await standIn(page, { available: false, allowed: [], puts: [] });
  await page.goto('/people?view=hub', { waitUntil: 'networkidle' });
  await expect(page.getByRole('columnheader', { name: 'Last sign-in' })).toBeVisible();
  await expect(page.getByRole('columnheader', { name: 'OrcaSlicer in the browser' })).toHaveCount(0);
  await expect(page.getByRole('switch')).toHaveCount(0);
});
