"""Any other link: a direct model file (STL, 3MF, STEP, OBJ, ZIP) or a web page's own description of itself
(OpenGraph tags, schema.org JSON-LD, <title>, rel=license). Everything goes through services/safe_fetch, so only
public hosts are reached, with size limits and content-type checks.

The id is a hash of the link with tracking parameters and the #fragment removed, so the same link is the same
model (WEB-<id>)."""
import hashlib
import json
import re
from html import escape, unescape
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from ..clients.base import ApiError
from ..services import safe_fetch
from .base import file_kind

TRACKING = re.compile(r"^(utm_[a-z]+|fbclid|gclid|mc_[a-z]+|ref|ref_src|igshid|_ga|yclid|msclkid)$", re.I)
MAX_PAGE_FILES = 10


def canonical(url: str) -> str:
    u = urlsplit((url or "").strip())
    q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if not TRACKING.match(k)]
    path = u.path or "/"
    return u._replace(scheme=u.scheme.lower(), netloc=u.netloc.lower(), path=path, query=urlencode(q), fragment="").geturl()


def parse_id(url: str) -> str | None:
    u = urlsplit((url or "").strip())
    if u.scheme.lower() not in ("http", "https") or not u.hostname:
        return None
    return hashlib.sha1(canonical(url).encode()).hexdigest()[:16]


def _ld_items(soup: BeautifulSoup) -> list[dict]:
    out = []
    for s in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            d = json.loads(s.string or s.get_text() or "")
        except (ValueError, TypeError):
            continue
        stack = d if isinstance(d, list) else [d]
        while stack:
            x = stack.pop(0)
            if isinstance(x, dict):
                if "@graph" in x and isinstance(x["@graph"], list):
                    stack.extend(x["@graph"])
                out.append(x)
            elif isinstance(x, list):
                stack.extend(x)
    return out


def _first(v):
    if isinstance(v, list):
        return _first(v[0]) if v else None
    return v


def _name(v) -> str | None:
    v = _first(v)
    if isinstance(v, dict):
        return v.get("name") or v.get("alternateName")
    return v if isinstance(v, str) else None


def _imgs(v) -> list[str]:
    out = []
    for x in (v if isinstance(v, list) else [v]):
        if isinstance(x, dict):
            x = x.get("url") or x.get("contentUrl")
        if isinstance(x, str) and x:
            out.append(x)
    return out


def meta_from_html(html: str, base_url: str) -> dict:
    """What a page says about itself. Keys: title, description, images, designer {name, url}, license_raw,
    license_url, tags, category, files [{name, url, kind}], site."""
    soup = BeautifulSoup(html or "", "html.parser")

    def meta(*names):
        for n in names:
            t = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
            if t and t.get("content"):
                return unescape(t["content"]).strip()
        return None

    ld = [x for x in _ld_items(soup)
          if str(x.get("@type", "")).lower() in ("product", "creativework", "3dmodel", "article", "webpage", "thing",
                                                  "imageobject", "visualartwork", "dataset", "softwaresourcecode")
          or isinstance(x.get("@type"), list)]
    main = next((x for x in ld if str(x.get("@type", "")).lower() in ("3dmodel", "product", "creativework")), ld[0] if ld else {})
    title = (main.get("name") if isinstance(main.get("name"), str) else None) or meta("og:title", "twitter:title") \
        or (soup.title.get_text().strip() if soup.title else None)
    desc = (main.get("description") if isinstance(main.get("description"), str) else None) \
        or meta("og:description", "twitter:description", "description")
    images = []
    for x in [*_imgs(main.get("image")), meta("og:image:secure_url", "og:image"), meta("twitter:image")]:
        if x:
            a = urljoin(base_url, x)
            if a.startswith("https://") and a not in images:
                images.append(a)
    designer = None
    au = main.get("author") or main.get("creator") or main.get("brand")
    if _name(au):
        a0 = _first(au)
        designer = {"name": _name(au), "url": a0.get("url") if isinstance(a0, dict) else None}
    elif meta("author", "article:author"):
        designer = {"name": meta("author", "article:author"), "url": None}
    lic = main.get("license")
    license_url, license_raw = None, None
    if isinstance(lic, dict):
        license_url, license_raw = lic.get("url"), lic.get("name") or lic.get("url")
    elif isinstance(lic, str):
        license_raw = lic
        license_url = lic if lic.startswith("http") else None
    rel = soup.find(["a", "link"], attrs={"rel": re.compile(r"\blicense\b", re.I)})
    if rel and not license_raw:
        license_url = urljoin(base_url, rel.get("href") or "") or None
        license_raw = (rel.get_text() or "").strip() or license_url
    if license_raw and "creativecommons.org" in license_raw:
        license_raw = _cc_from_url(license_raw)
    kw = main.get("keywords") or meta("keywords")
    tags = [t.strip() for t in (kw if isinstance(kw, list) else str(kw or "").split(",")) if t and t.strip()][:20]
    files, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        name = PurePosixPath(unquote(urlsplit(href).path)).name
        if not safe_fetch.looks_like_model(name) or href in seen or not href.startswith(("https://", "http://")):
            continue
        seen.add(href)
        files.append({"id": hashlib.sha1(href.encode()).hexdigest()[:10], "name": name, "url": href, "kind": file_kind(name)})
    return {"title": (title or "").strip()[:300] or None, "description": desc, "images": images[:12],
            "designer": designer, "license_raw": license_raw, "license_url": license_url, "tags": tags,
            "category": _name(main.get("category")) if main.get("category") else None,
            "files": files[:40], "site": meta("og:site_name") or urlsplit(base_url).hostname}


