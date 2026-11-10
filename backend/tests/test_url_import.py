"""Add a model by URL: site parsers against recorded fixtures, the SSRF-safe fetcher, licenses, the clip key and
the browser add-on routes. No network: every site answer comes from tests/fixtures/sources (recorded 2026-11-08
from the live sites, except cults3d_creation.json, which is built from the documented GraphQL shape because Cults3D
needs an account)."""
import gzip
import io
import json
import secrets as _s
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

FX = Path(__file__).parent / "fixtures" / "sources"


def fx(name):
    p = FX / name
    if name.endswith(".gz"):
        return gzip.open(p, "rt").read()
    return json.loads(p.read_text())


# ---------------------------------------------------------------- link detection
@pytest.mark.parametrize("url,platform,ext", [
    ("https://thangs.com/designer/Mandalorian/3d-model/Benchy%20%23Benchy%27sLegacy-56337", "thangs", "56337"),
    ("https://thangs.com/m/1058038", "thangs", "1058038"),
    ("https://thangs.com/designer/LoftedGoods/3d-model/Eero%20Planter-1058038?part=1", "thangs", "1058038"),
    ("https://www.myminifactory.com/object/3d-print-drone-joust-17834", "myminifactory", "17834"),
    ("https://www.myminifactory.com/fr/object/3d-print-drone-joust-17834#download", "myminifactory", "17834"),
    ("https://cults3d.com/en/3d-model/home/eero-planter", "cults3d", "eero-planter"),
    ("https://cults3d.com/fr/modele-3d/maison/pot-de-fleur?x=1", "cults3d", "pot-de-fleur"),
    ("https://www.printables.com/model/3161-3d-benchy", "printables", "3161"),
    ("https://www.thingiverse.com/thing:763622/files", "thingiverse", "763622"),
    ("www.printables.com/model/3161-3d-benchy", "printables", "3161"),
])
def test_detect_sites(url, platform, ext):
    from app.sources import detect
    assert detect(url) == (platform, ext)


def test_detect_any_other_link_is_web_and_stable():
    from app.sources import detect
    p, i = detect("https://example.com/files/bracket.stl?utm_source=x#top")
    assert p == "web" and len(i) == 16
    assert detect("https://EXAMPLE.com/files/bracket.stl")[1] == i      # same link, same model
    with pytest.raises(ValueError):
        detect("not a link")
    with pytest.raises(ValueError):
        detect("ftp://example.com/a.stl")
    with pytest.raises(ValueError):
        detect("https://www.myminifactory.com/users/RicSal")         # a site page that is not a model


# ---------------------------------------------------------------- parsers
def test_printables_fixture(monkeypatch):
    from app.sources import printables
    d = fx("printables_3161.json")
    monkeypatch.setattr(printables, "gql", lambda q, v=None, cache_s=0: d["data"])
    n = printables.fetch("3161")
    assert n["title"] == "3D BENCHY" and n["license_raw"] == "CC0" and n["platform"] == "printables"
    assert n["designer"]["handle"] and n["photos"] and n["files"]
    assert n["url"].startswith("https://www.printables.com/model/3161")
    from app.services.licensing import normalize
    assert normalize(n["license_raw"], "printables") == "CC0"


def test_thingiverse_fixture(monkeypatch):
    from app.sources import thingiverse
    d = fx("thingiverse_763622.json")
    monkeypatch.setattr(thingiverse.Client, "get", lambda self, path, cache_s=0, **kw: d[path])
    n = thingiverse.fetch("token-not-used", "763622")
    assert n["title"].startswith("#3DBenchy") and n["designer"]["name"] == "CreativeTools"
    assert n["license_raw"] == "Creative Commons - Public Domain Dedication"
    assert any(f["name"] == "3DBenchy.stl" and f["kind"] == "stl" for f in n["files"])
    assert len(n["photos"]) >= 3
    from app.services.licensing import normalize
    assert normalize(n["license_raw"], "thingiverse") == "CC0"


