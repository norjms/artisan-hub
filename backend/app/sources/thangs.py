"""Thangs through the JSON its own pages read (https://thangs.com/api/models/<id>; no account needed).

Downloads are not possible from the hub: /api/v2/models/<id>/download-url sits behind a Cloudflare check and wants
a signed-in Thangs session, so files are marked "download in browser" and the browser add-on hands them over.
Thangs has no fixed license list: a designer may attach their own license document (a PDF), which is recorded as
the license text with a link; with none the license is Unknown (not sellable until you record one)."""
import re
from pathlib import PurePosixPath
from urllib.parse import quote

from ..clients.base import ApiError
from .base import Http, file_kind, supplies_from_text

WEB = "https://thangs.com"
API = "https://thangs.com/api/models/"
THUMBS = "https://storage.googleapis.com/thangs-thumbnails/production/"
UPLOADS = "https://storage.googleapis.com/production-thangs-public/uploads/"
HTTP = Http("thangs", gap=1.0)
PAGE = Http("thangs web page", gap=1.0)


def parse_id(url: str) -> str | None:
    u = url or ""          # not unquoted: a %23 (#) in the model name would end the match early
    if "thangs.com" not in u:
        return None
    m = re.search(r"thangs\.com/m/(\d+)", u) or re.search(r"thangs\.com/.*?/3d-model/[^?#]*?-(\d+)(?:[/?#]|$)", u) \
        or re.search(r"thangs\.com/.*?[?&]modelId=(\d+)", u)
    return m.group(1) if m else None


def _thumb(filename: str | None) -> str | None:
    if not filename:
        return None
    stem = filename.rsplit(".", 1)[0]
    return THUMBS + quote(stem + ".png", safe="/")


def normalize(d: dict) -> dict:
    mid = str(d.get("id"))
    owner = d.get("owner") or {}
    user = owner.get("username")
    parts = d.get("parts") or []
    primary = next((p for p in parts if p.get("isPrimary")), parts[0] if parts else {})
    photos = []
    t = _thumb(primary.get("filename"))
    if t:
        photos.append(t)
    for a in d.get("attachments") or []:
        # Only the designer's own photos: the others are makes uploaded by other people.
        if a.get("attachmentType") == "image" and a.get("uploadedByModelOwner") and a.get("imageUrl"):
            photos.append(a["imageUrl"])
    lic = d.get("license")
    lic_name = PurePosixPath(lic).name if isinstance(lic, str) and lic else None
    ident = d.get("identifier") or ""
    url = f"{WEB}/designer/{ident.split('/', 1)[0]}/3d-model/{ident.split('/', 1)[1]}" if "/" in ident else f"{WEB}/m/{mid}"
    files = [{"id": p.get("filename"), "name": PurePosixPath(p.get("filename") or p.get("name") or "part").name,
              "size": p.get("size"), "kind": file_kind(p.get("filename") or "")} for p in parts]
    desc = d.get("description") or ""
    return {"platform": "thangs", "id": mid, "url": url, "title": d.get("name") or f"Thangs model {mid}",
            "description_html": "".join(f"<p>{x}</p>" for x in re.split(r"\n\s*\n", desc) if x.strip()),
            "tags": [x for x in (d.get("allTags") or d.get("tags") or []) if x][:20], "category": d.get("category"),
            "designer": {"name": user or owner.get("fullName"), "handle": user,
                         "url": f"{WEB}/designer/{quote(user)}" if user else None},
            "license_raw": f"Thangs designer license: {lic_name}" if lic_name else None,
            "license_text": (f"The designer attached their own license document '{lic_name}'. Read it on Thangs: "
                             f"{WEB}/m/{mid}?showLicense=true" + (f" (file {UPLOADS}{quote(lic, safe='/')})" if lic else ""))
            if lic_name else None,
            "photos": photos, "profiles": [], "files": files, "supplies": supplies_from_text(desc),
            "stats": {"downloads": d.get("downloadCount"), "likes": d.get("likesCount")},
            "updated": d.get("published") or d.get("created"), "api_url": API + mid,
            "paid": bool(d.get("isPaid") or d.get("marketplaceInfo")), "files_in_browser": True,
            "download_page": f"{WEB}/m/{mid}", "raw": {k: v for k, v in d.items() if k not in ("likes",)}}


def fetch(mid: str) -> dict:
    d = HTTP.get(API + str(int(mid)), cache_s=60)
    if not isinstance(d, dict) or not d.get("id"):
        raise ApiError("thangs", 404, "model not found or not public")
    if d.get("isPublic") is False or d.get("visibility") not in (None, "public"):
        raise ApiError("thangs", 404, "model is not public")
    return normalize(d)


def page_html(n: dict) -> str | None:
    try:
        r = PAGE.s.get(n["url"], headers={"Accept": "text/html"})
        return r.text if r.status_code == 200 and "Just a moment" not in r.text[:3000] else None
    except Exception:  # noqa: BLE001
        return None
