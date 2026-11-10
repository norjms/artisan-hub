"""Filament material and name clean-up for spools added by people, phones or AI (2026-11-08).

A label or a model often answers OTHER for materials outside a short list (ASA-CF, PA6-GF), and product names carry
trademark marks (Fiberon™). Spools keep the real material, read from the name when the given one is OTHER or less
specific, and no trademark marks.
"""
import re

MARKS_RE = re.compile(r"\s*(?:[\u2122\u00ae\u00a9\u2120]|\((?:tm|TM|Tm|r|R|c|C)\))")
# A material code in a product name, e.g. "PA6-GF25" -> PA6-GF, "ASA-CF08" -> ASA-CF, "PLA+" -> PLA+.
_MAT_RE = re.compile(r"(?<![A-Za-z0-9])(PLA\+?|PETG|PCTG|PET|ABS|ASA|TPU|TPE|PAHT|PPA|PA\d{0,2}|PC|PVA|HIPS|PPS|PP|"
                     r"PEEK|PEI|BVOH|PVB)(?:[-_ ]?(CF|GF))?\d{0,2}(?![A-Za-z])")
VAGUE = {"", "OTHER", "OTHERS", "UNKNOWN", "N/A", "NA", "MIXED"}


def strip_marks(s: str | None) -> str:
    """The text without trademark marks (TM, R, C, SM) and with single spaces."""
    return re.sub(r"\s{2,}", " ", MARKS_RE.sub("", s or "")).strip()


def material_from_text(*texts: str | None) -> str | None:
    """The first material code written in the texts (upper case only, so ordinary words never match)."""
    for t in texts:
        m = _MAT_RE.search(strip_marks(t))
        if m:
            return m.group(1) + (f"-{m.group(2)}" if m.group(2) else "")
    return None


def clean_material(material: str | None, *texts: str | None) -> str:
    """The material to keep: the name's code when the given one is vague (OTHER) or a less specific form of it
    (ASA given, ASA-CF in the name); otherwise the given one, upper case."""
    given = strip_marks(material).upper()
    found = material_from_text(*texts)
    if given in VAGUE:
        return found or ("OTHER" if given else "")
    if found and found != given and found.startswith(given):
        return found
    return given


def clean_filament(fil: dict) -> dict:
    """A copy of a filament description with marks removed and the material made specific."""
    out = dict(fil)
    for k in ("vendor", "name", "color_name"):
        if out.get(k) is not None:
            out[k] = strip_marks(str(out[k]))
    if "material" in out or out.get("name"):
        out["material"] = clean_material(out.get("material"), out.get("name"), out.get("color_name")) or out.get("material")
    return out