def test_thangs_fixture_free_and_designer_license():
    from app.sources import thangs
    from app.services.licensing import normalize, info
    n = thangs.normalize(fx("thangs_56337.json"))
    assert n["title"] == "Benchy #Benchy'sLegacy" and n["designer"]["name"] == "Mandalorian"
    assert n["license_raw"] is None and normalize(n["license_raw"], "thangs") == "UNKNOWN"
    assert n["photos"][0].endswith("/3dBenchy.png")          # the model's own render
    # attachments in this fixture were uploaded by other people (makes): never imported as the designer's photos
    assert all("production-thangs-public/uploads/attachments" not in p for p in n["photos"])
    assert n["files"][0]["name"] == "3dBenchy.stl" and n["files_in_browser"] is True
    assert n["url"] == "https://thangs.com/designer/Mandalorian/3d-model/Benchy%20%23Benchy%27sLegacy-56337"
    m = thangs.normalize(fx("thangs_1058038.json"))
    assert m["license_raw"] == "Thangs designer license: Hendricks Design License - Restrictive.pdf"
    assert "showLicense=true" in m["license_text"]
    code = normalize(m["license_raw"], "thangs")
    assert code == "DESIGNER-CUSTOM" and info(code)["sell"] is False


def test_myminifactory_fixture():
    from app.sources import myminifactory
    from app.services.licensing import normalize
    n = myminifactory.parse_page(fx("myminifactory_17834.html.gz"), "17834")
    assert n["title"] == "Battle Armor Parrot Race Drone!"
    assert n["designer"] == {"name": "Ricardo Salomao", "handle": "RicSal", "url": "https://www.myminifactory.com/users/RicSal"}
    assert n["license_raw"] == "MyMiniFactory - Credit - Remix - Noncommercial"
    assert normalize(n["license_raw"], "myminifactory") == "BY-NC"
    assert "Knight-Base.stl" in [f["name"] for f in n["files"]]
    assert n["photos"][0].startswith("https://assets.myminifactory.com/object-images/")   # the original, not the resize
    assert n["files_in_browser"] and n["download_page"] == "https://www.myminifactory.com/download/17834"
    assert n["category"] == "Gadgets & Electronics" or n["category"]


def test_myminifactory_page_without_model_is_404():
    from app.sources import myminifactory
    from app.clients.base import ApiError
    with pytest.raises(ApiError):
        myminifactory.parse_page("<html><body>gone</body></html>", "1")


def test_cults3d_without_key_and_with_api(monkeypatch):
    from app.sources import cults3d
    from app.clients.base import ApiError
    with pytest.raises(ApiError) as e:
        cults3d.fetch({}, "eero-planter")
    assert e.value.status == 403
    d = fx("cults3d_creation.json")
    calls = []
    monkeypatch.setattr(cults3d.HTTP, "post", lambda url, **kw: calls.append(kw) or d)
    n = cults3d.fetch({"username": "me", "token": "k"}, "eero-planter")
    assert calls[0]["auth"] == ("me", "k") and calls[0]["json"]["variables"] == {"slug": "eero-planter"}
    assert n["title"] == "Eero planter" and n["designer"]["name"] == "LoftedGoods" and n["paid"] is False
    assert n["license_raw"] == "CC BY-NC-SA" and n["photos"] and n["files_in_browser"]
    from app.services.licensing import normalize
    assert normalize(n["license_raw"], "cults3d") == "BY-NC-SA"
    assert normalize("Cults - Private use", "cults3d") == "STORE-PERSONAL"


