"""Thingiverse through its official REST API (https://www.thingiverse.com/developers).

The API needs an App Token: create a free app at thingiverse.com/apps/create (type "Web App"),
copy its App Token and paste it in hub Settings. The token reads public things and downloads files.
"""
import re

from ..clients.base import ApiError
from .base import Http, supplies_from_text, file_kind

API = "https://api.thingiverse.com"
WEB = "https://www.thingiverse.com"
PAGE = Http("thingiverse web page", gap=1.0)   # separate: a bot check on the website must not block the API
HTTP = Http("thingiverse", gap=1.1)       # API allows 300 requests / 5 min
SORT = {"downloads": "popular", "likes": "popular", "newest": "newest", "relevance": "relevant", "prints": "makes"}


def parse_id(url: str) -> str | None:
    m = re.search(r"thingiverse\.com/thing:(\d+)", url or "")
    return m.group(1) if m else None


class Client:
    def __init__(self, token: str | None):
        if not token:
            raise ApiError("thingiverse", 401, "add a Thingiverse App Token in Settings first")
        self.h = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def get(self, path: str, cache_s: int = 0, **params):
        return HTTP.get(API + path, headers=self.h, params=params or None, cache_s=cache_s)

    def thing(self, tid: str) -> dict:
        return self.get(f"/things/{int(tid)}", cache_s=120)

    def license_of(self, tid: str) -> str | None:
        try:
            return self.get(f"/things/{int(tid)}", cache_s=86400).get("license")
        except ApiError:
            return None


def fetch(token: str | None, tid: str) -> dict:
    c = Client(token)
    t = c.thing(tid)
    images = c.get(f"/things/{int(tid)}/images", cache_s=120) or []
    files = c.get(f"/things/{int(tid)}/files", cache_s=120) or []
    try:
        tags = [x.get("name") for x in c.get(f"/things/{int(tid)}/tags", cache_s=600) or []]
    except ApiError:
        tags = [x.get("name") if isinstance(x, dict) else x for x in t.get("tags") or []]
    photos = []
    for im in images:
        best = None
        for s in im.get("sizes") or []:
            if s.get("type") == "display" and s.get("size") == "large":
                best = s.get("url")
        photos.append(best or im.get("url"))
    creator = t.get("creator") or {}
    desc = t.get("description_html") or t.get("description") or ""
    instr = t.get("instructions_html") or t.get("instructions") or ""
    details = "".join(f"<h3>{p.get('name')}</h3>" + "".join(f"<p>{x.get('data')}</p>" for x in p.get("data") or [] if isinstance(x, dict))
                      for p in t.get("details_parts") or [] if isinstance(p, dict))
    settings = {}
    for p in t.get("details_parts") or []:
        if isinstance(p, dict) and (p.get("name") or "").lower().startswith("print settings"):
            for x in p.get("data") or []:
                if isinstance(x, dict):
                    for k, v in x.items():
                        settings[k.lower()] = v
    cats = t.get("categories") or []
    return {"platform": "thingiverse", "id": str(t["id"]), "url": t.get("public_url") or f"{WEB}/thing:{t['id']}",
            "title": t.get("name") or f"Thing {tid}", "description_html": desc + (f"<h3>Instructions</h3>{instr}" if instr else "") + details,
            "tags": [x for x in tags if x], "category": (cats[0].get("name") if cats and isinstance(cats[0], dict) else None),
            "designer": {"name": creator.get("name"), "handle": creator.get("name"), "url": creator.get("public_url")},
            "license_raw": t.get("license"), "license_text": t.get("license"),
            "photos": [p for p in photos if p],
            "profiles": [{"id": "designer", "title": "Designer settings", "default": True,
                          "printer": settings.get("printer") or settings.get("printer brand"),
                          "layer_height": settings.get("resolution"), "infill": settings.get("infill"),
                          "supports": settings.get("supports"),
                          "filaments": [{"type": (settings.get("filament material") or settings.get("filament_material") or "PLA"),
                                         "color": None, "grams": None}]}],
            "files": [{"id": str(f["id"]), "name": f["name"], "size": f.get("size"), "kind": file_kind(f["name"]),
                       "download": f.get("download_url")} for f in files],
            "supplies": supplies_from_text(desc + instr + details),
            "stats": {"downloads": t.get("download_count"), "likes": t.get("like_count"), "prints": t.get("make_count")},
            "updated": t.get("modified") or t.get("added"), "api_url": f"{API}/things/{t['id']}", "raw": t}


def files_for(token: str | None, n: dict):
    c = Client(token)
    out = []
    for f in n.get("files") or []:
        if f["kind"] not in ("3mf", "stl", "step", "obj", "f3d", "scad", "zip", "gcode") or (f.get("size") or 0) > 600_000_000:
            continue
        url = f.get("download") or f"{API}/files/{f['id']}/download"
        out.append((f["name"], HTTP.bytes(url, headers=c.h)))
    return out


def page_html(n: dict) -> str | None:
    try:
        r = PAGE.s.get(n["url"], headers={"Accept": "text/html"})
        return r.text if r.status_code == 200 and "Just a moment" not in r.text[:3000] else None
    except Exception:  # noqa: BLE001
        return None


def search(token: str | None, q: str, licenses: list[str] | None, sort: str, offset: int, limit: int) -> dict:
    """Thingiverse search can't filter by license, so each hit's license is looked up (cached a day)."""
    from ..services import licensing
    c = Client(token)
    per = 20
    page = offset // per + 1
    d = c.get(f"/search/{q or 'popular'}", cache_s=600, type="things", per_page=per, page=page, sort=SORT.get(sort, "relevant"))
    hits = d.get("hits") if isinstance(d, dict) else d
    allowed = set(licenses or licensing.SELLABLE)
    items = []
    for h in hits or []:
        lic = c.license_of(h["id"])
        code = licensing.normalize(lic, "thingiverse")
        if code not in allowed:
            continue
        cr = h.get("creator") or {}
        items.append({"id": str(h["id"]), "title": h.get("name"), "cover": h.get("preview_image") or h.get("thumbnail"),
                      "creator": cr.get("name"), "license_raw": lic, "downloads": h.get("download_count"),
                      "likes": h.get("like_count"), "prints": h.get("make_count"),
                      "url": h.get("public_url") or f"{WEB}/thing:{h['id']}", "needs_parts": False})
    total = d.get("total") if isinstance(d, dict) else None
    return {"total": total, "items": items, "next_offset": page * per}
