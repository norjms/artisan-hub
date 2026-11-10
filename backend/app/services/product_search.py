"""Finding the real product online: Google Search through Gemini's search grounding.

Two questions, the same answer shape:
  by_barcode(code)  "which product has this UPC/EAN?" (as soon as a barcode is scanned and nothing local knows it)
  by_guess(guess)   "which product is this?" after the label was read by the phone or the hub

Two Gemini calls each time:
  1. the question in plain words with the google_search tool. Asked to answer "only in JSON", Gemini 3 models skip
     the search and answer from memory, so this call asks naturally and gets prose plus the pages Google returned;
  2. no search: that answer turned into the fields below (JSON mode, cheap).

Answer: {"matches": [product, ...], "sources": [{"title", "url"}], "engine", "note"} where a product is
{vendor, name, material, color_name, color_hex, weight_g, diameter_mm, price, currency, url, barcode, confidence,
verified}. verified means Google actually searched and returned pages for this answer; without that the matches are
the model's memory and are marked unverified.

Needs the team's Google (Gemini) connection with a paid-tier key (Google does not offer search on the free tier). A
model that answers "busy" (503) or times out is retried once on gemini-flash-lite-latest. Answers are cached for a day and
misses for an hour; failures are not cached, so the next scan tries again.
"""
import json
import logging
import re
import threading
import time

import httpx

from . import connections as conns
from .vision import GEMINI_URL, ProviderError, _raise_for, ask, parse_json

log = logging.getLogger("hub.product_search")

TIMEOUT = 60.0
HIT_TTL = 24 * 3600
MISS_TTL = 3600
BUSY_FALLBACK = "gemini-flash-lite-latest"
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()

FIELDS = ("vendor", "name", "material", "color_name", "color_hex", "weight_g", "diameter_mm", "price", "currency",
          "url", "barcode", "confidence")

STRUCTURE = '''Below is an answer from a web search about a 3D printer filament product, and the sites it came from.
Turn it into ONE JSON object and nothing else (no prose, no code fences):
{"matches": [{"vendor": "", "name": "", "material": "", "color_name": "", "color_hex": "", "weight_g": 0,
"diameter_mm": 1.75, "price": 0, "currency": "", "url": "", "barcode": "", "confidence": 0.0}]}
Up to 5 matches, best first, and only products the answer names. name is the product as the seller names it (line
and colour). material is the material code the product is sold as (PLA, PLA+, PETG, ABS, ASA, TPU, PA, PC, PVA, HIPS, or a
filled one such as PLA-CF, ASA-CF, PA6-GF, PAHT-CF), OTHER only when no code is given. Leave out trademark marks.
color_hex is 6 hex digits if the answer gives one or the colour clearly implies it, else "". weight_g is the net
filament weight in grams. url is a product link written in the answer, else "". barcode is the UPC/EAN if the answer
ties it to the product. confidence (0 to 1) is how sure the answer is that this is the product asked about.
{"matches": []} when the answer found nothing.'''


def gemini(db=None) -> dict | None:
    """The Gemini connection when it can search: enabled, with a key and a model."""
    cfg = conns.get("ai_gemini", required=False)
    if cfg and str(cfg.get("api_key") or "").strip() and str(cfg.get("model") or "").strip():
        return cfg
    return None


def _cached(key: str) -> dict | None:
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        _cache.pop(key, None)
    return None


def _store(key: str, value: dict) -> dict:
    with _lock:
        if len(_cache) > 500:
            _cache.clear()
        _cache[key] = (time.time() + (HIT_TTL if value.get("matches") else MISS_TTL), value)
    return value


def _search(cfg: dict, model: str, question: str) -> dict:
    """The grounded call: {"text", "sources", "queries"}."""
    r = httpx.post(f"{GEMINI_URL}/models/{model}:generateContent", timeout=TIMEOUT,
                   headers={"x-goog-api-key": str(cfg.get("api_key")).strip()},
                   json={"contents": [{"role": "user", "parts": [{"text": question}]}],
                         "tools": [{"google_search": {}}],
                         # Light thinking: Gemini 3 models otherwise think past the timeout. It does not stop the
                         # search; asking for JSON does, so the question stays in plain words.
                         "generationConfig": {"thinkingConfig": {"thinkingLevel": "low"}}})
    _raise_for(r, "Gemini")
    cand = (r.json().get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in ((cand.get("content") or {}).get("parts") or []))
    gm = cand.get("groundingMetadata") or {}
    sources = []
    for ch in gm.get("groundingChunks") or []:
        web = ch.get("web") or {}
        if web.get("uri"):
            sources.append({"title": str(web.get("title") or ""), "url": str(web["uri"])})
    real = resolve_links([x["url"] for x in sources])
    for x in sources:
        x["url"] = real.get(x["url"], x["url"])
    return {"text": text, "sources": sources, "queries": gm.get("webSearchQueries") or []}


