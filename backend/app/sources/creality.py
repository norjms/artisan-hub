"""Creality Cloud through the same endpoints its website uses.

Details and photos come from the model page; search is public; downloading 3MF files needs the
owner's Creality Cloud login (email + password, token stored, password not kept).
"""
import json
import re
import uuid

from ..clients.base import ApiError
from .base import Http, supplies_from_text, file_kind

WEB = "https://www.crealitycloud.com"
HTTP = Http("creality", gap=1.0)
DUID = str(uuid.uuid4())
LICENSES = {"CC0": "CC0", "BY": "CC BY", "BY-SA": "CC BY-SA", "BY-ND": "CC BY-ND", "BY-NC": "CC BY-NC",
            "BY-NC-SA": "CC BY-NC-SA", "BY-NC-ND": "CC BY-NC-ND", "CXY-SL": "CXY-SL"}
SORT = {"downloads": 2, "likes": 3, "newest": 1, "relevance": 0, "prints": 2}


def headers(acct: dict | None = None) -> dict:
    h = {"Content-Type": "application/json", "Origin": WEB, "Referer": WEB + "/", "__CXY_APP_ID_": "creality_model",
         "__CXY_PLATFORM_": "6", "__CXY_OS_LANG_": "0", "__CXY_DUID_": DUID, "__CXY_APP_VER_": "1.0.0"}
    if acct and acct.get("token"):
        h["__CXY_TOKEN_"] = acct["token"]
        h["__CXY_UID_"] = str(acct.get("user_id") or "")
    return h


def api(path: str, body: dict, acct: dict | None = None, cache_s: int = 0) -> dict:
    d = HTTP.post(WEB + path, headers=headers(acct), json=body, cache_s=cache_s)
    if isinstance(d, dict) and d.get("code") not in (0, None):
        raise ApiError("creality", 401 if d.get("code") == 4 else 400, f"Creality Cloud: {d.get('msg')}")
    return (d or {}).get("result") or {}


def login(email: str, password: str) -> dict:
    d = HTTP.post(WEB + "/api/cxy/account/v2/loginV2", headers=headers(),
                  json={"type": 2, "account": email, "password": password})
    if d.get("code") != 0:
        raise ApiError("creality", 400, d.get("msg") or "login failed")
    r = d.get("result") or {}
    tok = r.get("token") or (r.get("tokenInfo") or {}).get("token")
    uid = r.get("userId") or (r.get("userInfo") or {}).get("userId")
    if not tok:
        raise ApiError("creality", 400, f"unexpected login response: {str(r)[:150]}")
    return {"token": tok, "user_id": uid, "account": email, "name": (r.get("userInfo") or {}).get("nickName")}


def parse_id(url: str) -> str | None:
    m = re.search(r"crealitycloud\.com/(?:[a-z-]+/)?model-detail/([0-9a-f]{20,32})", url or "")
    return m.group(1) if m else None


_WRAP = {"Reactive", "ShallowReactive", "Ref", "ShallowRef", "EmptyRef", "EmptyShallowRef", "NuxtError", "Island"}


def _nuxt(html: str) -> dict:
    m = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise ApiError("creality", 502, "model page didn't include its data (layout changed?)")
    raw = json.loads(m.group(1))

    def res(i, depth=0):
        if depth > 60 or not isinstance(i, int) or i < 0 or i >= len(raw):
            return None
        v = raw[i]
        if isinstance(v, dict):
            return {k: res(x, depth + 1) for k, x in v.items()}
        if isinstance(v, list):
            if v and isinstance(v[0], str) and v[0] in _WRAP:
                return res(v[1], depth + 1)
            if v and v[0] in ("Set", "Map"):
                return [res(x, depth + 1) for x in v[1:]]
            if v and v[0] == "Date":
                return v[1]
            return [res(x, depth + 1) for x in v]
        return v
    return res(0) or {}


