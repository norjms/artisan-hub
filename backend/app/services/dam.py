"""Push model photos into ResourceSpace with structured metadata (one collection per model)."""
import logging
import threading
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clients import dam
from ..clients.base import ApiError
from ..config import settings
from ..models import Model, Image, Setting
from ..kv import setting_row, set_setting  # noqa: F401

log = logging.getLogger("hub.dam")

FIELDS = {  # key -> ResourceSpace field title
    "sku": "AP SKU",
    "model_code": "AP Model ID",
    "model": "AP Model",
    "designer": "AP Designer",
    "category": "AP Product Category",
    "role": "AP Image Role",
    "hub_url": "AP Hub URL",
    "brand": "Brand",
}
TITLE_FIELD = 8


def ensure_fields(db: Session) -> dict:
    row = setting_row(db, "rs_fields")
    have = dict(row.value) if row and row.value else {}
    if all(k in have for k in FIELDS):
        return have
    existing = {}
    for f in dam().fields() or []:
        existing[(f.get("title") or "").strip()] = int(f["ref"])
    for key, title in FIELDS.items():
        if key in have:
            continue
        ref = existing.get(title)
        if not ref:
            ref = dam().create_field(title, "1", 0)
            try:
                ref = int(ref)
            except (TypeError, ValueError):
                raise ApiError("resourcespace", None, f"could not create field {title}: {ref}")
        have[key] = ref
    if row:
        row.value = have
    else:
        db.add(Setting(key="rs_fields", value=have))
    db.flush()
    return have


def featured_parent(db: Session, category: str | None) -> int:
    """Featured-collection tree in the DAM: <Team name> > <Category> > <Code - Name>."""
    row = setting_row(db, "rs_featured")
    tree = dict(row.value) if row and row.value else {}
    if "__root__" not in tree:
        from .teams import current_team
        root = dam().create_collection(current_team(db).name)
        dam().save_collection(root, {"public": 1, "type": 3})
        tree["__root__"] = root
    cat = category or "Uncategorized"
    if cat not in tree:
        ref = dam().create_collection(cat[:100])
        dam().save_collection(ref, {"public": 1, "type": 3, "parent": tree["__root__"]})
        tree[cat] = ref
    if row:
        row.value = tree
    else:
        db.add(Setting(key="rs_featured", value=tree))
    db.flush()
    return tree[cat]


def place_collection(db: Session, model: Model):
    parent = featured_parent(db, model.category.name if model.category else None)
    dam().save_collection(model.dam_collection_ref, {"public": 1, "type": 3, "parent": parent,
                                                     "name": f"{model.code} - {model.name}"[:100]})
    extra = dict(model.extra or {})
    extra["dam_featured_parent"] = parent
    model.extra = extra


def ensure_collection(db: Session, model: Model) -> int:
    if model.dam_collection_ref:
        want = featured_parent(db, model.category.name if model.category else None)
        if (model.extra or {}).get("dam_featured_parent") != want:
            place_collection(db, model)
        return model.dam_collection_ref
    model.dam_collection_ref = dam().create_collection(f"{model.code} - {model.name}"[:100])
    db.flush()
    place_collection(db, model)
    return model.dam_collection_ref


# ResourceSpace builds previews during the upload request; on a small DAM host, parallel uploads of
# large photos starve each other until every request times out. One upload at a time.
_UPLOAD_SLOTS: dict = {}
_SLOTS_LOCK = threading.Lock()


def _slot():
    """One upload at a time per team (each team may have its own DAM)."""
    from ..tenant import current_team_id
    tid = current_team_id()
    with _SLOTS_LOCK:
        return _UPLOAD_SLOTS.setdefault(tid, threading.Semaphore(1))
PENDING = "upload pending"


def _brand(db: Session) -> str:
    """The Brand field: the team's name."""
    from .teams import current_team
    return current_team(db).name


def push_image(db: Session, img: Image, fields: dict) -> int:
    model = img.model
    product = model.product
    src = Path(settings().data_dir) / img.store_rel
    meta = {
        str(TITLE_FIELD): f"{model.name} ({img.role})",
        str(fields["sku"]): product.sku if product else "",
        str(fields["model_code"]): model.code,
        str(fields["model"]): model.name,
        str(fields["designer"]): model.designer.name if model.designer else "",
        str(fields["category"]): model.category.name if model.category else "",
        str(fields["role"]): img.role,
        str(fields["hub_url"]): f"{settings().public_url}/models/{model.id}",
        str(fields["brand"]): _brand(db),
    }
    if not img.dam_ref:
        ref = dam().create_resource(1, meta, archive=0)
        # Keep the ref even if the upload below fails, so a retry reuses this resource
        # instead of leaving an empty one behind in the DAM.
        img.dam_ref = ref
        img.dam_error = PENDING
        db.commit()
        dam().upload(ref, str(src), img.filename)
    elif img.dam_error and not dam().has_file(img.dam_ref):
        dam().upload(img.dam_ref, str(src), img.filename)
        for k, v in meta.items():
            dam().update_field(img.dam_ref, int(k), v)
    else:
        for k, v in meta.items():
            dam().update_field(img.dam_ref, int(k), v)
    coll = ensure_collection(db, model)
    dam().add_to_collection(img.dam_ref, coll)
    img.dam_error = None
    return img.dam_ref


def sync_model(db: Session, model_id: int, include_archive: bool = True) -> dict:
    model = db.get(Model, model_id)
    if not model:
        return {"skipped": "missing"}
    todo = [i for i in model.images if not (i.dam_ref and not i.dam_error)
            and not (i.role == "archive" and not include_archive)]
    if not todo:
        return {"uploaded": 0}
    slot = _slot()
    if not slot.acquire(blocking=False):
        from ..jobs import Defer
        raise Defer(20, "another DAM upload is running")
    try:
        return _sync_images(db, model, include_archive)
    finally:
        slot.release()


def _sync_images(db: Session, model: Model, include_archive: bool) -> dict:
    fields = ensure_fields(db)
    done = errors = 0
    for img in model.images:
        if img.role == "archive" and not include_archive:
            continue
        if img.dam_ref and not img.dam_error:
            continue
        try:
            push_image(db, img, fields)
            done += 1
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            img = db.get(Image, img.id)
            img.dam_error = str(e)[:1000]
            db.commit()
            errors += 1
    if errors:
        raise RuntimeError(f"{errors} image(s) failed to upload to the DAM ({done} ok)")
    return {"uploaded": done}


def refresh_metadata(db: Session, model_id: int) -> dict:
    model = db.get(Model, model_id)
    fields = ensure_fields(db)
    n = 0
    for img in model.images:
        if img.dam_ref:
            push_image(db, img, fields)
            n += 1
    return {"updated": n}
