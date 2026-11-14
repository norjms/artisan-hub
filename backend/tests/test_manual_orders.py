"""Create an order for a model from its page: price, Woo order, invoice, print now."""
import pytest

from tests.test_slicing import FakeSlicer, _setup_model


class FakeWoo:
    def __init__(self):
        self.created, self.invoices, self.next = [], [], 9000

    def create_order(self, data):
        self.next += 1
        self.created.append(data)
        li = []
        for i, x in enumerate(data["line_items"]):
            li.append({"id": self.next * 10 + i, "name": x.get("name") or "Product", "quantity": x["quantity"],
                       "sku": "" if "product_id" not in x else "SKU-FROM-WOO", "product_id": x.get("product_id", 0),
                       "meta_data": x.get("meta_data") or []})
        return {"id": self.next, "number": str(self.next), "status": "pending", "total": data["line_items"][0]["total"],
                "currency": "USD", "billing": data["billing"], "line_items": li, "meta_data": data["meta_data"],
                "payment_url": f"https://shop.example/checkout/order-pay/{self.next}/?key=wc_order_x"}

    def send_order_details(self, oid, email=None):
        self.invoices.append((oid, email))
        return {"message": "sent"}


class FarmForOrders:
    kind, label = "openprinthq", "OpenPrintHQ"

    def __init__(self):
        self.jobs, self.uploads = [], 0

    def has(self, cap):
        return cap in ("queue",)

    def upload_file(self, path, filename=None):
        self.uploads += 1
        return {"uuid": str(700 + self.uploads)}

    def submit_job(self, file_ref=None, quantity=1, job_name=None, **kw):
        self.jobs.append((str(file_ref), quantity, job_name))
        return {"job_ids": [len(self.jobs)]}


@pytest.fixture
def shop_rig(env, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app import clients
    from app.main import app
    from app.services import farm, shop, slicing
    w, f, sl = FakeWoo(), FarmForOrders(), FakeSlicer(tmp_path)
    monkeypatch.setattr(clients, "woo", lambda: w)
    monkeypatch.setattr(slicing, "client", lambda: sl)
    monkeypatch.setattr(farm, "_client", lambda: f)
    monkeypatch.setattr(shop, "farm", lambda: f)
    monkeypatch.setattr(clients, "farm", lambda: f)
    return TestClient(app), w, f


@pytest.fixture(autouse=True)
def order_printer(shop_rig, monkeypatch):
    """A printer profile used as the default for these tests only (the shared test database's real default is
    left alone, other tests rely on it)."""
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import PrinterProfile
    from app.services import slicing
    c, w, f = shop_rig
    with session_scope() as db:
        pid = db.execute(select(PrinterProfile.id).where(PrinterProfile.name == "Order H2D")).scalar()
    if not pid:
        pid = c.post("/api/printer-profiles", json={"name": "Order H2D", "engine": "orca",
                                                    "machine_preset": "Bambu Lab H2D 0.4 nozzle"}).json()["id"]
        with session_scope() as db:      # the first profile becomes the default on its own; this one must not
            db.get(PrinterProfile, pid).is_default = False
    monkeypatch.setattr(slicing, "default_profile", lambda db: db.get(PrinterProfile, pid))
    with session_scope() as db:
        db.get(PrinterProfile, pid).active = True
    yield pid
    with session_scope() as db:          # out of the way of later tests that fall back to the first active profile
        db.get(PrinterProfile, pid).active = False


def _approve_default(c, mid):
    from app.db import session_scope
    from app.services import slicing
    with session_scope() as db:
        slicing.slice_model(db, mid, slicing.default_profile(db).id, {"approve": True})


def test_price_from_slice_then_store(shop_rig):
    from app.db import session_scope
    from app.models import Model
    c, w, f = shop_rig
    mid = _setup_model(c, "Order Me", "om1")
    assert c.get(f"/api/models/{mid}/order-price").json()["source"] == "none"
    _approve_default(c, mid)
    s = c.get(f"/api/models/{mid}/order-price?quantity=2").json()
    assert s["source"] == "slice" and s["total"] > 0 and "markup" in s["detail"]
    with session_scope() as db:
        db.get(Model, mid).product.price = 19.5
    s = c.get(f"/api/models/{mid}/order-price?quantity=2").json()
    assert s == {**s, "source": "store", "unit": 19.5, "total": 39.0}


def test_create_order_invoice_and_mirror(shop_rig):
    from app.db import session_scope
    from app.models import Model, Order
    c, w, f = shop_rig
    mid = _setup_model(c, "Order Two", "om2")
    with session_scope() as db:
        sku = db.get(Model, mid).product.sku
    r = c.post(f"/api/models/{mid}/order", json={
        "customer": {"first_name": "Ana", "last_name": "Diaz", "email": "ana@example.com"},
        "quantity": 2, "total": 30, "note": "blue please", "send_invoice": True, "due_date": "2026-11-01"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["invoice_sent"] and out["payment_url"].startswith("https://shop.example/checkout/order-pay/")
    sent = w.created[-1]
    assert sent["status"] == "pending" and sent["billing"]["email"] == "ana@example.com"
    line = sent["line_items"][0]
    assert line["total"] == "30.00" and line["quantity"] == 2 and "product_id" not in line
    assert {"key": "_artisan_sku", "value": sku} in line["meta_data"]      # not on the store: SKU rides as meta
    assert sent["shipping_lines"][0]["method_id"] == "local_pickup" and sent["customer_note"] == "blue please"
    assert w.invoices[-1] == (out["woo_order_id"], "ana@example.com")
    with session_scope() as db:
        o = db.get(Order, out["order_id"])
        assert o.status == "pending" and o.customer == "Ana Diaz" and o.due_at.date().isoformat() == "2026-11-01"
        assert o.lines[0].sku == sku and o.lines[0].product_id is not None
    assert f.jobs == []                                                    # waits for payment
    assert c.post(f"/api/orders/{out['order_id']}/send-invoice").status_code == 200
    assert len(w.invoices) == 2


def test_print_now_and_validation(shop_rig):
    c, w, f = shop_rig
    mid = _setup_model(c, "Order Three", "om3")
    _approve_default(c, mid)
    bad = c.post(f"/api/models/{mid}/order", json={"customer": {"first_name": "", "last_name": ""}, "total": 5})
    assert bad.status_code == 400 and "name" in bad.text
    bad = c.post(f"/api/models/{mid}/order", json={"customer": {"first_name": "Bo"}, "total": 5, "send_invoice": True})
    assert bad.status_code == 400 and "email" in bad.text
    r = c.post(f"/api/models/{mid}/order", json={"customer": {"first_name": "Bo"}, "total": 12.5, "send_invoice": False,
                                                 "print_now": True, "fulfilment": "ship", "shipping_cost": 6,
                                                 "address": {"address_1": "1 Main St", "city": "Ames", "postcode": "50010"}})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["print"]["jobs"] == 1 and len(f.jobs) == 1 and f.jobs[0][1] == 1
    sent = w.created[-1]
    assert sent["shipping_lines"][0]["total"] == "6.00" and sent["shipping"]["city"] == "Ames"