def _cc_from_url(u: str) -> str:
    m = re.search(r"creativecommons\.org/(?:licenses|publicdomain)/([a-z-]+)", u, re.I)
    if not m:
        return u
    k = m.group(1).lower()
    return "CC0" if k == "zero" else "CC " + k.upper()


def text_to_html(s: str | None) -> str:
    s = (s or "").strip()
    return "".join(f"<p>{escape(p.strip())}</p>" for p in re.split(r"\n\s*\n", s) if p.strip())


def fetch(url: str) -> dict:
    try:
        f = safe_fetch.fetch(url, "page")
    except safe_fetch.FetchError as e:
        raise ApiError("web", e.status, e.message) from None
    host = urlsplit(f.url).hostname or ""
    ext = parse_id(url)
    if f.content is None:       # a model file itself
        name = f.filename or PurePosixPath(urlsplit(f.url).path).name or "model.stl"
        return {"platform": "web", "id": ext, "url": canonical(url), "title": PurePosixPath(name).stem[:300] or name,
                "description_html": f"<p>Downloaded from {escape(f.url)}</p>", "tags": [], "category": None,
                "designer": {"name": host, "handle": host, "url": f"https://{host}"}, "license_raw": None,
                "license_text": None, "photos": [], "profiles": [],
                "files": [{"id": "direct", "name": name, "size": f.size, "kind": file_kind(name), "url": f.url}],
                "supplies": [], "stats": {}, "updated": None, "api_url": None, "direct_file": True,
                "raw": {"url": url, "final_url": f.url, "content_type": f.content_type, "size": f.size}}
    html = f.content.decode("utf-8", "replace")
    m = meta_from_html(html, f.url)
    return {"platform": "web", "id": ext, "url": canonical(url), "title": m["title"] or host,
            "description_html": text_to_html(m["description"]), "tags": m["tags"], "category": m["category"],
            "designer": {"name": (m["designer"] or {}).get("name") or m["site"] or host,
                         "handle": (m["designer"] or {}).get("name") or host,
                         "url": (m["designer"] or {}).get("url") or f"https://{host}"},
            "license_raw": m["license_raw"], "license_text": m["license_url"] or m["license_raw"],
            "photos": m["images"], "profiles": [], "files": m["files"], "supplies": [], "stats": {}, "updated": None,
            "api_url": None, "files_in_browser": not m["files"], "_html": html,
            "raw": {"url": url, "final_url": f.url, "meta": {k: v for k, v in m.items() if k != "files"}}}


def files_for(n: dict):
    """The page's model files (at most 10), each through safe_fetch. One that fails is a warning, not a failure."""
    out = []
    for f in (n.get("files") or [])[:MAX_PAGE_FILES]:
        try:
            out.append(safe_fetch.file_bytes(f["url"]))
        except ApiError as e:
            n.setdefault("_warnings", []).append(f"{f.get('name') or 'file'} not downloaded: {e.message[:120]}")
    if len(n.get("files") or []) > MAX_PAGE_FILES:
        n.setdefault("_warnings", []).append(f"the page links {len(n['files'])} model files; the first {MAX_PAGE_FILES} were downloaded")
    return out
