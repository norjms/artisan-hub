"""Printables (Prusa) through the same GraphQL API its website uses. No account needed for free models."""
import re

from ..clients.base import ApiError
from .base import Http, supplies_from_text, file_kind

GQL = "https://api.printables.com/graphql/"
FILES = "https://files.printables.com/"
WEB = "https://www.printables.com"
PAGE = Http("printables web page", gap=1.0)   # separate: a bot check on the website must not block the API
HTTP = Http("printables", gap=0.8)
HEAD = {"Content-Type": "application/json", "Origin": WEB, "Referer": WEB + "/",
        "Accept": "application/graphql-response+json, application/json"}

# Printables license ids (from its `licenses` query)
LICENSE_IDS = {"CC0": "7", "BY": "1", "BY-SA": "2", "BY-ND": "8", "BY-NC": "3", "BY-NC-SA": "4", "BY-NC-ND": "6",
               "GPL": "9,12", "LGPL": "10", "BSD": "11", "PR-SDFL": "13", "PR-COMMERCIAL-ND": "14", "PR-COMMERCIAL": "15",
               "CERN-OHL": "18", "APACHE": "24"}
SELLABLE_IDS = ["7", "1", "2", "8", "9", "12", "10", "11", "14", "15", "18", "24"]
ORDER = {"downloads": "download_count", "likes": "likes_count", "newest": "latest", "relevance": "best_match",
         "prints": "makes_count"}

DETAIL = """query P($id: ID!) { print(id: $id) {
  id name slug summary description datePublished printDuration weight numPieces nozzleDiameters layerHeights mmu
  premium price filesType likesCount downloadCount makesCount
  license { id name abbreviation content }
  user { id publicUsername handle }
  category { id path { name } }
  tags { name }
  images { filePath }
  materials { name }
  printer { name }
  stls { id name fileSize }
  gcodes { id name fileSize }
  slas { id name fileSize }
  otherFiles { id name fileSize }
  downloadPacks { id name fileType fileSize }
} }"""


def gql(query: str, variables: dict | None = None, cache_s: int = 0) -> dict:
    d = HTTP.post(GQL, headers=HEAD, json={"query": query, "variables": variables or {}}, cache_s=cache_s)
    if d.get("errors"):
        raise ApiError("printables", 400, "; ".join(e.get("message", "")[:150] for e in d["errors"]))
    return d.get("data") or {}


def parse_id(url: str) -> str | None:
    m = re.search(r"printables\.com/(?:[a-z]{2}/)?model/(\d+)", url or "")
    return m.group(1) if m else (url.strip() if (url or "").strip().isdigit() else None)


MEDIA = "https://media.printables.com/"


def _img(path: str | None) -> str | None:
    """The original file (for importing: the hub downloads it, which Printables allows)."""
    return (FILES + path.lstrip("/")) if path else None


def _thumb(path: str | None, size: str = "640x640") -> str | None:
    """Printables' own resized copy, for showing. The originals are often several MB, and files.printables.com
    refuses the phone app (its requests say Dart, not a browser: 403), while these thumbnails are served to it."""
    if not path:
        return None
    folder, _, name = path.lstrip("/").rpartition("/")
    stem, dot, ext = name.rpartition(".")
    if not dot:
        return _img(path)
    # The segment before the name is the original's own extension, exactly as stored (jpg, jpeg, png, JPG...):
    # any other spelling is a 400 "No such object".
    return f"{MEDIA}{folder}/thumbs/inside/{size}/{ext}/{stem}.webp"


def fetch(pid: str) -> dict:
    p = gql(DETAIL, {"id": str(pid)}, cache_s=60).get("print")
    if not p:
        raise ApiError("printables", 404, "model not found or not public")
    lic = p.get("license") or {}
    files = [{"id": f["id"], "name": f["name"], "size": f.get("fileSize"), "kind": file_kind(f["name"]), "ptype": "stl"}
             for f in p.get("stls") or []]
    files += [{"id": f["id"], "name": f["name"], "size": f.get("fileSize"), "kind": file_kind(f["name"]), "ptype": "gcode"}
              for f in p.get("gcodes") or []]
    files += [{"id": f["id"], "name": f["name"], "size": f.get("fileSize"), "kind": file_kind(f["name"]), "ptype": "other"}
              for f in p.get("otherFiles") or []]
    hours = None
    try:
        hours = float(p.get("printDuration") or 0) or None
    except ValueError:
        pass
    mats = [m["name"] for m in p.get("materials") or []] or ["PLA"]
    grams = float(p["weight"]) if p.get("weight") else None
    profile = {"id": "designer", "title": "Designer settings", "default": True,
               "printer": (p.get("printer") or {}).get("name"),
               "time_s": int(hours * 3600) if hours else None, "grams": grams,
               "layer_height": ", ".join(p.get("layerHeights") or []) or None,
               "nozzle": ", ".join(p.get("nozzleDiameters") or []) or None,
               "filaments": [{"type": mats[0], "color": None, "grams": grams}] if not p.get("mmu") else
                            [{"type": m, "color": None, "grams": None} for m in mats]}
    cat = [c["name"] for c in ((p.get("category") or {}).get("path") or [])]
    user = p.get("user") or {}
    return {"platform": "printables", "id": str(p["id"]), "url": f"{WEB}/model/{p['id']}-{p.get('slug') or ''}".rstrip("-"),
            "title": p["name"], "description_html": (p.get("summary") or "") and f"<p>{p['summary']}</p>" + (p.get("description") or ""),
            "tags": [t["name"] for t in p.get("tags") or []], "category": cat[-1] if cat else None,
            "designer": {"name": user.get("publicUsername") or user.get("handle"), "handle": user.get("handle"),
                         "url": f"{WEB}/@{user['handle']}" if user.get("handle") else None},
            "license_raw": lic.get("abbreviation") or lic.get("name"),
            "license_text": re.sub(r"<[^>]+>", " ", lic.get("content") or "").strip() or lic.get("name"),
            "photos": [_img(i.get("filePath")) for i in p.get("images") or []],
            "pictures": [_thumb(i.get("filePath"), "1280x960") for i in p.get("images") or []],
            "profiles": [profile], "files": files,
            "supplies": supplies_from_text(p.get("description")),
            "stats": {"downloads": p.get("downloadCount"), "likes": p.get("likesCount"), "prints": p.get("makesCount")},
            "updated": p.get("datePublished"), "api_url": None, "premium": p.get("premium"), "price": p.get("price"),
            "packs": p.get("downloadPacks") or [], "raw": p}


