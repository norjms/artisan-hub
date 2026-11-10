"""Orders made in the hub for a model: "print me one of these".

The hub creates a pending WooCommerce order (so it has a payment link and Woo's own invoice email), mirrors it
into Orders like any store order, and either sends it to the farm straight away or lets the normal routing print
it once it is paid, asking each time.

Price: the store price when the model has one, else the approved slice's cost on the default printer through the
quote pricing (markup, setup fee, minimum). Either way the person can change it.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import Model, Order


def suggest(db: Session, model: Model, qty: int = 1) -> dict:
    """A price for qty of the model, and where it came from."""
    from . import quotes, slicing
    qty = max(1, int(qty or 1))
    prod = model.product
    cur = quotes.config(db).get("currency") or "USD"
    if prod and (prod.sale_price or prod.price):
        unit = float(prod.sale_price or prod.price)
        return {"source": "store", "unit": round(unit, 2), "total": round(unit * qty, 2), "currency": cur,
                "detail": f"store price {unit:.2f} each"}
    prof = slicing.default_profile(db)
    r = slicing.result_for(db, model.id, prof.id, create=False) if prof else None
    stats = (r.approved_stats if r and r.approved_file_ids else None) or {}
    cost = slicing.cost_of(db, model, prof, stats) if prof and stats else None
    if not cost:
        return {"source": "none", "unit": None, "total": None, "currency": cur,
                "detail": "no store price and no approved slice: type a price"}
    c = quotes.config(db)
    markup, setup, minimum = float(c.get("markup") or 1), float(c.get("setup_fee") or 0), float(c.get("minimum") or 0)
    unit = cost["total"] * markup
    total = max(minimum, unit * qty + setup)
    return {"source": "slice", "unit": round(unit, 2), "total": round(total, 2), "currency": cur,
            "detail": f"cost {cost['total']:.2f} each on {prof.name} (filament {cost['material']:.2f} + printer "
                      f"{cost['machine']:.2f}) x {markup:g} markup, + {setup:.2f} setup, minimum {minimum:.2f}"}


def _money(v) -> str:
    return f"{float(v):.2f}"


def create(db: Session, model: Model, body: dict, actor: str) -> dict:
    """Create the Woo order, mirror it, then optionally email the invoice and send it to the farm."""
    from ..clients import woo
    from ..jobs import event
    from . import farm, shop
    cust = body.get("customer") or {}
    email = (cust.get("email") or "").strip()
    first, last = (cust.get("first_name") or "").strip(), (cust.get("last_name") or "").strip()
    if not (first or last):
        raise ValueError("give the customer's name")
    if body.get("send_invoice") and not email:
        raise ValueError("an invoice needs the customer's email")
    qty = max(1, min(500, int(body.get("quantity") or 1)))
    try:
        total = float(body.get("total"))
    except (TypeError, ValueError):
        raise ValueError("give a price")
    if total < 0:
        raise ValueError("the price can't be negative")
    prod = model.product
    sku = prod.sku if prod else None
    if prod and prod.woo_product_id:
        line = {"product_id": prod.woo_product_id, "quantity": qty, "subtotal": _money(total), "total": _money(total)}
    else:
        line = {"name": model.name, "quantity": qty, "subtotal": _money(total), "total": _money(total),
                "meta_data": [{"key": "_artisan_sku", "value": sku}] if sku else []}
    billing = {"first_name": first, "last_name": last, "email": email or None, "phone": (cust.get("phone") or "").strip() or None}
    addr = body.get("address") or {}
    for k in ("address_1", "address_2", "city", "state", "postcode", "country"):
        if (addr.get(k) or "").strip():
            billing[k] = addr[k].strip()
    billing = {k: v for k, v in billing.items() if v}
    data = {"status": "pending", "set_paid": False, "billing": billing, "line_items": [line],
            "customer_note": (body.get("note") or "").strip() or None,
            "meta_data": [{"key": "_artisan_manual", "value": actor or "hub"}]}
    if body.get("fulfilment") == "ship":
        data["shipping"] = {k: v for k, v in billing.items() if k not in ("email", "phone")}
        data["shipping_lines"] = [{"method_id": "flat_rate", "method_title": "Shipping",
                                   "total": _money(body.get("shipping_cost") or 0)}]
    else:
        data["shipping_lines"] = [{"method_id": "local_pickup", "method_title": "Pickup", "total": "0.00"}]
    data = {k: v for k, v in data.items() if v is not None}
    w = woo()
    created = w.create_order(data)
    row = shop.upsert_order(db, created)
    if body.get("due_date"):
        try:
            row.due_at = datetime.fromisoformat(str(body["due_date"])).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    out = {"order_id": row.id, "woo_order_id": created.get("id"), "number": row.number,
           "payment_url": created.get("payment_url"), "total": created.get("total"),
           "invoice_sent": False, "print": None}
    event(db, f"order:{row.id}", f"Order #{row.number} created in the hub for {model.code} {model.name}", actor=actor)
    if body.get("send_invoice"):
        try:
            w.send_order_details(created["id"], email)
            out["invoice_sent"] = True
            event(db, f"order:{row.id}", f"Invoice emailed to {email}", actor=actor)
        except Exception as e:  # noqa: BLE001
            out["invoice_error"] = (getattr(e, "message", None) or str(e))[:300]
    if body.get("print_now"):
        db.flush()
        out["print"] = farm.route_order(db, row.id, force=True)
    db.flush()
    return out


def send_invoice(db: Session, order: Order, actor: str) -> dict:
    from ..clients import woo
    from ..jobs import event
    woo().send_order_details(order.woo_order_id)
    event(db, f"order:{order.id}", f"Invoice emailed for order #{order.number}", actor=actor)
    return {"invoice_sent": True}
