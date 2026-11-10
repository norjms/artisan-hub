"""Import model folders (DeltaPrints library layout, the hub inbox, or API uploads) into the hub.

DeltaPrints layout:  Models/<Category>/<MID - Name>/{model.json, README.md, model-guide.md,
                     images/*.jpg, images/patreon/*, images/archive/*, model-files/*, extras/*}
Generic inbox:       <inbox>/<Name>/  any mix of images and model files, optional hub.json
"""
import hashlib
import json
import logging
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

from slugify import slugify
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Model, Image, ModelFile, Category, Designer, Product, BomFilament
from . import images as imgsvc
from .threemf import parse_3mf, parse_gcode_header, guess_slicer

log = logging.getLogger("hub.import")

MODEL_EXT = {".stl": "stl", ".3mf": "3mf", ".gcode": "gcode", ".bgcode": "gcode", ".step": "step", ".stp": "step",
             ".f3d": "f3d", ".obj": "obj", ".mtl": "other", ".scad": "other", ".blend": "other", ".svg": "other",
             ".dxf": "other", ".lys": "other"}
SKIP_DIRS = {"source", "__MACOSX", ".git"}


def folder_fingerprint(folder: Path) -> tuple[str, float]:
    h = hashlib.sha256()
    newest = 0.0
    for p in sorted(folder.rglob("*")):
        if p.is_file() and not any(part in SKIP_DIRS for part in p.relative_to(folder).parts[:-1]):
            st = p.stat()
            newest = max(newest, st.st_mtime, st.st_ctime)
            h.update(f"{p.relative_to(folder)}|{st.st_size}|{int(st.st_mtime)}".encode())
    return h.hexdigest(), newest


def get_or_create_category(db: Session, name: str | None) -> Category | None:
    if not name:
        return None
    name = name.strip()
    c = db.execute(select(Category).where(Category.name == name)).scalar()
    if not c:
        c = Category(name=name, slug=slugify(name))
        db.add(c)
        db.flush()
    return c


def get_or_create_designer(db: Session, name: str | None, url: str | None = None) -> Designer | None:
    if not name:
        return None
    d = db.execute(select(Designer).where(Designer.name == name)).scalar()
    if not d:
        d = Designer(name=name, slug=slugify(name), url=url)
        db.add(d)
        db.flush()
    return d


def unique_slug(db: Session, base: str, model_id: int | None = None) -> str:
    s = slugify(base)[:300] or "model"
    cand, n = s, 2
    while True:
        other = db.execute(select(Model.id).where(Model.slug == cand)).scalar()
        if not other or other == model_id:
            return cand
        cand = f"{s}-{n}"
        n += 1


def next_own_code(db: Session) -> str:
    codes = db.execute(select(Model.code).where(Model.code.like("AP-%"))).scalars().all()
    nums = [int(c[3:]) for c in codes if c[3:].isdigit()]
    return f"AP-{(max(nums) + 1) if nums else 1:04d}"


_G = re.compile(r"([\d.,]+)\s*g", re.I)
MATERIALS = ["PLA+", "PLA", "PETG", "ABS", "ASA", "TPU", "PA", "PC", "PVA", "HIPS", "Silk PLA", "PLA-CF", "PETG-CF"]


def parse_filament_used(text: str | None) -> tuple[str, str | None]:
    """'Anycubic PLA+ "Black"' -> ('PLA+', 'Black'); 'Marble Grey PLA' -> ('PLA', 'Marble Grey')."""
    if not text:
        return "PLA", None
    t = text.replace('"', " ").replace("“", " ").replace("”", " ")
    material = "PLA"
    for m in sorted(MATERIALS, key=len, reverse=True):
        if re.search(rf"(?<![A-Za-z]){re.escape(m)}(?![A-Za-z])", t, re.I):
            material = m.upper() if m.isupper() else m
            break
    color = re.sub(rf"(?i)\b(anycubic|bambu( lab)?|sunlu|elegoo|eryone|polymaker|esun|prusament|jayo|kingroon|"
                   rf"filament|{'|'.join(re.escape(x) for x in MATERIALS)})\b", " ", t)
    color = re.sub(r"[+\-/,]+", " ", color)
    color = re.sub(r"\s+", " ", color).strip() or None
    return material, color