GOOGLE_REDIRECT = "vertexaisearch.cloud.google.com"


def resolve_links(urls: list[str]) -> dict:
    """Google's search sources are its own redirect links (vertexaisearch.cloud.google.com/grounding-api-redirect/...);
    read where each points (the Location header, the page itself is not fetched) so the app shows the shop's site."""
    from concurrent.futures import ThreadPoolExecutor
    from urllib.parse import urlparse

    todo = [u for u in dict.fromkeys(urls) if urlparse(u).hostname == GOOGLE_REDIRECT]

    def one(u: str):
        try:
            r = httpx.get(u, follow_redirects=False, timeout=6)
            loc = r.headers.get("location") or ""
            return u, loc if loc.startswith(("http://", "https://")) else None
        except httpx.HTTPError:
            return u, None

    out = {}
    if todo:
        with ThreadPoolExecutor(max_workers=min(8, len(todo))) as pool:
            for u, loc in pool.map(one, todo):
                if loc:
                    out[u] = loc
    return out


def _clean(m: dict, searched: bool, fallback_url: str) -> dict | None:
    if not isinstance(m, dict):
        return None
    out = {k: m.get(k) for k in FIELDS}
    for k in ("vendor", "name", "material", "color_name", "currency", "url", "barcode"):
        out[k] = str(out[k] or "").strip()
    out["material"] = out["material"].upper()
    hexv = str(out["color_hex"] or "").strip().lstrip("#")
    out["color_hex"] = hexv.lower() if re.fullmatch(r"[0-9a-fA-F]{6}", hexv) else ""
    for k in ("weight_g", "diameter_mm", "price", "confidence"):
        try:
            v = float(out[k]) if out[k] not in (None, "") else None
        except (TypeError, ValueError):
            v = None
        out[k] = v if v and v > 0 else None
    if out["confidence"] is not None:
        out["confidence"] = min(out["confidence"], 1.0)
    if out["weight_g"] is not None and out["weight_g"] <= 10:
        out["weight_g"] = out["weight_g"] * 1000            # "1 kg" read as 1: no spool weighs 10 g
    if not out["url"].startswith(("http://", "https://")):
        out["url"] = fallback_url
    out["verified"] = searched
    if not (out["name"] or out["vendor"]):
        return None
    return out


def _answer(cfg: dict, question: str, key: str) -> dict:
    hit = _cached(key)
    if hit is not None:
        return hit
    model = str(cfg.get("model")).strip()
    got = None
    for m in dict.fromkeys([model, BUSY_FALLBACK]):
        try:
            got = _search(cfg, m, question)
            model = m
            break
        except ProviderError as e:
            if " 503" in str(e) and m != BUSY_FALLBACK:
                log.info("product search: %s is busy, trying %s", m, BUSY_FALLBACK)
                continue
            return _failed(m, str(e))
        except httpx.HTTPError as e:
            if isinstance(e, httpx.TimeoutException) and m != BUSY_FALLBACK:
                log.info("product search: %s timed out, trying %s", m, BUSY_FALLBACK)
                continue
            return _failed(m, "timed out" if isinstance(e, httpx.TimeoutException) else type(e).__name__)
    if got is None:
        return _failed(model, "Gemini is busy")
    searched = bool(got["sources"])
    sites = ", ".join(dict.fromkeys(s["title"] for s in got["sources"] if s["title"])) or "none (no search was made)"
    try:
        doc = parse_json(ask("ai_gemini", {**cfg, "model": model},
                             f"{STRUCTURE}\n\nAnswer:\n{got['text'][:4000]}\n\nSites: {sites}", None))
    except (ProviderError, httpx.HTTPError, ValueError, TypeError, KeyError) as e:
        return _failed(model, str(e) if isinstance(e, ProviderError) else type(e).__name__)
    first = got["sources"][0]["url"] if got["sources"] else ""
    matches = [c for c in (_clean(m, searched, first) for m in (doc.get("matches") or [])[:5]) if c]
    real = resolve_links([m["url"] for m in matches])          # the model sometimes copies a redirect link too
    for m in matches:
        m["url"] = real.get(m["url"], m["url"])
    matches.sort(key=lambda x: -(x["confidence"] or 0))
    for m in matches:
        _with_catalog(m)
    note = None if searched or not matches else "Gemini answered from memory without searching; check it"
    return _store(key, {"matches": matches, "sources": got["sources"][:8], "engine": f"gemini:{model}", "note": note,
                        "queries": got["queries"][:4]})


