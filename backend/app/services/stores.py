"""Stores besides WooCommerce: Shopify and Etsy, with the same jobs as Woo (services/shop.py).

- Products: the hub pushes a product to every connected store it sells on (a StoreListing row with enabled false
  keeps it off one store; Woo honours the same row). Drafts stay hidden until the product goes live.
- Stock: one shared pool. availability.stock_for_woo is the number for every store: units buildable from supplies
  on hand minus every open order, whatever store it came from (all orders live in shop_order).
- Orders: webhooks from each store, plus a poll that catches anything missed, land in shop_order with the store's
  status mapped onto the hub's (pending, on-hold, processing, completed, cancelled, refunded, failed), so routing
  to the farm, part consumption and reports work the same for every store.
- Shipping: marking an order shipped in the hub fulfils it in its store.
"""
import base64
import hashlib
import hmac
import html
import logging
import re
import secrets
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clients.base import ApiError
from ..config import settings
from ..jobs import PermanentError, enqueue, event
from ..kv import set_setting, setting
from ..models import Image, Order, OrderLine, Product, StoreListing, Variant
from . import availability

log = logging.getLogger("hub.stores")

CHANNELS = ("shopify", "etsy")              # stores this module drives; Woo stays in services/shop.py
ALL = ("woo",) + CHANNELS
LABEL = {"woo": "WooCommerce", "shopify": "Shopify", "etsy": "Etsy"}
OPEN = ("pending", "processing", "on-hold")
ETSY_COLOR = 513                            # Etsy's first custom variation property: values are free text
ETSY_TOKENS, ETSY_SHOP, ETSY_PENDING = "etsy_tokens", "etsy_shop", "etsy_oauth_pending"


def now():
    return datetime.now(timezone.utc)


# ---------- connections ----------

def connected(channel: str, team_id: int | None = None) -> bool:
    from .connections import get
    cfg = get(channel, team_id, required=False)
    if not cfg:
        return False
    if channel == "etsy":
        return bool(_etsy_tokens(team_id).get("refresh_token"))
    return True


def connected_channels(team_id: int | None = None) -> list[str]:
    from .connections import is_connected
    return [c for c in ALL if (is_connected(c, team_id) if c == "woo" else connected(c, team_id))]


def _etsy_tokens(team_id: int | None = None) -> dict:
    """The Etsy OAuth tokens, stored encrypted as a team setting (they change every hour)."""
    from ..db import SessionLocal
    from ..tenant import bind_session, current_team_id
    from .connections import decrypt
    tid = team_id or current_team_id()
    if not tid:
        return {}
    s = SessionLocal()
    try:
        bind_session(s, tid)
        return decrypt(setting(s, ETSY_TOKENS))
    finally:
        s.close()


def _save_etsy_tokens(team_id: int, tokens: dict):
    """In its own session: a renewal must stick even when the job that triggered it fails."""
    from ..db import SessionLocal
    from ..tenant import bind_session
    from .connections import encrypt
    s = SessionLocal()
    try:
        bind_session(s, team_id)
        set_setting(s, ETSY_TOKENS, encrypt(tokens))
        s.commit()
    finally:
        s.close()


def client(channel: str):
    from ..tenant import current_team_id
    from .connections import get
    cfg = get(channel)
    if channel == "shopify":
        from ..clients.shopify_api import ShopifyClient
        return ShopifyClient(cfg.get("shop"), cfg.get("client_id"), cfg.get("client_secret"), cfg.get("access_token"))
    if channel == "etsy":
        from ..clients.etsy_api import EtsyClient
        tid = current_team_id()
        shop = _etsy_shop()
        return EtsyClient(cfg.get("keystring"), cfg.get("shared_secret"), _etsy_tokens(tid),
                          save_tokens=lambda t: _save_etsy_tokens(tid, t), shop_id=shop.get("shop_id"))
    raise ValueError(f"unknown store {channel}")


def _etsy_shop() -> dict:
    from ..db import SessionLocal
    from ..tenant import bind_session, current_team_id
    s = SessionLocal()
    try:
        bind_session(s, current_team_id())
        return setting(s, ETSY_SHOP) or {}
    finally:
        s.close()


def test(channel: str) -> tuple[bool, str]:
    c = client(channel)
    if channel == "shopify":
        info = c.shop_info()
        return True, f"connected to {info.get('name')} ({info.get('myshopifyDomain')})"
    if not _etsy_tokens().get("refresh_token"):
        return False, "saved; now use Connect Etsy to sign in to the shop"
    shop = c.shop()
    return True, f"connected to {shop.get('shop_name')}"


# ---------- Etsy sign-in (OAuth 2.0 with PKCE) ----------

def etsy_redirect_uri() -> str:
    return f"{settings().public_url.rstrip('/')}/oauth/etsy/callback"


def etsy_connect(db: Session) -> str:
    """The address that sends the shop owner to Etsy to approve the hub."""
    from ..clients.etsy_api import connect_url, pkce
    from ..tenant import current_team_id
    from .connections import get
    cfg = get("etsy")
    verifier, challenge = pkce()
    state = f"{current_team_id()}.{secrets.token_urlsafe(24)}"
    set_setting(db, ETSY_PENDING, {"state": state, "verifier": verifier, "at": time.time()})
    return connect_url(cfg["keystring"], etsy_redirect_uri(), state, challenge)


