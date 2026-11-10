"""Print defaults for each library filament: extruder and bed temperature, density, diameter, spool weight and the
official colour code, from SpoolmanDB (services/filament_db.py), kept per team on the hub.

They are found when a spool of the filament is added (services set_for) and for the whole library at once
(backfill). Someone can set them by hand too (source "manual"), and SpoolmanDB never overwrites those. The app shows
them on the spool sheet; the slicer presets and the OpenPrintHQ/Spoolman filament are made from them.

Stored under the team setting "filament_defaults": {"<library filament id>": {extruder_temp, extruder_temp_range,
bed_temp, bed_temp_range, density, diameter_mm, spool_weight_g, weight_g, color_hex, finish, material, name,
manufacturer, source, source_id, at}}.
"""
import logging

from sqlalchemy.orm import Session

from ..kv import set_setting, setting
from ..models import now
from . import filament_db

log = logging.getLogger("hub.print_defaults")

KEY = "filament_defaults"


def all_(db: Session) -> dict:
    return dict(setting(db, KEY) or {})


def get(db: Session, filament_id) -> dict | None:
    if filament_id is None:
        return None
    return all_(db).get(str(filament_id))


def _store(db: Session, filament_id, row: dict) -> dict:
    allr = all_(db)
    allr[str(filament_id)] = row
    set_setting(db, KEY, allr)
    return row


def lookup(vendor, material="", name="", color_name="", weight_g=None) -> dict | None:
    """The SpoolmanDB defaults for a filament described this way, or None."""
    hit = filament_db.find(str(vendor or ""), str(material or ""), str(name or ""), str(color_name or ""), weight_g)
    if not hit:
        return None
    full = None
    for rows in filament_db._makers(str(vendor or "")):
        full = next((x for x in rows if x.get("id") == hit["id"]), None)
        if full:
            break
    full = full or {}
    return {"extruder_temp": hit.get("extruder_temp"), "extruder_temp_range": full.get("extruder_temp_range"),
            "bed_temp": hit.get("bed_temp"), "bed_temp_range": full.get("bed_temp_range"),
            "density": full.get("density"), "diameter_mm": hit.get("diameter_mm"),
            "spool_weight_g": hit.get("spool_weight_g"), "weight_g": hit.get("weight_g"),
            "color_hex": hit.get("color_hex"), "finish": hit.get("finish"), "material": hit.get("material"),
            "name": hit.get("name"), "manufacturer": hit.get("manufacturer"),
            "source": "SpoolmanDB", "source_id": hit.get("id")}


def set_for(db: Session, filament_id, vendor, material="", name="", color_name="", weight_g=None,
            replace: bool = False) -> dict | None:
    """Find and keep the defaults for one library filament. Leaves hand-set (manual) defaults alone, and existing
    ones unless replace."""
    if filament_id is None:
        return None
    have = get(db, filament_id)
    if have and (have.get("source") == "manual" or not replace):
        return have
    found = lookup(vendor, material, name, color_name, weight_g)
    if not found:
        if replace and have and have.get("source") == "SpoolmanDB":
            allr = all_(db)
            allr.pop(str(filament_id), None)              # an earlier match SpoolmanDB no longer supports
            set_setting(db, KEY, allr)
            return None
        return have
    return _store(db, filament_id, {**found, "at": now().isoformat()})


def set_manual(db: Session, filament_id, values: dict, by: str) -> dict:
    keep = ("extruder_temp", "bed_temp", "density", "diameter_mm", "spool_weight_g", "color_hex")
    row = {**(get(db, filament_id) or {}), **{k: values[k] for k in keep if k in values},
           "source": "manual", "by": by, "at": now().isoformat()}
    return _store(db, filament_id, row)


def for_spool(db: Session, spool: dict) -> dict | None:
    """The defaults for a spool's filament, finding them now when they are missing."""
    fid = spool.get("filament_id")
    have = get(db, fid)
    if have or fid is None:
        return have
    try:
        return set_for(db, fid, spool.get("vendor"), spool.get("material"),
                       " ".join(x for x in (spool.get("subtype"), spool.get("name")) if x), spool.get("color_name"),
                       spool.get("label_weight_g"))
    except Exception as e:  # noqa: BLE001
        log.info("print defaults for %s: %s", fid, e)
        return None


def backfill(db: Session, filaments: list[dict], replace: bool = False) -> dict:
    """Every library filament: {found, kept, missing: [names]}."""
    found = kept = 0
    missing = []
    for f in filaments:
        fid = f.get("id")
        before = get(db, fid)
        vendor = f.get("vendor") if isinstance(f.get("vendor"), str) else (f.get("vendor") or {}).get("name")
        got = set_for(db, fid, vendor, f.get("material"), f.get("name") or "", f.get("color_name") or "",
                      f.get("weight_g") or f.get("weight"), replace=replace)
        if got and got is not before:
            found += 1
        elif got:
            kept += 1
        else:
            missing.append(f"{vendor or '?'} {f.get('name') or ''}".strip())
    return {"found": found, "kept": kept, "missing": missing}