COLOR_HEX = {
    "black": "#111111", "white": "#F5F5F5", "grey": "#8A8D91", "gray": "#8A8D91", "marble grey": "#B9B5AE",
    "marble": "#C9C5BE", "light grey": "#C4C4C4", "dark grey": "#4A4A4A", "silver": "#C0C0C0", "red": "#C8102E",
    "blue": "#1F4FB2", "navy": "#1B2A4A", "green": "#2E8B3D", "sage": "#9CAF88", "olive": "#6B7A3A", "yellow": "#F2C230",
    "orange": "#F07F1A", "pink": "#F4A7B9", "purple": "#6D3FA0", "brown": "#6B4226", "beige": "#D9C7A7", "cream": "#F3E9D2",
    "ivory": "#F4EFE1", "gold": "#C9A227", "copper": "#B8734A", "bronze": "#8C6A3F", "wood": "#9C7248", "teal": "#1F8A8A",
    "terracotta": "#C46A4A", "sand": "#D8C3A0", "mint": "#A8DCC2", "lavender": "#B9A7D9", "transparent": "#E8EEF0",
}


def color_hex_for(name: str | None) -> str | None:
    if not name:
        return None
    n = name.strip().lower()
    if n in COLOR_HEX:
        return COLOR_HEX[n]
    for k in sorted(COLOR_HEX, key=len, reverse=True):
        if re.search(rf"\b{re.escape(k)}\b", n):
            return COLOR_HEX[k]
    return None


def kind_for(path: Path, parsed: dict | None = None) -> str:
    k = MODEL_EXT.get(path.suffix.lower(), "other")
    if k == "3mf":
        return "3mf_sliced" if parsed and parsed.get("sliced") else "3mf_project"
    return k


def library_folder(model: Model) -> str:
    cat = slugify(model.category.name) if model.category else "uncategorized"
    safe = re.sub(r'[<>:"/\\|?*]', "_", f"{model.code} - {model.name}")[:180].strip()
    from ..tenant import current_team_id
    tid = model.team_id or current_team_id()
    prefix = "" if tid in (None, 1) else f"teams/{tid}/"     # the first team keeps the original layout
    return f"{prefix}{cat}/{safe}"


def add_image(db: Session, model: Model, src: Path, role: str, sort: int, source_rel: str | None = None,
              existing: dict | None = None) -> Image | None:
    try:
        sha = imgsvc.sha256_file(src)
    except OSError:
        return None
    if existing is not None and sha in existing:
        img = existing[sha]
        if source_rel and not img.source_rel:
            img.source_rel = source_rel
        return img
    try:
        meta = imgsvc.store(src, sha)
    except Exception as e:  # noqa: BLE001
        log.warning("image %s skipped: %s", src, e)
        return None
    img = Image(sha256=sha, filename=src.name, role=role, sort=sort,
                width=meta.get("width"), height=meta.get("height"), bytes=meta.get("bytes"),
                store_rel=meta.get("store_rel"), source_rel=source_rel, for_web=role in ("hero", "gallery"))
    if img.width and img.height and max(img.width, img.height) < 500 and role != "archive":
        img.role, img.for_web = "detail", False
    model.images.append(img)
    db.flush()
    if existing is not None:
        existing[sha] = img
    return img


def add_model_file(db: Session, model: Model, src: Path, existing: dict, copy_to_library: bool = True) -> ModelFile | None:
    sha = imgsvc.sha256_file(src)
    if sha in existing:
        return existing[sha]
    parsed = None
    if src.suffix.lower() == ".3mf":
        parsed = parse_3mf(str(src), src.name)
    elif src.suffix.lower() in (".gcode",):
        parsed = parse_gcode_header(str(src))
    mf = ModelFile(sha256=sha, filename=src.name, kind=kind_for(src, parsed), bytes=src.stat().st_size)
    if parsed:
        mf.plates = parsed.get("plates")
        mf.print_seconds = parsed.get("print_seconds")
        mf.filament = parsed.get("filament") or []
        mf.settings = parsed.get("settings") or {}
        mf.printer = parsed.get("printer")
        mf.slicer = parsed.get("slicer") or guess_slicer(src.name)
    else:
        mf.slicer = guess_slicer(src.name)
    if copy_to_library:
        lib_rel = f"{library_folder(model)}/{src.name}"
        dst = Path(settings().library_root) / lib_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists() or dst.stat().st_size != src.stat().st_size:
            tmp = dst.with_name(dst.name + ".part")
            shutil.copyfile(src, tmp)
            tmp.replace(dst)
        mf.library_rel = lib_rel
    model.files.append(mf)
    db.flush()
    existing[sha] = mf
    return mf


