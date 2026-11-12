// People, Whole hub: shared folders for OrcaSlicer in the browser. The owner adds a folder, gives someone read or
// write, and marks one for removal (its files go only from the installer's summary). With one desktop shared by
// everyone, the page says no folder is opened in it. At both desktop sizes, with no page scroll and nothing cut off.
// The hub's answers are stood in for.
import { test, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';

const OUT = process.env.SHOTS_DIR || 'test-results/screens';
const TEAM = [{ id: 1, slug: 'artisan-prints', name: 'Artisan Prints', personal: false, members: 3, models: 212, source_dirs: {}, storage_prefix: '', created_at: '2026-10-01T10:00:00Z' }];
const USERS = [
  { id: 1, name: 'James Norman', email: 'admin@hub.example', platform_admin: true, teams: [{ team_id: 1, role: 'owner' }], last_login_at: '2026-11-12T09:00:00Z' },
  { id: 2, name: 'Riley Chen', email: 'riley@example.com', platform_admin: false, teams: [{ team_id: 1, role: 'admin' }], last_login_at: '2026-11-11T16:20:00Z' },
  { id: 3, name: 'Sam Ortiz', email: 'sam@example.com', platform_admin: false, teams: [{ team_id: 1, role: 'member' }], last_login_at: '2026-11-10T08:05:00Z' }
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

const view = (s) => ({ ...s, read_group: `share-${s.slug}-read`, write_group: `share-${s.slug}-write`, created_at: '2026-11-12T10:00:00Z' });

async function standIn(page, state) {
  await page.route('**/api/platform/teams', (r) => r.fulfill({ json: { teams: TEAM, source_root: '' } }));
  await page.route('**/api/platform/users', (r) => r.fulfill({ json: USERS }));
  await page.route('**/api/platform/invites', (r) => r.fulfill({ json: [] }));
  await page.route('**/api/platform/apps/orcaslicer', (r) => r.fulfill({ json:
    { available: true, group: 'orcaslicer-users', owners: [1], allowed: [1, 2, 3], error: null, desktops: state.desktops === 'per-person' } }));
  await page.route('**/apps/orcaslicer/_desktop/homes**', (r) => r.fulfill({ json: [] }));
  await page.route('**/api/platform/shares**', async (r) => {
    const req = r.request(), url = new URL(req.url()), parts = url.pathname.split('/');
    const method = req.method();
    if (method === 'GET') {
      const live = state.shares.filter((s) => !s.deleting).map((s) => s.slug);
      const access = { 1: Object.fromEntries(live.map((x) => [x, 'write'])), 2: {}, 3: {} };
      for (const [k, v] of Object.entries(state.access)) for (const [slug, a] of Object.entries(v)) if (live.includes(slug) && a !== 'none') access[k][slug] = a;
      return r.fulfill({ json: { available: true, desktops: state.desktops, shares: state.shares.map(view), access, error: null } });
    }
    if (method === 'POST' && parts.length === 4) {
      const name = req.postDataJSON().name.trim();
      const s = { slug: name.toLowerCase().replace(/[^a-z0-9]+/g, '-'), name, deleting: false };
      state.shares.push(s);
      return r.fulfill({ status: 201, json: view(s) });
    }
    const s = state.shares.find((x) => x.slug === parts[4]);
    if (method === 'PATCH') s.name = req.postDataJSON().name;
    if (parts[5] === 'remove') s.deleting = true;
    if (parts[5] === 'keep') s.deleting = false;
    state.calls.push(`${method} ${url.pathname}`);
    return r.fulfill({ json: view(s) });
  });
  await page.route('**/api/platform/users/*/shares/*', async (r) => {
    const parts = new URL(r.request().url()).pathname.split('/');
    const access = r.request().postDataJSON().access;
    (state.access[parts[4]] ||= {})[parts[6]] = access;
    state.calls.push(`PUT ${parts[4]} ${parts[6]} ${access}`);
    return r.fulfill({ json: { user_id: Number(parts[4]), slug: parts[6], access, changed: true } });
  });
}

async function check(page, info, name) {
  const dir = `${OUT}/${info.project.name}`;
  mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: `${dir}/${name}.png` });
  const r = await page.evaluate(fits);
  expect(r.pageScroll, 'the page scrolls').toBe(false);
  expect(r.clipped, 'cut off').toEqual([]);
}

test('the owner adds a shared folder, gives people read or write, and marks one for removal', async ({ page }, info) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  const state = {
    desktops: 'per-person', calls: [],
    shares: [{ slug: 'production', name: 'Production', deleting: false }, { slug: 'old-jigs', name: 'Old jigs', deleting: true }],
    access: { 2: { production: 'write' } }
  };
  await standIn(page, state);
  page.on('dialog', (d) => d.accept());
  await page.goto('/people?view=hub', { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  const card = page.getByTestId('shared-folders');
  await expect(card.getByRole('row', { name: /Production/ })).toContainText('1 can write');
  await expect(card.getByRole('row', { name: /Old jigs/ })).toContainText('Marked for removal');
  await check(page, info, 'people_hub_shared_folders');

  await card.getByLabel("New shared folder's name").fill('Customer jobs');
  await card.getByRole('button', { name: 'Add folder' }).click();
  await expect(card.getByRole('row', { name: /Customer jobs/ })).toContainText('Owners only');

  await page.getByRole('button', { name: 'Choose shared folders for Sam Ortiz' }).click();
  const dialog = page.getByRole('dialog', { name: 'Shared folders for Sam Ortiz' });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText('Takes effect the next time their OrcaSlicer desktop starts.')).toBeVisible();
  await expect(dialog.getByLabel('Old jigs')).toHaveCount(0);
  await dialog.getByLabel('Production').selectOption('read');
  await expect(page.getByText('Sam Ortiz: Production, read only')).toBeVisible();
  await check(page, info, 'people_hub_shared_folders_person');
  await dialog.getByRole('button', { name: 'Done' }).click();
  await expect(page.getByRole('row', { name: /Sam Ortiz/ })).toContainText('1 of 2');

  await card.getByRole('row', { name: /Old jigs/ }).getByRole('button', { name: 'Keep it' }).click();
  await expect(card.getByRole('row', { name: /Old jigs/ })).not.toContainText('Marked for removal');
  await card.getByRole('row', { name: /Customer jobs/ }).getByRole('button', { name: 'Remove' }).click();
  await expect(card.getByRole('row', { name: /Customer jobs/ })).toContainText("Its files go when you confirm on the installer's summary.");
  expect(state.calls).toEqual(['PUT 3 production read', 'POST /api/platform/shares/old-jigs/keep', 'POST /api/platform/shares/customer-jobs/remove']);
  expect(errors).toEqual([]);
});

test('with one desktop for everyone the page says no shared folder is opened in it', async ({ page }, info) => {
  await standIn(page, { desktops: 'shared', calls: [], shares: [{ slug: 'production', name: 'Production', deleting: false }], access: {} });
  await page.goto('/people?view=hub', { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  await expect(page.getByTestId('shared-folders')).toContainText('Everyone shares one OrcaSlicer desktop now, so it opens no shared folders.');
  await check(page, info, 'people_hub_shared_folders_one_desktop');
});
