"""Job handlers and periodic schedule."""
import logging
import smtplib
import time
from email.message import EmailMessage
from pathlib import Path

from sqlalchemy import select

from .config import settings
from .jobs import handler, every, enqueue, event, PermanentError
from .models import Model, Product, Setting
from .services import dam as damsvc, shop, inventory, importer
from .kv import setting_row, set_setting  # noqa: F401

log = logging.getLogger("hub.tasks")


@handler("dam.sync_model")
def _dam_sync(db, job):
    r = damsvc.sync_model(db, job.payload["model_id"], include_archive=job.payload.get("archive", True))
    db.commit()
    return r


@handler("dam.refresh_metadata")
def _dam_meta(db, job):
    return damsvc.refresh_metadata(db, job.payload["model_id"])


@handler("woo.push_product")
def _woo_push(db, job):
    return shop.push_product(db, job.payload["product_id"], job.created_by)


@handler("woo.set_live")
def _woo_live(db, job):
    return shop.set_live(db, job.payload["product_id"], bool(job.payload["live"]), job.created_by)


@handler("woo.sync_stock")
def _woo_stock(db, job):
    return shop.sync_stock(db, job.payload.get("product_id"))


@handler("woo.poll_orders")
def _woo_orders(db, job):
    return shop.poll_orders(db)


@handler("order.ingest")
def _order_ingest(db, job):
    from .clients import woo
    o = job.payload.get("order") or woo().get_order(job.payload["woo_order_id"])
    row = shop.upsert_order(db, o)
    return {"order": row.id, "status": row.status}


@handler("order.consume")
def _order_consume(db, job):
    return shop.consume_order(db, job.payload["order_id"])


@handler("watchtower.sync_product")
def _wt_sync(db, job):
    return shop.sync_watchtower_product(db, job.payload["product_id"])


@handler("watchtower.mirror_model")
def _wt_mirror(db, job):
    if setting_value(db, "wt_mirror_library", True) is False:
        return {"skipped": "disabled"}
    return shop.mirror_model_to_watchtower(db, job.payload["model_id"])


def setting_value(db, key, default=None):
    row = setting_row(db, key)
    return row.value if row else default


@handler("inventory.refresh_parts")
def _inv_parts(db, job):
    r = inventory.refresh_parts(db)
    enqueue(db, "woo.sync_stock", ref="stock:all", delay=1)
    return r


@handler("inventory.refresh_spools")
def _inv_spools(db, job):
    r = inventory.refresh_spools(db)
    enqueue(db, "woo.sync_stock", ref="stock:all", delay=1)
    return r


@handler("manyfold.link")
def _mf_link(db, job):
    from .clients import manyfold
    idx = manyfold().index()
    linked = 0
    for m in db.execute(select(Model).where(Model.library_rel.is_not(None), Model.manyfold_model_id.is_(None))).scalars():
        row = idx.get(m.library_rel.strip("/"))
        if row:
            m.manyfold_model_id = row["public_id"]
            linked += 1
    return {"linked": linked, "indexed": len(idx)}


@handler("manyfold.rescan")
def _mf_rescan(db, job):
    from .clients import manyfold
    manyfold().request_rescan()
    enqueue(db, "manyfold.link", ref="manyfold:link", delay=240)
    return {"requested": True}


@handler("import.folder")
def _import_folder(db, job):
    folder = Path(job.payload["path"])
    if not folder.exists():
        raise PermanentError(f"folder not found: {folder}")
    if job.payload.get("layout") == "deltaprints":
        m = importer.import_deltaprints_folder(db, folder, force=job.payload.get("force", False))
    else:
        m = importer.import_generic_folder(db, folder, job.payload.get("fields"))
    if not m:
        raise PermanentError("no model.json")
    db.flush()
    event(db, f"model:{m.id}", f"Imported from {folder.name}")
    from .services import slicing as _sl
    _sl.after_import(db, m)
    enqueue(db, "dam.sync_model", ref=f"model:{m.id}:dam", payload={"model_id": m.id}, delay=2)
    enqueue(db, "manyfold.rescan", ref="manyfold:rescan", delay=120)
    if any(f.kind in shop.WT_KINDS and not shop.farm_ref(f) for f in m.files):
        enqueue(db, "watchtower.mirror_model", ref=f"model:{m.id}:wt", payload={"model_id": m.id}, delay=30, priority=9)
    return {"model_id": m.id, "code": m.code, "images": len(m.images), "files": len(m.files)}


@handler("library.scan_deltaprints")
def _scan_dp(db, job):
    from .services.teams import team_dir
    base = team_dir(db, "deltaprints")
    root = base / "Models" if base else None
    if not root or not root.exists():
        return {"skipped": "no library yet"}
    queued = unchanged = settling = 0
    known = {m.code: m.source_fingerprint for m in db.execute(select(Model).where(Model.source == "deltaprints")).scalars()}
    from .services import removal
    blocked = set(removal.blocklist(db)["codes"])
    now = time.time()
    for cat in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        for folder in sorted(p for p in cat.iterdir() if p.is_dir()):
            if not (folder / "model.json").exists():
                continue
            code = folder.name.split(" - ")[0].strip()
            if code in blocked:
                continue
            fp, newest = importer.folder_fingerprint(folder)
            if known.get(code) == fp and not job.payload.get("force"):
                unchanged += 1
                continue
            if now - newest < 180:      # still being copied
                settling += 1
                continue
            enqueue(db, "import.folder", ref=f"import:{code}", payload={"path": str(folder), "layout": "deltaprints",
                                                                        "force": bool(job.payload.get("force"))},
                    max_attempts=3)
            queued += 1
    return {"queued": queued, "unchanged": unchanged, "settling": settling}


