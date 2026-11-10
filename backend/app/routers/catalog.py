"""Models, images, files, products."""
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select, or_, func
from sqlalchemy.orm import Session, selectinload

from ..auth import need, Principal
from ..config import settings
from ..db import get_db
from ..jobs import enqueue, event
from ..models import Model, Image, ModelFile, Product, Variant, BomFilament, BomPart, Category, Designer, Event, Job
from ..serialize import model_card, model_detail, image as ser_image, mfile, product as ser_product
from ..services import removal, licensing, availability, importer, shop
from ..services import images as imgsvc

router = APIRouter(prefix="/api")


def load_model(db: Session, model_id: int) -> Model:
    m = db.execute(select(Model).where(Model.id == model_id).options(
        selectinload(Model.images), selectinload(Model.files), selectinload(Model.category),
        selectinload(Model.designer), selectinload(Model.product).selectinload(Product.variants),
        selectinload(Model.product).selectinload(Product.filaments),
        selectinload(Model.product).selectinload(Product.parts))).scalar()
    if not m:
        raise HTTPException(404, "model not found")
    return m


@router.get("/models")
def list_models(q: str = "", category: int | None = None, status: str | None = None, live: bool | None = None,
                attention: str | None = None, sort: str = "recent", page: int = 1, per: int = Query(60, le=500),
                db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    stmt = select(Model).options(selectinload(Model.images), selectinload(Model.files),
                                 selectinload(Model.category), selectinload(Model.designer),
                                 selectinload(Model.product).selectinload(Product.variants),
                                 selectinload(Model.product).selectinload(Product.filaments),
                                 selectinload(Model.product).selectinload(Product.parts))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.outerjoin(Product).where(or_(Model.name.ilike(like), Model.code.ilike(like), Product.sku.ilike(like)))
    if category:
        stmt = stmt.where(Model.category_id == category)
    if status:
        stmt = stmt.where(Model.status == status)
    else:
        stmt = stmt.where(Model.status != "archived")
    if live is not None:
        stmt = stmt.join(Product, Product.model_id == Model.id) if not q else stmt
        stmt = stmt.where(Product.live.is_(live))
    has_photo = select(Image.id).where(Image.model_id == Model.id, Image.role != "archive").exists()
    order = {"recent": Model.created_at.desc(), "name": Model.name.asc(), "code": Model.code.asc(),
             "published": Model.published_at.desc().nullslast()}.get(sort, Model.created_at.desc())
    total = db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar()
    rows = db.execute(stmt.order_by(has_photo.desc(), order, Model.id.desc()).offset((page - 1) * per).limit(per)).scalars().unique().all()
    snap = availability.load_snapshot(db)
    out = []
    for m in rows:
        units = None
        if m.product:
            a = availability.product_availability(db, m.product, snap)
            units = a.get("units") if a.get("has_bom", True) else None
        card = model_card(m, units)
        out.append(card)
    if attention == "supplies":
        out = [c for c in out if c["live"] and (c["units"] or 0) == 0]
    return {"total": total, "page": page, "per": per, "items": out}


@router.get("/models/{model_id}")
def get_model(model_id: int, db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    m = load_model(db, model_id)
    d = model_detail(m)
    if m.product:
        d["availability"] = availability.product_availability(db, m.product)
    d["events"] = [{"at": e.created_at.isoformat(), "level": e.level, "message": e.message, "actor": e.actor}
                   for e in db.execute(select(Event).where(Event.ref == f"model:{m.id}")
                                       .order_by(Event.id.desc()).limit(50)).scalars()]
    refs = [f"model:{m.id}", f"model:{m.id}:dam"] + ([f"product:{m.product.id}"] if m.product else [])
    d["jobs"] = [{"id": j.id, "kind": j.kind, "state": j.state, "error": (j.error or "")[:400], "attempts": j.attempts,
                  "at": (j.finished_at or j.created_at).isoformat()}
                 for j in db.execute(select(Job).where(Job.ref.in_(refs)).order_by(Job.id.desc()).limit(15)).scalars()]
    return d


class ModelIn(BaseModel):
    code: str | None = None
    name: str | None = None
    category: str | None = None
    designer: str | None = None
    source: str | None = None
    source_url: str | None = None
    patreon_url: str | None = None
    summary: str | None = None
    description: str | None = None
    model_dims: str | None = None
    print_dims: str | None = None
    print_settings: dict | None = None
    tags: list[str] | None = None
    license_ok: bool | None = None
    license: str | None = None
    license_note: str | None = None
    credit_override: str | None = None
    designer_id: int | None = None
    status: str | None = None
    favorite: bool | None = None
    notes: str | None = None
    # optional product fields for one-shot creation
    sku: str | None = None
    price: float | None = None
    filament: str | None = None
    grams: float | None = None


@router.post("/models")
def create_model(body: ModelIn, db: Session = Depends(get_db), p: Principal = Depends(need("write"))):
    o = body.model_dump(exclude_none=True)
    if not o.get("name"):
        raise HTTPException(400, "name is required")
    if o.get("code") and db.execute(select(Model).where(Model.code == o["code"])).scalar():
        raise HTTPException(409, "a model with that code exists")
    code = o.get("code") or importer.next_own_code(db)
    m = Model(code=code, name=o["name"], slug=importer.unique_slug(db, f"{code}-{o['name']}"),
              source=o.get("source", "own"), license_ok=o.get("license_ok", o.get("source", "own") == "own"))
    db.add(m)
    db.flush()
    importer.apply_fields(db, m, o)
    prod = importer.ensure_product(db, m, o.get("filament"), o.get("grams"))
    if o.get("sku"):
        prod.sku = o["sku"]
    if o.get("price") is not None:
        prod.price = o["price"]
    m.library_rel = importer.library_folder(m)
    event(db, f"model:{m.id}", "Created", actor=p.actor)
    db.commit()
    return model_detail(load_model(db, m.id))


@router.patch("/models/{model_id}")
def update_model(model_id: int, body: ModelIn, db: Session = Depends(get_db), p: Principal = Depends(need("write"))):
    m = load_model(db, model_id)
    o = body.model_dump(exclude_unset=True)
    importer.apply_fields(db, m, {k: v for k, v in o.items() if v is not None})
    for k in ("favorite",):
        if k in o and o[k] is not None:
            setattr(m, k, o[k])
    for k in ("summary", "description", "notes"):   # allow clearing
        if k in o and o[k] is None:
            setattr(m, k, None)
    db.commit()
    if m.images and any(i.dam_ref for i in m.images):
        enqueue(db, "dam.refresh_metadata", ref=f"model:{m.id}:dammeta", payload={"model_id": m.id}, delay=10)
        db.commit()
    return model_detail(load_model(db, m.id))


@router.delete("/models/{model_id}")
def archive_model(model_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("write"))):
    """Archive (hide, keep everything). Permanent delete is POST /models/{id}/purge."""
    m = load_model(db, model_id)
    try:
        removal.archive(db, m, p.actor)
    except ValueError as e:
        raise HTTPException(409, str(e))
    event(db, f"model:{m.id}", "Archived", actor=p.actor)
    db.commit()
    return {"ok": True, "status": m.status}


@router.post("/models/{model_id}/restore")
def restore_model(model_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("write"))):
    m = load_model(db, model_id)
    removal.restore(db, m)
    event(db, f"model:{m.id}", "Restored from archive", actor=p.actor)
    db.commit()
    return {"ok": True, "status": m.status}