def etsy_finish(db: Session, state: str, code: str) -> dict:
    """Etsy's redirect back: check the state, trade the code for tokens, remember the shop."""
    from ..clients.etsy_api import EtsyClient, exchange_code
    from ..tenant import current_team_id
    from .connections import encrypt, get
    pend = setting(db, ETSY_PENDING) or {}
    if not pend or not hmac.compare_digest(str(pend.get("state")), state) or time.time() - pend.get("at", 0) > 900:
        raise PermanentError("this Etsy sign-in link has expired: start again from Connections")
    cfg = get("etsy")
    tok = exchange_code(cfg["keystring"], etsy_redirect_uri(), code, pend["verifier"])
    set_setting(db, ETSY_TOKENS, encrypt(tok))
    set_setting(db, ETSY_PENDING, None)
    tid = current_team_id()
    shop = EtsyClient(cfg["keystring"], cfg["shared_secret"], tok, save_tokens=lambda t: _save_etsy_tokens(tid, t)).shop()
    info = {"shop_id": shop["shop_id"], "shop_name": shop.get("shop_name"), "url": shop.get("url")}
    set_setting(db, ETSY_SHOP, info)
    event(db, "etsy", f"Etsy shop {info['shop_name']} connected")
    return info


def etsy_disconnect(db: Session):
    set_setting(db, ETSY_TOKENS, None)
    set_setting(db, ETSY_SHOP, None)


def _policy_name(x: dict) -> str:
    name = "Returns" if x.get("accepts_returns") else "No returns"
    if x.get("accepts_exchanges"):
        name += ", exchanges"
    if x.get("return_deadline"):
        name += f", {x['return_deadline']} days"
    return name


def etsy_options() -> dict:
    """The shop's shipping profiles, return policies and processing times, for the Etsy defaults."""
    c = client("etsy")
    return {"shipping_profiles": [{"id": x["shipping_profile_id"], "name": x.get("title")} for x in c.shipping_profiles()],
            "return_policies": [{"id": x["return_policy_id"], "name": _policy_name(x)}
                                for x in c.return_policies()],
            "readiness_states": [{"id": x["readiness_state_id"],
                                  "name": f"{x.get('readiness_state')}: {x.get('min_processing_time')}-"
                                          f"{x.get('max_processing_time')} {x.get('processing_time_unit') or 'days'}"}
                                 for x in c.readiness_states()]}


# ---------- which stores a product sells on ----------

def listing(db: Session, product: Product, channel: str, create: bool = False) -> StoreListing | None:
    row = db.execute(select(StoreListing).where(StoreListing.product_id == product.id,
                                                StoreListing.channel == channel)).scalar()
    if not row and create:
        row = StoreListing(product_id=product.id, channel=channel, enabled=True, refs={}, options={}, stock_pushed={})
        db.add(row)
        db.flush()
    return row


def sells_on(db: Session, product: Product, channel: str) -> bool:
    """Every connected store, unless the product was taken off it."""
    row = listing(db, product, channel)
    return row is None or row.enabled


def targets(db: Session, product: Product) -> list[str]:
    return [c for c in connected_channels() if sells_on(db, product, c)]


def queue_push(db: Session, product: Product, actor: str | None = None) -> list[str]:
    """Queue a push of the product to each store it sells on. Returns the stores."""
    out = []
    for c in targets(db, product):
        if c == "woo":
            enqueue(db, "woo.push_product", ref=f"product:{product.id}", payload={"product_id": product.id},
                    actor=actor, max_attempts=3)
        else:
            enqueue(db, "stores.push_product", ref=f"product:{product.id}:{c}",
                    payload={"product_id": product.id, "channel": c}, actor=actor, max_attempts=3)
        out.append(c)
    return out


def queue_live(db: Session, product: Product, live: bool, actor: str | None = None) -> list[str]:
    out = []
    for c in targets(db, product):
        if c == "woo":
            enqueue(db, "woo.set_live", ref=f"product:{product.id}:live", payload={"product_id": product.id, "live": live},
                    actor=actor, dedupe=False, max_attempts=3)
        else:
            enqueue(db, "stores.set_live", ref=f"product:{product.id}:{c}:live",
                    payload={"product_id": product.id, "channel": c, "live": live}, actor=actor, dedupe=False,
                    max_attempts=3)
        out.append(c)
    return out


def queue_stock_sync(db: Session, delay: int = 1, priority: int | None = None):
    enqueue(db, "store.sync_stock", ref="stock:all", delay=delay, priority=priority)


# ---------- product text and pictures ----------

def _images(product: Product) -> list[Image]:
    return [i for i in sorted(product.model.images, key=lambda i: (i.role != "hero", i.sort))
            if i.for_web and i.role in ("hero", "gallery")]


