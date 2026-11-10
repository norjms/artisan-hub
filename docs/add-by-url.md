# Add a model by URL

Paste a model's link (web app, phone app, browser add-on, or share it to the phone app) and the hub imports it as a
draft model and product: title, description, designer, photos, tags, print settings where the site has them, the
license with a dated license record, and the model files where the site lets a server download them.

Added 2026-11-08. Code: `backend/app/sources/` (one module per site), `backend/app/services/safe_fetch.py`,
`backend/app/routers/clipper.py`, `frontend/src/lib/SourceImport.svelte`, `frontend/src/routes/add-on/`.
Tests: `backend/tests/test_url_import.py` with recorded answers in `backend/tests/fixtures/sources/`.

## Where an imported model goes

Into the hub catalogue, exactly like the MakerWorld/Printables/Thingiverse imports that already existed: a `model`
row (code `<site prefix>-<site id>`, `source_platform`, `source_ext_id`, `source_url`), its photos, a draft
`product` (never live until the license allows and you set it live), and the files copied into the model's folder
in the shared Manyfold library (`/srv/library`, Manyfold `/libraries/artisan`) followed by a Manyfold rescan. So
the files show up in Manyfold for reslicing without a second store.

Not into the OpenPrintHQ engine's library: the hub hands print-ready files to the farm through its existing
Watchtower/OpenPrintHQ paths (slicing, `watchtower.mirror_model`), and imported source models are not print-ready.
Not a parallel store either: one import path (`sources.run_import` -> `sources.base.import_normalized`) serves the
web app, the phone and the add-on.

## Sites

| Site | Code | How the hub reads it | Files | Needs |
|---|---|---|---|---|
| MakerWorld | MW- | MakerWorld API (existing importer: profiles, filament, hardware) | Hub downloads | MakerWorld account in Settings for files |
| Printables | PR- | Public GraphQL `api.printables.com/graphql/` | Hub downloads free models (model-files pack) | Nothing |
| Thingiverse | TV- | Official REST API | Hub downloads | Thingiverse App Token in Settings (connected) |
| Creality Cloud | CR- | Page data (Nuxt) | Hub downloads 3MF | Creality account in Settings for files |
| Thangs | TH- | `thangs.com/api/models/<id>` (the JSON its pages read) | Browser: download is behind Cloudflare and a Thangs sign-in | Nothing for details |
| MyMiniFactory | MMF- | Object page (schema.org JSON-LD, license line, designer, file names) | Browser: downloads need a signed-in account | Nothing for details (the API v2 needs a per-app key) |
| Cults3D | CU- | Official GraphQL API with your nick + API key, or the add-on's page capture | Browser: every download needs a signed-in account | Cults3D API key in Settings for pasted links; the add-on works without one |
| Any other link | WEB- | A direct model file (STL, 3MF, STEP, OBJ, ZIP...) is downloaded; a page gives its OpenGraph/JSON-LD title, description, photos, author, license and up to 10 linked model files | Hub downloads (public hosts only) | Nothing |

"Browser" means the import brings details, photos and the license, marks the model `files_in_browser`, and the
files arrive later from a browser that is signed in to the site: the add-on's **Send files** (it tries the site's
download endpoint with your sign-in, then falls back to picking the files you downloaded), or dropping them on the
model's Files tab.

Paid models anywhere are the same: the hub imports the details; you buy and download, then send the files.

## Licenses

Every import stores the site's own license text (`license_raw`), the mapped code (`license_code`) and a dated
license record (raw site data, the page HTML when the hub could fetch it, a PDF, an Internet Archive copy). Nothing
is stripped; designers are created or matched per site. New codes for the new sites:

- `DESIGNER-CUSTOM`: Thangs designers attach their own license PDF. Recorded with a link to it; not sellable until
  you read it and record a license.
- `STORE-PERSONAL`: MyMiniFactory store license, Cults3D private use and similar: personal use only.
- MyMiniFactory's own labels map to Creative Commons equivalents ("Credit - Remix - Noncommercial" -> BY-NC,
  "Credit - No Remix" -> ND), with the original wording kept.