@router.get("/models/{model_id}/delete-check")
def delete_check(model_id: int, db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    m = load_model(db, model_id)
    p = m.product
    return {"blockers": removal.delete_blockers(db, m), "code": m.code,
            "will_remove": {"photos": len(m.images), "files": len(m.files), "woo_draft": bool(p and p.woo_product_id),
                            "dam_photos": sum(1 for i in m.images if i.dam_ref), "dam_collection": bool(m.dam_collection_ref),
                            "watchtower_files": sum(1 for f in m.files if shop.farm_ref(f)),
                            "watchtower_mappings": len((p.wt_mapping_ids or {}) if p else {}),
                            "library_folder": m.library_rel}}


class PurgeIn(BaseModel):
    confirm: str


@router.post("/models/{model_id}/purge")
def purge_model(model_id: int, body: PurgeIn, db: Session = Depends(get_db), p: Principal = Depends(need("delete"))):
    m = load_model(db, model_id)
    if body.confirm.strip().upper() != m.code.upper():
        raise HTTPException(400, f"type the model code ({m.code}) to confirm")
    b = removal.delete_blockers(db, m)
    if b:
        raise HTTPException(409, "Can't delete: " + "; ".join(b))
    m.status = "archived"
    j = enqueue(db, "model.purge", ref=f"model:{m.id}:purge", payload={"model_id": m.id}, actor=p.actor, max_attempts=2)
    db.commit()
    return {"job": j.id}


class BulkModels(BaseModel):
    model_ids: list[int]
    action: str          # archive | restore | delete
    confirm: str | None = None


@router.post("/models/bulk")
def bulk_models(body: BulkModels, db: Session = Depends(get_db), p: Principal = Depends(need("write"))):
    if body.action == "delete" and "delete" not in (p.get("perms") or []):
        raise HTTPException(403, "you don't have permission to permanently delete models")
    if body.action == "delete" and (body.confirm or "").strip().upper() != "DELETE":
        raise HTTPException(400, "type DELETE to confirm")
    done, refused = [], []
    for mid in body.model_ids:
        m = db.get(Model, mid)
        if not m:
            continue
        try:
            if body.action == "archive":
                removal.archive(db, m, p.actor)
                event(db, f"model:{m.id}", "Archived", actor=p.actor)
            elif body.action == "restore":
                removal.restore(db, m)
                event(db, f"model:{m.id}", "Restored from archive", actor=p.actor)
            elif body.action == "delete":
                b = removal.delete_blockers(db, m)
                if b:
                    raise ValueError("; ".join(b))
                m.status = "archived"
                enqueue(db, "model.purge", ref=f"model:{m.id}:purge", payload={"model_id": m.id}, actor=p.actor, max_attempts=2)
            else:
                raise HTTPException(400, "unknown action")
            done.append(m.code)
        except ValueError as e:
            refused.append({"code": m.code, "problem": str(e)})
    db.commit()
    return {"done": done, "refused": refused}


@router.get("/import-blocklist")
def get_blocklist(db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    return removal.blocklist(db)


class UnblockIn(BaseModel):
    code: str | None = None
    makerworld_id: int | None = None


@router.post("/import-blocklist/remove")
def unblock(body: UnblockIn, db: Session = Depends(get_db), _: Principal = Depends(need("write"))):
    removal.unblock(db, body.code, body.makerworld_id)
    db.commit()
    return removal.blocklist(db)


# ---------- images ----------
@router.post("/models/{model_id}/images")
async def upload_images(model_id: int, files: list[UploadFile] = File(...), role: str = Form("gallery"),
                        db: Session = Depends(get_db), p: Principal = Depends(need("write"))):
    m = load_model(db, model_id)
    existing = {i.sha256: i for i in m.images}
    sort = max([i.sort for i in m.images] or [0])
    added = []
    with tempfile.TemporaryDirectory(dir=settings().data_dir) as td:
        for f in files:
            dst = Path(td) / Path(f.filename or "upload.jpg").name
            with open(dst, "wb") as out:
                shutil.copyfileobj(f.file, out)
            sort += 10
            img = importer.add_image(db, m, dst, role, sort, None, existing)
            if img:
                added.append(img.id)
    if m.images and not any(i.role == "hero" for i in m.images):
        first = sorted(m.images, key=lambda i: i.sort)[0]
        first.role = "hero"
    event(db, f"model:{m.id}", f"Added {len(added)} image(s)", actor=p.actor)
    enqueue(db, "dam.sync_model", ref=f"model:{m.id}:dam", payload={"model_id": m.id}, delay=2)
    db.commit()
    return {"added": added}


class ImageIn(BaseModel):
    role: str | None = None
    sort: int | None = None
    caption: str | None = None
    for_web: bool | None = None


@router.patch("/images/{image_id}")
def update_image(image_id: int, body: ImageIn, db: Session = Depends(get_db), _: Principal = Depends(need("write"))):
    img = db.get(Image, image_id)
    if not img:
        raise HTTPException(404)
    o = body.model_dump(exclude_none=True)
    if o.get("role") == "hero":
        for other in img.model.images:
            if other.role == "hero" and other.id != img.id:
                other.role = "gallery"
        img.sort = -1
    for k, v in o.items():
        setattr(img, k, v)
    if o.get("role") == "archive":
        img.for_web = False
    db.commit()
    return ser_image(img)


@router.post("/models/{model_id}/images/order")
def reorder_images(model_id: int, ids: list[int], db: Session = Depends(get_db), _: Principal = Depends(need("write"))):
    m = load_model(db, model_id)
    pos = {iid: n for n, iid in enumerate(ids)}
    for i in m.images:
        if i.id in pos:
            i.sort = pos[i.id] * 10
    db.commit()
    return {"ok": True}


@router.delete("/images/{image_id}")
def delete_image(image_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("write"))):
    img = db.get(Image, image_id)
    if not img:
        raise HTTPException(404)
    mid = img.model_id
    db.delete(img)
    event(db, f"model:{mid}", f"Removed image {img.filename} (DAM copy kept)", actor=p.actor)
    db.commit()
    return {"ok": True}


# ---------- files ----------
@router.post("/models/{model_id}/files")
async def upload_files(model_id: int, files: list[UploadFile] = File(...), db: Session = Depends(get_db),
                       p: Principal = Depends(need("write"))):
    m = load_model(db, model_id)
    existing = {f.sha256: f for f in m.files}
    added = []
    with tempfile.TemporaryDirectory(dir=settings().data_dir) as td:
        for f in files:
            dst = Path(td) / Path(f.filename or "model.stl").name
            with open(dst, "wb") as out:
                shutil.copyfileobj(f.file, out)
            if dst.suffix.lower() in imgsvc.IMAGE_EXT:
                importer.add_image(db, m, dst, "gallery", 1000, None, {i.sha256: i for i in m.images})
                continue
            mf = importer.add_model_file(db, m, dst, existing)
            if mf:
                added.append(mf.id)
    m.library_rel = importer.library_folder(m)
    importer.copy_preview_images(m)
    event(db, f"model:{m.id}", f"Added {len(added)} file(s)", actor=p.actor)
    enqueue(db, "manyfold.rescan", ref="manyfold:rescan", delay=30)
    db.commit()
    return {"added": added}


class FileIn(BaseModel):
    is_print_file: bool | None = None


@router.patch("/files/{file_id}")
def update_file(file_id: int, body: FileIn, db: Session = Depends(get_db), p: Principal = Depends(need("write"))):
    f = db.get(ModelFile, file_id)
    if not f:
        raise HTTPException(404)
    if body.is_print_file is not None:
        if body.is_print_file and f.kind not in ("3mf_sliced", "gcode"):
            raise HTTPException(400, "Watchtower needs a sliced .3mf or .gcode file. Slice it and upload the result.")
        f.is_print_file = body.is_print_file
        if f.model.product and f.model.product.woo_product_id:
            enqueue(db, "watchtower.sync_product", ref=f"product:{f.model.product.id}",
                    payload={"product_id": f.model.product.id}, delay=2)
    db.commit()
    return mfile(f)


@router.delete("/files/{file_id}")
def delete_file(file_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("write"))):
    """Delete a file for good: library, farm, and every printer's slice that used it."""
    from ..services import slicing
    f = db.get(ModelFile, file_id)
    if not f:
        raise HTTPException(404)
    m = f.model
    out = slicing.delete_file(db, f)
    event(db, f"model:{m.id}", f"Deleted file {out['deleted']}" + (f" (used by {', '.join(out['printers'])})" if out["printers"] else ""),
          actor=p.actor)
    if out["was_production"] and m.product and m.product.woo_product_id:
        enqueue(db, "watchtower.sync_product", ref=f"product:{m.product.id}", payload={"product_id": m.product.id}, delay=2)
    enqueue(db, "manyfold.rescan", ref="manyfold:rescan", delay=30)
    db.commit()
    return out


