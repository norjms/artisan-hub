"""Cults3D. Its pages sit behind a Cloudflare bot check that refuses servers, so the hub can only read a model
through the official GraphQL API (https://cults3d.com/en/api, HTTP Basic auth with your Cults3D user name and an
API key made at cults3d.com/en/api/keys), or from what the browser add-on reads off the page you are looking at.
Downloads always need a signed-in Cults3D account (free models included), so files are "download in browser"."""
import re

from ..clients.base import ApiError
from .base import Http

WEB = "https://cults3d.com"
GQL = "https://cults3d.com/graphql"
HTTP = Http("cults3d", gap=1.0)

QUERY = """query C($slug: String!) { creation(slug: $slug) {
  name(locale: EN) url(locale: EN) description(locale: EN) tags(locale: EN) publishedAt
  illustrations { imageUrl } license { code name url } creator { nick url(locale: EN) }
  price(currency: USD) { cents } downloadsCount likesCount
} }"""
QUERY_MIN = """query C($slug: String!) { creation(slug: $slug) { name url illustrationImageUrl creator { nick } } }"""


def parse_id(url: str) -> str | None:
    m = re.search(r"cults3d\.com/(?:[a-z]{2}/)?(?:3d-model|modele-3d|modelo-3d|3d-modell|3d-model[a-z-]*)/[^/?#]+/([^/?#]+)",
                  url or "")
    return m.group(1) if m else None


def fetch(account: dict | None, slug: str) -> dict:
    if not (account or {}).get("token"):
        raise ApiError("cults3d", 403, "Cults3D blocks servers: add a Cults3D API key in Settings, or add it with the "
                                       "browser add-on, which reads the page you have open")
    auth = (account.get("username") or "", account["token"])
    d = HTTP.post(GQL, json={"query": QUERY, "variables": {"slug": slug}}, auth=auth, cache_s=60)
    if d.get("errors"):
        d = HTTP.post(GQL, json={"query": QUERY_MIN, "variables": {"slug": slug}}, auth=auth, cache_s=60)
    c = (d.get("data") or {}).get("creation")
    if not c:
        raise ApiError("cults3d", 404, "; ".join(e.get("message", "") for e in d.get("errors") or []) or "model not found")
    return normalize(c, slug)


def normalize(c: dict, slug: str) -> dict:
    lic = c.get("license") or {}
    cr = c.get("creator") or {}
    photos = [i.get("imageUrl") for i in c.get("illustrations") or [] if i.get("imageUrl")] or \
        ([c["illustrationImageUrl"]] if c.get("illustrationImageUrl") else [])
    desc = c.get("description") or ""
    cents = (c.get("price") or {}).get("cents") or 0
    return {"platform": "cults3d", "id": slug, "url": c.get("url") or f"{WEB}/en/3d-model/{slug}",
            "title": c.get("name") or slug, "description_html": desc if "<" in desc else f"<p>{desc}</p>",
            "tags": [t for t in c.get("tags") or [] if t][:20], "category": None,
            "designer": {"name": cr.get("nick"), "handle": cr.get("nick"), "url": cr.get("url")},
            "license_raw": lic.get("name") or lic.get("code"), "license_text": lic.get("url") or lic.get("name"),
            "photos": photos, "profiles": [], "files": [], "supplies": [],
            "stats": {"downloads": c.get("downloadsCount"), "likes": c.get("likesCount")},
            "updated": c.get("publishedAt"), "api_url": GQL, "paid": cents > 0, "files_in_browser": True,
            "download_page": c.get("url") or f"{WEB}/en/3d-model/{slug}", "raw": c}