def test_capture_from_browser_is_sanitised():
    from app.sources import from_capture
    n = from_capture("cults3d", "x", "https://cults3d.com/en/3d-model/home/x", {
        "title": "  Planter  ", "description": "Line one.\n\n<script>alert(1)</script>",
        "images": ["https://cdn.example.com/a.jpg", "http://insecure.example.com/b.jpg", "javascript:alert(1)"],
        "designer": {"name": "Ann", "url": "https://cults3d.com/en/users/ann"}, "license": "CC BY",
        "tags": ["pot", 3, "x" * 100], "files": [{"name": "pot.stl", "size": 10}, {"bad": 1}]})
    assert n["title"] == "Planter" and n["photos"] == ["https://cdn.example.com/a.jpg"]
    assert "<script>" not in n["description_html"] and "&lt;script&gt;" in n["description_html"]
    assert n["tags"] == ["pot", "x" * 60] and n["files"][0]["name"] == "pot.stl" and len(n["files"]) == 1
    assert n["from_browser"] and n["files_in_browser"] and n["raw"]["captured_by"]


def test_web_meta_from_html():
    from app.sources.web import meta_from_html
    html = """<html><head><title>Fallback</title>
    <meta property="og:title" content="Cable clip &amp; holder"><meta property="og:image" content="/img/clip.jpg">
    <script type="application/ld+json">{"@context":"https://schema.org","@type":"3DModel","name":"Cable clip",
      "description":"A clip.","author":{"@type":"Person","name":"Jo","url":"https://example.com/jo"},
      "license":"https://creativecommons.org/licenses/by-sa/4.0/","keywords":"clip, cable"}</script>
    </head><body><a href="files/clip.stl">STL</a><a href="https://cdn.example.com/clip.3mf">3MF</a>
    <a href="/about">about</a></body></html>"""
    m = meta_from_html(html, "https://example.com/things/clip")
    assert m["title"] == "Cable clip" and m["designer"]["name"] == "Jo" and m["license_raw"] == "CC BY-SA"
    assert m["images"] == ["https://example.com/img/clip.jpg"] and m["tags"] == ["clip", "cable"]
    assert [f["url"] for f in m["files"]] == ["https://example.com/things/files/clip.stl", "https://cdn.example.com/clip.3mf"]
    from app.services.licensing import normalize
    assert normalize(m["license_raw"], "web") == "BY-SA"


# ---------------------------------------------------------------- SSRF-safe fetcher
def _resolver(table):
    def r(host):
        return table[host]
    return r


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/a.stl", "http://10.0.0.5/a.stl", "http://192.168.0.10:8080/", "http://[::1]/",
    "http://169.254.169.254/latest/meta-data/", "http://100.64.0.1/", "http://localhost/", "http://hub.lan/x",
    "http://printer.local/x", "file:///etc/passwd", "gopher://example.com/", "http://user:pw@example.com/",
    "http://example.com:8080/", "http://0.0.0.0/", "http://[::ffff:192.168.0.1]/", "http://intranet/",
])
def test_check_url_refuses_private_and_odd(url):
    from app.services.safe_fetch import check_url, FetchError
    with pytest.raises(FetchError):
        check_url(url, _resolver({"example.com": ["93.184.215.14"]}))


def test_check_url_refuses_a_name_that_resolves_inside():
    from app.services.safe_fetch import check_url, FetchError
    with pytest.raises(FetchError):
        check_url("https://rebind.example.com/", _resolver({"rebind.example.com": ["93.184.215.14", "192.168.0.10"]}))
    assert check_url("https://ok.example.com/a", _resolver({"ok.example.com": ["93.184.215.14"]}))[1] == "ok.example.com"


def _transport(routes):
    def handler(request: httpx.Request):
        key = (request.headers["host"], request.url.path)
        status, headers, body = routes[key]
        return httpx.Response(status, headers=headers, content=body)
    return httpx.MockTransport(handler)