@router.post("/files/{file_id}/replace")
async def replace_file(file_id: int, file: UploadFile = File(...), db: Session = Depends(get_db),
                       p: Principal = Depends(need("write"))):
    """Upload a new version of a file: it takes the old one's place (production, printers) and the old one goes."""
    from ..services import slicing
    old = db.get(ModelFile, file_id)
    if not old:
        raise HTTPException(404)
    m = old.model
    with tempfile.TemporaryDirectory(dir=settings().data_dir) as td:
        dst = Path(td) / Path(file.filename or old.filename).name
        with open(dst, "wb") as out_f:
            shutil.copyfileobj(file.file, out_f)
        sfx = dst.suffix.lower()
        parsed = importer.parse_3mf(str(dst), dst.name) if sfx == ".3mf" else (
            importer.parse_gcode_header(str(dst)) if sfx == ".gcode" else None)
        if slicing._in_use(db, m.id, old.id) and importer.kind_for(dst, parsed) not in slicing.SLICED_KINDS:
            raise HTTPException(400, f"{old.filename} is what a printer prints; replace it with a sliced .3mf or .gcode file")
        had = {f.sha256: f for f in m.files}
        fresh = imgsvc.sha256_file(dst) not in had
        new = importer.add_model_file(db, m, dst, had)
    try:
        out = slicing.replace_file(db, old, new, p.actor)
    except ValueError as e:
        if fresh and new.library_rel and new.library_rel != old.library_rel:
            (Path(settings().library_root) / new.library_rel).unlink(missing_ok=True)
        db.rollback()
        raise HTTPException(400, str(e))
    event(db, f"model:{m.id}", f"Replaced {out['replaced']} with {out['file']}", actor=p.actor)
    if out.get("bom_updated"):
        enqueue(db, "woo.sync_stock", ref="stock:all", delay=5)
    from ..tasks import _after_approve
    _after_approve(db, m, out)
    if m.product and m.product.woo_product_id:
        enqueue(db, "watchtower.sync_product", ref=f"product:{m.product.id}", payload={"product_id": m.product.id}, delay=2)
    enqueue(db, "manyfold.rescan", ref="manyfold:rescan", delay=30)
    db.commit()
    return out


