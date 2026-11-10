"""Instant quotes. /api/store/quotes is what the store calls (the WordPress connector, with the store's hub API key)
on behalf of its customers; /api/quotes is the hub's own review and settings."""
import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import need, Principal
from ..db import get_db
from ..models import Quote
from ..services import quotes

router = APIRouter()


def _by_token(db, token) -> Quote:
    q = db.execute(select(Quote).where(Quote.token == token)).scalar()
    if not q:
        raise HTTPException(404, "quote not found")
    return q


def _preview(q: Quote, name: str):
    if not re.fullmatch(r"preview_plate_\d+\.png", name) or name not in (q.previews or []):
        raise HTTPException(404)
    p = quotes.qdir(q.token) / "preview" / name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/png")


# ---------- for the store ----------

@router.get("/api/store/quotes/config")
def store_config(db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    c = quotes.config(db)
    return {"enabled": bool(c["enabled"]), "max_mb": c["max_mb"], "extensions": list(quotes.EXTS),
            "currency": c.get("currency", "USD"), "minimum": c["minimum"], "setup_fee": c["setup_fee"],
            "woo_product_id": c.get("woo_product_id")}


@router.post("/api/store/quotes")
async def store_new(request: Request, file: UploadFile = File(...), customer_ip: str | None = Form(None),
                    db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    data = await file.read()
    try:
        q = quotes.create(db, file.filename, data, customer_ip)
    except PermissionError as e:
        raise HTTPException(429 if "too many" in str(e) else 403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return quotes.public(db, q)


@router.get("/api/store/quotes/{token}")
def store_get(token: str, db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    return quotes.public(db, _by_token(db, token))


class ChooseIn(BaseModel):
    material: str
    colour: str | None = None
    quantity: int = 1


@router.post("/api/store/quotes/{token}/choose")
def store_choose(token: str, body: ChooseIn, db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    q = _by_token(db, token)
    if q.status != "ready":
        raise HTTPException(409, "this quote can't be changed now" if q.status != "slicing" else "still working out the price")
    pub = quotes.public(db, q)
    if pub["status"] == "expired":
        raise HTTPException(410, "this quote has expired; upload the model again")
    try:
        quotes.price(db, q, body.material, body.colour, body.quantity)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return quotes.public(db, q)


@router.get("/api/store/quotes/{token}/preview/{name}")
def store_preview(token: str, name: str, db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    return _preview(_by_token(db, token), name)


# ---------- review and settings ----------

@router.get("/api/quotes")
def list_quotes(status: str | None = None, limit: int = 100, db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    s = select(Quote).order_by(Quote.id.desc()).limit(max(1, min(limit, 500)))
    if status:
        s = s.where(Quote.status.in_([x.strip() for x in status.split(",")]))
    rows = list(db.execute(s).scalars())
    waiting = db.execute(select(Quote).where(Quote.status == "ordered").order_by(Quote.id)).scalars().all()
    return {"waiting": [quotes.admin_dict(db, q) for q in waiting], "quotes": [quotes.admin_dict(db, q) for q in rows]}


@router.get("/api/quotes/settings")
def get_settings(db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    return quotes.config(db)


@router.put("/api/quotes/settings")
def put_settings(body: dict, db: Session = Depends(get_db), _: Principal = Depends(need("settings"))):
    try:
        c = quotes.save_config(db, body)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    db.commit()
    return c


class ReviewIn(BaseModel):
    reason: str | None = None


@router.post("/api/quotes/{qid}/approve")
def approve(qid: int, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    q = db.get(Quote, qid)
    if not q:
        raise HTTPException(404)
    try:
        quotes.approve(db, q, p.get("username") or "someone")
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return quotes.admin_dict(db, q)


@router.post("/api/quotes/{qid}/refuse")
def refuse(qid: int, body: ReviewIn, db: Session = Depends(get_db), p: Principal = Depends(need("farm"))):
    q = db.get(Quote, qid)
    if not q:
        raise HTTPException(404)
    try:
        quotes.refuse(db, q, p.get("username") or "someone", body.reason or "")
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return quotes.admin_dict(db, q)


@router.get("/api/quotes/{qid}/preview/{name}")
def preview(qid: int, name: str, db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    q = db.get(Quote, qid)
    if not q:
        raise HTTPException(404)
    return _preview(q, name)


@router.get("/api/quotes/{qid}/model")
def model_file(qid: int, db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    """The customer's original file, for checking it before approving."""
    from ..config import settings
    q = db.get(Quote, qid)
    if not q:
        raise HTTPException(404)
    p = Path(settings().data_dir) / "quotes" / q.stored
    if not p.exists():
        raise HTTPException(404, "the file has been removed")
    return FileResponse(p, filename=q.filename)


@router.get("/api/quotes/store")
def store_status(db: Session = Depends(get_db), _: Principal = Depends(need("view"))):
    """Is the store's Custom 3D print product there, and is it open for orders?"""
    from ..clients import woo
    c = quotes.config(db)
    pid = c.get("woo_product_id")
    if not pid:
        return {"product": None}
    try:
        p = woo().get(f"/products/{pid}")
        return {"product": {"id": p["id"], "status": p.get("status"), "name": p.get("name"), "permalink": p.get("permalink")}}
    except Exception as e:  # noqa: BLE001
        return {"product": None, "error": (getattr(e, "message", None) or str(e))[:300]}


class StoreIn(BaseModel):
    open: bool


@router.post("/api/quotes/store")
def store_setup(body: StoreIn, db: Session = Depends(get_db), p: Principal = Depends(need("store"))):
    """Create the hidden Custom 3D print product the quotes are sold as (a draft), or open / close it for orders."""
    from ..clients import woo
    from ..jobs import event
    c = quotes.config(db)
    w = woo()
    pid = c.get("woo_product_id")
    if pid:
        try:
            w.get(f"/products/{pid}")
        except Exception:  # noqa: BLE001
            pid = None
    if not pid:
        prod = w.post("/products", json={
            "name": "Custom 3D print", "type": "simple", "status": "draft", "catalog_visibility": "hidden",
            "regular_price": "0", "sku": "AP-CUSTOM-PRINT", "sold_individually": True, "virtual": False,
            "short_description": "Your own model, printed to order. Priced by the instant quote.",
            "description": "Added to the cart from the instant quote page; the price comes from your quote."})
        pid = prod["id"]
        c["woo_product_id"] = pid
        quotes.save_config(db, c)
    w.put(f"/products/{pid}", json={"status": "publish" if body.open else "draft", "catalog_visibility": "hidden"})
    c["enabled"] = bool(body.open)
    quotes.save_config(db, c)
    event(db, "team", f"Instant quotes {'opened' if body.open else 'closed'} on the store", actor=p.get("username"))
    db.commit()
    return store_status(db, p)