def ensure_product(db: Session, model: Model, filament_text: str | None = None, grams: float | None = None) -> Product:
    if model.product:
        return model.product
    sku = f"AP-{model.code}" if not model.code.startswith("AP-") else model.code
    p = Product(model_id=model.id, sku=sku, title=model.name, short_description=model.summary,
                description=model.description, live=False)
    db.add(p)
    db.flush()
    material, color = parse_filament_used(filament_text)
    p.filaments.append(BomFilament(slot=1, label="Body", material=material, color_name=color, grams=grams or 0,
                                   color_hex=color_hex_for(color), follows_variant=False))
    model.product = p
    return p


def import_deltaprints_folder(db: Session, folder: Path, force: bool = False) -> Model | None:
    meta_path = folder / "model.json"
    if not meta_path.exists():
        return None
    fp, _ = folder_fingerprint(folder)
    meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
    code = meta.get("model_id") or folder.name.split(" - ")[0].strip()
    model = db.execute(select(Model).where(Model.code == code)).scalar()
    if model and model.source_fingerprint == fp and not force:
        return model
    name = meta.get("name_clean") or meta.get("name") or folder.name.split(" - ", 1)[-1]
    category_name = meta.get("category") or folder.parent.name
    rel = os.path.relpath(folder, settings().source_root)
    if not model:
        model = Model(code=code, name=name, slug=unique_slug(db, f"{code}-{name}"), source="deltaprints",
                      license_ok=True, license_code="COMMERCIAL",
                      license_note="Deltaprints commercial membership", status="draft")
        db.add(model)
        db.flush()
    pi = meta.get("print_info") or {}
    model.name = name
    model.category = get_or_create_category(db, category_name)
    model.designer = get_or_create_designer(db, "Deltaprints", "https://deltaprints.de")
    model.source_url = meta.get("url")
    model.patreon_url = meta.get("patreon_url")
    model.guide_url = meta.get("guide_url")
    model.summary = meta.get("short_description") or model.summary
    model.description = model.description or meta.get("content_text") or meta.get("short_description")
    gpath = folder / "model-guide.md"
    if gpath.exists():
        model.guide_md = gpath.read_text(encoding="utf-8", errors="ignore")[:20000]
    model.model_dims = pi.get("Model Dimensions")
    model.print_dims = pi.get("Printing Dimensions")
    model.print_settings = {k: v for k, v in {
        "layer_height": pi.get("Layer Height"), "walls": pi.get("Walls"), "infill": pi.get("Infill"),
        "supports": pi.get("Need Supports") or pi.get("Supports"), "filament_grams": pi.get("Filament Consumption"),
        "filament": pi.get("Filament Used"), "filament_link": (meta.get("filament_link") or {}).get("url"),
    }.items() if v}
    model.tags = sorted(set((model.tags or []) + [category_name]))
    pub = meta.get("published")
    if pub:
        try:
            model.published_at = datetime.fromisoformat(pub.replace("Z", "+00:00"))
        except ValueError:
            pass
    model.source_folder = rel
    model.extra = {"deltaprints": {k: meta.get(k) for k in ("wp_id", "slug", "similar_models", "stl_files_listed",
                                                             "original_file_names", "images") if meta.get(k)}}

    # images
    existing_imgs = {i.sha256: i for i in model.images}
    sort = len(existing_imgs) * 10
    img_dir = folder / "images"
    groups = [(img_dir, "gallery"), (img_dir / "patreon", "gallery"), (img_dir / "archive", "archive")]
    for d, role in groups:
        if not d.is_dir():
            continue
        for p in sorted(x for x in d.iterdir() if x.is_file() and x.suffix.lower() in imgsvc.IMAGE_EXT):
            sort += 10
            add_image(db, model, p, role, sort, os.path.relpath(p, settings().source_root), existing_imgs)
    # any loose images elsewhere (extras)
    for p in sorted(folder.glob("extras/**/*")):
        if p.is_file() and p.suffix.lower() in imgsvc.IMAGE_EXT:
            sort += 10
            add_image(db, model, p, "archive", sort, os.path.relpath(p, settings().source_root), existing_imgs)
    hero = next((i for i in sorted(model.images, key=lambda i: i.sort) if i.role in ("hero", "gallery")), None)
    if hero and not any(i.role == "hero" for i in model.images):
        hero.role = "hero"
        hero.sort = 0

    # model files
    existing_files = {f.sha256: f for f in model.files}
    for d in (folder / "model-files", folder / "extras"):
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if p.is_file() and p.suffix.lower() in MODEL_EXT:
                add_model_file(db, model, p, existing_files)
    copy_preview_images(model)
    model.library_rel = library_folder(model)
    grams = None
    m = _G.search(pi.get("Filament Consumption") or "")
    if m:
        try:
            grams = float(m.group(1).replace(",", "."))
        except ValueError:
            grams = None
    if not grams:
        sliced = [f for f in model.files if f.filament]
        if sliced:
            grams = sum((x.get("grams") or 0) for x in sliced[0].filament) or None
    ensure_product(db, model, pi.get("Filament Used"), grams)
    model.source_fingerprint = fp
    db.flush()
    return model