@handler("library.scan_inbox")
def _scan_inbox(db, job):
    from .services.teams import team_dir
    inbox = team_dir(db, "inbox")
    if not inbox or not inbox.exists():
        return {"skipped": True}
    done_dir = inbox.parent / "imported"
    n = 0
    now = time.time()
    for folder in sorted(p for p in inbox.iterdir() if p.is_dir() and not p.name.startswith(".")):
        _, newest = importer.folder_fingerprint(folder)
        if now - newest < 120:
            continue
        m = importer.import_generic_folder(db, folder)
        db.flush()
        event(db, f"model:{m.id}", f"Imported from inbox folder {folder.name}")
        enqueue(db, "dam.sync_model", ref=f"model:{m.id}:dam", payload={"model_id": m.id}, delay=2)
        enqueue(db, "manyfold.rescan", ref="manyfold:rescan", delay=60)
        done_dir.mkdir(parents=True, exist_ok=True)
        target = done_dir / f"{time.strftime('%Y%m%d-%H%M%S')}_{folder.name}"
        folder.rename(target)
        n += 1
    return {"imported": n}


@handler("alert.out_of_supplies")
def _alert(db, job):
    p = db.get(Product, job.payload["product_id"])
    key = f"alert_sent:{p.id}"
    row = setting_row(db, key)
    if row and time.time() - float(row.value or 0) < 86400:
        return {"skipped": "already alerted today"}
    from .services.availability import product_availability
    a = product_availability(db, p)
    body = [f"{p.title} ({p.sku}) is live on the store but you are out of supplies for it.",
            "Woo stock was set to 0 so no new orders can come in.", ""]
    lines = a.get("lines") or [ln for v in (a.get("variants") or {}).values() for ln in v["lines"]]
    for ln in lines:
        if ln["units"] == 0:
            body.append(f"- {ln['label']}: need {ln['per_unit']} per unit, available {ln['available']}")
    body += ["", f"{settings().public_url}/planner"]
    send_mail(f"Out of supplies: {p.title}", "\n".join(body), db=db)
    if row:
        row.value = time.time()
    else:
        db.add(Setting(key=key, value=time.time()))
    return {"sent": True}


def send_mail(subject: str, body: str, db=None, to: list[str] | None = None, reply_to: str | None = None):
    s = settings()
    if to is None:
        from .services.teams import alert_recipients
        if db is None:
            from .db import SessionLocal
            db2 = SessionLocal()
            try:
                to = alert_recipients(db2)
            finally:
                db2.close()
        else:
            to = alert_recipients(db)
    if not to:
        log.warning("no alert recipients; not sent: %s", subject)
        return
    if not s.smtp_host:
        log.warning("SMTP not configured; alert not sent: %s", subject)
        return
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, ", ".join(to), subject
    if reply_to:
        msg["Reply-To"] = reply_to
    from email.utils import formatdate, make_msgid
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=(s.smtp_from.rsplit("@", 1)[-1].strip(" >") or "localhost"))
    msg.set_content(body)
    from urllib.parse import urlparse
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30, local_hostname=urlparse(s.public_url).hostname) as smtp:
        smtp.starttls()
        if s.smtp_user:
            smtp.login(s.smtp_user, s.smtp_password)
        smtp.send_message(msg)


@handler("setup.run")
def _setup(db, job):
    from .services import setup
    return setup.run(db)


@handler("spoolman.import_watchtower")
def _sm_import(db, job):
    """Seed Spoolman with spools Watchtower already tracks (idempotent, keyed by Watchtower spool id)."""
    from .clients import farm, spoolman
    sm = spoolman()
    done = dict((setting_row(db, "wt_spool_import") or Setting(value={})).value or {})
    wt = farm().spools()
    spools = wt.get("spools", wt) if isinstance(wt, dict) else wt
    vendors = {v["name"].lower(): v["id"] for v in sm.vendors()}
    fils = {(f.get("vendor", {}).get("name", "").lower(), f.get("material", ""), (f.get("color_hex") or "").lower(),
             f.get("name", "")): f["id"] for f in sm.filaments()}
    created = 0
    for w in spools:
        key = str(w["id"])
        if key in done:
            continue
        vname = w.get("manufacturer") or "Unknown"
        vid = vendors.get(vname.lower())
        if not vid:
            vid = sm.post("/vendor", json={"name": vname})["id"]
            vendors[vname.lower()] = vid
        hexv = (w.get("color_hex") or "").lstrip("#").lower() or None
        fname = " ".join(x for x in [w.get("color"), w.get("variant")] if x) or w.get("material_type") or "Filament"
        fk = (vname.lower(), w.get("material_type") or "PLA", hexv or "", fname)
        fid = fils.get(fk)
        if not fid:
            body = {"name": fname, "vendor_id": vid, "material": w.get("material_type") or "PLA",
                    "density": w.get("density") or 1.24, "diameter": w.get("diameter") or 1.75,
                    "weight": w.get("initial_weight") or 1000}
            if hexv:
                body["color_hex"] = hexv
            if w.get("cost"):
                body["price"] = w["cost"]
            fid = sm.post("/filament", json=body)["id"]
            fils[fk] = fid
        sp = sm.post("/spool", json={"filament_id": fid, "remaining_weight": w.get("remaining_weight") or 0,
                                     "location": w.get("location") or None,
                                     "comment": f"Imported from Watchtower spool #{w['id']} by Artisan Hub"})
        done[key] = sp["id"]
        created += 1
    row = setting_row(db, "wt_spool_import")
    if row:
        row.value = done
    else:
        db.add(Setting(key="wt_spool_import", value=done))
    enqueue(db, "inventory.refresh_spools", ref="refresh:spools", delay=1)
    return {"created": created, "total_mapped": len(done)}


