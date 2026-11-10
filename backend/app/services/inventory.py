"""Refresh local caches of InvenTree parts and the spool library (Spoolman, or the farm's library)."""
from datetime import datetime, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..clients import inventree, spoolman
from ..models import PartCache, SpoolCache, FilamentCatalog


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def refresh_parts(db: Session) -> dict:
    inv = inventree()
    parts = inv.parts(active="true")
    sup_by_part: dict[int, list] = {}
    try:
        for sp in inv.supplier_parts():
            sup_by_part.setdefault(sp["part"], []).append({
                "name": (sp.get("supplier_detail") or {}).get("name"),
                "supplier_id": sp.get("supplier"), "supplier_part_id": sp.get("pk"),
                "sku": sp.get("SKU"), "link": sp.get("link"), "pack_qty": _f(sp.get("pack_quantity_native")) or 1,
                "price": None})
    except Exception:  # noqa: BLE001
        pass
    seen = set()
    for p in parts:
        pid = p["pk"]
        seen.add(pid)
        row = _row(db, PartCache, "inventree_part_id", pid)
        row.name = p.get("full_name") or p.get("name")
        row.ipn = p.get("IPN")
        row.category = (p.get("category_detail") or {}).get("pathstring") or p.get("category_name")
        row.units = p.get("units")
        row.in_stock = _f(p.get("in_stock") if p.get("in_stock") is not None else p.get("total_in_stock"))
        row.allocated = _f(p.get("allocated_to_sales_orders")) + _f(p.get("allocated_to_build_orders"))
        row.on_order = _f(p.get("ordering"))
        row.minimum_stock = _f(p.get("minimum_stock"))
        row.image = p.get("thumbnail") or p.get("image")
        row.suppliers = sup_by_part.get(pid, [])
        row.updated_at = datetime.now(timezone.utc)
    if seen:
        db.execute(delete(PartCache).where(PartCache.inventree_part_id.not_in(seen)))
    return {"parts": len(seen)}


def refresh_spools(db: Session) -> dict:
    """The spool cache from the team's filament library: the farm's (OpenPrintHQ) or the hub's own Spoolman."""
    from .spool_library import farm_library, refresh_cache
    if farm_library():
        return refresh_cache(db)
    sm = spoolman()
    spools = sm.spools(allow_archived=False)
    filaments = sm.filaments()
    seen = set()
    for s in spools:
        f = s.get("filament") or {}
        v = f.get("vendor") or {}
        _fill(_row(db, SpoolCache, "spool_id", s["id"]), filament_id=f.get("id"), vendor=v.get("name"),
              name=f.get("name"), material=f.get("material"),
              color_hex=("#" + f["color_hex"]) if f.get("color_hex") and not f["color_hex"].startswith("#")
              else f.get("color_hex"),
              remaining_g=_f(s.get("remaining_weight")), spool_weight_g=_f(f.get("weight")) or None,
              location=s.get("location"), archived=bool(s.get("archived")),
              updated_at=datetime.now(timezone.utc))
        seen.add(s["id"])
    db.execute(delete(SpoolCache).where(SpoolCache.spool_id.not_in(seen or {-1})))
    fseen = set()
    for f in filaments:
        v = f.get("vendor") or {}
        ch = f.get("color_hex")
        _fill(_row(db, FilamentCatalog, "filament_id", f["id"]), vendor=v.get("name"), name=f.get("name"),
              material=f.get("material"), color_hex=("#" + ch) if ch and not ch.startswith("#") else ch,
              weight_g=_f(f.get("weight")) or None, price=f.get("price"),
              article_number=f.get("article_number"),
              url=(f.get("extra") or {}).get("url", "").strip('"') or None)
        fseen.add(f["id"])
    db.execute(delete(FilamentCatalog).where(FilamentCatalog.filament_id.not_in(fseen or {-1})))
    return {"spools": len(seen), "filaments": len(fseen)}


def _row(db: Session, cls, key: str, value):
    """The team's cache row for an outside id, created if missing."""
    from sqlalchemy import select as _select
    row = db.execute(_select(cls).where(getattr(cls, key) == value)).scalar()
    if row is None:
        row = cls(**{key: value})
        db.add(row)
    return row


def _fill(row, **values):
    for k, v in values.items():
        setattr(row, k, v)
    return row


def remove_part(part_id: int, qty: float, note: str) -> float:
    """Take qty of an InvenTree part out of stock, oldest stock items first. Answers how much could not be taken."""
    inv = inventree()
    left = float(qty)
    batch = []
    for it in inv.stock(part=part_id, in_stock="true"):
        take = min(left, float(it["quantity"]))
        if take > 0:
            batch.append({"pk": it["pk"], "quantity": take})
            left -= take
        if left <= 0:
            break
    if batch:
        inv.remove_stock(batch, note)
    return max(0.0, left)