@router.get("/files/{file_id}/download")
def download_file(file_id: int, db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    f = db.get(ModelFile, file_id)
    if not f or not f.library_rel:
        raise HTTPException(404)
    path = Path(settings().library_root) / f.library_rel
    if not path.exists():
        raise HTTPException(404, "file missing from library")
    return FileResponse(path, filename=f.filename)


# ---------- products ----------
class ProductIn(BaseModel):
    sku: str | None = None
    title: str | None = None
    price: float | None = None
    sale_price: float | None = None
    short_description: str | None = None
    description: str | None = None
    weight_g: float | None = None
    dims_cm: str | None = None
    color_mode: str | None = None
    target_buffer: int | None = None
    stock_cap: int | None = None


@router.patch("/products/{product_id}")
def update_product(product_id: int, body: ProductIn, db: Session = Depends(get_db), p: Principal = Depends(need("write"))):
    prod = db.get(Product, product_id)
    if not prod:
        raise HTTPException(404)
    o = body.model_dump(exclude_unset=True)
    if "sku" in o and o["sku"] and o["sku"] != prod.sku:
        if db.execute(select(Product).where(Product.sku == o["sku"])).scalar():
            raise HTTPException(409, "SKU already used")
    for k, v in o.items():
        setattr(prod, k, v)
    db.commit()
    return ser_product(prod)


class VariantIn(BaseModel):
    id: int | None = None
    sku: str | None = None
    color_name: str
    color_hex: str | None = None
    material: str | None = None
    spoolman_filament_id: int | None = None
    price: float | None = None
    active: bool = True
    image_id: int | None = None


@router.put("/products/{product_id}/variants")
def put_variants(product_id: int, items: list[VariantIn], db: Session = Depends(get_db),
                 p: Principal = Depends(need("write"))):
    prod = db.get(Product, product_id)
    if not prod:
        raise HTTPException(404)
    keep = []
    for n, it in enumerate(items):
        v = next((x for x in prod.variants if it.id and x.id == it.id), None) or \
            next((x for x in prod.variants if x.color_name.lower() == it.color_name.lower()), None)
        if not v:
            v = Variant(product_id=prod.id, color_name=it.color_name, sku="")
            prod.variants.append(v)
        from slugify import slugify
        v.sku = it.sku or v.sku or f"{prod.sku}-{slugify(it.color_name).upper()[:12]}"
        for k in ("color_name", "color_hex", "material", "spoolman_filament_id", "price", "active", "image_id"):
            setattr(v, k, getattr(it, k))
        v.sort = n
        keep.append(v)
    for v in list(prod.variants):
        if v not in keep:
            prod.variants.remove(v)
    if items and prod.color_mode != "options":
        prod.color_mode = "options"
        if prod.filaments and not any(f.follows_variant for f in prod.filaments):
            prod.filaments[0].follows_variant = True
    db.commit()
    return ser_product(prod)


class FilamentLine(BaseModel):
    slot: int = 1
    label: str | None = None
    follows_variant: bool = False
    material: str = "PLA"
    color_name: str | None = None
    color_hex: str | None = None
    spoolman_filament_id: int | None = None
    grams: float = 0
    waste_pct: float = 5


class PartLine(BaseModel):
    inventree_part_id: int
    name: str
    qty: float = 1
    variant_id: int | None = None
    note: str | None = None


class BomIn(BaseModel):
    filaments: list[FilamentLine] = []
    parts: list[PartLine] = []


@router.put("/products/{product_id}/bom")
def put_bom(product_id: int, body: BomIn, db: Session = Depends(get_db), p: Principal = Depends(need("write"))):
    prod = db.get(Product, product_id)
    if not prod:
        raise HTTPException(404)
    prod.filaments.clear()
    prod.parts.clear()
    db.flush()
    for f in body.filaments:
        prod.filaments.append(BomFilament(**f.model_dump()))
    for x in body.parts:
        prod.parts.append(BomPart(**x.model_dump()))
    event(db, f"model:{prod.model_id}", "Updated bill of materials", actor=p.actor)
    enqueue(db, "woo.sync_stock", ref="stock:all", delay=2)
    db.commit()
    return {"product": ser_product(prod), "availability": availability.product_availability(db, prod)}


@router.post("/products/{product_id}/push")
def push(product_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("store"))):
    prod = db.get(Product, product_id)
    if not prod:
        raise HTTPException(404)
    probs = licensing.sale_problems(prod.model)
    if probs:
        raise HTTPException(400, "Can't push to the store: " + "; ".join(probs))
    j = enqueue(db, "woo.push_product", ref=f"product:{prod.id}", payload={"product_id": prod.id}, actor=p.actor,
                max_attempts=3)
    db.commit()
    return {"job": j.id}