@handler("farm.plate_scan")
def _farm_plate_scan(db, job):
    """Pending farm jobs held only by the plate on an idle printer: reslice for that plate (services/plate_reslice)."""
    from .services import plate_reslice
    return plate_reslice.scan(db)


@handler("farm.plate_reslice")
def _farm_plate_reslice(db, job):
    from .services import plate_reslice
    return plate_reslice.run_reslice(db, job.payload or {})


@handler("farm.sync_locations")
def _farm_sync_locations(db, job):
    """Printer, AMS, slot and external locations on the engine: after a printer comes online, else every 30 minutes."""
    from .services import spool_library
    return spool_library.sync_printer_locations(db, force=bool((job.payload or {}).get("force")))


@handler("spools.migrate_to_farm")
def _spools_migrate(db, job):
    """Copy the hub's Spoolman into the farm's filament library once, then turn the hub's spoolman connection off.
    Payload: dry_run (count only), keep_spoolman (do not turn the connection off)."""
    from .services import spool_library
    p = job.payload or {}
    try:
        r = spool_library.migrate_to_farm(db, dry_run=bool(p.get("dry_run")), disable=not p.get("keep_spoolman"))
    except spool_library.LibraryError as e:
        raise PermanentError(e.detail)
    if not r["dry_run"]:
        event(db, "spools", f"Spool library moved to the farm: {r['spools_copied']} spools copied, "
                            f"{r['spools_already']} already there, {len(r['spools_failed'])} failed"
                            + ("; the hub's Spoolman connection is off" if r["spoolman_disabled"] else ""),
              level="warn" if r["spools_failed"] else "info", actor=job.created_by)
    return r


@handler("maintenance.noop")
def _noop(db, job):
    return {"ok": True}


def _finish_import(db, m, platform, report, payload, job):
    """English text, files and folder, and the category (services/import_language.py)."""
    from .services import import_language
    try:
        fin = import_language.finish(db, m, platform, report.get("source_category"), payload.get("overrides"),
                                     payload.get("category"))
    except Exception as e:  # noqa: BLE001
        event(db, f"model:{m.id}", f"Import language and category step failed: {str(e)[:200]}", level="warn")
        return
    bits = []
    if fin["translated"]:
        bits.append("English " + ", ".join(fin["translated"]))
    if fin["renamed_files"]:
        bits.append(f"{fin['renamed_files']} file name(s) in English")
    if fin["category"]:
        bits.append(f"category {fin['category']}")
    if bits:
        event(db, f"model:{m.id}", "Import: " + "; ".join(bits), actor=job.created_by)
    for n in fin["notes"]:
        event(db, f"model:{m.id}", f"Import: {n}", level="warn")
    report["finish"] = fin


@handler("makerworld.import")
def _mw_import(db, job):
    from .services import makerworld as mw
    from .clients.base import ApiError
    p = job.payload
    from .services import removal
    removal.unblock(db, f"MW-{int(p['design_id'])}", int(p["design_id"]))
    try:
        m, report = mw.import_design(db, int(p["design_id"]), p.get("instance_id"), p.get("profile_id"),
                                     p.get("download_files", True), actor=job.created_by)
    except ApiError as e:
        if e.status in (403, 404):
            raise PermanentError(f"MakerWorld: {e.message}")
        raise
    db.flush()
    _finish_import(db, m, "makerworld", report, p, job)
    msg = f"Imported from MakerWorld ({len(m.images)} photos, {report.get('files', 0)} files"
    if report.get("supplies"):
        msg += f", {len(report['supplies'])} supplies"
    msg += ")"
    event(db, f"model:{m.id}", msg, actor=job.created_by)
    for w in report.get("warnings") or []:
        event(db, f"model:{m.id}", f"MakerWorld import: {w}", level="warn")
    enqueue(db, "dam.sync_model", ref=f"model:{m.id}:dam", payload={"model_id": m.id}, delay=2)
    if m.files:
        enqueue(db, "manyfold.rescan", ref="manyfold:rescan", delay=120)
    from .services import slicing as _sl
    _sl.after_import(db, m)
    if m.files and m.product and not sum(b.grams or 0 for b in m.product.filaments):
        enqueue(db, "model.estimate_filament", ref=f"model:{m.id}:estimate", payload={"model_id": m.id}, delay=5)
    if any(f.kind in shop.WT_KINDS and not shop.farm_ref(f) for f in m.files):
        enqueue(db, "watchtower.mirror_model", ref=f"model:{m.id}:wt", payload={"model_id": m.id}, delay=30, priority=9)
    return {**report, "model_id": m.id, "product_id": m.product.id if m.product else None}