def _text(product: Product) -> str:
    """Plain text (Etsy shows no HTML): the description, then the designer credit when the license asks for one."""
    from . import licensing
    raw = product.description or product.model.description or product.short_description or product.title
    raw = re.sub(r"<br\s*/?>", "\n", raw or "", flags=re.I)
    raw = re.sub(r"</p\s*>", "\n\n", raw, flags=re.I)
    raw = html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()
    credit = licensing.credit_line(product.model)
    return (raw + (f"\n\n{credit}" if credit else "")).strip()


def _html(product: Product) -> str:
    from .shop import licensing_credit, to_html
    return to_html(product.description or product.model.description) + licensing_credit(product.model)


def _active_variants(product: Product) -> list[Variant]:
    return [v for v in product.variants if v.active] if product.color_mode == "options" else []


def _stock(db: Session, product: Product, snap=None) -> dict:
    snap = snap or availability.load_snapshot(db)
    return availability.stock_for_woo(product, availability.product_availability(db, product, snap))


def problems(db: Session, product: Product, channel: str, going_live: bool) -> list[str]:
    from .shop import validate_for_push
    out = list(validate_for_push(product, going_live))
    if not product.price and not any(v.price for v in _active_variants(product)):
        out.append("price is not set")
    if channel == "etsy":
        o = etsy_settings(db, product)
        for f, label in (("taxonomy_id", "Etsy category"), ("shipping_profile_id", "shipping profile"),
                         ("readiness_state_id", "processing time")):
            if not o.get(f):
                out.append(f"no {label} set for Etsy (Connections, Etsy)")
        if not _images(product):
            out.append("Etsy needs at least one web image")
    return sorted(set(out), key=out.index)


def etsy_settings(db: Session, product: Product) -> dict:
    from .connections import get
    cfg = get("etsy", required=False) or {}
    row = listing(db, product, "etsy")
    o = {k: cfg.get(k) for k in ("taxonomy_id", "shipping_profile_id", "return_policy_id", "readiness_state_id",
                                 "who_made", "when_made")}
    o.update({k: v for k, v in ((row.options if row else None) or {}).items() if v not in (None, "")})
    o["who_made"] = o.get("who_made") or "i_did"
    o["when_made"] = o.get("when_made") or "made_to_order"
    return o


# ---------- push a product ----------

def push_product(db: Session, product_id: int, channel: str, actor: str | None = None) -> dict:
    p = db.get(Product, product_id)
    if not p:
        raise PermanentError("product not found")
    row = listing(db, p, channel, create=True)
    if not row.enabled:
        return {"skipped": f"not sold on {LABEL[channel]}"}
    probs = problems(db, p, channel, p.live)
    if probs:
        row.error = "; ".join(probs)
        raise PermanentError(row.error)
    stock = _stock(db, p)
    try:
        out = (_push_shopify if channel == "shopify" else _push_etsy)(db, p, row, stock)
    except ApiError as e:
        row.error = e.message[:1000]
        raise
    row.stock_pushed, row.synced_at, row.error = stock, now(), None
    enqueue(db, "farm.sync_product", ref=f"product:{p.id}", payload={"product_id": p.id}, delay=5)
    event(db, f"model:{p.model_id}", f"Pushed to {LABEL[channel]} as {'live' if p.live else 'draft'} "
                                     f"(listing {row.ext_id})", actor=actor)
    return {"channel": channel, "ext_id": row.ext_id, "status": row.status, "stock": stock, **out}