class LiveIn(BaseModel):
    live: bool


@router.post("/products/{product_id}/live")
def go_live(product_id: int, body: LiveIn, db: Session = Depends(get_db), p: Principal = Depends(need("store"))):
    prod = db.get(Product, product_id)
    if not prod:
        raise HTTPException(404)
    if body.live:
        from ..services.shop import validate_for_push
        problems = validate_for_push(prod, True, switching_live=not prod.live)
        if problems:
            raise HTTPException(400, "Can't go live: " + "; ".join(problems))
    j = enqueue(db, "woo.set_live", ref=f"product:{prod.id}:live", payload={"product_id": prod.id, "live": body.live},
                actor=p.actor, dedupe=False, max_attempts=3)
    db.commit()
    return {"job": j.id}


class BulkLive(BaseModel):
    model_ids: list[int]
    live: bool


@router.post("/products/bulk-live")
def bulk_live(body: BulkLive, db: Session = Depends(get_db), p: Principal = Depends(need("store"))):
    from ..services.shop import validate_for_push
    queued, refused = [], []
    for mid in body.model_ids:
        m = db.get(Model, mid)
        if not m or not m.product:
            continue
        problems = validate_for_push(m.product, body.live, switching_live=not m.product.live) if body.live else []
        if problems:
            refused.append({"model_id": mid, "code": m.code, "problems": problems})
            continue
        enqueue(db, "woo.set_live", ref=f"product:{m.product.id}:live",
                payload={"product_id": m.product.id, "live": body.live}, actor=p.actor, dedupe=False)
        queued.append(mid)
    db.commit()
    return {"queued": queued, "refused": refused}


