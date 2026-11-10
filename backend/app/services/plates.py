"""Build plates: the team's catalog, the plate on each printer, and whether a plate suits a job's filaments.

The hub's record of the installed plate is the source of truth. A Bambu printer can recognise only Bambu's own
marked plates, so there is nothing to read back for a third-party plate; keeping the record current when a plate
is swapped is what makes plate-aware reslicing work (services/plate_reslice.py).
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import BuildPlate, PrinterProfile

log = logging.getLogger(__name__)

# Surfaces: what the slicer is told (plate type) and the filament families a surface of that kind usually suits.
# The slicer only knows Bambu's plate types, so every plate maps to one of them. Rules from the makers and from
# Bambu's own G-code: smooth third-party plates (PEI, PEX, G10, carbon, Darkmoon's range) go in as "High Temp
# Plate" (Bambu Studio's "Smooth/High Temp"); only genuinely textured plates use "Textured PEI Plate", because
# Bambu's G-code applies Z-offset exceptions for that type. Materials are starting points: edit them per plate.
# Glue or adhesion promoter is the operator's job, so a filament that needs it still counts.
SURFACES = {
    "smooth_pei":    ("High Temp Plate",     ["PLA", "PETG", "PET", "ABS", "ASA", "PC", "PA", "TPU"]),
    "textured_pei":  ("Textured PEI Plate",  ["PLA", "PETG", "PET", "ABS", "ASA", "TPU", "PVA", "PC", "PA"]),
    "cool":          ("Cool Plate",          ["PLA", "PETG", "TPU"]),
    "textured_cool": ("Textured Cool Plate", ["PLA", "PETG", "TPU"]),
    "supertack":     ("Supertack Plate",     ["PLA", "PETG"]),
    "engineering":   ("Engineering Plate",   ["PLA", "PETG", "ABS", "ASA", "PC", "PA", "TPU"]),
    "g10":           ("High Temp Plate",     ["PLA", "PETG", "ABS", "ASA", "PC", "PA", "PPA", "TPU"]),
    "g10_hobby":     ("High Temp Plate",     ["PLA", "PETG", "ABS", "ASA", "PC", "TPU"]),
    "carbon":        ("High Temp Plate",     ["PLA", "PETG", "PCTG", "PET", "TPU", "ABS", "ASA", "PC", "PA", "PPA", "PPS", "PP"]),
    "pex":           ("High Temp Plate",     ["PLA", "PETG", "ABS", "ASA", "PC", "PA", "TPU"]),
    "cold":          ("Cool Plate",          ["PLA", "PETG", "TPU"]),
    "ice":           ("High Temp Plate",     ["PLA", "PETG", "TPU"]),
}

# (maker, name, surface, genuine Bambu marker, notes). Seeded into every team's catalog, adding only the plates it
# doesn't have yet; a plate already there keeps the slicer type and filaments someone set.
CATALOG = [
    ("Bambu Lab", "Bambu Cool Plate", "cool", True, ""),
    ("Bambu Lab", "Bambu SuperTack", "supertack", True,
     "Bambu: PLA and PETG. PLA Silk may stick too strongly (Bambu PLA Silk+ is fine on the SuperTack Pro)."),
    ("Bambu Lab", "Bambu SuperTack Pro", "supertack", True, "Bambu: PLA and PETG; lasts longer, runs a little cooler."),
    ("Bambu Lab", "Bambu Engineering Plate", "engineering", True, ""),
    ("Bambu Lab", "Bambu High Temperature Plate", "smooth_pei", True, "Bambu's smooth PEI plate."),
    ("Bambu Lab", "Bambu Textured PEI", "textured_pei", True, "Bambu's table. TPU, PC and PA need glue stick."),
    ("Bambu Lab", "Bambu Textured Cool Plate", "textured_cool", True, ""),
    ("Prusa", "Prusa Smooth PEI Sheet", "smooth_pei", False, ""),
    ("Prusa", "Prusa Textured PEI Sheet", "textured_pei", False, "Powder-coated PEI."),
    ("Prusa", "Prusa Satin Sheet", "textured_pei", False, "Fine powder-coated texture."),
    ("Prusa", "Prusa Smooth Hi-Temp Sheet", "smooth_pei", False, "For high-temperature materials."),
    ("Darkmoon", "Darkmoon CFX", "carbon", False,
     "Darkmoon CFX carbon fiber plate. PP needs a PP adhesion promoter. Bambu plate marker "
     "detection is turned off on the printer while it is fitted."),
    ("Darkmoon", "Darkmoon G10 Pro", "g10", False, "Garolite; nylon-rated."),
    ("Darkmoon", "Darkmoon G10 Hobby", "g10_hobby", False, "Garolite; not rated for nylon."),
    ("Darkmoon", "Darkmoon ICE", "ice", False, "Very high PLA adhesion."),
    ("Darkmoon", "Darkmoon Satin", "smooth_pei", False, "Darkmoon recommends the smooth/high temp plate setting."),
    ("Darkmoon", "Darkmoon LUX", "smooth_pei", False, ""),
    ("Wham Bam", "Wham Bam PEX", "pex", False, "Ships with markers that identify it as a high temp plate."),
    ("Wham Bam", "Wham Bam Textured PEI", "textured_pei", False, ""),
    ("Wham Bam", "Wham Bam Smooth PEI", "smooth_pei", False, ""),
    ("BIQU", "BIQU CryoGrip Pro Glacier", "cold", False, "Prints PLA on a cold or barely warm bed."),
    ("BIQU", "BIQU CryoGrip Pro Frostbite", "cold", False, ""),
    ("BIQU", "BIQU Smooth PEI", "smooth_pei", False, ""),
    ("BIQU", "BIQU Textured PEI", "textured_pei", False, ""),
    ("Energetic", "Energetic Smooth PEI", "smooth_pei", False, ""),
    ("Energetic", "Energetic Textured PEI", "textured_pei", False, ""),
    ("Energetic", "Energetic PEO", "cool", False, ""),
    ("Fysetc", "Fysetc Smooth PEI", "smooth_pei", False, ""),
    ("Fysetc", "Fysetc Textured PEI", "textured_pei", False, ""),
    ("Fysetc", "Fysetc PEO", "cool", False, ""),
    ("Garolite LLC", "Garolite G10 Sheet", "g10", False, ""),
    ("Lightyer", "Lightyer G10", "g10", False, "Users run it 5-10 C hotter than the stock plate."),
    ("Lightyer", "Lightyer Textured PEI", "textured_pei", False, ""),
    ("Siboor", "Siboor Smooth PEI", "smooth_pei", False, ""),
    ("Siboor", "Siboor Textured PEI", "textured_pei", False, ""),
    ("Creality", "Creality Smooth PEI", "smooth_pei", False, ""),
    ("Creality", "Creality Textured PEI", "textured_pei", False, ""),
    ("Generic", "Generic Smooth PEI", "smooth_pei", False, ""),
    ("Generic", "Generic Textured PEI", "textured_pei", False, ""),
    ("Generic", "Generic PEO", "cool", False, ""),
    ("Generic", "Generic Garolite (G10)", "g10", False, ""),
]
SEED = [{"maker": mk, "name": nm, "surface": sf, "slicer_type": SURFACES[sf][0], "bambu_marker": mark,
         "materials": list(SURFACES[sf][1]), "notes": note or None} for mk, nm, sf, mark, note in CATALOG]

# Printers whose slicer preset has no "High Temp Plate" (Bambu removed smooth/high temp for the H2C): a plate that
# slices as High Temp goes to them as Engineering Plate, the type Darkmoon and others recommend there.
NO_HIGH_TEMP = ("h2c",)


def slicer_type_for(plate: BuildPlate, prof: PrinterProfile | None) -> str:
    """The plate type to slice with (and to record on the engine) for this plate on this printer."""
    t = plate.slicer_type
    preset = ((prof.machine_preset if prof else "") or "").lower()
    if t == "High Temp Plate" and any(k in preset for k in NO_HIGH_TEMP):
        return "Engineering Plate"
    return t


# Slicer plate names that mean the same plate (the engine's plate check treats them alike too)
_ALIASES = {"coolplatesupertack": "supertack"}


def plate_key(name: str | None) -> str:
    """Comparable key for a slicer plate name, the same normalising the engine uses."""
    if not name:
        return ""
    k = re.sub(r"[^a-z0-9]", "", name.lower())
    k = k[:-5] if k.endswith("plate") else k
    return _ALIASES.get(k, k)


def family(material: str | None) -> str:
    """The filament family a type belongs to: "PA6-CF" -> "PA", "PETG-HF" -> "PETG", "PLA+" -> "PLA"."""
    base = re.split(r"[-+ /_]", (material or "").strip().upper())[0]
    if base.startswith("PA") and base != "PA":
        return "PA"            # PA6, PA12, PAHT
    if base in ("PET", "PETG", "PCTG", "PPA", "PPS", "PP", "PC", "PLA", "ABS", "ASA", "TPU", "TPE", "PVA", "HIPS"):
        return base
    return base


def suits(plate: BuildPlate, filament_types: list[str] | None) -> tuple[bool, str | None]:
    """(ok, why not). An empty materials list means nobody said what the plate suits, so it does not."""
    mats = {m.upper() for m in (plate.materials or [])}
    if not mats:
        return False, f"{plate.name}: which filaments it suits isn't filled in"
    if "*" in mats:
        return True, None
    wanted = sorted({family(t) for t in (filament_types or []) if t})
    if not wanted:
        return False, "the job's filament types are unknown"
    missing = [w for w in wanted if w not in mats]
    if missing:
        return False, f"{plate.name} doesn't suit {', '.join(missing)}"
    return True, None


def ensure_seed(db: Session) -> None:
    """Add the catalog's plates this team doesn't have yet; fill in maker and surface on ones it has. A plate's
    slicer type, filaments and notes are never overwritten here (someone may have edited them)."""
    have = {p.name: p for p in db.execute(select(BuildPlate)).scalars()}
    for p in SEED:
        row = have.get(p["name"])
        if row is None:
            db.add(BuildPlate(**p))
        else:
            row.maker = row.maker or p["maker"]
            row.surface = row.surface or p["surface"]
    db.flush()


def by_slicer_type(db: Session, slicer_type: str | None) -> BuildPlate | None:
    key = plate_key(slicer_type)
    if not key:
        return None
    for p in db.execute(select(BuildPlate)).scalars():
        if plate_key(p.slicer_type) == key:
            return p
    return None


def installed(db: Session, prof: PrinterProfile) -> BuildPlate | None:
    return db.get(BuildPlate, prof.installed_plate_id) if prof.installed_plate_id else None


def farm_printer_name(prof: PrinterProfile) -> str:
    return prof.wt_printer or prof.name


def is_bambu(prof: PrinterProfile) -> bool:
    return (prof.machine_preset or "").lower().startswith("bambu")


def set_installed(db: Session, prof: PrinterProfile, plate: BuildPlate | None, actor: str = "") -> dict:
    """Record the plate now on the printer and tell the farm: its plate check, and the Bambu marker detection
    (off for a plate without Bambu's marker, on for one with it). Logs the swap as maintenance."""
    from ..clients import farm
    from ..clients.farm_base import FarmUnsupported
    from ..models import Event

    prof.installed_plate_id = plate.id if plate else None
    db.flush()
    out: dict = {"printer": prof.name, "plate": plate.name if plate else None, "farm": {}, "errors": []}
    name = farm_printer_name(prof)
    try:
        c = farm()
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"farm not connected: {e}")
        return out
    for label, call in (
        ("installed_plate", lambda: c.set_installed_plate(name, slicer_type_for(plate, prof) if plate else None)),
        ("marker_detection", (lambda: c.set_plate_detection(name, bool(plate.bambu_marker)))
         if plate and is_bambu(prof) else None),
        ("maintenance_log", (lambda: c.maint_log(name, f"Plate swapped: {plate.name}", "other", actor or "hub",
                                                 f"Slices as {plate.slicer_type}"))
         if plate and c.has("maintenance_log") else None),
    ):
        if call is None:
            continue
        try:
            call()
            out["farm"][label] = True
        except (FarmUnsupported, AttributeError):
            out["farm"][label] = "unsupported"
        except Exception as e:  # noqa: BLE001
            out["farm"][label] = False
            out["errors"].append(f"{label}: {getattr(e, 'message', None) or e}")
    db.add(Event(ref=f"printer:{prof.id}", level="warn" if out["errors"] else "info", actor=actor or None,
                 message=f"{prof.name}: plate set to {plate.name if plate else 'none'}"
                         + (f" ({'; '.join(out['errors'])})" if out["errors"] else "")))
    db.flush()
    return out