def _push_shopify(db: Session, p: Product, row: StoreListing, stock: dict) -> dict:
    from ..clients.shopify_api import gid
    from .connections import get
    from .shop import public_image_url
    c = client("shopify")
    refs = dict(row.refs or {})
    loc = (get("shopify").get("location_id") or "").strip()
    loc = (loc if loc.startswith("gid://") else gid("Location", loc)) if loc else (refs.get("location") or c.primary_location())
    refs["location"] = loc
    imgs = _images(p)[:50]
    known = refs.get("images") or {}
    files = [{"id": known[str(i.id)]} if str(i.id) in known else
             {"originalSource": public_image_url(i), "alt": p.title[:500], "contentType": "IMAGE",
              "filename": f"{p.sku}-{i.id}.jpg"} for i in imgs]
    variants = _active_variants(p)
    weight = {"measurement": {"weight": {"value": float(p.weight_g), "unit": "GRAMS"}}} if p.weight_g else {}
    item = {"inventoryItem": {"tracked": True, **weight}}
    if variants:
        options = [{"name": "Color", "values": [{"name": v.color_name} for v in variants]}]
        vin = [{"optionValues": [{"optionName": "Color", "name": v.color_name}], "sku": v.sku,
                "price": f"{(v.price or p.price or 0):.2f}", "inventoryPolicy": "DENY", **item,
                **({"compareAtPrice": f"{p.price:.2f}", "price": f"{p.sale_price:.2f}"}
                   if p.sale_price and not v.price and p.price else {})}
               for v in variants]
    else:
        options = [{"name": "Title", "values": [{"name": "Default Title"}]}]
        vin = [{"optionValues": [{"optionName": "Title", "name": "Default Title"}], "sku": p.sku,
                "price": f"{(p.sale_price or p.price or 0):.2f}", "inventoryPolicy": "DENY", **item,
                **({"compareAtPrice": f"{p.price:.2f}"} if p.sale_price and p.price else {})}]
    pid = row.ext_id
    if pid:
        ex = {v["sku"]: v["id"] for v in (refs.get("variant_nodes") or [])}
        for v in vin:
            if v["sku"] in ex:
                v["id"] = ex[v["sku"]]
        if not c.product_exists(pid):
            pid = None
    if not pid:
        pid = c.find_product_by_sku(variants[0].sku if variants else p.sku)
    data = {"title": p.title, "descriptionHtml": _html(p), "status": "ACTIVE" if p.live else "DRAFT",
            "tags": [t for t in (p.model.tags or [])][:250], "productType": p.model.category.name if p.model.category else "",
            "files": files, "productOptions": options, "variants": vin}
    res = c.product_set(data, pid)
    row.ext_id, row.status = res["id"], "active" if p.live else "draft"
    media = (res.get("media") or {}).get("nodes") or []
    if len(media) == len(imgs):
        known = {str(i.id): m["id"] for i, m in zip(imgs, media)}
    nodes = (res.get("variants") or {}).get("nodes") or []
    by_sku = {n["sku"]: n for n in nodes}
    items = {}
    for v in variants:
        if v.sku in by_sku:
            items[str(v.id)] = by_sku[v.sku]["inventoryItem"]["id"]
    if not variants and p.sku in by_sku:
        items["p"] = by_sku[p.sku]["inventoryItem"]["id"]
    refs.update({"images": known, "items": items, "handle": res.get("handle"), "url": res.get("onlineStoreUrl"),
                 "variant_nodes": [{"sku": n["sku"], "id": n["id"]} for n in nodes],
                 "variants": {str(v.id): by_sku[v.sku]["id"] for v in variants if v.sku in by_sku}})
    row.refs = refs
    c.stock_at(loc, list(items.values()))
    c.set_quantities(loc, {items[k]: q for k, q in stock.items() if k in items},
                     key=f"hub-{p.id}-{hashlib.sha1(repr(sorted(stock.items())).encode()).hexdigest()[:12]}-{int(time.time())}")
    return {"url": refs.get("url")}


def _etsy_inventory(db: Session, p: Product, stock: dict) -> dict:
    o = etsy_settings(db, p)
    rs = int(o["readiness_state_id"])
    variants = _active_variants(p)
    if not variants:
        return {"products": [{"sku": p.sku, "property_values": [],
                              "offerings": [{"price": round(float(p.price or 0), 2), "quantity": int(stock.get("p", 0)),
                                             "is_enabled": True, "readiness_state_id": rs}]}],
                "price_on_property": [], "quantity_on_property": [], "sku_on_property": [],
                "readiness_state_on_property": []}
    priced = len({(v.price or p.price) for v in variants}) > 1
    return {"products": [{"sku": v.sku,
                          "property_values": [{"property_id": ETSY_COLOR, "property_name": "Color", "value_ids": [],
                                               "values": [v.color_name[:45]]}],
                          "offerings": [{"price": round(float(v.price or p.price or 0), 2),
                                         "quantity": int(stock.get(str(v.id), 0)), "is_enabled": True,
                                         "readiness_state_id": rs}]} for v in variants],
            "price_on_property": [ETSY_COLOR] if priced else [], "quantity_on_property": [ETSY_COLOR],
            "sku_on_property": [ETSY_COLOR], "readiness_state_on_property": []}


_TITLE_BAD = re.compile(r"[^\w\s\-.,'\"!?()/™©®$#@*;%:&+]", re.UNICODE)


def etsy_title(t: str) -> str:
    """Etsy titles: letters, numbers, punctuation; % : & + at most once each; 140 characters."""
    t = _TITLE_BAD.sub(" ", t or "")
    for ch in "%:&+":
        first = t.find(ch)
        if first >= 0:
            t = t[:first + 1] + t[first + 1:].replace(ch, " ")
    return re.sub(r"\s+", " ", t).strip()[:140]


def etsy_tags(tags) -> list[str]:
    out = []
    for t in tags or []:
        t = re.sub(r"\s+", " ", re.sub(r"[^\w\s\-'™©®]", " ", str(t))).strip()[:20].strip()
        if t and t.lower() not in [x.lower() for x in out]:
            out.append(t)
    return out[:13]