@router.post("/products/bulk-push")
def bulk_push(body: BulkLive, db: Session = Depends(get_db), p: Principal = Depends(need("store"))):
    n = 0
    for mid in body.model_ids:
        m = db.get(Model, mid)
        if m and m.product and not licensing.sale_problems(m):
            enqueue(db, "woo.push_product", ref=f"product:{m.product.id}", payload={"product_id": m.product.id},
                    actor=p.actor)
            n += 1
    db.commit()
    return {"queued": n}


@router.get("/categories")
def categories(db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    rows = db.execute(select(Category, func.count(Model.id)).outerjoin(Model, Model.category_id == Category.id)
                      .where(or_(Model.status != "archived", Model.id.is_(None)))
                      .group_by(Category.id).order_by(Category.sort, Category.name)).all()
    return [{"id": c.id, "name": c.name, "count": n} for c, n in rows]


@router.get("/designers")
def designers(db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    return [{"id": d.id, "name": d.name, "url": d.url} for d in db.execute(select(Designer).order_by(Designer.name)).scalars()]


@router.post("/models/{model_id}/estimate-filament")
def estimate_filament(model_id: int, overwrite: bool = False, db: Session = Depends(get_db), p: Principal = Depends(need("write"))):
    m = load_model(db, model_id)
    j = enqueue(db, "model.estimate_filament", ref=f"model:{m.id}:estimate", payload={"model_id": m.id, "overwrite": overwrite},
                actor=p.actor)
    db.commit()
    return {"job": j.id}
