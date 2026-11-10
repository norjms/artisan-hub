"""SpoolmanDB (https://github.com/Donkie/SpoolmanDB): the open database of filaments by maker, with official colour
codes, extruder and bed temperatures, spool weight and diameter. Downloaded once a day (about 6 MB, 9,000+ entries)
and searched in memory.

find(vendor, material, name, color_name) answers the best entry for a filament someone read off a label or found
online, or None when nothing is close enough. Colour names must match: "Clay Brown" from Bambu Lab finds
"Clay Brown" (PLA+WOOD, #995F11), never another brown.
"""
import logging
import re
import threading
import time

import httpx

log = logging.getLogger("hub.filament_db")

URL = "https://donkie.github.io/SpoolmanDB/filaments.json"
TTL = 24 * 3600
_rows: list[dict] = []
_by_maker: dict[str, list[dict]] = {}
_at = 0.0
_lock = threading.Lock()

FINISHES = ("matte", "glossy", "silk", "satin", "metallic", "sparkle", "glow", "translucent")
# Filler only: words that never tell two filaments apart. Product-line words (silk, matte, wood, marble, metal, glow,
# cf, ...) are NOT filler: "Silk White" needs "silk" in what was read, and a read saying "wood" never matches a
# non-wood entry. The maker's own name is filler too (added per lookup).
STOP = {"filament", "filaments", "3d", "printer", "printing", "spool", "refill", "with", "and", "the", "for", "kg", "1kg",
        "175mm", "175", "mm", "pla", "petg", "abs", "asa", "tpu", "pa", "pc", "pp", "basic", "plus", "lab"}
LINE_WORDS = {"silk", "matte", "wood", "marble", "metal", "metallic", "glow", "sparkle", "galaxy", "cf", "gf",
              "translucent", "gradient", "dual", "tri", "rainbow", "aero", "lw", "support", "hf", "hs"}


def _words(s, extra=frozenset()) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", str(s or "").lower().replace(".", ""))
            if w not in STOP and w not in extra and len(w) > 1}


def _maker_key(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _load() -> list[dict]:
    global _rows, _by_maker, _at
    with _lock:
        if _rows and time.time() - _at < TTL:
            return _rows
        try:
            data = httpx.get(URL, timeout=60, follow_redirects=True).json()
            if isinstance(data, list) and data:
                by: dict[str, list[dict]] = {}
                for x in data:
                    by.setdefault(_maker_key(x.get("manufacturer")), []).append(x)
                _rows, _by_maker, _at = data, by, time.time()
        except Exception as e:  # noqa: BLE001
            log.info("SpoolmanDB: %s", e)
            _at = time.time() - TTL + 600                  # try again in ten minutes
        return _rows


def _makers(vendor: str) -> list[list[dict]]:
    """The maker's entries: exact name, or one name starting with the other (Bambu, Bambu Lab, BambuLab)."""
    k = _maker_key(vendor)
    if not k:
        return []
    if k in _by_maker:
        return [_by_maker[k]]
    return [rows for m, rows in _by_maker.items() if len(k) >= 4 and len(m) >= 4 and (m.startswith(k) or k.startswith(m))]


def _material_ok(want: str, have: str) -> bool:
    w, h = re.sub(r"[^A-Z]", "", want.upper()), re.sub(r"[^A-Z]", "", have.upper())
    if not w:
        return True
    return h.startswith(w) or w.startswith(h) or w in h


def find(vendor: str, material: str = "", name: str = "", color_name: str = "", weight_g=None) -> dict | None:
    _load()
    groups = _makers(vendor)
    if not groups:
        return None
    brand = _words(vendor) | {_maker_key(vendor)}
    colour = _words(color_name, brand)
    text = _words(name, brand) | colour
    if not text:
        return None
    best, best_score = None, 0.0
    for rows in groups:
        for x in rows:
            entry = _words(x.get("name"), brand)
            if not entry:
                continue
            # the entry's name (colour and line) must be fully named by what was read
            if not entry <= text:
                continue
            # a product-line word that was read must belong to the entry (its name, material or finish)
            about = entry | _words(x.get("material")) | set(re.findall(r"[a-z]+", str(x.get("material") or "").lower())) \
                | _words(x.get("finish"))
            if (text & LINE_WORDS) - about:
                continue
            score = len(entry) * 10.0
            if colour and entry == colour:
                score += 20
            if material and not _material_ok(material, x.get("material") or ""):
                score -= 15
            if weight_g and x.get("weight") and abs(float(x["weight"]) - float(weight_g)) < 1:
                score += 2
            if (x.get("diameter") or 1.75) == 1.75:
                score += 1
            fin = str(x.get("finish") or "").lower()
            said = {f for f in FINISHES if f in name.lower()}
            if fin and fin in said:
                score += 6
            elif said and fin and fin not in said:
                score -= 6
            if score > best_score:
                best, best_score = x, score
    if not best or best_score < 10:
        return None
    hexes = best.get("color_hexes") or []
    return {"source": "SpoolmanDB", "id": best.get("id"), "manufacturer": best.get("manufacturer"),
            "name": best.get("name"), "material": best.get("material"),
            "color_hex": (best.get("color_hex") or (hexes[0] if hexes else "") or "").lower(),
            "weight_g": best.get("weight"), "diameter_mm": best.get("diameter"), "spool_weight_g": best.get("spool_weight"),
            "extruder_temp": best.get("extruder_temp"), "bed_temp": best.get("bed_temp"), "finish": best.get("finish")}
