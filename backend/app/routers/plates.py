"""Build plates: the catalog, the plate on each printer, and plate reslices waiting for review."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import need, Principal
from ..db import get_db
from ..jobs import enqueue
from ..models import BuildPlate, PlateSlice, PrinterProfile
from ..services import plates, plate_reslice, slicing
from .farm import _actor

router = APIRouter(prefix="/api")

# The slicer's plate types (OrcaSlicer names, plus Bambu Studio's name for the SuperTack)
SLICER_TYPES = list(slicing.BED_TYPES) + ["Cool Plate (SuperTack)"]


def plate_dict(p: BuildPlate, db: Session | None = None) -> dict:
    out = {"id": p.id, "name": p.name, "maker": p.maker, "surface": p.surface, "slicer_type": p.slicer_type,
           "bambu_marker": p.bambu_marker,
           "materials": list(p.materials or []), "notes": p.notes,
           "ready": bool(p.materials), }
    if db is not None:
        out["installed_on"] = [x.name for x in db.execute(
            select(PrinterProfile).where(PrinterProfile.installed_plate_id == p.id)).scalars()]
    return out


class PlateIn(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    maker: str | None = Field(default=None, max_length=60)
    slicer_type: str | None = None
    bambu_marker: bool | None = None
    materials: list[str] | None = None
    notes: str | None = Field(default=None, max_length=2000)


def _apply(p: BuildPlate, body: PlateIn):
    if body.name is not None:
        if not body.name.strip():
            raise HTTPException(400, "give the plate a name")
        p.name = body.name.strip()
    if body.maker is not None:
        p.maker = body.maker.strip() or None
    if body.slicer_type is not None:
        if body.slicer_type not in SLICER_TYPES:
            raise HTTPException(400, f"slicer_type must be one of: {', '.join(SLICER_TYPES)}")
        p.slicer_type = body.slicer_type
    if body.bambu_marker is not None:
        p.bambu_marker = body.bambu_marker
    if body.materials is not None:
        p.materials = sorted({m.strip().upper() for m in body.materials if m and m.strip()})
    if body.notes is not None:
        p.notes = body.notes.strip() or None


@router.get("/plates")
def list_plates(db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    plates.ensure_seed(db)
    db.commit()
    return {"plates": [plate_dict(p, db) for p in db.execute(select(BuildPlate).order_by(BuildPlate.maker, BuildPlate.name)).scalars()],
            "slicer_types": SLICER_TYPES}


@router.post("/plates")
def create_plate(body: PlateIn, db: Session = Depends(get_db), _: Principal = Depends(need("slice"))):
    if not body.name or not body.slicer_type:
        raise HTTPException(400, "a plate needs a name and a slicer_type")
    p = BuildPlate(name="", slicer_type="", materials=[])
    _apply(p, body)
    db.add(p)
    db.commit()
    return plate_dict(p, db)


@router.patch("/plates/{plate_id}")
def update_plate(plate_id: int, body: PlateIn, db: Session = Depends(get_db), _: Principal = Depends(need("slice"))):
    p = db.get(BuildPlate, plate_id)
    if not p:
        raise HTTPException(404, "plate not found")
    _apply(p, body)
    db.commit()
    return plate_dict(p, db)


@router.delete("/plates/{plate_id}")
def delete_plate(plate_id: int, db: Session = Depends(get_db), _: Principal = Depends(need("slice"))):
    p = db.get(BuildPlate, plate_id)
    if not p:
        raise HTTPException(404, "plate not found")
    if db.execute(select(PrinterProfile.id).where(PrinterProfile.installed_plate_id == p.id)).first():
        raise HTTPException(409, "that plate is on a printer: swap it off first")
    db.delete(p)
    db.commit()
    return {"deleted": True}


@router.get("/printer-profiles/plates")
def printer_plates(db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    out = []
    for prof in db.execute(select(PrinterProfile).where(PrinterProfile.active.is_(True)).order_by(PrinterProfile.name)).scalars():
        p = plates.installed(db, prof)
        out.append({"profile_id": prof.id, "printer": prof.name, "farm_printer": plates.farm_printer_name(prof),
                    "usual_plate": prof.bed_type, "installed_plate": plate_dict(p) if p else None,
                    "bambu": plates.is_bambu(prof)})
    return {"printers": out}


class InstalledIn(BaseModel):
    plate_id: int | None = None


@router.put("/printer-profiles/{profile_id}/plate")
def set_printer_plate(profile_id: int, body: InstalledIn, db: Session = Depends(get_db),
                      who: Principal = Depends(need("farm"))):
    prof = db.get(PrinterProfile, profile_id)
    if not prof:
        raise HTTPException(404, "printer profile not found")
    plate = None
    if body.plate_id is not None:
        plate = db.get(BuildPlate, body.plate_id)
        if not plate:
            raise HTTPException(404, "plate not found")
    out = plates.set_installed(db, prof, plate, actor=_actor(who))
    db.commit()
    enqueue(db, "farm.plate_scan", ref="plate:scan", delay=2)
    db.commit()
    return out


def slice_dict(ps: PlateSlice, db: Session) -> dict:
    prof = db.get(PrinterProfile, ps.profile_id)
    return {"id": ps.id, "model_id": ps.model_id, "printer": prof.name if prof else None, "plate": ps.plate,
            "state": ps.state, "problems": ps.problems or [], "stats": ps.stats or {}, "error": ps.error,
            "file_ids": ps.file_ids or [], "sliced_at": ps.sliced_at.isoformat() if ps.sliced_at else None}


@router.get("/plate-slices")
def list_plate_slices(state: str | None = None, db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    q = select(PlateSlice).order_by(PlateSlice.id.desc()).limit(200)
    if state:
        q = q.where(PlateSlice.state == state)
    return {"plate_slices": [slice_dict(ps, db) for ps in db.execute(q).scalars()]}


@router.post("/plate-slices/{ps_id}/approve")
def approve_plate_slice(ps_id: int, db: Session = Depends(get_db), who: Principal = Depends(need("slice"))):
    ps = db.get(PlateSlice, ps_id)
    if not ps:
        raise HTTPException(404, "plate slice not found")
    if ps.state != "review":
        raise HTTPException(409, f"nothing to approve: the plate slice is {ps.state}")
    plate_reslice.approve(db, ps, _actor(who))
    db.commit()
    enqueue(db, "farm.plate_scan", ref="plate:scan", delay=2)
    db.commit()
    return slice_dict(ps, db)
