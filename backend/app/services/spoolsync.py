"""Two-way spool sync between Spoolman (source of truth for filament on hand) and Watchtower (records what prints use).

Every spool is linked one to one (spool_link). At each sync the weight both sides agreed on last time (last_g)
tells the changes apart:
- Watchtower used filament (its weight went down) and Spoolman didn't change: the grams are recorded as use in
  Spoolman, so Spoolman's history shows them.
- Spoolman changed (weighed on a scale, corrected by hand) and Watchtower didn't: Watchtower is set to Spoolman's
  weight.
- Watchtower's weight went up (weighed there) and Spoolman didn't change: Spoolman takes Watchtower's reading.
- Both changed: Watchtower's use is applied on top of Spoolman's new weight, then Watchtower is set to the result;
  a Watchtower increase loses to Spoolman's change.
A spool added on either side is created on the other and linked. A spool archived in Spoolman stops syncing; a
spool Watchtower reports empty (0 g) is archived in Spoolman. Nothing is ever deleted on either side; a spool
deleted on one side leaves its link marked broken. Watchtower stock rows holding several identical spools
(quantity > 1) are not synced, since Spoolman keeps one row per spool.
"""
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..jobs import event
from ..kv import setting, set_setting
from ..models import SpoolLink

log = logging.getLogger("hub.spoolsync")
EPS = 0.05
DENSITY = {"PLA": 1.24, "PETG": 1.27, "ABS": 1.04, "ASA": 1.07, "TPU": 1.21, "PC": 1.20, "PA": 1.14, "PVA": 1.23,
           "HIPS": 1.04, "PET": 1.38}


def _now():
    return datetime.now(timezone.utc)


def _clients():
    from ..clients import farm, spoolman
    return spoolman(), farm()


def _hex(v):
    v = (v or "").strip().lstrip("#")
    return v.lower() if re.fullmatch(r"[0-9a-fA-F]{6}", v) else None


def _wt_list(w) -> list[dict]:
    r = w.spools()
    return r.get("spools", r) if isinstance(r, dict) else (r or [])


def _density(material: str | None) -> float:
    m = (material or "PLA").upper()
    for k, v in DENSITY.items():
        if m.startswith(k):
            return v
    return 1.24


class _Catalog:
    """Spoolman vendors and filaments, looked up or created for a Watchtower spool."""

    def __init__(self, sm):
        self.sm = sm
        self.vendors = {v["name"].lower(): v["id"] for v in sm.vendors()}
        self.fils = {((f.get("vendor") or {}).get("name", "").lower(), (f.get("material") or "").upper(),
                      (f.get("color_hex") or "").lower(), (f.get("name") or "").lower()): f["id"] for f in sm.filaments()}

    def filament_for(self, w: dict) -> int:
        vname = w.get("manufacturer") or "Unknown"
        vid = self.vendors.get(vname.lower())
        if not vid:
            vid = self.sm.create_vendor(vname)["id"]
            self.vendors[vname.lower()] = vid
        hexv = _hex(w.get("color_hex"))
        fname = " ".join(x for x in [w.get("color"), w.get("variant")] if x) or w.get("material_type") or "Filament"
        mat = w.get("material_type") or "PLA"
        key = (vname.lower(), mat.upper(), hexv or "", fname.lower())
        if key in self.fils:
            return self.fils[key]
        body = {"name": fname, "vendor_id": vid, "material": mat, "density": w.get("density") or _density(mat),
                "diameter": w.get("diameter") or 1.75, "weight": w.get("initial_weight") or 1000}
        if hexv:
            body["color_hex"] = hexv
        if w.get("cost"):
            body["price"] = w["cost"]
        fid = self.sm.create_filament(body)["id"]
        self.fils[key] = fid
        return fid


def _wt_body(s: dict) -> dict:
    """A Watchtower spool from a Spoolman one."""
    f = s.get("filament") or {}
    v = f.get("vendor") or {}
    name = f.get("name") or f.get("material") or "Filament"
    hexv = _hex(f.get("color_hex"))
    body = {"material_type": f.get("material") or "PLA", "color": name, "manufacturer": v.get("name"),
            "initial_weight": s.get("initial_weight") or f.get("weight") or 1000,
            "remaining_weight": max(0.0, float(s.get("remaining_weight") or 0)),
            "diameter": 2.85 if abs((f.get("diameter") or 1.75) - 2.85) < 0.1 else 1.75,
            "location": s.get("location") or None, "notes": f"Spoolman spool #{s['id']} (Artisan Hub sync)",
            "status": "available"}
    if hexv:
        body["color_hex"] = "#" + hexv.upper()
    if f.get("density") and 0.5 <= f["density"] <= 5:
        body["density"] = f["density"]
    if s.get("price") or f.get("price"):
        body["cost"] = s.get("price") or f.get("price")
    body["initial_weight"] = max(body["initial_weight"], body["remaining_weight"], 1)
    return {k: v for k, v in body.items() if v is not None}


