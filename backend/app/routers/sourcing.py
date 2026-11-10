"""MakerWorld browsing/import, license records, designers."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..auth import need, Principal
from ..clients.base import ApiError
from ..clients.makerworld_api import parse_design_id, parse_profile_id
from ..config import settings
from ..db import get_db
from ..jobs import enqueue, event
from ..models import Model, Designer, LicenseEvidence, Setting
from ..services import licensing, makerworld as mwsvc
from ..kv import setting_row, set_setting  # noqa: F401

router = APIRouter(prefix="/api")


# ---------- account ----------
@router.get("/makerworld/status")
def mw_status(db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    row = setting_row(db, "mw_auth")
    v = row.value if row and row.value else {}
    return {"connected": bool(v.get("access_token")), "account": v.get("account"), "name": v.get("name"),
            "connected_at": v.get("connected_at")}


class LoginIn(BaseModel):
    email: str
    password: str | None = None
    code: str | None = None
    tfa_key: str | None = None


def _save_token(db: Session, email: str, token: str, refresh: str | None = None):
    from datetime import datetime, timezone
    name = None
    try:
        prof = mwsvc.MakerWorldClient(token).profile()
        name = prof.get("name") or prof.get("handle")
    except ApiError:
        pass
    row = setting_row(db, "mw_auth") or Setting(key="mw_auth")
    row.value = {"access_token": token, "refresh_token": refresh, "account": email, "name": name,
                 "connected_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    db.merge(row)
    db.commit()
    return {"status": "connected", "name": name}


@router.post("/makerworld/login")
def mw_login(body: LoginIn, db: Session = Depends(get_db), _: Principal = Depends(need("admin"))):
    c = mwsvc.MakerWorldClient()
    try:
        if body.tfa_key and body.code:
            tok = c.tfa(body.tfa_key, body.code)
            if not tok:
                raise HTTPException(400, "2FA code was not accepted")
            return _save_token(db, body.email, tok)
        if not body.password and not body.code:
            c.send_email_code(body.email)
            return {"status": "code_sent", "message": "Bambu Lab emailed you a login code. Enter it to finish."}
        data = c.login(body.email, body.password, body.code)
    except ApiError as e:
        raise HTTPException(400, f"Bambu Lab login failed: {e.message}")
    if data.get("accessToken"):
        return _save_token(db, body.email, data["accessToken"], data.get("refreshToken"))
    lt = data.get("loginType")
    if lt == "verifyCode":
        c.send_email_code(body.email)
        return {"status": "code_sent", "message": "Bambu Lab emailed you a login code. Enter it to finish."}
    if lt == "tfa":
        return {"status": "tfa", "tfa_key": data.get("tfaKey"), "message": "Enter the code from your authenticator app."}
    raise HTTPException(400, f"Unexpected login response: {str(data)[:200]}")


@router.delete("/makerworld/login")
def mw_logout(db: Session = Depends(get_db), _: Principal = Depends(need("admin"))):
    row = setting_row(db, "mw_auth")
    if row:
        db.delete(row)
        db.commit()
    return {"ok": True}


# ---------- browse / preview / import ----------
def _hit(h: dict, imported: dict) -> dict:
    lic = licensing.normalize(h.get("license"))
    c = h.get("designCreator") or {}
    return {"id": h["id"], "title": h.get("title"), "cover": h.get("coverPortrait") or h.get("cover"),
            "creator": c.get("name"), "creator_handle": c.get("handle"), "license": lic,
            "license_label": licensing.info(lic)["label"], "sellable": licensing.info(lic)["sell"],
            "downloads": h.get("downloadCount"), "likes": h.get("likeCount"), "prints": h.get("printCount"),
            "url": f"https://makerworld.com/en/models/{h['id']}" + (f"-{h['slug']}" if h.get("slug") else ""),
            "needs_parts": bool(h.get("bomsNeeded")), "imported_model_id": imported.get(h["id"]),
            "ip_flags": licensing.ip_flags(h.get("title"), " ".join(h.get("tags") or []))}


@router.get("/makerworld/search")
def mw_search(q: str = "", licenses: str = "sellable", sort: str = "relevance", offset: int = 0, limit: int = 40,
              db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    lics = licensing.SELLABLE_MAKERWORLD if licenses == "sellable" else [x for x in licenses.split(",") if x]
    order = {"downloads": "downloadCount", "likes": "likeCount", "newest": "createTime", "prints": "printCount"}.get(sort)
    try:
        r = mwsvc.client(db).search(q, lics, offset, min(limit, 60), order)
    except ApiError as e:
        raise HTTPException(502, f"MakerWorld: {e.message}")
    hits = r.get("hits") or []
    ids = [h["id"] for h in hits]
    imported = dict(db.execute(select(Model.makerworld_id, Model.id).where(Model.makerworld_id.in_(ids))).all()) if ids else {}
    items = [_hit(h, imported) for h in hits]
    if licenses == "sellable":
        items = [i for i in items if i["sellable"]]
    return {"total": r.get("total"), "offset": offset, "items": items}


@router.get("/makerworld/preview")
def mw_preview(url: str, db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    try:
        did = parse_design_id(url)
        d = mwsvc.client(db).design(did)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except ApiError as e:
        raise HTTPException(502, f"MakerWorld: {e.message}")
    lic = licensing.normalize(d.get("license"))
    existing = db.execute(select(Model.id).where(Model.makerworld_id == did)).scalar()
    ext = d.get("designExtension") or {}
    return {"id": did, "title": d.get("title"), "cover": d.get("coverPortrait") or d.get("coverUrl"),
            "creator": (d.get("designCreator") or {}).get("name"), "license": lic, "license_label": licensing.info(lic)["label"],
            "sellable": licensing.info(lic)["sell"], "needs_credit": licensing.info(lic)["credit"],
            "license_text": (d.get("licenseDescriptionInfo") or {}).get("content"),
            "photos": len(ext.get("design_pictures") or []) + len(ext.get("real_pictures") or []),
            "supplies": mwsvc.supplies_from(d), "summary": mwsvc.html_to_text(d.get("summary"))[:600],
            "profiles": [{"id": i["id"], "profileId": i.get("profileId"), "title": i.get("title"),
                          "printer": (((i.get("extention") or {}).get("modelInfo") or {}).get("compatibility") or {}).get("devProductName"),
                          "time_s": i.get("prediction"), "grams": i.get("weight"), "filaments": i.get("instanceFilaments"),
                          "default": i["id"] == d.get("defaultInstanceId")} for i in d.get("instances") or []],
            "selected_profile": parse_profile_id(url), "existing_model_id": existing,
            "ip_flags": licensing.ip_flags(d.get("title"), " ".join(d.get("tags") or []), mwsvc.html_to_text(d.get("summary"))[:3000]),
            "logged_in": bool(mwsvc.client(db).token)}


class ImportIn(BaseModel):
    url: str
    instance_id: int | None = None
    download_files: bool = True
    category: str | None = None
    overrides: dict | None = None          # English name, summary, description, tags (from the phone)


@router.post("/makerworld/import")
def mw_import(body: ImportIn, db: Session = Depends(get_db), p: Principal = Depends(need("write"))):
    try:
        did = parse_design_id(body.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    j = enqueue(db, "makerworld.import", ref=f"makerworld:{did}",
                payload={"design_id": did, "instance_id": body.instance_id, "profile_id": parse_profile_id(body.url),
                         "download_files": body.download_files, "category": body.category,
                         "overrides": body.overrides}, actor=p.actor, max_attempts=4)
    db.commit()
    return {"job": j.id, "design_id": did}


# ---------- import language and categories ----------
class ImportLanguageIn(BaseModel):
    translate: bool = True
    connection: str = "order"       # an AI connection kind (ai_ollama, ai_gemini, ...) or "order"


@router.get("/sources/plan")
def sources_plan(url: str, db: Session = Depends(get_db), p: Principal = Depends(need("read"))):
    """The import plan (text not in English, suggested category, categories), as the phone gets it."""
    from .mobile_control import sources_plan as plan
    return plan(url, db, p)


@router.get("/import-language")
def import_language_get(db: Session = Depends(get_db), p: Principal = Depends(need("read"))):
    from ..services import import_language as il, vision
    return {**il.options(db), "connections": vision.order(db)}


@router.put("/import-language")
def import_language_put(body: ImportLanguageIn, db: Session = Depends(get_db), p: Principal = Depends(need("settings"))):
    from ..services import import_language as il
    out = il.save_options(db, body.translate, body.connection)
    db.commit()
    return out


# ---------- license ----------
class LicenseIn(BaseModel):
    code: str
    note: str | None = None
    credit_override: str | None = None


@router.put("/models/{model_id}/license")
def set_license(model_id: int, body: LicenseIn, db: Session = Depends(get_db), p: Principal = Depends(need("licenses"))):
    m = db.get(Model, model_id)
    if not m:
        raise HTTPException(404)
    if body.code not in licensing.LICENSES:
        raise HTTPException(400, "unknown license code")
    old = m.license_code
    licensing.apply(m, body.code)
    m.license_note = body.note if body.note is not None else m.license_note
    m.credit_override = (body.credit_override or None) if body.credit_override is not None else m.credit_override
    if old != body.code:
        db.add(LicenseEvidence(model_id=m.id, kind="note", license_code=body.code, source_url=m.source_url,
                               note=f"License set to {licensing.info(body.code)['label']} by {p.actor}"
                                    + (f" (was {old})" if old else "") + (f": {body.note}" if body.note else "")))
        event(db, f"model:{m.id}", f"License set to {licensing.info(body.code)['label']}", actor=p.actor)
    db.commit()
    return {"license": licensing.info(m.license_code), "credit_line": licensing.credit_line(m),
            "problems": licensing.sale_problems(m)}


@router.get("/models/{model_id}/evidence")
def evidence(model_id: int, db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    rows = db.execute(select(LicenseEvidence).where(LicenseEvidence.model_id == model_id)
                      .order_by(LicenseEvidence.captured_at.desc())).scalars()
    out = []
    for r in rows:
        files = {k: f"/api/evidence/{r.id}/{k}" for k, v in (("pdf", r.pdf_rel), ("json", r.json_rel), ("html", r.html_rel)) if v}
        out.append({"id": r.id, "kind": r.kind, "license": licensing.info(r.license_code)["label"],
                    "license_code": r.license_code, "license_raw": r.license_raw, "note": r.note,
                    "wayback_url": r.wayback_url, "sha256": r.sha256, "captured_at": r.captured_at.isoformat(),
                    "files": files})
    return out


@router.post("/models/{model_id}/evidence")
async def upload_evidence(model_id: int, file: UploadFile = File(...), note: str = Form(""),
                          code: str = Form(""), db: Session = Depends(get_db), p: Principal = Depends(need("licenses"))):
    m = db.get(Model, model_id)
    if not m:
        raise HTTPException(404)
    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(413, "file too large")
    r = licensing.add_uploaded(db, m, file.filename or "evidence.pdf", data, note or None, code or None)
    event(db, f"model:{m.id}", f"License document added: {file.filename}", actor=p.actor)
    db.commit()
    return {"id": r.id}


@router.post("/models/{model_id}/evidence/snapshot")
def snapshot(model_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("licenses"))):
    m = db.get(Model, model_id)
    if not m or not (m.makerworld_id or m.source_platform in ("printables", "thingiverse", "creality", "thangs",
                                                              "myminifactory", "web")):
        raise HTTPException(400, "only models imported from a model site can be snapshotted automatically; upload a PDF instead")
    j = enqueue(db, "license.snapshot", ref=f"model:{m.id}:license", payload={"model_id": m.id}, actor=p.actor)
    db.commit()
    return {"job": j.id}


@router.get("/evidence/{ev_id}/{kind}")
def evidence_file(ev_id: int, kind: str, db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    r = db.get(LicenseEvidence, ev_id)
    rel = {"pdf": r.pdf_rel, "json": r.json_rel, "html": r.html_rel}.get(kind) if r else None
    if not rel:
        raise HTTPException(404)
    path = Path(settings().data_dir) / rel
    media = {"pdf": None, "json": "application/json", "html": "text/plain"}[kind]
    return FileResponse(path, filename=path.name, media_type=media)


# ---------- designers ----------
@router.get("/designers/full")
def designers_full(db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    from ..serialize import designer
    counts = dict(db.execute(select(Model.designer_id, func.count(Model.id)).group_by(Model.designer_id)).all())
    return [{**designer(d), "models": counts.get(d.id, 0)} for d in db.execute(select(Designer).order_by(Designer.name)).scalars()]


class DesignerIn(BaseModel):
    name: str | None = None
    url: str | None = None
    handle: str | None = None
    platform: str | None = None
    credit_template: str | None = None
    always_credit: bool | None = None
    notes: str | None = None
    license_note: str | None = None


@router.post("/designers")
def create_designer(body: DesignerIn, db: Session = Depends(get_db), _: Principal = Depends(need("write"))):
    from slugify import slugify
    from ..serialize import designer
    if not body.name:
        raise HTTPException(400, "name required")
    if db.execute(select(Designer).where(Designer.name == body.name)).scalar():
        raise HTTPException(409, "designer exists")
    d = Designer(slug=slugify(body.name), **{k: v for k, v in body.model_dump().items() if v is not None})
    db.add(d)
    db.commit()
    return designer(d)


@router.patch("/designers/{designer_id}")
def update_designer(designer_id: int, body: DesignerIn, db: Session = Depends(get_db), _: Principal = Depends(need("write"))):
    from ..serialize import designer
    d = db.get(Designer, designer_id)
    if not d:
        raise HTTPException(404)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(d, k, v)
    db.commit()
    return designer(d)


# ---------- all model sites (MakerWorld, Printables, Thingiverse, Creality Cloud) ----------
from .. import sources as srcs  # noqa: E402
from ..sources.base import save_account  # noqa: E402


@router.get("/sources/status")
def sources_status(db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    return srcs.status(db)


class SourceLoginIn(BaseModel):
    token: str | None = None
    email: str | None = None
    password: str | None = None


@router.post("/sources/{platform}/login")
def source_login(platform: str, body: SourceLoginIn, db: Session = Depends(get_db), _: Principal = Depends(need("admin"))):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        if platform == "thingiverse":
            tok = (body.token or "").strip()
            if not tok:
                raise HTTPException(400, "paste the App Token")
            srcs.thingiverse.Client(tok).get("/things/763622")      # any public thing proves the token works
            save_account(db, "thingiverse_auth", {"token": tok, "account": "App Token", "connected_at": now})
            return {"status": "connected"}
        if platform == "cults3d":
            user, tok = (body.email or "").strip(), (body.token or "").strip()
            if not (user and tok):
                raise HTTPException(400, "your Cults3D user name (nick) and an API key from cults3d.com/en/api/keys")
            r = srcs.cults3d.HTTP.post(srcs.cults3d.GQL, json={"query": "{ __typename }"}, auth=(user, tok))   # 401 when the key is wrong
            if r.get("errors"):
                raise HTTPException(400, "Cults3D: " + "; ".join(e.get("message", "") for e in r["errors"])[:200])
            save_account(db, "cults3d_auth", {"username": user, "token": tok, "account": user, "connected_at": now})
            return {"status": "connected", "name": user}
        if platform == "creality":
            if not (body.email and body.password):
                raise HTTPException(400, "email and password required")
            a = srcs.creality.login(body.email.strip(), body.password)
            save_account(db, "creality_auth", {**a, "connected_at": now})
            return {"status": "connected", "name": a.get("name")}
    except ApiError as e:
        raise HTTPException(400, f"{srcs.PLATFORM_LABEL.get(platform, platform)}: {e.message}")
    raise HTTPException(404, "use the MakerWorld login for MakerWorld; Printables needs no account")


@router.delete("/sources/{platform}/login")
def source_logout(platform: str, db: Session = Depends(get_db), _: Principal = Depends(need("admin"))):
    key = srcs.ACCOUNT_KEY.get(platform)
    row = setting_row(db, key) if key else None
    if row:
        db.delete(row)
        db.commit()
    return {"ok": True}


def _norm_hit(platform: str, h: dict, imported: dict) -> dict:
    code = h.get("license") if platform == "makerworld" else licensing.normalize(h.get("license_raw"), platform)
    li = licensing.info(code)
    return {**h, "platform": platform, "license": code, "license_label": li["label"], "sellable": li["sell"],
            "imported_model_id": imported.get(str(h["id"])), "ip_flags": h.get("ip_flags") or licensing.ip_flags(h.get("title"))}


@router.get("/sources/search")
def sources_search(platform: str = "makerworld", q: str = "", licenses: str = "sellable", sort: str = "downloads",
                   offset: int = 0, limit: int = 40, db: Session = Depends(get_db), p: Principal = Depends(need("read"))):
    if platform == "makerworld":
        r = mw_search(q, licenses, sort, offset, limit, db, p)
        r["next_offset"] = offset + limit
        r["items"] = [_norm_hit("makerworld", {**i, "id": str(i["id"])}, {str(k): v for k, v in
                      ((i["id"], i["imported_model_id"]) for i in r["items"]) if v}) for i in r["items"]]
        return r
    lics = None if licenses == "sellable" else [x for x in licenses.split(",") if x]
    try:
        r = srcs.search(db, platform, q, lics, sort, offset, min(limit, 40))
    except ApiError as e:
        raise HTTPException(502 if e.status != 401 else 400, f"{srcs.PLATFORM_LABEL.get(platform, platform)}: {e.message}")
    except ValueError:
        raise HTTPException(400, "unknown platform")
    ids = [str(h["id"]) for h in r["items"]]
    imported = dict(db.execute(select(Model.source_ext_id, Model.id).where(Model.source_platform == platform,
                                                                            Model.source_ext_id.in_(ids))).all()) if ids else {}
    items = [_norm_hit(platform, h, imported) for h in r["items"]]
    items = [i for i in items if i["sellable"] or licenses != "sellable"]
    return {"total": r.get("total"), "offset": offset, "next_offset": r.get("next_offset", offset + limit), "items": items}


@router.get("/sources/preview")
def sources_preview(url: str, db: Session = Depends(get_db), p: Principal = Depends(need("read"))):
    try:
        platform, _ = srcs.detect(url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if platform == "makerworld":
        d = mw_preview(url, db, p)
        return {**d, "platform": "makerworld", "platform_label": "MakerWorld", "id": str(d["id"]), "files_need_login": True}
    try:
        return srcs.preview(db, url)
    except ApiError as e:
        raise HTTPException(502 if e.status not in (400, 401, 404) else 400,
                            f"{srcs.PLATFORM_LABEL[platform]}: {e.message}")


class SourceImportIn(BaseModel):
    url: str
    profile_id: str | None = None
    download_files: bool = True
    category: str | None = None
    overrides: dict | None = None          # English name, summary, description, tags (from the phone)


@router.post("/sources/import")
def sources_import(body: SourceImportIn, db: Session = Depends(get_db), p: Principal = Depends(need("write"))):
    try:
        platform, ext_id = srcs.detect(body.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if platform == "makerworld":
        return mw_import(ImportIn(url=body.url, instance_id=int(body.profile_id) if (body.profile_id or "").isdigit() else None,
                                  download_files=body.download_files, category=body.category,
                                  overrides=body.overrides), db, p)
    j = enqueue(db, "source.import", ref=f"{platform}:{ext_id}",
                payload={"platform": platform, "id": ext_id, "url": body.url.strip(), "profile_id": body.profile_id,
                         "download_files": body.download_files, "category": body.category, "overrides": body.overrides},
                actor=p.actor, max_attempts=4)
    db.commit()
    return {"job": j.id, "platform": platform, "id": ext_id}


# ---------- commercial licenses you hold (memberships, purchases, permissions) ----------
from datetime import date as _date  # noqa: E402
from ..models import LicenseGrant, Product  # noqa: E402


def grant_dict(g: LicenseGrant) -> dict:
    st = licensing.grant_status(g)
    return {"id": g.id, "designer_id": g.designer_id, "designer": g.designer.name if g.designer else None,
            "name": g.name, "kind": g.kind, "starts_on": g.starts_on.isoformat() if g.starts_on else None,
            "expires_on": g.expires_on.isoformat() if g.expires_on else None, "auto_renews": g.auto_renews,
            "renew_url": g.renew_url, "covers_all": g.covers_all, "model_codes": g.model_codes or [],
            "sales_need_active": g.sales_need_active, "terms": g.terms, "state": st["state"], "days_left": st["days_left"],
            "document": f"/api/grants/{g.id}/document" if g.doc_rel else None}


@router.get("/licenses")
def licenses_overview(db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    """Designers you sell under a commercial license, with their grants and how many models depend on them."""
    rows = db.execute(select(Model.designer_id, func.count(Model.id),
                             func.count(Model.id).filter(Product.live.is_(True)))
                      .outerjoin(Product, Product.model_id == Model.id)
                      .where(Model.license_code == "COMMERCIAL", Model.status != "archived")
                      .group_by(Model.designer_id)).all()
    counts = {r[0]: {"models": r[1], "live": r[2]} for r in rows}
    out = []
    ids = set(counts) | {g.designer_id for g in db.execute(select(LicenseGrant)).scalars()}
    for d in db.execute(select(Designer).where(Designer.id.in_([i for i in ids if i]))).scalars():
        gs = [grant_dict(g) for g in d.grants]
        active = [g for g in gs if g["state"] in ("active", "expiring")]
        out.append({"designer_id": d.id, "designer": d.name, "url": d.url, **counts.get(d.id, {"models": 0, "live": 0}),
                    "grants": gs, "covered": bool(active) or any(not g["sales_need_active"] for g in gs),
                    "status": "none" if not gs else ("expiring" if any(g["state"] == "expiring" for g in active) and
                                                     not any(g["state"] == "active" for g in active)
                                                     else "active" if active else "expired")})
    unassigned = counts.get(None)
    return {"designers": sorted(out, key=lambda x: (-x["models"], x["designer"])),
            "commercial_without_designer": unassigned["models"] if unassigned else 0}


class GrantIn(BaseModel):
    name: str | None = None
    kind: str | None = None
    starts_on: _date | None = None
    expires_on: _date | None = None
    auto_renews: bool | None = None
    renew_url: str | None = None
    covers_all: bool | None = None
    model_codes: list[str] | None = None
    sales_need_active: bool | None = None
    terms: str | None = None


@router.post("/designers/{designer_id}/grants")
def add_grant(designer_id: int, body: GrantIn, db: Session = Depends(get_db), p: Principal = Depends(need("licenses"))):
    d = db.get(Designer, designer_id)
    if not d:
        raise HTTPException(404)
    if not body.name:
        raise HTTPException(400, "name the license (for example: Patreon commercial tier)")
    g = LicenseGrant(designer_id=d.id, **{k: v for k, v in body.model_dump().items() if v is not None})
    db.add(g)
    event(db, "licenses", f"Recorded commercial license '{g.name}' from {d.name}", actor=p.actor)
    db.commit()
    return grant_dict(g)


@router.patch("/grants/{grant_id}")
def edit_grant(grant_id: int, body: GrantIn, db: Session = Depends(get_db), p: Principal = Depends(need("licenses"))):
    g = db.get(LicenseGrant, grant_id)
    if not g:
        raise HTTPException(404)
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(g, k, v)
    if "expires_on" in changes:
        g.last_alert = None
    event(db, "licenses", f"Updated license '{g.name}' ({', '.join(changes)})", actor=p.actor)
    db.commit()
    return grant_dict(g)


@router.delete("/grants/{grant_id}")
def delete_grant(grant_id: int, db: Session = Depends(get_db), p: Principal = Depends(need("licenses"))):
    g = db.get(LicenseGrant, grant_id)
    if g:
        event(db, "licenses", f"Removed license record '{g.name}'", actor=p.actor)
        db.delete(g)
        db.commit()
    return {"ok": True}


@router.post("/grants/{grant_id}/document")
async def grant_doc(grant_id: int, file: UploadFile = File(...), db: Session = Depends(get_db),
                    p: Principal = Depends(need("licenses"))):
    import hashlib
    import re as _re
    g = db.get(LicenseGrant, grant_id)
    if not g:
        raise HTTPException(404)
    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(413, "file too large")
    folder = Path(settings().data_dir) / "evidence" / "grants" / str(g.id)
    folder.mkdir(parents=True, exist_ok=True)
    name = _date.today().strftime("%Y%m%d") + "_" + (_re.sub(r"[^A-Za-z0-9._-]+", "_", file.filename or "terms.pdf")[-120:])
    (folder / name).write_bytes(data)
    g.doc_rel = str((folder / name).relative_to(settings().data_dir))
    g.doc_sha256 = hashlib.sha256(data).hexdigest()
    event(db, "licenses", f"Attached '{file.filename}' to license '{g.name}'", actor=p.actor)
    db.commit()
    return grant_dict(g)


@router.get("/grants/{grant_id}/document")
def grant_doc_get(grant_id: int, db: Session = Depends(get_db), _: Principal = Depends(need("read"))):
    g = db.get(LicenseGrant, grant_id)
    if not g or not g.doc_rel:
        raise HTTPException(404)
    path = Path(settings().data_dir) / g.doc_rel
    return FileResponse(path, filename=path.name)
