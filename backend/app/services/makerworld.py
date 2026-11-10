"""Import a MakerWorld model: photos, description, print profile settings, filament, supplies, files, license record."""
import logging
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clients.base import ApiError
from ..clients.makerworld_api import MakerWorldClient
from ..models import Model, Designer, Setting, BomFilament, BomPart, PartCache
from . import importer, licensing
from ..kv import setting_row, set_setting  # noqa: F401

log = logging.getLogger("hub.makerworld")


def client(db: Session) -> MakerWorldClient:
    row = setting_row(db, "mw_auth")
    tok = (row.value or {}).get("access_token") if row and row.value else None
    return MakerWorldClient(tok)


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert(0, "- ")
    parts = []
    for el in soup.find_all(["p", "li", "h1", "h2", "h3", "h4", "div"], recursive=True):
        if el.find(["p", "li", "div"]):
            continue
        t = el.get_text("", strip=False).strip()
        if t:
            parts.append(t)
    text = "\n\n".join(parts) if parts else soup.get_text("\n").strip()
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_profile_title(title: str) -> dict:
    out = {}
    t = (title or "").lower()
    m = re.search(r"([\d.]+)\s*mm\s*layer", t)
    if m:
        out["layer_height"] = m.group(1)
    m = re.search(r"(\d+)\s*walls?", t)
    if m:
        out["walls"] = m.group(1)
    m = re.search(r"(\d+)\s*%\s*infill", t)
    if m:
        out["infill"] = f"{m.group(1)}%"
    if "support" in t:
        out["supports"] = "yes" if "no support" not in t else "no"
    return out


def designer_for(db: Session, creator: dict) -> Designer | None:
    if not creator:
        return None
    handle = creator.get("handle") or str(creator.get("uid") or "")
    name = creator.get("name") or handle
    d = db.execute(select(Designer).where(Designer.platform == "makerworld", Designer.handle == handle)).scalar()
    if not d:
        d = db.execute(select(Designer).where(Designer.name == name)).scalar()
    if not d:
        slug = slugify(f"{name}-mw")
        d = Designer(name=name if not db.execute(select(Designer).where(Designer.name == name)).scalar() else f"{name} (MakerWorld)",
                     slug=slug, platform="makerworld", handle=handle, always_credit=False,
                     url=f"https://makerworld.com/en/@{handle}" if handle else None)
        db.add(d)
        db.flush()
    else:
        d.platform = d.platform or "makerworld"
        d.handle = d.handle or handle
        d.url = d.url or (f"https://makerworld.com/en/@{handle}" if handle else None)
    return d


PACK = re.compile(r"\((\d+)\s*(?:pcs|pieces|pack)\)", re.I)


def ensure_part(db: Session, title: str, link: str | None, sku: str | None, supplier: str | None,
                origin: str = "MakerWorld") -> int:
    """Find an InvenTree part by name/SKU or create one under Hardware/From <site>."""
    from ..clients import inventree
    from .inventory import refresh_parts
    t = (title or "").strip()[:180]
    for pc in db.execute(select(PartCache)).scalars():
        if pc.name.lower() == t.lower() or (sku and any((s.get("sku") or "").lower() == sku.lower() for s in pc.suppliers or [])):
            return pc.inventree_part_id
    inv = inventree()
    cats = {c["pathstring"]: c["pk"] for c in inv.categories()}
    path = f"Hardware/From {origin}"
    if path not in cats:
        parent = cats.get("Hardware") or inv.create_category("Hardware")["pk"]
        cats[path] = inv.create_category(f"From {origin}", parent, f"Parts listed by {origin} models")["pk"]
    part = inv.create_part({"name": t, "category": cats[path], "description": t,
                            "component": True, "purchaseable": True, "active": True})
    if supplier or link:
        comps = {c["name"].lower(): c["pk"] for c in inv.companies(is_supplier="true")}
        sname = supplier or "Unknown supplier"
        sid = comps.get(sname.lower()) or inv.create_company(sname)["pk"]
        pack = PACK.search(t)
        inv.create_supplier_part({"part": part["pk"], "supplier": sid, "SKU": (sku or t)[:90], "link": link or "",
                                  "pack_quantity": pack.group(1) if pack else "1"})
    refresh_parts(db)
    return part["pk"]


def parse_qty_line(line: str) -> tuple[int, str]:
    """'4 x M3x8 screws' -> (4, 'M3x8 screws'); 'Magnet 6x3mm x 8' -> (8, 'Magnet 6x3mm'); 'M3x10 bolt' -> (1, ...)."""
    line = line.strip()
    m = re.match(r"^(\d+)\s*(?:pcs?\.?|pieces?|x|\u00d7)\s+(.+)$", line, re.I) or re.match(r"^(\d+)\s+(\D.*)$", line)
    if m:
        return int(m.group(1)), m.group(2).strip()
    m = re.match(r"^(.+?)\s+(?:x|\u00d7)\s*(\d+)\s*(?:pcs?|pieces?)?$", line, re.I)
    if m and not re.search(r"\bM\d+$", m.group(1).strip(), re.I):   # "Bolt M3 x 10" is a size, not a count
        return int(m.group(2)), m.group(1).strip()
    m = re.match(r"^(.+?)\s*[:=]\s*(\d+)\s*(?:pcs?|pieces?)?$", line, re.I) or \
        re.match(r"^(.+?)\s*\(\s*x?\s*(\d+)\s*\)$", line, re.I)
    if m:
        return int(m.group(2)), m.group(1).strip()
    return 1, line


