"""Idempotent one-time wiring of the external systems (safe to re-run)."""
import secrets

from sqlalchemy.orm import Session

from ..clients import woo, farm, inventree
from ..config import settings
from . import dam as damsvc, connections
from .connections import NotConnected
from .shop import setting, set_setting


def hook_url(db: Session, service: str) -> str:
    """The first team keeps the original webhook URLs; other teams get their own path."""
    from .teams import current_team
    t = current_team(db)
    base = f"{settings().public_url}/hooks/{service}"
    return base if t.id == 1 else f"{base}/{t.slug}"

WOO_TOPICS = ["order.created", "order.updated", "product.updated"]
WT_EVENTS = ["queue.job_submitted", "queue.job_assigned", "queue.job_printing", "queue.job_completed", "queue.job_failed",
             "queue.job_cancelled", "printer.online", "printer.offline", "alarm.raised", "alarm.cleared",
             "vision.failure_detected", "shop.order.fulfilled", "shop.order.status_changed", "shop.order.ready_to_ship"]
INV_CATEGORIES = ["Hardware", "Hardware/Magnets", "Hardware/Fasteners", "Hardware/Heat-set inserts", "Hardware/Bearings",
                  "Electronics", "Electronics/LEDs", "Packaging", "Packaging/Boxes", "Packaging/Bags", "Packaging/Labels",
                  "Packaging/Fill", "Finishing", "Finishing/Felt pads", "Finishing/Adhesives"]
INV_LOCATIONS = ["Workshop", "Workshop/Parts bins", "Workshop/Packaging shelf"]


def run(db: Session) -> dict:
    out = {}
    try:
        out.update(_woo(db))
    except NotConnected as e:
        out["woo"] = str(e)
    try:
        out.update(_watchtower(db))
    except NotConnected as e:
        out["watchtower"] = str(e)
    try:
        out["dam_fields"] = damsvc.ensure_fields(db)
    except NotConnected as e:
        out["dam"] = str(e)
    try:
        out["inventree"] = _inventree()
    except NotConnected as e:
        out["inventree"] = str(e)
    set_setting(db, "setup_done", True)
    return out


def _woo(db: Session) -> dict:
    out = {}
    cfg = connections.get("woo")
    secret = cfg.get("webhook_secret")
    if not secret:
        secret = secrets.token_hex(24)
        connections.save(db, "woo", None, {"webhook_secret": secret})
    url = hook_url(db, "woo")
    have = {(w["topic"], w["delivery_url"]): w for w in woo().list_webhooks()}
    made = []
    for t in WOO_TOPICS:
        w = have.get((t, url))
        if w:
            if w.get("status") != "active":
                woo().update_webhook(w["id"], {"status": "active", "secret": secret})
            continue
        woo().create_webhook(f"Artisan Hub {t}", t, url, secret)
        made.append(t)
    out["woo_webhooks_created"] = made
    return out


def _watchtower(db: Session) -> dict:
    """The farm's webhook to the hub (Watchtower, or OpenPrintHQ's signed webhook provider), and Watchtower's
    storefront. Keys in the answer keep the watchtower_ names the setup page shows."""
    from ..clients import farm_kind
    out = {}
    kind = farm_kind()
    secret_key = "wt_webhook_secret" if kind == "watchtower" else "ophq_webhook_secret"
    wurl = hook_url(db, kind)
    hooks = farm().webhooks()
    mine = [h for h in hooks if h.get("url") == wurl]
    secret = setting(db, secret_key)
    if mine and secret and (set(mine[0].get("events") or []) != set(WT_EVENTS) or not mine[0].get("active", True)):
        # more events than when it was registered (or disabled after failures): register it again, same secret
        for h in mine:
            farm().delete_webhook(h["id"])
        farm().add_webhook(wurl, WT_EVENTS, secret)
        out["watchtower_webhook"] = "updated"
    elif not mine or not secret:
        for h in mine:
            farm().delete_webhook(h["id"])
        secret = secrets.token_hex(24)
        farm().add_webhook(wurl, WT_EVENTS, secret)
        set_setting(db, secret_key, secret)
        out["watchtower_webhook"] = "created"
    else:
        out["watchtower_webhook"] = "exists"
    if not farm().has("storefronts"):
        return out
    try:
        fronts = farm().storefronts()
        if fronts:
            set_setting(db, "wt_storefront_id", fronts[0]["id"])
            out["watchtower_storefront"] = fronts[0]["name"]
    except Exception as e:  # noqa: BLE001
        out["watchtower_storefront_error"] = str(e)[:200]
    return out


def _inventree() -> dict:
    inv = inventree()
    cats = {c["pathstring"]: c["pk"] for c in inv.categories()}
    for path in INV_CATEGORIES:
        if path in cats:
            continue
        parent = cats.get(path.rsplit("/", 1)[0]) if "/" in path else None
        cats[path] = inv.create_category(path.rsplit("/", 1)[-1], parent)["pk"]
    locs = {l["pathstring"]: l["pk"] for l in inv.locations()}
    for path in INV_LOCATIONS:
        if path in locs:
            continue
        parent = locs.get(path.rsplit("/", 1)[0]) if "/" in path else None
        locs[path] = inv.create_location(path.rsplit("/", 1)[-1], parent)["pk"]
    return {"categories": len(cats), "locations": len(locs)}
