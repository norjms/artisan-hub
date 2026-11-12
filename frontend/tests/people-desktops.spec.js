// People, Whole hub, on an installation that gives each person an OrcaSlicer desktop of their own: "Desktop files"
// lists each desktop from the installation (who has one open, who has left the hub), and the owner removes one
// after confirming. At both desktop sizes, opened, with no page scroll and nothing cut off. The hub's and the
// installation's answers are stood in for, the way an installation gives them.
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

async function standIn(page, state) {
  await page.route('**/api/platform/teams', (r) => r.fulfill({ json: { teams: TEAM, source_root: '' } }));
  await page.route('**/api/platform/users', (r) => r.fulfill({ json: USERS }));
  await page.route('**/api/platform/invites', (r) => r.fulfill({ json: [] }));
  await page.route('**/api/platform/apps/orcaslicer', (r) => r.fulfill({ json:
    { available: true, group: 'orcaslicer-users', owners: [1], allowed: [1, 2], error: null, desktops: state.desktops } }));
  await page.route('**/apps/orcaslicer/_desktop/homes**', async (r) => {
    const req = r.request();
    if (req.method() === 'DELETE') {
      state.headers.push(req.headers()['x-ahi-desktop']);
      const who = decodeURIComponent(req.url().split('/').at(-1));
      state.rows = state.rows.filter((d) => d.person !== who);
      state.removed.push(who);
    }
    return r.fulfill({ json: state.rows });
  });
}

test('the owner sees each person\'s desktop files and removes those of someone who left', async ({ page }, info) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  const state = {
    desktops: true, headers: [], removed: [],
    rows: [
      { person: 'owner', name: 'James Norman', current: true, running: true },
      { person: 'riley', name: 'Riley Chen', current: true, running: false },
      { person: 'morgan', name: 'morgan', current: false, running: false }
    ]
  };
  await standIn(page, state);
  page.on('dialog', (d) => d.accept());
  await page.goto('/people?view=hub', { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  const files = page.getByTestId('desktop-files');
  await expect(files.locator('summary')).toHaveText(/Desktop files \(3\)/);
  await files.locator('summary').click();
  await expect(files.getByText('Open now')).toBeVisible();
  await expect(files.getByText('Left the hub')).toBeVisible();
  const dir = `${OUT}/${info.project.name}`;
  mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: `${dir}/people_hub_desktop_files.png` });
  const r = await page.evaluate(fits);
  expect(r.pageScroll, 'the page scrolls').toBe(false);
  expect(r.clipped, 'cut off').toEqual([]);

  await files.getByRole('row', { name: /morgan/ }).getByRole('button', { name: 'Remove files' }).click();
  await expect(page.getByText("morgan's desktop files were removed")).toBeVisible();
  await expect(files.locator('summary')).toHaveText(/Desktop files \(2\)/);
  expect(state.removed).toEqual(['morgan']);
  expect(state.headers).toEqual(['1']);
  expect(errors).toEqual([]);
});

test('with one shared desktop People lists no desktop files', async ({ page }) => {
  await standIn(page, { desktops: false, rows: [], headers: [], removed: [] });
  await page.goto('/people?view=hub', { waitUntil: 'networkidle' });
  await expect(page.getByRole('columnheader', { name: 'OrcaSlicer in the browser' })).toBeVisible();
  await expect(page.getByTestId('desktop-files')).toHaveCount(0);
});
