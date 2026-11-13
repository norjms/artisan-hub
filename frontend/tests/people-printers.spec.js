// People: printers shared by the team, narrowed per person by the owner. Everyone has every printer until the owner
// chooses; owners always have all. At both desktop sizes, with no page scroll and nothing cut off. The hub's answers
// are stood in for.
import { test, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';

const OUT = process.env.SHOTS_DIR || 'test-results/screens';
const PRINTERS = [{ id: '1', name: 'H2D' }, { id: '2', name: 'MK4S' }, { id: '3', name: 'Voron 2.4' }, { id: '4', name: 'X1C' }];
const ALL = ['view', 'models', 'farm', 'inventory', 'people'];

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
  await page.route('**/api/team', (r) => r.fulfill({ json: {
    id: 1, slug: 'artisan-prints', name: 'Artisan Prints', personal: false, role: 'owner', my_perms: ALL,
    perm_catalog: Object.fromEntries(ALL.map((x) => [x, x])), role_perms: { viewer: ['view'], member: ALL.slice(0, 4), admin: ALL, owner: ALL },
    members: state.members, invites: [] } }));
  await page.route('**/api/team/printers', (r) => r.fulfill({ json: { printers: PRINTERS } }));
  await page.route('**/api/team/members/*/printers', async (r) => {
    const id = Number(new URL(r.request().url()).pathname.split('/')[4]);
    const printers = r.request().postDataJSON().printers;
    const m = state.members.find((x) => x.user_id === id);
    m.printers = printers;
    state.calls.push(`PUT ${id} ${JSON.stringify(printers)}`);
    return r.fulfill({ json: m });
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

const person = (user_id, name, role, printers) => ({ user_id, name, email: `${name.split(' ')[0].toLowerCase()}@example.com`,
  username: null, role, perms: role === 'owner' ? ALL : ALL.slice(0, 4), custom: false, joined_at: '2026-11-01T10:00:00Z',
  last_login_at: '2026-11-12T09:00:00Z', printers });

test('the owner narrows the printers one person may use, and back to all', async ({ page }, info) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  const state = { calls: [], members: [person(1, 'James Norman', 'owner', 'all'), person(2, 'Riley Chen', 'member', 'all'),
    person(3, 'Sam Ortiz', 'member', ['2'])] };
  await standIn(page, state);
  await page.goto('/people', { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  await expect(page.getByRole('row', { name: /Riley Chen/ })).toContainText('All');
  await expect(page.getByRole('row', { name: /Sam Ortiz/ })).toContainText('1 of 4');
  await expect(page.getByRole('button', { name: 'Choose printers for James Norman' })).toHaveCount(0);
  const box = await page.locator('table.grid').first().evaluate((t) => {
    const card = t.closest('section').getBoundingClientRect(), r = t.getBoundingClientRect();
    return { table: Math.round(r.right), card: Math.round(card.right) };
  });
  expect(box.table, 'the table fits its card').toBeLessThanOrEqual(box.card);
  await expect(page.getByRole('row', { name: /Riley Chen/ }).getByRole('button', { name: 'Remove' })).toBeInViewport();
  await check(page, info, 'people_printers');

  await page.getByRole('button', { name: 'Choose printers for Riley Chen' }).click();
  const dialog = page.getByRole('dialog', { name: 'Printers for Riley Chen' });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel('Only the printers chosen below').check();
  await dialog.getByLabel('H2D').uncheck();
  await dialog.getByLabel('X1C').uncheck();
  await check(page, info, 'people_printers_person');
  await dialog.getByRole('button', { name: 'Save printers' }).click();
  await expect(page.getByRole('row', { name: /Riley Chen/ })).toContainText('2 of 4');

  await page.getByRole('button', { name: 'Choose printers for Sam Ortiz' }).click();
  await page.getByRole('dialog', { name: 'Printers for Sam Ortiz' }).getByLabel('All printers, including ones added later').check();
  await page.getByRole('dialog', { name: 'Printers for Sam Ortiz' }).getByRole('button', { name: 'Save printers' }).click();
  await expect(page.getByRole('row', { name: /Sam Ortiz/ })).toContainText('All');
  expect(state.calls).toEqual(['PUT 2 ["2","3"]', 'PUT 3 "all"']);
  expect(errors).toEqual([]);
});