@handler("model.purge")
def _purge(db, job):
    from .services import removal
    try:
        r = removal.purge(db, job.payload["model_id"])
    except ValueError as e:
        raise PermanentError(str(e))
    event(db, "models", f"Deleted {r.get('code')} {r.get('name') or ''}".strip(), actor=job.created_by)
    return r


def after_new_files(db, m):
    """Files were added to a model outside an import (the browser add-on's Send files): library rescan, slicing,
    filament estimate and the Watchtower mirror, as an import does."""
    if not m.files:
        return
    enqueue(db, "manyfold.rescan", ref="manyfold:rescan", delay=120)
    from .services import slicing as _sl
    _sl.after_import(db, m)
    if m.product and not sum(b.grams or 0 for b in m.product.filaments):
        enqueue(db, "model.estimate_filament", ref=f"model:{m.id}:estimate", payload={"model_id": m.id}, delay=5)
    if any(f.kind in shop.WT_KINDS and not shop.farm_ref(f) for f in m.files):
        enqueue(db, "watchtower.mirror_model", ref=f"model:{m.id}:wt", payload={"model_id": m.id}, delay=30, priority=9)


@handler("source.import")
def _src_import(db, job):
    from . import sources
    from .services import removal
    from .clients.base import ApiError
    p = job.payload
    code = f"{sources.base.CODE_PREFIX[p['platform']]}-{p['id']}"[:40]
    removal.unblock(db, code)
    try:
        m, report = sources.run_import(db, p["platform"], p["id"], p.get("profile_id"), p.get("download_files", True),
                                       actor=job.created_by, url=p.get("url"), capture=p.get("capture"))
    except ApiError as e:
        if e.status in (400, 401, 403, 404):
            raise PermanentError(f"{sources.PLATFORM_LABEL[p['platform']]}: {e.message}")
        raise
    db.flush()
    _finish_import(db, m, p["platform"], report, p, job)
    label = sources.PLATFORM_LABEL[p["platform"]]
    msg = f"Imported from {label} ({len(m.images)} photos, {report.get('files', 0)} files"
    if report.get("supplies"):
        msg += f", {len(report['supplies'])} supplies"
    event(db, f"model:{m.id}", msg + ")", actor=job.created_by)
    for w in report.get("warnings") or []:
        event(db, f"model:{m.id}", f"{label} import: {w}", level="warn")
    enqueue(db, "dam.sync_model", ref=f"model:{m.id}:dam", payload={"model_id": m.id}, delay=2)
    if m.files:
        enqueue(db, "manyfold.rescan", ref="manyfold:rescan", delay=120)
    from .services import slicing as _sl
    _sl.after_import(db, m)
    if m.files and m.product and not sum(b.grams or 0 for b in m.product.filaments):
        enqueue(db, "model.estimate_filament", ref=f"model:{m.id}:estimate", payload={"model_id": m.id}, delay=5)
    if any(f.kind in shop.WT_KINDS and not shop.farm_ref(f) for f in m.files):
        enqueue(db, "watchtower.mirror_model", ref=f"model:{m.id}:wt", payload={"model_id": m.id}, delay=30, priority=9)
    return {**{k: v for k, v in report.items() if k != "filament"}, "model_id": m.id,
            "product_id": m.product.id if m.product else None}


@handler("slice.model")
def _slice(db, job):
    from .services import slicing
    from .clients.base import ApiError
    from .jobs import Defer
    from .models import Job
    from sqlalchemy import func as _f
    p = job.payload
    if slicing.cfg(db, "slice_paused"):
        raise Defer(300, "slicing is paused for this team (Settings > Slicing)")
    from .models import PrinterProfile as _PP
    _m, _pp = db.get(Model, p["model_id"]), db.get(_PP, p["profile_id"])
    if _pp is not None and not _pp.active:
        slicing.drop_pending(db, p["model_id"], _pp.id)
        return {"skipped": f"{_pp.name} is turned off for slicing"}
    _why = slicing.skips_on(db, _m, _pp) if _m is not None and _pp is not None else None
    if _why:
        slicing.drop_pending(db, _m.id, _pp.id)
        return {"skipped": _why}
    busy = db.execute(select(_f.count()).select_from(Job).where(Job.kind == "slice.model", Job.state == "running",
                                                                Job.id != job.id)).scalar()
    if busy >= 2:     # the shared slicer serves every team; one team can use at most two of its slots
        raise Defer(15, "team already has two slices running")

    def fail(msg: str, permanent: bool):
        db.rollback()
        if permanent or job.attempts >= job.max_attempts:
            slicing.mark_failed(db, p["model_id"], p["profile_id"], msg)
            db.commit()
        raise PermanentError(msg) if permanent else RuntimeError(msg)

    try:
        r = slicing.slice_model(db, p["model_id"], p["profile_id"], p.get("opts") or {}, actor=job.created_by)
    except ValueError as e:
        fail(str(e), True)
    except ApiError as e:
        fail(f"slicer: {e.message[:300]}", bool(e.status and 400 <= e.status < 500))
    m = db.get(Model, p["model_id"])
    mins = round((r.get("time_s") or 0) / 60)
    event(db, f"model:{m.id}", f"Sliced for {r['printer']} ({r['process']}): {mins // 60}h {mins % 60}m, {r['grams']} g, "
                               + ("approved" if r.get("approved") else "ready for review"), actor=job.created_by)
    if r.get("bom_updated"):
        enqueue(db, "woo.sync_stock", ref="stock:all", delay=5)
    if r.get("approved"):
        _after_approve(db, m, r)
    enqueue(db, "manyfold.rescan", ref="manyfold:rescan", delay=120)
    return r