def test_fetch_pins_ip_follows_checked_redirects_and_limits():
    from app.services.safe_fetch import fetch, FetchError
    res = _resolver({"a.example.com": ["93.184.215.14"], "b.example.com": ["93.184.215.15"],
                     "evil.example.com": ["10.0.0.1"]})
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers["host"]))
        k = (request.headers["host"], request.url.path)
        return {
            ("a.example.com", "/m.stl"): httpx.Response(302, headers={"location": "https://b.example.com/real.stl"}),
            ("b.example.com", "/real.stl"): httpx.Response(200, headers={"content-type": "application/sla"}, content=b"solid x"),
            ("a.example.com", "/evil"): httpx.Response(302, headers={"location": "http://evil.example.com/"}),
            ("a.example.com", "/page"): httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html></html>"),
            ("a.example.com", "/big.stl"): httpx.Response(200, headers={"content-type": "application/sla"}, content=b"x" * 2048),
            ("a.example.com", "/login.stl"): httpx.Response(200, headers={"content-type": "text/html"}, content=b"<form>"),
        }[k]
    t = httpx.MockTransport(handler)
    f = fetch("http://a.example.com/m.stl", "file", resolver=res, transport=t)
    assert f.content == b"solid x" and f.filename == "real.stl" and f.url == "https://b.example.com/real.stl"
    assert seen[0][0].startswith("http://93.184.215.14/") and seen[1][0].startswith("https://93.184.215.15/")
    with pytest.raises(FetchError):
        fetch("http://a.example.com/evil", "html", resolver=res, transport=t)          # redirect into the LAN
    with pytest.raises(FetchError):
        fetch("http://a.example.com/page", "file", resolver=res, transport=t)          # a page is not a model file
    with pytest.raises(FetchError) as e:
        fetch("http://a.example.com/big.stl", "file", max_bytes=1024, resolver=res, transport=t)
    assert e.value.status == 413
    with pytest.raises(FetchError) as e:
        fetch("http://a.example.com/login.stl", "file", resolver=res, transport=t)
    assert "signed in" in e.value.message
    p = fetch("http://a.example.com/m.stl", "page", resolver=res, transport=t)
    assert p.content is None and p.filename == "real.stl"                              # a file, seen as a file


# ---------------------------------------------------------------- licenses
@pytest.mark.parametrize("raw,platform,code", [
    ("MyMiniFactory - Credit - Remix - Noncommercial", "myminifactory", "BY-NC"),
    ("MyMiniFactory - Credit - Remix - Commercial", "myminifactory", "BY"),
    ("MyMiniFactory - Credit - No Remix - Noncommercial", "myminifactory", "BY-NC-ND"),
    ("MyMiniFactory Standard Digital File Store License", "myminifactory", "STORE-PERSONAL"),
    ("Thangs designer license: x.pdf", "thangs", "DESIGNER-CUSTOM"),
    ("CC BY-NC-SA", "cults3d", "BY-NC-SA"), ("Cults - Private use", "cults3d", "STORE-PERSONAL"),
    ("CC BY-SA", "web", "BY-SA"), (None, "web", "UNKNOWN"),
])
def test_licenses_new_sites(raw, platform, code):
    from app.services.licensing import normalize, LICENSES
    assert normalize(raw, platform) == code and code in LICENSES


# ---------------------------------------------------------------- clip key and routes
def _client():
    from app.main import app
    return TestClient(app, follow_redirects=False)


def _member(role="member"):
    from app.db import session_scope
    from app.models import Membership, User
    from app.tenant import unscoped
    tag = _s.token_hex(3)
    with session_scope() as db, unscoped():
        u = User(sub=f"c-{tag}", email=f"c-{tag}@x.example", name="C")
        db.add(u)
        db.flush()
        db.add(Membership(team_id=1, user_id=u.id, role=role))
        return u.id


def _key(scopes, user_id):
    from app.auth import new_api_key
    from app.db import session_scope
    with session_scope() as db:
        return new_api_key(db, "clip test", scopes, team_id=1, user_id=user_id)


def H(raw):
    return {"Authorization": f"Bearer {raw}"}