def _push_etsy(db: Session, p: Product, row: StoreListing, stock: dict) -> dict:
    from . import images as imgsvc
    c = client("etsy")
    o = etsy_settings(db, p)
    refs = dict(row.refs or {})
    lid = row.ext_id
    if lid and not c.get_listing(lid):
        lid, refs = None, {}
    total = sum(stock.values()) if stock else 0
    data = {"title": etsy_title(p.title), "description": _text(p), "who_made": o["who_made"], "when_made": o["when_made"],
            "is_supply": False, "taxonomy_id": int(o["taxonomy_id"]), "shipping_profile_id": int(o["shipping_profile_id"]),
            "return_policy_id": int(o["return_policy_id"]) if o.get("return_policy_id") else None,
            "tags": etsy_tags(p.model.tags), "should_auto_renew": True, "type": "physical"}
    if p.weight_g:
        data.update({"item_weight": round(float(p.weight_g), 1), "item_weight_unit": "g"})
    dims = [x.strip() for x in re.split(r"[x×*]", p.dims_cm or "") if x.strip()]
    if len(dims) == 3:
        try:
            data.update({"item_length": float(dims[0]), "item_width": float(dims[1]), "item_height": float(dims[2]),
                         "item_dimensions_unit": "cm"})
        except ValueError:
            pass
    if not lid:
        price = p.price or next((v.price for v in _active_variants(p) if v.price), 0)
        res = c.create_draft_listing({**data, "quantity": max(1, total), "price": round(float(price), 2),
                                      "readiness_state_id": int(o["readiness_state_id"])})
        lid = str(res["listing_id"])
        refs = {}
    else:
        c.update_listing(lid, data)
    row.ext_id = lid
    # pictures: upload new ones in order, drop the ones no longer shown
    want = _images(p)[:10]
    have = {str(k): v for k, v in (refs.get("images") or {}).items()}
    for rank, img in enumerate(want, 1):
        if str(img.id) not in have:
            from pathlib import Path
            path = imgsvc.web_jpeg(img.sha256, Path(settings().data_dir) / img.store_rel)
            r = c.upload_image(lid, path, f"{p.sku}-{img.id}.jpg", rank, alt=p.title)
            have[str(img.id)] = r.get("listing_image_id")
    keep = {str(i.id) for i in want}
    for k in [k for k in have if k not in keep]:
        try:
            c.delete_image(lid, have[k])
        except ApiError as e:
            if e.status != 404:
                raise
        have.pop(k)
    refs["images"] = have
    inv = c.update_inventory(lid, _etsy_inventory(db, p, stock))
    by_sku = {x.get("sku"): x.get("product_id") for x in (inv or {}).get("products") or []}
    refs["variants"] = {str(v.id): by_sku[v.sku] for v in _active_variants(p) if v.sku in by_sku}
    row.refs = refs
    state = _etsy_state(c, lid, p.live, total)
    row.status = state
    shop = setting(db, ETSY_SHOP) or {}
    return {"url": f"https://www.etsy.com/listing/{lid}", "shop": shop.get("shop_name")}


def _etsy_state(c, lid, live: bool, total: int) -> str:
    cur = (c.get_listing(lid) or {}).get("state") or "draft"
    if live and cur != "active" and total > 0:
        cur = c.update_listing(lid, {"state": "active"}).get("state", "active")
    elif not live and cur == "active":
        cur = c.update_listing(lid, {"state": "inactive"}).get("state", "inactive")
    return cur


def set_live(db: Session, product_id: int, channel: str, live: bool, actor: str | None = None,
             this_store_only: bool = False) -> dict:
    """Show or hide the product on one store. this_store_only: the product was taken off this store; its live
    state elsewhere stays as it is."""
    p = db.get(Product, product_id)
    if not p:
        raise PermanentError("product not found")
    if not this_store_only:
        p.live = live
    row = listing(db, p, channel, create=True)
    if not row.ext_id:
        return {"skipped": "not on the store"} if this_store_only else push_product(db, product_id, channel, actor)
    if channel == "shopify":
        client("shopify").set_status(row.ext_id, "ACTIVE" if live else "DRAFT")
        row.status = "active" if live else "draft"
    else:
        stock = _stock(db, p)
        row.status = _etsy_state(client("etsy"), row.ext_id, live, sum(stock.values()))
    row.synced_at = now()
    event(db, f"model:{p.model_id}", f"{'Went live' if live else 'Hidden'} on {LABEL[channel]}", actor=actor)
    sync_stock(db, channel, product_id)
    return {"channel": channel, "status": row.status}


def remove_listing(db: Session, product: Product, channel: str) -> str | None:
    """Take a product's listing down from a store (the product is being removed from the hub)."""
    row = listing(db, product, channel)
    if not row or not row.ext_id or not connected(channel):
        return None
    try:
        if channel == "shopify":
            client("shopify").delete_product(row.ext_id)
        else:
            client("etsy").delete_listing(row.ext_id)
    except ApiError as e:
        if e.status != 404:
            raise
    return row.ext_id


# ---------- stock (one shared pool) ----------