def supplies_from(design: dict) -> list[dict]:
    ext = design.get("designExtension") or {}
    out = []
    for b in ext.get("boms") or []:
        title = b.get("displayTitle") or b.get("title") or b.get("parentTitle")
        if title:
            # Bambu's "quantity" is packs to buy; "pieces" (often 0) is the count the model uses
            pieces = int(b.get("pieces") or 0)
            out.append({"title": title, "qty": pieces or 1, "qty_known": pieces > 0, "packs": b.get("quantity") or 1,
                        "link": b.get("url"), "sku": b.get("sku"), "supplier": "Bambu Lab Store"})
    other = ext.get("boms_of_other_part_list") or []
    for o in other:
        if isinstance(o, str):
            out.append({"title": o, "qty": 1, "link": None, "sku": None, "supplier": None})
        elif isinstance(o, dict):
            title = o.get("name") or o.get("title") or o.get("partName")
            if title:
                out.append({"title": title, "qty": o.get("quantity") or o.get("count") or 1,
                            "link": o.get("link") or o.get("url"), "sku": o.get("sku"), "supplier": o.get("store") or None})
    text = ext.get("boms_of_other_parts")
    if isinstance(text, str) and text.strip() and not other:
        for line in re.split(r"[\n;]+", BeautifulSoup(text, "html.parser").get_text("\n")):
            line = line.strip(" -*\t")
            if line:
                q, title = parse_qty_line(line)
                out.append({"title": title, "qty": q, "link": None, "sku": None, "supplier": None})
    return out


def pick_instance(design: dict, instance_id: int | None = None, profile_id: int | None = None) -> dict | None:
    inst = design.get("instances") or []
    for i in inst:
        if instance_id and i.get("id") == instance_id:
            return i
        if profile_id and i.get("profileId") == profile_id:
            return i
    for i in inst:
        if i.get("id") == design.get("defaultInstanceId"):
            return i
    return inst[0] if inst else None