def _link_existing(db: Session, sm_spools: list, wt_spools: list) -> int:
    """First run: link spools the earlier one-way import created (its comment and mapping name the pair)."""
    linked = {l.spoolman_id for l in db.execute(select(SpoolLink)).scalars()}
    wt_linked = {l.wt_spool_id for l in db.execute(select(SpoolLink)).scalars()}
    wt_ids = {w["id"] for w in wt_spools}
    pairs = {int(k): int(v) for k, v in ((setting(db, "wt_spool_import") or {}).items())}
    n = 0
    for s in sm_spools:
        if s["id"] in linked:
            continue
        m = re.search(r"Watchtower spool #(\d+)", s.get("comment") or "")
        wid = int(m.group(1)) if m else next((w for w, sid in pairs.items() if sid == s["id"]), None)
        if wid and wid in wt_ids and wid not in wt_linked:
            w = next(x for x in wt_spools if x["id"] == wid)
            g = float(w.get("remaining_weight") or 0)
            db.add(SpoolLink(spoolman_id=s["id"], wt_spool_id=wid,
                             last_g=g if abs(g - float(s.get("remaining_weight") or 0)) < EPS else None))
            linked.add(s["id"])
            wt_linked.add(wid)
            n += 1
    db.flush()
    return n


def sync(db: Session, dry_run: bool = False) -> dict:
    """dry_run: report what would change, writing nothing to either side or to the hub."""
    if not dry_run:
        return _sync(db, False)
    sp = db.begin_nested()
    try:
        return _sync(db, True)
    finally:
        sp.rollback()