def test_clip_key_is_scope_limited():
    c = _client()
    raw = _key(["clip"], _member())
    r = c.get("/api/clip/v1/hello", headers=H(raw))
    assert r.status_code == 200 and r.json()["clip_only"] is True
    assert {s["platform"] for s in r.json()["sites"]} >= {"printables", "thingiverse", "makerworld", "thangs",
                                                          "myminifactory", "cults3d", "web"}
    assert c.get("/api/models", headers=H(raw)).status_code == 403          # no reading the catalogue
    assert c.get("/api/mobile/v1/me", headers=H(raw)).status_code in (401, 403)
    assert c.post("/api/clip/v1/keys", json={}, headers=H(raw)).status_code == 403   # can't make keys
    # a viewer's clip key does nothing: the person can't add models
    raw_v = _key(["clip"], _member("viewer"))
    assert c.get("/api/clip/v1/hello", headers=H(raw_v)).status_code == 403
    # a clip key without a person is refused too
    from app.auth import new_api_key
    from app.db import session_scope
    with session_scope() as db:
        raw_n = new_api_key(db, "x", ["clip"], team_id=1)
    assert c.get("/api/clip/v1/hello", headers=H(raw_n)).status_code == 403


def test_clip_import_and_send_files(monkeypatch):
    from app.sources import thangs, base
    from app.services import licensing, safe_fetch
    from app.db import session_scope
    from app import sources
    import app.sources as srcs_mod
    monkeypatch.setattr(licensing, "wayback", lambda url: None)
    monkeypatch.setattr(safe_fetch, "image_bytes", lambda u: _png_bytes())
    d = fx("thangs_1058038.json")
    monkeypatch.setattr(thangs, "fetch", lambda mid: {**thangs.normalize(d), "supplies": []})
    monkeypatch.setattr(thangs, "page_html", lambda n: "<html>thangs page</html>")
    c = _client()
    raw = _key(["clip"], _member())
    url = "https://thangs.com/designer/LoftedGoods/3d-model/Eero%20Planter-1058038"
    pv = c.post("/api/clip/v1/preview", json={"url": url}, headers=H(raw))
    assert pv.status_code == 200, pv.text
    pv = pv.json()
    assert pv["platform"] == "thangs" and pv["files_in_browser"] and pv["license"] == "DESIGNER-CUSTOM"
    assert pv["sellable"] is False and pv["existing_model_id"] is None
    # run the job's work directly (the worker is not running in tests)
    with session_scope() as db:
        m, report = srcs_mod.run_import(db, "thangs", "1058038", url=url)
        mid = m.id
        assert report["files_in_browser"] and m.license_code == "DESIGNER-CUSTOM" and m.code == "TH-1058038"
        assert m.extra["thangs"]["files_in_browser"] is True and m.images
    pv2 = c.post("/api/clip/v1/preview", json={"url": url}, headers=H(raw)).json()
    assert pv2["existing_model_id"] == mid                                     # duplicate by source link
    stl = b"solid planter\nendsolid planter\n"
    r = c.post("/api/clip/v1/files", data={"source_url": url}, headers=H(raw),
               files=[("files", ("Eero Planter.stl", stl, "application/sla")),
                      ("files", ("readme.txt", b"hi", "text/plain"))])
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["added"] == ["Eero Planter.stl"] and any("readme.txt" in n for n in j["notes"])
    r = c.post("/api/clip/v1/files", data={"model_id": str(mid)}, headers=H(raw),
               files=[("files", ("again.stl", stl, "application/sla"))])
    assert r.json()["added"] == [] and any("already in this model" in n for n in r.json()["notes"])
    with session_scope() as db:
        from app.models import Model
        assert db.get(Model, mid).extra["thangs"]["files_in_browser"] is False
    # a zip with a model inside is unpacked; zip entries that are not models are ignored
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("parts/tray.stl", "solid tray\nendsolid tray\n")
        z.writestr("__MACOSX/parts/._tray.stl", "junk")
        z.writestr("notes.pdf", "pdf")
    r = c.post("/api/clip/v1/files", data={"model_id": str(mid)}, headers=H(raw),
               files=[("files", ("planter.zip", buf.getvalue(), "application/zip"))])
    assert r.json()["added"] == ["tray.stl"]