def _with_catalog(m: dict) -> dict:
    """SpoolmanDB's official colour code and print defaults for a match, when it knows the filament."""
    from . import print_defaults
    try:
        cat = print_defaults.lookup(m.get("vendor"), m.get("material"), m.get("name") or "", m.get("color_name") or "",
                                    m.get("weight_g"))
    except Exception as e:  # noqa: BLE001
        log.info("SpoolmanDB lookup: %s", e)
        cat = None
    if cat:
        if cat.get("color_hex"):
            m["color_hex"], m["color_hex_source"] = cat["color_hex"].lower(), "SpoolmanDB"
        m["weight_g"] = m.get("weight_g") or cat.get("weight_g")
        m["diameter_mm"] = m.get("diameter_mm") or cat.get("diameter_mm")
        m["print_defaults"] = cat
    return m


def _catalog_only(guess: dict, barcode: str) -> dict | None:
    """Without Gemini: SpoolmanDB alone, as one match, when it knows the filament."""
    from . import print_defaults
    cat = print_defaults.lookup(guess.get("vendor"), guess.get("material"), guess.get("name") or "",
                                guess.get("color_name") or "", guess.get("weight_g"))
    if not cat:
        return None
    m = {"vendor": cat.get("manufacturer"), "name": f"{cat.get('material') or ''} {cat.get('name') or ''}".strip(),
         "material": str(cat.get("material") or "").upper(), "color_name": cat.get("name"),
         "color_hex": str(cat.get("color_hex") or "").lower(), "weight_g": cat.get("weight_g"),
         "diameter_mm": cat.get("diameter_mm"), "price": None, "currency": "", "url": "", "barcode": barcode,
         "confidence": 0.8, "verified": True, "color_hex_source": "SpoolmanDB", "print_defaults": cat}
    return {"matches": [m], "sources": [], "engine": "SpoolmanDB", "note": None}


def _failed(model: str, why: str) -> dict:
    log.info("product search %s: %s", model, why)
    return {"matches": [], "sources": [], "engine": f"gemini:{model}", "note": f"the online search failed: {why}"[:300]}


def _not_set_up() -> dict:
    return {"matches": [], "sources": [], "engine": None,
            "note": "online search needs the Google (Gemini) connection with a paid-tier key (Settings, Connected services)"}


def by_barcode(code: str) -> dict:
    cfg = gemini()
    if not cfg:
        return _not_set_up()
    code = re.sub(r"\D", "", code or "")
    if len(code) < 8:
        return {"matches": [], "sources": [], "engine": None, "note": "not a UPC/EAN"}
    q = (f"What product has the barcode {code}? It is probably a 3D printer filament. Give the brand, the exact "
         f"product name and colour, the material, the net weight, the diameter and a typical price, and say which "
         f"listings show this barcode.")
    return _answer(cfg, q, f"barcode:{code}")


def by_guess(guess: dict, barcode: str = "", text: str = "") -> dict:
    cfg = gemini()
    if not cfg:
        return _catalog_only(guess, barcode) or _not_set_up()
    g = {k: guess.get(k) for k in ("vendor", "name", "material", "color_name", "weight_g", "diameter_mm")}
    if not (g.get("vendor") or g.get("name")):
        return {"matches": [], "sources": [], "engine": None, "note": "read a brand or product name first"}
    desc = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in g.items() if v)
    q = (f"Which 3D printer filament product is this: {desc}?"
         + (f" Its barcode is {barcode}." if barcode else "")
         + (f" Text on its label: {text[:600]}." if text else "")
         + " Find the exact product as the maker or big retailers sell it, and give the brand, the exact product name "
           "and colour, the colour's hex code if listed, the material, the net weight, the diameter and a typical price.")
    key = "guess:" + json.dumps({**{k: str(v).lower() for k, v in g.items() if v}, "barcode": barcode}, sort_keys=True)
    return _answer(cfg, q, key)