def _after_approve(db, m, r):
    """Approved files go where the printer's dispatch says (pluggable; Watchtower is one option)."""
    if r.get("production") and m.product:
        from .clients import farm_connected
        if farm_connected():
            enqueue(db, "watchtower.mirror_model", ref=f"model:{m.id}:wt", payload={"model_id": m.id}, delay=10, priority=9)
            enqueue(db, "watchtower.sync_product", ref=f"product:{m.product.id}:wt", payload={"product_id": m.product.id}, delay=60)


@handler("slice.inspect")
def _inspect(db, job):
    from .services import slicing
    from .clients.base import ApiError
    try:
        return slicing.inspect_model(db, job.payload["model_id"])
    except ValueError as e:
        raise PermanentError(str(e))
    except ApiError as e:
        if e.status and 400 <= e.status < 500:
            raise PermanentError(f"slicer: {e.message[:300]}")
        raise


@handler("filaments.match_all")
def _match_all(db, job):
    """Document every model's requested filament and, with a team brand set, put that brand's closest colours
    into the materials list."""
    from .services import filaments, slicing
    brand = slicing.cfg(db, "filament_brand")
    n = changed = empty = 0
    for m in db.execute(select(Model).where(Model.status != "archived")).scalars():
        if brand:
            r = filaments.apply_brand(db, m, brand, refresh=bool((job.payload or {}).get("refresh")))
            changed += r.get("changed") or 0
            empty += 1 if r.get("reason") else 0
        else:
            filaments.document(db, m, refresh=True)
        n += 1
        if n % 50 == 0:
            db.commit()
    return {"models": n, "slots_changed": changed, "no_filament_recorded": empty, "brand": brand}


@handler("slicing.nightly")
def _nightly(db, job):
    from .services import slicing
    return slicing.nightly(db, force=bool((job.payload or {}).get("force")))