def sync_stock(db: Session, channel: str | None = None, product_id: int | None = None) -> dict:
    """Push each listed product's buildable units to the stores that list it, when they changed."""
    chans = [channel] if channel else [c for c in CHANNELS if connected(c)]
    if not chans:
        return {"changed": 0}
    q = select(StoreListing).where(StoreListing.channel.in_(chans), StoreListing.enabled.is_(True),
                                   StoreListing.ext_id.is_not(None))
    if product_id:
        q = q.where(StoreListing.product_id == product_id)
    rows = db.execute(q).scalars().all()
    snap = availability.load_snapshot(db) if rows else None
    changed, errors = 0, []
    for row in rows:
        p = row.product
        stock = _stock(db, p, snap)
        if stock == (row.stock_pushed or {}):
            continue
        try:
            if row.channel == "shopify":
                c = client("shopify")
                items = (row.refs or {}).get("items") or {}
                loc = (row.refs or {}).get("location")
                if not loc or not items:
                    continue
                c.set_quantities(loc, {items[k]: q for k, q in stock.items() if k in items},
                                 key=f"hub-{p.id}-{int(time.time())}-{sum(stock.values())}")
            else:
                c = client("etsy")
                c.update_inventory(row.ext_id, _etsy_inventory(db, p, stock))
                if p.live and sum(stock.values()) > 0 and row.status != "active":
                    row.status = _etsy_state(c, row.ext_id, True, sum(stock.values()))
        except ApiError as e:
            row.error = f"stock: {e.message}"[:1000]
            errors.append(f"{LABEL[row.channel]} {p.sku}: {e.message[:120]}")
            continue
        old = row.stock_pushed or {}
        row.stock_pushed, row.error = stock, None
        changed += 1
        if p.live and any((old.get(k, 1) > 0) and v == 0 for k, v in stock.items()):
            event(db, f"model:{p.model_id}", f"Out of supplies: {LABEL[row.channel]} stock set to 0", level="warn")
    return {"changed": changed, "errors": errors}


def sync_all_stock(db: Session) -> dict:
    """Every connected store, Woo included: the one job behind store.sync_stock."""
    from .connections import is_connected
    out = {}
    if is_connected("woo"):
        from .shop import sync_stock as woo_sync
        try:
            out["woo"] = woo_sync(db)
        except ApiError as e:
            out["woo"] = {"error": e.message[:200]}
    for c in CHANNELS:
        if connected(c):
            out[c] = sync_stock(db, c)
    return out


# ---------- orders ----------

def _money(v) -> float | None:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _ts(v) -> datetime | None:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, tz=timezone.utc)
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def shopify_status(o: dict) -> str:
    fin, ful = o.get("displayFinancialStatus"), o.get("displayFulfillmentStatus")
    if o.get("cancelledAt"):
        return "cancelled"
    if fin in ("REFUNDED", "VOIDED"):
        return "refunded"
    if fin == "EXPIRED":
        return "failed"
    if ful == "FULFILLED":
        return "completed"
    if fin in ("PAID", "PARTIALLY_REFUNDED"):
        return "processing"
    if fin in ("AUTHORIZED", "PARTIALLY_PAID"):
        return "on-hold"
    return "pending"


def etsy_status(r: dict) -> str:
    s = (r.get("status") or "").lower()
    if s == "canceled":
        return "cancelled"
    if s == "fully refunded":
        return "refunded"
    if s == "completed" or r.get("is_shipped"):
        return "completed"
    if s in ("paid", "partially refunded") or r.get("is_paid"):
        return "processing"
    return "pending"


def normalize_shopify(o: dict) -> dict:
    money = ((o.get("totalPriceSet") or {}).get("shopMoney") or {})
    return {"channel": "shopify", "ext_id": o["id"], "number": str(o.get("name") or o.get("legacyResourceId")).lstrip("#"),
            "status": shopify_status(o), "customer": ((o.get("customer") or {}).get("displayName")),
            "total": _money(money.get("amount")), "currency": money.get("currencyCode") or o.get("currencyCode"),
            "is_test": bool(o.get("test")), "placed_at": _ts(o.get("createdAt")),
            "raw": {"name": o.get("name"), "legacy_id": o.get("legacyResourceId"), "note": o.get("note"),
                    "financial": o.get("displayFinancialStatus"), "fulfillment": o.get("displayFulfillmentStatus")},
            "lines": [{"ext_line_id": li["id"], "sku": li.get("sku"), "name": li.get("name"), "qty": int(li.get("quantity") or 1),
                       "variant_ext_id": (li.get("variant") or {}).get("id"), "product_ext_id": (li.get("product") or {}).get("id")}
                      for li in ((o.get("lineItems") or {}).get("nodes") or [])]}


def normalize_etsy(r: dict) -> dict:
    g = r.get("grandtotal") or {}
    total = (g.get("amount") or 0) / (g.get("divisor") or 100) if g else None
    return {"channel": "etsy", "ext_id": str(r["receipt_id"]), "number": str(r["receipt_id"]), "status": etsy_status(r),
            "customer": r.get("name"), "total": _money(total), "currency": g.get("currency_code"), "is_test": False,
            "placed_at": _ts(r.get("create_timestamp") or r.get("created_timestamp")),
            "raw": {"status": r.get("status"), "message_from_buyer": r.get("message_from_buyer"),
                    "is_gift": r.get("is_gift")},
            "lines": [{"ext_line_id": str(t["transaction_id"]), "sku": t.get("sku"), "name": t.get("title"),
                       "qty": int(t.get("quantity") or 1), "product_ext_id": str(t.get("listing_id") or ""),
                       "variant_ext_id": t.get("product_id")} for t in r.get("transactions") or []]}