def download_link(pid: str, fid: str, ftype: str) -> str:
    q = """mutation D($id: ID!, $printId: ID!, $t: DownloadFileTypeEnum!) {
      getDownloadLink(id: $id, printId: $printId, fileType: $t, source: model_detail) { ok errors { messages } output { link } } }"""
    d = gql(q, {"id": str(fid), "printId": str(pid), "t": ftype}).get("getDownloadLink") or {}
    if not d.get("ok") or not (d.get("output") or {}).get("link"):
        raise ApiError("printables", 403, str(d.get("errors") or "no download link (paid or members-only model?)")[:200])
    return d["output"]["link"]


def files_for(n: dict, include_gcode: bool = False):
    """Model files as Printables' single "model files" pack when there is one (one request instead of many),
    otherwise one by one. G-code only when asked (it is printer specific)."""
    pack = next((x for x in n.get("packs") or [] if x.get("fileType") == "MODEL_FILES"), None)
    if pack and not include_gcode:
        link = download_link(n["id"], pack["id"], "pack")
        return [(f"{n['title'][:80]} model files.zip", HTTP.bytes(link))]
    out = []
    for f in n.get("files") or []:
        if f["ptype"] == "gcode" and not include_gcode:
            continue
        if f["ptype"] == "other" and f["kind"] not in ("3mf", "stl", "step", "obj", "f3d", "scad", "zip"):
            continue
        if (f.get("size") or 0) > 600_000_000:
            continue
        link = download_link(n["id"], f["id"], {"stl": "stl", "gcode": "gcode", "other": "other"}[f["ptype"]])
        out.append((f["name"], HTTP.bytes(link)))
    return out


def page_html(n: dict) -> str | None:
    try:
        r = PAGE.s.get(n["url"], headers={"Accept": "text/html"})
        return r.text if r.status_code == 200 and "Just a moment" not in r.text[:3000] else None
    except Exception:  # noqa: BLE001
        return None


def search(q: str, licenses: list[str] | None, sort: str, offset: int, limit: int) -> dict:
    query = """query S($q: String!, $l: Int, $o: Int, $lic: [ID], $ord: SearchChoicesEnum) {
      searchPrints2(query: $q, limit: $l, offset: $o, licenses: $lic, ordering: $ord) {
        totalCount items { id name slug likesCount downloadCount makesCount license { abbreviation }
        user { publicUsername handle } image { filePath } premium } } }"""
    ids = SELLABLE_IDS if not licenses else [x for c in licenses for x in LICENSE_IDS.get(c, "").split(",") if x]
    v = {"q": q or "", "l": limit, "o": offset, "lic": ids}
    if ORDER.get(sort):
        v["ord"] = ORDER[sort]
    try:
        d = gql(query, v, cache_s=600)["searchPrints2"]
    except ApiError as e:
        if "ordering" in e.message or "SearchChoicesEnum" in e.message:
            v.pop("ord", None)
            d = gql(query.replace(", $ord: SearchChoicesEnum", "").replace(", ordering: $ord", ""), v, cache_s=600)["searchPrints2"]
        else:
            raise
    items = []
    for h in d.get("items") or []:
        u = h.get("user") or {}
        items.append({"id": str(h["id"]), "title": h["name"], "cover": _thumb((h.get("image") or {}).get("filePath")),
                      "creator": u.get("publicUsername") or u.get("handle"), "license_raw": (h.get("license") or {}).get("abbreviation"),
                      "downloads": h.get("downloadCount"), "likes": h.get("likesCount"), "prints": h.get("makesCount"),
                      "url": f"{WEB}/model/{h['id']}-{h.get('slug') or ''}".rstrip("-"), "needs_parts": False,
                      "paid": bool(h.get("premium"))})
    return {"total": d.get("totalCount"), "items": items}