@handler("slicing.farm_job")
def _farm_job(db, job):
    """A print-farm job finished: record it against the slice it printed."""
    from datetime import datetime as _dt
    from .services import slicing
    d = job.payload or {}
    outcome = {"queue.job_completed": "ok", "queue.job_failed": "failed", "queue.job_cancelled": "cancelled"}.get(d.get("event"))
    if not outcome:
        return {"skipped": "not a finished job"}
    data = d.get("data") or {}
    jid = data.get("job_id")
    source = data.get("source") or "watchtower"
    info = {}
    if jid:
        try:
            from .clients import farm
            info = farm().job(jid) or {}
        except Exception:  # noqa: BLE001
            info = {}
    elif source == "openprinthq":
        # OpenPrintHQ's print webhook names the printer, not the queue item: take the item that just ended there.
        try:
            from .clients import farm
            f = farm()
            info = (f.last_finished(data.get("printer_name")) if hasattr(f, "last_finished") else None) or {}
            jid = info.get("id")
        except Exception:  # noqa: BLE001
            info = {}
    name = info.get("name") or data.get("job_name")
    r, model_id, file_id = slicing.match_farm_job(db, name, data.get("printer") or data.get("printer_name"))
    if not model_id:
        return {"skipped": f"no hub model matches job {name!r}"}
    dur = None

    def _t(v):
        try:
            return _dt.fromisoformat(str(v).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    st, en = _t(info.get("started_at")), _t(info.get("completed_at") or data.get("timestamp"))
    if st and en and en > st:
        dur = int((en - st).total_seconds())
    ext = str(jid) if jid else f"{name}@{data.get('printer_name') or data.get('printer')}@{data.get('timestamp')}"
    rec = slicing.record_print(db, r, model_id, outcome, source=source, external_id=ext,
                               printer_name=info.get("assigned_printer") or data.get("printer_name") or data.get("printer"),
                               duration_s=dur, note=info.get("error_message") or data.get("message"), actor=source,
                               file_id=file_id, printed_at=en)
    if rec and outcome == "failed":
        event(db, f"model:{model_id}", f"Print failed on {rec.printer_name or 'a printer'}: {(rec.note or '')[:200]}", level="warn")
    return {"recorded": bool(rec), "outcome": outcome, "result_id": r.id if r else None}


FARM_SYNC_QUIET = 600     # periodic farm sync interval while the farm's webhooks are arriving
FARM_HOOK_FRESH = 86400   # a webhook within this long counts as "arriving"


@handler("farm.sync")
def _farm_sync(db, job):
    """Mirror the farm's queue: shortly after each farm webhook, plus a periodic pull as a safety net. The periodic
    pull runs every 2 minutes until the farm's webhooks are seen, then every 10 (webhook-triggered syncs always run)."""
    from .services import farm
    if job.ref == "periodic:farm.sync":
        now = time.time()
        hook = float(setting_value(db, "farm_webhook_at", 0) or 0)
        last = float(setting_value(db, "farm_periodic_sync_at", 0) or 0)
        if now - hook < FARM_HOOK_FRESH and now - last < FARM_SYNC_QUIET - 30:
            return {"skipped": "webhooks arriving; periodic sync every 10 minutes"}
        shop.set_setting(db, "farm_periodic_sync_at", now)
    return farm.sync(db)


@handler("farm.event")
def _farm_event(db, job):
    from .services import farm
    p = job.payload or {}
    out = farm.ingest_event(db, p.get("event") or "", p.get("data") or {})
    enqueue(db, "farm.sync", ref="farm:sync", delay=20)
    return out


@handler("farm.check_opened")
def _farm_check_opened(db, job):
    from .services import farm
    return farm.check_opened(db, job.payload["check_id"])


@handler("filament.snapshot")
def _filament_snapshot(db, job):
    """Which spools a job that just started is using."""
    from .models import FarmJob
    from .services import filament_use
    fj = db.get(FarmJob, job.payload["job_id"])
    if not fj:
        return {"skipped": "job gone"}
    return {"spools": filament_use.snapshot_spools(db, fj)}


@handler("filament.measure")
def _filament_measure(db, job):
    """Filament a finished job really used (printer history, else the estimate scaled by how far it got)."""
    from .models import FarmJob
    from .services import filament_use
    if (job.payload or {}).get("job_id") is None:
        return filament_use.measure_recent(db, int((job.payload or {}).get("days") or 3))
    fj = db.get(FarmJob, job.payload["job_id"])
    if not fj:
        return {"skipped": "job gone"}
    return {"source": filament_use.measure(db, fj), "used_g": fj.used_g}


@handler("farm.set_ready")
def _farm_set_ready(db, job):
    from .services import farm
    return farm.ready_due(db, job.payload["check_id"])


@handler("farm.route_order")
def _farm_route(db, job):
    from .services import farm
    return farm.route_order(db, job.payload["order_id"])


@handler("farm.cancel_order")
def _farm_cancel(db, job):
    from .services import farm
    return farm.cancel_order(db, job.payload["order_id"])


@handler("farm.order_printed")
def _farm_printed(db, job):
    from .services import farm
    return farm.order_printed(db, job.payload["order_id"])


@handler("alerts.send")
def _alerts_send(db, job):
    from .services import alerts
    return alerts.deliver(db, job.payload or {})


@handler("maint.check")
def _maint_check(db, job):
    from .services import maintenance
    return maintenance.check_plans(db)


@handler("farm.timeline_check")
def _timeline_check(db, job):
    from .services import schedule
    return schedule.check_late(db)


@handler("quote.slice")
def _quote_slice(db, job):
    from .services import quotes
    return quotes.run_slice(db, job.payload["quote_id"])


@handler("quote.produce")
def _quote_produce(db, job):
    from .services import quotes
    return quotes.produce(db, job.payload["quote_id"])


@handler("quote.refuse_note")
def _quote_refuse(db, job):
    from .services import quotes
    return quotes.refuse_note(db, job.payload["quote_id"], job.payload["woo_order_id"])


@handler("quotes.expire")
def _quotes_expire(db, job):
    from .services import quotes
    return quotes.expire(db)


@handler("spools.sync")
def _spools_sync(db, job):
    """Two-way spool sync: Watchtower's use into Spoolman, Spoolman's weights into Watchtower, new spools both ways."""
    from .services import spoolsync
    if setting_value(db, "spool_sync", True) is False:
        return {"skipped": "turned off"}
    out = spoolsync.sync(db, dry_run=bool((job.payload or {}).get("dry_run")))
    enqueue(db, "inventory.refresh_spools", ref="refresh:spools", delay=1)
    return {k: (len(v) if isinstance(v, list) else v) for k, v in out.items()}


@handler("slicing.check_stale")
def _check_stale(db, job):
    from .services import slicing
    p = job.payload or {}
    return slicing.check_stale(db, p.get("profile_id"), p.get("model_id"))


@handler("model.estimate_filament")
def _estimate(db, job):
    from .services import geometry
    m = db.get(Model, job.payload["model_id"])
    if not m:
        return {"skipped": "gone"}
    r = geometry.estimate_model(db, m, overwrite=job.payload.get("overwrite", False))
    if r.get("applied"):
        event(db, f"model:{m.id}", f"Filament estimated from geometry: about {r['estimate_g']:.0f} g")
        enqueue(db, "woo.sync_stock", ref="stock:all", delay=5)
    return r


@handler("license.grants_check")
def _grants_check(db, job):
    """Daily: warn 14 days before a commercial license (membership) ends, and when it has ended with live products."""
    from datetime import date
    from .models import LicenseGrant
    from .services import licensing
    today = date.today()
    hide = setting_value(db, "hide_on_license_expiry", False)
    notes, hidden = [], 0
    for g in db.execute(select(LicenseGrant)).scalars():
        st = licensing.grant_status(g, today)
        d = g.designer
        if st["state"] not in ("expiring", "expired") or not g.sales_need_active:
            continue
        key = f"{st['state']}:{g.expires_on}"
        models = [m for m in db.execute(select(Model).where(Model.designer_id == d.id, Model.license_code == "COMMERCIAL",
                                                            Model.status != "archived")).scalars()
                  if g.covers_all or m.code in (g.model_codes or [])]
        live = [m for m in models if m.product and m.product.live]
        if st["state"] == "expired" and live:
            still = [m for m in live if licensing.sale_problems(m)]      # another grant may still cover them
            if still and hide:
                for m in still:
                    enqueue(db, "woo.set_live", ref=f"product:{m.product.id}:live",
                            payload={"product_id": m.product.id, "live": False}, dedupe=False)
                    event(db, f"model:{m.id}", f"Hidden on the store: commercial license '{g.name}' ended", level="warn")
                    hidden += 1
            live = still
        if g.last_alert == key:
            continue
        g.last_alert = key
        if st["state"] == "expiring":
            notes.append(f"- {d.name}: '{g.name}' ends {g.expires_on} ({st['days_left']} days). {len(models)} models, "
                         f"{len(live)} live." + (" Set to auto-renew." if g.auto_renews else "") +
                         (f" Renew: {g.renew_url}" if g.renew_url else ""))
        else:
            notes.append(f"- {d.name}: '{g.name}' ended {g.expires_on}. {len(live)} live product(s) depend on it"
                         + (" and were hidden on the store." if hide and live else
                            ". They stay live until you renew (update the end date) or hide them.") +
                         (f" Renew: {g.renew_url}" if g.renew_url else ""))
        event(db, "licenses", notes[-1][2:], level="warn")
    if notes:
        try:
            send_mail("Artisan Hub: commercial license reminder",
                      "Commercial licenses you sell under:\n\n" + "\n".join(notes) +
                      f"\n\nManage them at {settings().public_url}/licenses")
        except Exception:  # noqa: BLE001
            log.exception("license reminder mail failed")
    return {"notices": len(notes), "hidden": hidden}


@handler("license.snapshot")
def _lic_snapshot(db, job):
    from datetime import datetime, timezone
    from .services import makerworld as mw, licensing
    m = db.get(Model, job.payload["model_id"])
    if m and not m.makerworld_id and m.source_platform in ("printables", "thingiverse", "creality", "thangs",
                                                           "myminifactory", "web"):
        from . import sources
        n = sources.fetch(db, m.source_platform, m.source_ext_id, m.source_url)
        html = n.pop("_html", None)
        if html is None:
            mod = getattr(sources, m.source_platform)
            html = mod.page_html(n) if hasattr(mod, "page_html") else None
        cover = None
        try:
            if n.get("photos"):
                cover = sources.base.Http("cover").bytes(n["photos"][0])
        except Exception:  # noqa: BLE001
            pass
        ev = licensing.capture_generic(db, m, m.source_platform, n.get("raw") or {}, html, cover, n.get("license_raw"),
                                       n.get("license_text"), n.get("api_url"), n["url"],
                                       designer=(n.get("designer") or {}).get("name"), design_updated=n.get("updated"))
        m.license_checked_at = datetime.now(timezone.utc)
        event(db, f"model:{m.id}", f"License snapshot saved ({licensing.info(ev.license_code)['label']})", actor=job.created_by)
        return {"evidence_id": ev.id, "license": ev.license_code, "wayback": ev.wayback_url}
    if not m or not m.makerworld_id:
        raise PermanentError("not a model-site import")
    c = mw.client(db)
    d = c.design(m.makerworld_id)
    try:
        page = c.page_html(m.makerworld_id, d.get("slug") or "")
    except Exception:  # noqa: BLE001
        page = None
    cover = None
    try:
        if d.get("coverUrl"):
            cover = c.fetch_bytes(d["coverUrl"])
    except Exception:  # noqa: BLE001
        pass
    ev = licensing.capture_makerworld(db, m, d, page, cover)
    m.license_checked_at = datetime.now(timezone.utc)
    event(db, f"model:{m.id}", f"License snapshot saved ({licensing.info(ev.license_code)['label']})", actor=job.created_by)
    return {"evidence_id": ev.id, "license": ev.license_code, "wayback": ev.wayback_url}


@handler("license.recheck")
def _lic_recheck(db, job):
    """Weekly: compare each MakerWorld model's current license with what we recorded."""
    from datetime import datetime, timezone
    from .services import makerworld as mw, licensing
    last = setting_value(db, "license_recheck_at")
    if last and not job.created_by and time.time() - float(last) < 6 * 86400:
        return {"skipped": "ran recently"}
    set_setting(db, "license_recheck_at", time.time())
    c = mw.client(db)
    changed, checked = [], 0
    for m in db.execute(select(Model).where(Model.makerworld_id.is_not(None))).scalars():
        try:
            d = c.design(m.makerworld_id)
        except Exception as e:  # noqa: BLE001
            if getattr(e, "status", None) == 404:
                changed.append((m, "removed from MakerWorld"))
            continue
        checked += 1
        now_code = licensing.normalize(d.get("license"))
        m.license_checked_at = datetime.now(timezone.utc)
        if m.license_code in ("COMMERCIAL", "OWN") or now_code == m.license_code:
            continue
        # keep what we recorded (the license at download time governs our copy) but record the change
        try:
            licensing.capture_makerworld(db, m, d, None, None, do_wayback=False)
        except Exception:  # noqa: BLE001
            log.exception("snapshot failed for %s", m.code)
        changed.append((m, f"{m.license_code} -> {now_code}"))
        event(db, f"model:{m.id}", f"License on MakerWorld changed: {m.license_code} -> {now_code}. "
                                   "Your recorded license at download time is kept.", level="warn")
    from . import sources
    for m in db.execute(select(Model).where(Model.source_platform.in_(["printables", "thingiverse", "creality", "thangs",
                                                                       "myminifactory"]),
                                            Model.source_ext_id.is_not(None))).scalars():
        label = sources.PLATFORM_LABEL[m.source_platform]
        try:
            raw = sources.current_license(db, m.source_platform, m.source_ext_id)
        except Exception:  # noqa: BLE001
            continue
        if raw is None:
            changed.append((m, f"removed from {label}"))
            continue
        checked += 1
        now_code = licensing.normalize(raw, m.source_platform)
        m.license_checked_at = datetime.now(timezone.utc)
        if m.license_code in ("COMMERCIAL", "OWN") or now_code == m.license_code:
            continue
        changed.append((m, f"{m.license_code} -> {now_code}"))
        event(db, f"model:{m.id}", f"License on {label} changed: {m.license_code} -> {now_code}. "
                                   "Your recorded license at download time is kept.", level="warn")
    if changed:
        lines = []
        for m, what in changed:
            live = bool(m.product and m.product.live)
            lines.append(f"- {m.code} {m.name}: {what}{' (LIVE in store)' if live else ''}\n  {settings().public_url}/models/{m.id}")
        try:
            send_mail(f"Artisan Hub: {len(changed)} model license change(s)",
                      "These designs changed license since you downloaded them. Your saved license record "
                      "shows the terms at download time; review whether you want to keep selling.\n\n" + "\n".join(lines))
        except Exception:  # noqa: BLE001
            log.exception("license alert mail failed")
    return {"checked": checked, "changed": len(changed)}


def _has_maint(team):
    from .db import SessionLocal
    from .tenant import bind_session
    from .models import MaintPlan
    s = SessionLocal()
    try:
        bind_session(s, team.id)
        return s.execute(select(MaintPlan.id).limit(1)).first() is not None
    finally:
        s.close()


def _farm(team):
    """The team runs a print farm the hub has a driver for (Watchtower or OpenPrintHQ)."""
    from .clients import farm_connected
    return farm_connected(team.id)


def _ophq_farm(team):
    from .services.spool_library import farm_library
    return farm_library(team.id)


def _spool_library(team):
    """The team has a filament library: the farm's (OpenPrintHQ) or its own Spoolman."""
    from .services.connections import is_connected
    from .services.spool_library import farm_library
    return is_connected("spoolman", team.id) or farm_library(team.id)


def _spools_both(team):
    """Spoolman and a farm that keeps its own spool list (Watchtower). OpenPrintHQ as the farm has none to sync:
    the hub reads Spoolman directly, and OpenPrintHQ's tenants already use Spoolman themselves."""
    from .services.connections import is_connected
    from .clients import farm_kind
    return is_connected("spoolman", team.id) and is_connected("watchtower", team.id) and farm_kind(team.id) == "watchtower"


def _has_dir(key):
    def check(team):
        return bool((team.source_dirs or {}).get(key))
    return check


def _has_models(team):
    from .db import SessionLocal
    from .tenant import bind_session
    s = SessionLocal()
    try:
        bind_session(s, team.id)
        return s.execute(select(Model.id).limit(1)).first() is not None
    finally:
        s.close()


def _has_slices(team) -> bool:
    from .db import SessionLocal
    from .models import SliceResult
    from . import tenant
    s = SessionLocal()
    try:
        tenant.bind_session(s, team.id)
        return s.execute(select(SliceResult.id).limit(1)).first() is not None
    finally:
        s.close()


every("inventory.refresh_parts", 300, needs="inventree")
every("inventory.refresh_spools", 300, needs=_spool_library)
every("woo.sync_stock", 300, needs="woo")
every("woo.poll_orders", 600, needs="woo")
every("library.scan_deltaprints", 900, needs=_has_dir("deltaprints"))
every("library.scan_inbox", 120, needs=_has_dir("inbox"))
every("manyfold.link", 900, needs="manyfold")
every("license.recheck", 86400, needs=_has_models)
every("license.grants_check", 6 * 3600, needs=_has_models)
every("slicing.check_stale", 6 * 3600, needs=_has_slices)
every("slicing.nightly", 1800, needs=_has_slices)
every("farm.sync", 120, needs=_farm)
every("spools.sync", 300, needs=_spools_both)
every("farm.sync_locations", 300, needs=_ophq_farm)
every("farm.plate_scan", 60, needs=_ophq_farm)
every("maint.check", 3600, needs=_has_maint)
every("farm.timeline_check", 600, needs=_farm)
every("filament.measure", 3600, needs=_farm)
every("quotes.expire", 3600, needs=None)