- A web page's `license` (JSON-LD, `rel=license`, a creativecommons.org link) maps like any CC text; no license is
  `UNKNOWN`, which is never sellable.

## Safety

- **SSRF**: links people paste and photo/file links from sites' answers go through `services/safe_fetch.py`:
  http(s) only, ports 80/443, no `user:pass@`, `.local/.lan/.internal/localhost` refused, every resolved address
  must be public (no private, loopback, link-local, CGNAT, multicast, reserved, IPv4-mapped private), the request
  is pinned to the checked address (Host header and TLS SNI set to the name) so DNS rebinding cannot swap in a LAN
  address, and redirects are followed by hand (5 at most) with every hop checked. Known sites' API calls go to
  fixed hosts built from the id, never to the pasted text.
- **Limits**: 30 s per request (10 s to connect), pages 5 MB, photos 25 MB, files 500 MB, uploads 500 MB a file and
  2 GB a send (50 files), zips at most 500 entries and 2 GB unpacked (no nested zips, `__MACOSX` ignored).
- **Content types**: a page must be HTML; a photo must be `image/*`; a model file must be a model type or have a
  model extension, and an HTML answer for a file (a sign-in page) is refused with "use the browser add-on".
- **Duplicates**: the same site id, the same code or the same source link updates the existing model instead of
  making a new one (the preview says "Already in hub"). Files are de-duplicated per model by SHA-256, and a file
  whose contents are already in another model is reported in the import notes.
- **Page captures** from the add-on (only used when the site refuses the hub) are length-limited, HTML-escaped,
  photos must be https and are fetched through safe_fetch, and the record says it came from the browser.

## Browser add-on keys (clip scope)

The browser add-on signs in with a **clip key**: an ordinary hub API key with the single
scope `clip`, made by a person for themselves on the **Browser add-on** page (`/add-on`, any member who can add
models). A clip key:

- can only call `/api/clip/v1/*`: hello, sites, preview, import, its own person's import jobs, and send files to a
  model that was imported by link; it cannot read the catalogue (`view` is not granted), the store, settings or keys;
- works only while its person is in the team and may add models (checked on every request), expires after a year,
  and is revoked on the same page (or by an admin in Settings, API keys);
- is stored by the add-on in extension storage, never in a web page, and sent only as `Authorization: Bearer`.

## API

Web (signed in): `GET /api/sources/preview?url=`, `POST /api/sources/import {url, profile_id?, category?}`,
`GET /api/jobs/{id}` (existing, now every site above).

Phone (`/api/mobile/v1`, read/write key): `GET /sources/preview?url=` (now with `files_in_browser`,
`download_page`), `POST /sources/import {url, category?, overrides?}` -> `{job_id, model_id, platform,
files_in_browser}`, `GET /jobs/{id}`, and `GET /sources/sites`.

Add-on (`/api/clip/v1`, clip key): `GET /hello`, `GET /sites`, `POST /preview {url, capture?}`,
`POST /import {url, capture?, download_files?, category?}`, `GET /jobs/{id}`, `POST /files` (multipart
`source_url` or `model_id`, `files`), and for signed-in people `GET|POST /keys`, `DELETE /keys/{id}`.

## Site accounts

- Thingiverse: App Token (thingiverse.com/apps/create), Settings > Other model sites. Connected on the live hub.
- Cults3D: your nick and an API key from cults3d.com/en/api/keys, Settings > Other model sites. Not connected yet;
  without it Cults3D links work only from the add-on. The GraphQL field names used (`creation(slug:)`, `name`,
  `description`, `illustrations { imageUrl }`, `license { name url }`, `creator { nick }`, `price { cents }`) follow
  Cults3D's API explorer as published; a minimal query is tried if the full one is refused. Untested against the
  live API (no account here): check it once a key is added.
- MyMiniFactory and Thangs need no account for details.

## Verifying

`cd backend && python -m pytest -q tests/test_url_import.py`
runs the parsers against the recorded fixtures, the SSRF and limit checks, the clip key's scope and the routes.
To refresh a fixture, record the site's answer again and re-run.