def _match_line(db: Session, ln: OrderLine, li: dict, channel: str):
    """Find the hub product for an order line: by SKU, else by the listing the store says it came from."""
    ln.product_id = ln.variant_id = None
    sku = li.get("sku")
    v = db.execute(select(Variant).where(Variant.sku == sku)).scalar() if sku else None
    if v:
        ln.variant_id, ln.product_id = v.id, v.product_id
        return
    pr = db.execute(select(Product).where(Product.sku == sku)).scalar() if sku else None
    if not pr and li.get("product_ext_id"):
        row = db.execute(select(StoreListing).where(StoreListing.channel == channel,
                                                    StoreListing.ext_id == str(li["product_ext_id"]))).scalar()
        if row:
            pr = row.product
            vmap = {str(x): k for k, x in ((row.refs or {}).get("variants") or {}).items()}
            vid = vmap.get(str(li.get("variant_ext_id")))
            ln.variant_id = int(vid) if vid else None
    if pr:
        ln.product_id = pr.id


def upsert_order(db: Session, n: dict) -> Order:
    """Store a Shopify or Etsy order in its normalized form; then the same follow-up as a Woo order."""
    row = db.execute(select(Order).where(Order.channel == n["channel"], Order.ext_id == str(n["ext_id"]))).scalar()
    if not row:
        row = Order(channel=n["channel"], ext_id=str(n["ext_id"]), number=n["number"], status=n["status"])
        db.add(row)
    prev = row.status if row.id else None
    for k in ("number", "status", "customer", "total", "currency", "is_test", "placed_at", "raw"):
        if n.get(k) is not None or k in ("customer",):
            setattr(row, k, n.get(k) if n.get(k) is not None else getattr(row, k))
    db.flush()
    by_line = {ln.ext_line_id: ln for ln in row.lines}
    seen = set()
    for li in n["lines"]:
        ln = by_line.get(str(li["ext_line_id"])) or OrderLine(order_id=row.id, ext_line_id=str(li["ext_line_id"]))
        ln.sku, ln.name, ln.qty = li.get("sku"), (li.get("name") or "")[:300], li["qty"]
        _match_line(db, ln, li, n["channel"])
        if ln not in row.lines:
            row.lines.append(ln)
        seen.add(str(li["ext_line_id"]))
    for ln in list(row.lines):
        if ln.ext_line_id not in seen:
            row.lines.remove(ln)
    db.flush()
    if prev != row.status:
        event(db, f"order:{row.id}", f"{LABEL[n['channel']]} order #{row.number} is {row.status}")
    from .farm import on_order
    on_order(db, row, prev)
    if row.status == "completed" and not row.consumed:
        enqueue(db, "order.consume", ref=f"order:{row.id}", payload={"order_id": row.id})
    queue_stock_sync(db, delay=1, priority=1)
    return row


def ingest_order(db: Session, channel: str, ext_id: str) -> dict:
    if channel == "shopify":
        from ..clients.shopify_api import gid
        o = client("shopify").get_order(ext_id if str(ext_id).startswith("gid://") else gid("Order", ext_id))
        if not o:
            return {"skipped": "gone"}
        n = normalize_shopify(o)
    else:
        n = normalize_etsy(client("etsy").receipt(ext_id))
    row = upsert_order(db, n)
    return {"order": row.id, "status": row.status}