def fetch(mid: str) -> dict:
    r = HTTP.get(f"{WEB}/model-detail/{mid}", raw=True, headers={"Accept": "text/html"}, cache_s=60)
    html = r.text
    data = (_nuxt(html).get("data") or {})
    info = next((v for k, v in data.items() if k.startswith("model-info_") and isinstance(v, dict)), None) or {}
    g = ((info.get("result") or {}).get("groupItem")) or {}
    if not g:
        raise ApiError("creality", 404, "model not found or not public")
    tmf = next((v for k, v in data.items() if k.startswith("3mf-list") and isinstance(v, dict)), None) or {}
    profiles = []
    for p in tmf.get("list") or []:
        cf = p.get("colorFilament") or ((p.get("preParse3mfResult") or {}).get("total_consume_filaments")) or []
        profiles.append({"id": p["id"], "title": " ".join(x for x in (p.get("name"), p.get("secondName")) if x),
                         "printer": p.get("printerName"), "time_s": p.get("printTime"), "grams": p.get("filamentWeight"),
                         "layer_height": p.get("layerHeight"), "walls": p.get("wallLoops"),
                         "infill": p.get("sparseInfillDensity"), "nozzle": ", ".join(p.get("nozzleDiameter") or []) or None,
                         "filaments": [{"type": f.get("type"), "color": f.get("color"),
                                        "grams": round(float(f.get("weight") or 0), 1) or None} for f in cf],
                         "default": False, "size": p.get("size"), "name": p.get("name")})
    if profiles:
        best = max(profiles, key=lambda p: (p.get("printer") or "").startswith("K") and 1 or 0)
        best["default"] = True
    covers = [c.get("originUrl") or c.get("url") for c in (g.get("covers") or []) if c.get("url")]
    u = g.get("userInfo") or {}
    lic = g.get("license")
    cats = g.get("categories") or []
    return {"platform": "creality", "id": g["id"], "url": f"{WEB}/model-detail/{g['id']}", "title": g.get("groupName") or mid,
            "description_html": g.get("groupDesc") or "", "tags": [t.get("name") for t in g.get("tags") or [] if t.get("name")],
            "category": (cats[0].get("enName") or cats[0].get("name")) if cats else g.get("categoryName"),
            "designer": {"name": u.get("nickName"), "handle": str(u.get("userId") or ""),
                         "url": f"{WEB}/user/{u['userId']}" if u.get("userId") else None},
            "license_raw": lic, "license_text": None, "photos": covers, "profiles": profiles,
            "files": [{"id": p["id"], "name": (p.get("name") or "model") + ".3mf", "size": p.get("size"), "kind": "3mf"}
                      for p in profiles],
            "supplies": supplies_from_text(g.get("groupDesc")),
            "stats": {"downloads": g.get("downloadCount"), "likes": g.get("likeCount"), "prints": None},
            "updated": g.get("lastSharedTime") or g.get("createTime"), "api_url": None, "paid": bool(g.get("isPay")),
            "raw": {"groupItem": g, "3mf": tmf.get("list")}, "_html": html}


def files_for(acct: dict, n: dict, profile: dict | None):
    if not acct.get("token"):
        raise ApiError("creality", 401, "connect your Creality Cloud account in Settings to download files")
    targets = [profile] if profile and profile.get("id") else [p for p in n.get("profiles") or [] if p.get("default")][:1]
    out = []
    for p in targets:
        r = api("/api/cxy/v3/model/3mfDownload", {"id": p["id"]}, acct)
        url = r.get("signUrl") or r.get("url") or r.get("downloadUrl") or next(
            (v for v in r.values() if isinstance(v, str) and v.startswith("http")), None)
        if not url:
            raise ApiError("creality", 502, f"no download link in response: {str(r)[:120]}")
        out.append(((p.get("name") or n["title"])[:120] + ".3mf", HTTP.bytes(url)))
        try:
            api("/api/cxy/v3/model/3mfDownloadSuccess", {"id3mf": p["id"]}, acct)
        except ApiError:
            pass
    return out


def search(q: str, licenses: list[str] | None, sort: str, offset: int, limit: int) -> dict:
    from ..services import licensing
    lic = [LICENSES[c] for c in (licenses or licensing.SELLABLE) if c in LICENSES]
    per = min(limit, 40)
    body = {"page": offset // per + 1, "pageSize": per, "keyword": q or "", "licenses": lic, "isPay": 2,
            "sortType": SORT.get(sort, 0)}
    r = api("/api/cxy/smart_search/v1/model", body, cache_s=600)
    items = []
    for h in r.get("list") or []:
        u = h.get("userInfo") or {}
        cov = (h.get("covers") or [{}])[0]
        items.append({"id": h["id"], "title": h.get("groupName"), "cover": cov.get("url"), "creator": u.get("nickName"),
                      "license_raw": h.get("license"), "downloads": h.get("downloadCount"), "likes": h.get("likeCount"),
                      "prints": None, "url": f"{WEB}/model-detail/{h['id']}", "needs_parts": False,
                      "paid": bool(h.get("isPay"))})
    return {"total": r.get("count"), "items": items, "next_offset": (offset // per + 1) * per}
