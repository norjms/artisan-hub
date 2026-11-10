"""MyMiniFactory from the object page itself (schema.org JSON-LD for name, photos and description, the page for
license, designer, category and file names). The official API (api/v2) needs an API key per app and a signed-in
user to download, and downloads of free objects also need a signed-in MyMiniFactory account, so files are marked
"download in browser" and the browser add-on hands them over (https://www.myminifactory.com/download/<id>)."""
import json
import re
from html import unescape

from ..clients.base import ApiError
from .base import Http, file_kind, supplies_from_text

WEB = "https://www.myminifactory.com"
PAGE = Http("myminifactory", gap=1.2)


def parse_id(url: str) -> str | None:
    m = re.search(r"myminifactory\.com/(?:[a-z]{2}/)?object/[^?#]*?-(\d+)(?:[/?#]|$)", url or "")
    return m.group(1) if m else None


def _orig(u: str) -> str:
    """The original file behind MyMiniFactory's resizing proxy (images2.../plain/<original>)."""
    m = re.search(r"/plain/(https?://.+)$", u)
    return m.group(1) if m else u


def parse_page(html: str, oid: str) -> dict:
    ld = {}
    for m in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
        try:
            d = json.loads(m)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("@type") in ("CreativeWork", "Product", "3DModel"):
            ld = d
            break
    if not ld:
        raise ApiError("myminifactory", 404, "object not found (no model details on the page)")
    title = unescape(ld.get("name") or "").strip()
    imgs = ld.get("image") or []
    imgs = imgs if isinstance(imgs, list) else [imgs]
    photos = list(dict.fromkeys(_orig(u) for u in imgs if isinstance(u, str)))
    desc = unescape(ld.get("description") or "")
    lic = re.search(r'title="Learn more about licenses">\s*([^<]+?)\s*</a>', html)
    license_raw = unescape(lic.group(1)).strip() if lic else None
    if not license_raw and "Standard Digital File Store License" in html and re.search(r'class="[^"]*price', html):
        license_raw = "MyMiniFactory Standard Digital File Store License"
    who = re.search(r'class="rowUserInfo".*?<a href="/users/([^"]+)" class="under-hover">([^<]+)</a>', html, re.S)
    handle = unescape(who.group(1)) if who else None
    name = unescape(who.group(2)).strip() if who else handle
    crumbs = re.findall(r'itemprop="item"[^>]*href="[^"]*"><span itemprop="name">([^<]+)</span>', html)
    files = []
    for fid, fname in re.findall(r'state\.filesInfo\[(\d+)\]\s*=\s*\{\s*"name":\s*"([^"]+)"', html):
        fname = json.loads(f'"{fname}"')
        files.append({"id": fid, "name": fname, "size": None, "kind": file_kind(fname)})
    paid = bool(re.search(r'"price"\s*:\s*"?[1-9]', html)) or "Store License" in (license_raw or "")
    return {"platform": "myminifactory", "id": str(oid), "url": ld.get("url") or f"{WEB}/object/{oid}",
            "title": title or f"MyMiniFactory object {oid}",
            "description_html": "".join(f"<p>{x.strip()}</p>" for x in re.split(r"\r?\n", desc) if x.strip()),
            "tags": [], "category": unescape(crumbs[-1]) if crumbs else None,
            "designer": {"name": name, "handle": handle, "url": f"{WEB}/users/{handle}" if handle else None},
            "license_raw": license_raw, "license_text": (f"{license_raw} (see {WEB}/object-licensing)" if license_raw else None),
            "photos": photos, "profiles": [], "files": files, "supplies": supplies_from_text(desc),
            "stats": {}, "updated": None, "api_url": None, "paid": paid, "files_in_browser": True,
            "download_page": f"{WEB}/download/{oid}", "_html": html, "raw": {"jsonld": ld, "license": license_raw}}


def fetch(oid: str) -> dict:
    r = PAGE.get(f"{WEB}/object/{int(oid)}", raw=True, cache_s=60)
    return parse_page(r.text, oid)


def page_html(n: dict) -> str | None:
    return n.get("_html")