def poll_orders(db: Session, channel: str) -> dict:
    """Orders updated since the last poll (the first poll looks back 14 days)."""
    key = f"{channel}_orders_last_poll"
    last = setting(db, key)
    started = time.time()
    since = float(last) if last else started - 14 * 86400
    n = 0
    if channel == "shopify":
        c = client("shopify")
        cursor = None
        iso = datetime.fromtimestamp(since - 60, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        while True:
            nodes, cursor = c.orders_updated_since(iso, cursor)
            for o in nodes:
                upsert_order(db, normalize_shopify(o))
                n += 1
            if not cursor:
                break
    else:
        c = client("etsy")
        offset = 0
        while True:
            batch = c.receipts(int(since) - 60, offset)
            for r in batch:
                upsert_order(db, normalize_etsy(r))
                n += 1
            if len(batch) < 100:
                break
            offset += 100
    set_setting(db, key, started)
    return {"orders": n}


def order_note(db: Session, order: Order, text: str) -> bool:
    """A note on the order in its store, where the store has notes (Etsy has none for sellers through the API)."""
    if order.channel == "woo":
        from ..clients import woo
        woo().add_order_note(order.woo_order_id, text)
        return True
    if order.channel == "shopify":
        client("shopify").add_order_note(order.ext_id, text)
        return True
    return False


def mark_shipped(db: Session, order: Order, tracking: dict | None = None, actor: str | None = None) -> dict:
    """The order went out: completed in Woo, fulfilled in Shopify, marked shipped on Etsy."""
    tracking = {k: (v or "").strip() for k, v in (tracking or {}).items() if v}
    if order.channel == "woo":
        from ..clients import woo
        if tracking:
            woo().add_order_note(order.woo_order_id, "Shipped" + (f" with {tracking.get('company')}" if tracking.get("company") else "")
                                 + (f", tracking {tracking.get('number')}" if tracking.get("number") else ""), True)
        woo().update_order(order.woo_order_id, {"status": "completed"})
    elif order.channel == "shopify":
        client("shopify").fulfill_order(order.ext_id, tracking)
    elif order.channel == "etsy":
        client("etsy").ship_receipt(order.ext_id, tracking.get("number"), tracking.get("company"))
    prev = order.status
    order.status = "completed"
    event(db, f"order:{order.id}", f"Order #{order.number} marked shipped on {LABEL[order.channel]}", actor=actor)
    if not order.consumed:
        enqueue(db, "order.consume", ref=f"order:{order.id}", payload={"order_id": order.id})
    from .farm import on_order
    on_order(db, order, prev)
    return {"status": order.status}


def order_links(order: Order) -> dict:
    """Where to open the order in its store."""
    from .connections import get
    if order.channel == "woo":
        url = ((get("woo", required=False) or {}).get("url") or "").rstrip("/")
        return {"store_url": f"{url}/wp-admin/post.php?post={order.woo_order_id}&action=edit" if url and order.woo_order_id else None}
    if order.channel == "shopify":
        shop = ((get("shopify", required=False) or {}).get("shop") or "")
        handle = shop.replace(".myshopify.com", "").split("/")[-1] if shop else ""
        legacy = (order.raw or {}).get("legacy_id") or str(order.ext_id or "").rsplit("/", 1)[-1]
        return {"store_url": f"https://admin.shopify.com/store/{handle}/orders/{legacy}" if handle else None}
    return {"store_url": f"https://www.etsy.com/your/orders/sold?order_id={order.ext_id}"}


def listing_view(db: Session, product: Product) -> list[dict]:
    """The product on each store, for the product page."""
    from .connections import get, is_connected
    out = []
    for c in ALL:
        if not (is_connected(c) if c == "woo" else get(c, required=False)):
            continue
        row = listing(db, product, c)
        d = {"channel": c, "label": LABEL[c], "enabled": row is None or row.enabled,
             "connected": is_connected(c) if c == "woo" else connected(c)}
        if c == "woo":
            d.update({"ext_id": product.woo_product_id, "status": product.woo_status, "error": product.woo_error,
                      "synced_at": product.woo_synced_at.isoformat() if product.woo_synced_at else None,
                      "stock": product.woo_stock_pushed})
        elif row:
            refs = row.refs or {}
            d.update({"ext_id": row.ext_id, "status": row.status, "error": row.error, "stock": row.stock_pushed,
                      "synced_at": row.synced_at.isoformat() if row.synced_at else None, "options": row.options or {}})
            if c == "shopify" and row.ext_id:
                shop = (get("shopify", required=False) or {}).get("shop") or ""
                handle = shop.replace(".myshopify.com", "").split("/")[-1]
                d["admin_url"] = f"https://admin.shopify.com/store/{handle}/products/{row.ext_id.rsplit('/', 1)[-1]}"
                d["view_url"] = refs.get("url")
            if c == "etsy" and row.ext_id:
                d["admin_url"] = f"https://www.etsy.com/your/shops/me/listing-editor/edit/{row.ext_id}"
                d["view_url"] = f"https://www.etsy.com/listing/{row.ext_id}" if row.status == "active" else None
        out.append(d)
    return out


# ---------- webhooks ----------

def shopify_signature_ok(raw: bytes, header: str, team_id: int) -> bool:
    from .connections import get
    cfg = get("shopify", team_id, required=False) or {}
    for secret in (cfg.get("client_secret"), cfg.get("webhook_secret")):
        if secret:
            want = base64.b64encode(hmac.new(secret.encode(), raw, hashlib.sha256).digest()).decode()
            if hmac.compare_digest(want, header or ""):
                return True
    return False


def etsy_signature_ok(raw: bytes, headers, team_id: int, tolerance: int = 300) -> bool:
    """Etsy signs webhooks the Standard Webhooks way: HMAC-SHA256 over "id.timestamp.body" with the endpoint's
    signing secret (whsec_..., base64 after the prefix); the header may list several v1,<signature> values."""
    from .connections import get
    secret = ((get("etsy", team_id, required=False) or {}).get("webhook_secret") or "").strip()
    wid, ts, sig = headers.get("webhook-id"), headers.get("webhook-timestamp"), headers.get("webhook-signature") or ""
    if not secret or not wid or not ts:
        return False
    try:
        if abs(time.time() - int(ts)) > tolerance:
            return False
        key = base64.b64decode(secret.split("_", 1)[1] if secret.startswith("whsec_") else secret)
    except ValueError:
        return False
    want = base64.b64encode(hmac.new(key, f"{wid}.{ts}.".encode() + raw, hashlib.sha256).digest()).decode()
    for part in sig.split():
        val = part.split(",", 1)[1] if "," in part else part
        if hmac.compare_digest(val, want):
            return True
    return False


SHOPIFY_TOPICS = ("ORDERS_CREATE", "ORDERS_UPDATED", "ORDERS_CANCELLED", "ORDERS_FULFILLED", "ORDERS_PAID")


def shopify_webhooks(db: Session) -> dict:
    """Register the hub's order webhooks in Shopify (part of Set up connected apps). Safe to run again."""
    from .setup import hook_url
    url = hook_url(db, "shopify")
    c = client("shopify")
    have = {(w["topic"], w.get("uri")) for w in c.webhooks()}
    added = []
    for t in SHOPIFY_TOPICS:
        if (t, url) not in have:
            c.add_webhook(t, url)
            added.append(t)
    return {"url": url, "added": added}