def _sync(db: Session, dry_run: bool) -> dict:
    sm, w = _clients()
    sm_spools = sm.spools(allow_archived=True)
    wt_spools = _wt_list(w)
    out = {"linked": _link_existing(db, sm_spools, wt_spools), "to_spoolman": [], "to_watchtower": [],
           "created_in_spoolman": [], "created_in_watchtower": [], "archived": [], "skipped": [], "broken": []}
    sm_by = {s["id"]: s for s in sm_spools}
    wt_by = {x["id"]: x for x in wt_spools}
    links = list(db.execute(select(SpoolLink)).scalars())
    sm_linked = {l.spoolman_id for l in links}
    wt_linked = {l.wt_spool_id for l in links}

    for l in links:
        s, x = sm_by.get(l.spoolman_id), wt_by.get(l.wt_spool_id)
        if not s or not x:
            if l.state != "broken":
                l.state = "broken"
                l.note = ("deleted in Spoolman" if not s else "deleted in Watchtower") + "; not recreated"
                out["broken"].append(l.spoolman_id)
            continue
        if s.get("archived"):
            l.state, l.note = "archived", "archived in Spoolman; not synced"
            continue
        if l.state != "ok":
            l.state, l.note = "ok", None
        sg, wg = float(s.get("remaining_weight") or 0), float(x.get("remaining_weight") or 0)
        last = l.last_g
        if last is None:          # first sync of this pair: Spoolman is the truth
            if abs(sg - wg) > EPS:
                out["to_watchtower"].append({"spool": l.spoolman_id, "from": round(wg, 1), "to": round(sg, 1)})
                if not dry_run:
                    w.patch_spool(l.wt_spool_id, {"remaining_weight": round(sg, 2)})
            l.last_g, l.synced_at = sg, _now()
            continue
        used = last - wg              # Watchtower's change since the last sync (positive = filament used)
        sm_moved = abs(last - sg) > EPS
        if sm_moved and not dry_run:  # weighed or corrected in Spoolman: true up the prints on this spool
            from .filament_use import weigh_in
            w_in = weigh_in(db, l.spoolman_id, extra_used_g=last - sg, remaining_g=sg)
            out.setdefault("weigh_ins", []).append({"spool": l.spoolman_id, "factor": w_in.factor, "note": w_in.note})
        target = sg
        if used > EPS:
            target = max(0.0, sg - used)
            out["to_spoolman"].append({"spool": l.spoolman_id, "used_g": round(used, 1), "now": round(target, 1)})
            if not dry_run:
                sm.use(l.spoolman_id, round(used, 3))
        elif used < -EPS and not sm_moved:
            # weighed or corrected upward in Watchtower and Spoolman untouched: take Watchtower's reading
            target = wg
            out["to_spoolman"].append({"spool": l.spoolman_id, "set_g": round(wg, 1), "now": round(wg, 1)})
            if not dry_run:
                sm.patch_spool(l.spoolman_id, {"remaining_weight": round(wg, 3)})
        if abs(target - wg) > EPS:
            out["to_watchtower"].append({"spool": l.spoolman_id, "from": round(wg, 1), "to": round(target, 1)})
            if not dry_run:
                w.patch_spool(l.wt_spool_id, {"remaining_weight": round(target, 2)})
        if target <= EPS and wg <= EPS and not s.get("archived"):
            out["archived"].append(l.spoolman_id)
            if not dry_run:
                sm.patch_spool(l.spoolman_id, {"archived": True})
            l.state, l.note = "archived", "empty in Watchtower, archived in Spoolman"
        l.last_g, l.synced_at = target, _now()

    cat = None
    for x in wt_spools:                # new in Watchtower -> Spoolman
        if x["id"] in wt_linked:
            continue
        if (x.get("quantity") or 1) > 1:
            out["skipped"].append({"watchtower": x["id"], "why": f"one Watchtower row holds {x['quantity']} spools"})
            continue
        out["created_in_spoolman"].append({"watchtower": x["id"], "what": f"{x.get('manufacturer')} {x.get('material_type')} {x.get('color')}"})
        if dry_run:
            continue
        cat = cat or _Catalog(sm)
        g = float(x.get("remaining_weight") or 0)
        new = sm.create_spool({"filament_id": cat.filament_for(x), "initial_weight": x.get("initial_weight") or 1000,
                               "remaining_weight": g, "location": x.get("location") or None,
                               "comment": f"Watchtower spool #{x['id']} (Artisan Hub sync)"})
        db.add(SpoolLink(spoolman_id=new["id"], wt_spool_id=x["id"], last_g=g, synced_at=_now()))
    for s in sm_spools:                # new in Spoolman -> Watchtower
        if s["id"] in sm_linked or s.get("archived"):
            continue
        if re.search(r"Watchtower spool #\d+", s.get("comment") or ""):
            continue                   # came from Watchtower; its pair is gone
        body = _wt_body(s)
        out["created_in_watchtower"].append({"spoolman": s["id"], "what": f"{body.get('manufacturer')} {body['material_type']} {body['color']}"})
        if dry_run:
            continue
        r = w.create_spool(body) or {}
        ids = r.get("spool_ids") or r.get("ids") or ([r["id"]] if r.get("id") else []) or \
            [x.get("id") for x in (r.get("spools") or [])]
        if not ids:
            raise RuntimeError(f"Watchtower didn't say which spool it created: {str(r)[:200]}")
        db.add(SpoolLink(spoolman_id=s["id"], wt_spool_id=int(ids[0]), last_g=body["remaining_weight"], synced_at=_now()))
    db.flush()
    if not dry_run:
        moved = sum(len(out[k]) for k in ("to_spoolman", "to_watchtower", "created_in_spoolman", "created_in_watchtower", "archived"))
        if moved:
            event(db, "spools", "Spool sync: " + ", ".join(f"{len(v)} {k.replace('_', ' ')}" for k, v in out.items()
                                                       if isinstance(v, list) and v))
        set_setting(db, "spool_sync_last", {"at": _now().isoformat(), **{k: (len(v) if isinstance(v, list) else v) for k, v in out.items()}})
    return out


def status(db: Session) -> dict:
    links = list(db.execute(select(SpoolLink).order_by(SpoolLink.id)).scalars())
    return {"enabled": bool(setting(db, "spool_sync", True)), "last": setting(db, "spool_sync_last"),
            "links": [{"spoolman_id": l.spoolman_id, "watchtower_id": l.wt_spool_id, "grams": l.last_g, "state": l.state,
                       "note": l.note, "synced_at": l.synced_at.isoformat() if l.synced_at else None} for l in links]}