def import_design(db: Session, design_id: int, instance_id: int | None = None, profile_id: int | None = None,
                  download_files: bool = True, actor: str | None = None) -> tuple[Model, dict]:
    mw = client(db)
    d = mw.design(design_id)
    report = {"warnings": []}
    code = f"MW-{design_id}"
    model = db.execute(select(Model).where(Model.makerworld_id == design_id)).scalar() or \
        db.execute(select(Model).where(Model.code == code)).scalar()
    created = model is None
    if created:
        model = Model(code=code, name=d["title"][:300], slug=importer.unique_slug(db, f"{code}-{d['title']}"),
                      source="makerworld", status="draft")
        db.add(model)
        db.flush()
    model.makerworld_id = design_id
    model.source_platform, model.source_ext_id = "makerworld", str(design_id)
    model.name = d["title"][:300]
    model.source = "makerworld"
    model.source_url = f"https://makerworld.com/en/models/{design_id}" + (f"-{d.get('slug')}" if d.get("slug") else "")
    desc = html_to_text(d.get("summary"))
    model.description = desc or model.description
    model.summary = (desc.split("\n\n")[0][:500] if desc else model.summary)
    model.designer = designer_for(db, d.get("designCreator") or {})
    cats = d.get("categories") or []
    # The category is chosen at import, or suggested from MakerWorld's (import_language.finish): never made here.
    report["source_category"] = (cats[-1].get("name") or cats[0].get("name")) if cats else None
    model.tags = sorted(set((model.tags or []) + [t for t in (d.get("tags") or []) if t][:20]))
    new_code = licensing.normalize(d.get("license"))
    if model.license_code and model.license_code != new_code and model.license_code not in ("COMMERCIAL",):
        report["warnings"].append(f"license changed on MakerWorld from {model.license_code} to {new_code}")
    if model.license_code != "COMMERCIAL":
        licensing.apply(model, new_code)
    model.license_checked_at = datetime.now(timezone.utc)

    inst = pick_instance(d, instance_id, profile_id)
    if inst:
        model.makerworld_instance_id = inst["id"]
        comp = ((inst.get("extention") or {}).get("modelInfo") or {}).get("compatibility") or {}
        ps = parse_profile_title(inst.get("title"))
        ps.update({"profile": inst.get("title"), "printer": comp.get("devProductName"),
                   "nozzle": comp.get("nozzleDiameter"), "print_time_s": inst.get("prediction"),
                   "filament_grams": inst.get("weight"), "needs_ams": inst.get("needAms"),
                   "filament": ", ".join(f"{f.get('type')} {f.get('color')}" for f in inst.get("instanceFilaments") or [])})
        model.print_settings = {k: v for k, v in ps.items() if v not in (None, "", [])}
    model.extra = {**(model.extra or {}), "makerworld": {
        "id": design_id, "creator": (d.get("designCreator") or {}).get("handle"),
        "downloads": d.get("downloadCount"), "likes": d.get("likeCount"), "prints": d.get("printCount"),
        "instances": [{"id": i["id"], "profileId": i.get("profileId"), "title": i.get("title"),
                       "printer": (((i.get("extention") or {}).get("modelInfo") or {}).get("compatibility") or {}).get("devProductName"),
                       "time_s": i.get("prediction"), "grams": i.get("weight"),
                       "filaments": i.get("instanceFilaments")} for i in d.get("instances") or []],
        "updated": d.get("updateTime")}}
    model.library_rel = importer.library_folder(model)
    db.flush()

    # photos: cover first, then real-life photos, design photos, then the chosen profile's photos
    ext = d.get("designExtension") or {}
    urls = [d.get("coverUrl"), d.get("coverPortrait")]
    urls += [p.get("url") for p in ext.get("real_pictures") or []]
    urls += [p.get("url") for p in ext.get("design_pictures") or []]
    if inst:
        urls += [p.get("url") for p in inst.get("pictures") or []]
    seen, cover_bytes = set(), None
    existing = {i.sha256: i for i in model.images}
    sort = max([i.sort for i in model.images] or [0])
    with tempfile.TemporaryDirectory() as td:
        for u in [u for u in urls if u]:
            if u in seen:
                continue
            seen.add(u)
            try:
                b = mw.fetch_bytes(u)
            except ApiError as e:
                report["warnings"].append(f"photo skipped: {e.message[:80]}")
                continue
            cover_bytes = cover_bytes or b
            name = Path(u.split("?")[0]).name or f"photo-{len(seen)}.jpg"
            p = Path(td) / name
            p.write_bytes(b)
            sort += 10
            importer.add_image(db, model, p, "gallery", sort, None, existing)
        if model.images and not any(i.role == "hero" for i in model.images):
            first = sorted(model.images, key=lambda i: i.sort)[0]
            first.role, first.sort = "hero", 0

        # files
        report["files"] = 0
        if download_files and inst:
            try:
                info = mw.instance_download(inst["id"])
                url = info.get("url") if isinstance(info, dict) else None
                if url:
                    data = mw.fetch_bytes(url)
                    fname = info.get("name") or f"{model.code}.3mf"
                    if not fname.lower().endswith((".3mf", ".zip", ".stl")):
                        fname += ".3mf"
                    fp = Path(td) / fname
                    fp.write_bytes(data)
                    importer.add_model_file(db, model, fp, {f.sha256: f for f in model.files})
                    report["files"] += 1
            except ApiError as e:
                report["warnings"].append(f"files not downloaded: {e.message[:160]}")
        importer.copy_preview_images(model)

    # product + materials
    prod = importer.ensure_product(db, model, None, inst.get("weight") if inst else None)
    fils = (inst or {}).get("instanceFilaments") or []
    if fils and (created or not any(f.grams for f in prod.filaments) or len(prod.filaments) <= 1):
        prod.filaments.clear()
        db.flush()
        for n, f in enumerate(fils, start=1):
            try:
                grams = float(f.get("usedG") or 0)
            except ValueError:
                grams = 0
            prod.filaments.append(BomFilament(slot=n, label="Body" if n == 1 else f"Color {n}", material=f.get("type") or "PLA",
                                              color_hex=(f.get("color") or "")[:7] or None,
                                              color_name=None, grams=grams, waste_pct=5,
                                              follows_variant=(n == 1 and prod.color_mode == "options")))
    report["filament"] = [{"type": f.get("type"), "color": f.get("color"), "grams": f.get("usedG")} for f in fils]
    sup = supplies_from(d)
    report["supplies"] = []
    have = {b.name.lower() for b in prod.parts}
    for s in sup:
        if s["title"].lower() in have:
            continue
        try:
            pid = ensure_part(db, s["title"], s.get("link"), s.get("sku"), s.get("supplier"))
            prod.parts.append(BomPart(inventree_part_id=pid, name=s["title"][:300], qty=float(s.get("qty") or 1),
                                      note=("From MakerWorld bill of materials" if s.get("qty_known", True) else
                                            f"MakerWorld lists {s.get('packs', 1)} pack(s) but not the count per print: set the quantity")))
            report["supplies"].append(s["title"])
            if not s.get("qty_known", True):
                report["warnings"].append(f"set how many '{s['title'][:40]}' each print uses (Materials tab)")
        except Exception as e:  # noqa: BLE001
            report["warnings"].append(f"supply '{s['title'][:40]}' not added: {str(e)[:100]}")
    db.flush()

    # license record (raw data + page snapshot + PDF + Internet Archive copy)
    try:
        page = mw.page_html(design_id, d.get("slug") or "")
    except ApiError:
        page = None
    licensing.capture_makerworld(db, model, d, page, cover_bytes)
    report.update({"model_id": model.id, "code": model.code, "license": model.license_code, "created": created,
                   "images": len(model.images)})
    return model, report