def copy_preview_images(model: Model, n: int = 4):
    """Manyfold shows images sitting next to the model files; give it the best few."""
    if not model.files:
        return
    dst_dir = Path(settings().library_root) / library_folder(model)
    dst_dir.mkdir(parents=True, exist_ok=True)
    best = [i for i in sorted(model.images, key=lambda i: i.sort) if i.role in ("hero", "gallery")][:n]
    for idx, img in enumerate(best, start=1):
        src = Path(settings().data_dir) / img.store_rel if img.store_rel else None
        if not src or not src.exists():
            continue
        dst = dst_dir / f"{model.code}_preview_{idx:02d}{src.suffix}"
        if not dst.exists():
            shutil.copyfile(src, dst)


def import_generic_folder(db: Session, folder: Path, overrides: dict | None = None) -> Model:
    """Inbox / API folder: anything with images + model files. hub.json may provide fields."""
    o = dict(overrides or {})
    hj = folder / "hub.json"
    if hj.exists():
        try:
            o = {**json.loads(hj.read_text()), **o}
        except ValueError:
            pass
    code = o.get("code")
    model = db.execute(select(Model).where(Model.code == code)).scalar() if code else None
    if not model:
        code = code or next_own_code(db)
        name = o.get("name") or folder.name
        src = o.get("source", "own")
        model = Model(code=code, name=name, slug=unique_slug(db, f"{code}-{name}"), source=src, status="draft")
        from . import licensing
        lic = o.get("license") or ("OWN" if src == "own" else ("COMMERCIAL" if o.get("license_ok") else "UNKNOWN"))
        licensing.apply(model, licensing.normalize(lic) if lic not in licensing.LICENSES else lic)
        db.add(model)
        db.flush()
    apply_fields(db, model, o)
    existing_imgs = {i.sha256: i for i in model.images}
    existing_files = {f.sha256: f for f in model.files}
    sort = max([i.sort for i in model.images] or [0])
    for p in sorted(folder.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        if p.suffix.lower() in imgsvc.IMAGE_EXT:
            sort += 10
            add_image(db, model, p, "gallery", sort, None, existing_imgs)
        elif p.suffix.lower() in MODEL_EXT:
            add_model_file(db, model, p, existing_files)
    if model.images and not any(i.role == "hero" for i in model.images):
        first = sorted(model.images, key=lambda i: i.sort)[0]
        first.role, first.sort = "hero", 0
    copy_preview_images(model)
    model.library_rel = library_folder(model)
    grams = sum((x.get("grams") or 0) for f in model.files for x in (f.filament or [])) or None
    ensure_product(db, model, o.get("filament"), o.get("grams") or grams)
    db.flush()
    return model


FIELD_MAP = ("name", "summary", "description", "source_url", "patreon_url", "model_dims", "print_dims", "notes",
             "license_note", "status")


def apply_fields(db: Session, model: Model, o: dict):
    for k in FIELD_MAP:
        if o.get(k) is not None:
            setattr(model, k, o[k])
    from . import licensing
    if o.get("license"):
        code = o["license"] if o["license"] in licensing.LICENSES else licensing.normalize(o["license"])
        licensing.apply(model, code)
    elif o.get("license_ok") is not None:
        licensing.apply(model, "COMMERCIAL" if o["license_ok"] else (model.license_code if model.license_code not in (None, "COMMERCIAL") else "UNKNOWN"))
    if o.get("credit_override") is not None:
        model.credit_override = o["credit_override"] or None
    if o.get("category"):
        model.category = get_or_create_category(db, o["category"])
    if o.get("designer_id"):
        from ..models import Designer
        model.designer = db.get(Designer, int(o["designer_id"]))
    elif o.get("designer"):
        model.designer = get_or_create_designer(db, o["designer"], o.get("designer_url"))
    if o.get("tags"):
        model.tags = sorted(set((model.tags or []) + list(o["tags"])))
    if o.get("print_settings"):
        model.print_settings = {**(model.print_settings or {}), **o["print_settings"]}