def test_clip_key_cannot_send_files_to_a_hand_made_model():
    from app.db import session_scope
    from app.models import Model
    with session_scope() as db:
        m = Model(code=f"AP-T{_s.token_hex(2)}", name="Hand made", slug=f"hand-{_s.token_hex(3)}", source="own", status="draft")
        db.add(m)
        db.flush()
        mid = m.id
    c = _client()
    raw = _key(["clip"], _member())
    r = c.post("/api/clip/v1/files", data={"model_id": str(mid)}, headers=H(raw),
               files=[("files", ("a.stl", b"solid a\nendsolid a\n", "application/sla"))])
    assert r.status_code == 403
    assert c.post("/api/clip/v1/files", data={"source_url": "https://thangs.com/m/99999999"}, headers=H(raw),
                  files=[("files", ("a.stl", b"x", "application/sla"))]).status_code == 404


def test_cults_capture_used_when_the_site_refuses(monkeypatch):
    c = _client()
    raw = _key(["clip"], _member())
    url = "https://cults3d.com/en/3d-model/home/eero-planter"
    r = c.post("/api/clip/v1/preview", json={"url": url}, headers=H(raw))
    assert r.status_code == 400 and "browser add-on" in r.json()["detail"]
    cap = {"title": "Eero planter", "images": ["https://images.cults3d.com/x.jpg"], "license": "CC BY-NC-SA",
           "designer": {"name": "LoftedGoods"}, "files": [{"name": "planter.stl"}]}
    r = c.post("/api/clip/v1/preview", json={"url": url, "capture": cap}, headers=H(raw))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["from_browser"] and d["license"] == "BY-NC-SA" and d["files_in_browser"] and d["title"] == "Eero planter"


def test_clip_import_queues_job_with_url_and_capture():
    c = _client()
    raw = _key(["clip"], _member())
    url = "https://www.myminifactory.com/object/3d-print-drone-joust-17834"
    r = c.post("/api/clip/v1/import", json={"url": url, "capture": {"title": "x"}}, headers=H(raw))
    assert r.status_code == 200 and r.json()["platform"] == "myminifactory" and r.json()["files_in_browser"]
    jid = r.json()["job_id"]
    from app.db import session_scope
    from app.models import Job
    with session_scope() as db:
        j = db.get(Job, jid)
        assert j.kind == "source.import" and j.payload["url"] == url and j.payload["capture"] == {"title": "x"}
    s = c.get(f"/api/clip/v1/jobs/{jid}", headers=H(raw)).json()
    assert s["status"] == "queued"
    other = _key(["clip"], _member())
    assert c.get(f"/api/clip/v1/jobs/{jid}", headers=H(other)).status_code == 404   # someone else's job


def test_clip_keys_managed_by_signed_in_person():
    c = _client()      # dev bypass: signed in as the owner
    r = c.post("/api/clip/v1/keys", json={"name": "Firefox"})
    assert r.status_code == 200 and r.json()["key"].startswith("ahk_")
    from app.db import session_scope
    from app.models import ApiKey
    with session_scope() as db:
        k = db.get(ApiKey, r.json()["id"])
        assert k.scopes == ["clip"] and k.expires_at is not None


def test_mobile_sites_and_preview_flag(monkeypatch):
    from app.sources import thangs
    monkeypatch.setattr(thangs, "fetch", lambda mid: {**thangs.normalize(fx("thangs_56337.json")), "supplies": []})
    c = _client()
    raw = _key(["read", "write"], None)
    s = c.get("/api/mobile/v1/sources/sites", headers=H(raw))
    assert s.status_code == 200 and any(x["platform"] == "thangs" for x in s.json()["sites"])
    pv = c.get("/api/mobile/v1/sources/preview", params={"url": "https://thangs.com/m/56337"}, headers=H(raw))
    assert pv.status_code == 200, pv.text
    assert pv.json()["files_in_browser"] is True and pv.json()["platform"] == "thangs"


def _png_bytes():
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 120, 200)).save(b, "PNG")
    return b.getvalue()
