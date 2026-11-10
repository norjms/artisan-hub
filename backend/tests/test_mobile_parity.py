"""The phone app gets the same plate, service, print-now and order features as the web."""
from tests.test_manual_orders import FakeWoo, FarmForOrders, order_printer, shop_rig  # noqa: F401  (fixtures)
from tests.test_plate_reslice import FakeFarm, rig  # noqa: F401  (fixture)
from tests.test_slicing import _setup_model

M = "/api/mobile/v1"


def test_plate_and_service_from_the_app(rig, monkeypatch):
    from app.routers import mobile_control
    c, fake, farm = rig
    calls = []
    farm.set_down = lambda name, down, reason="": calls.append((name, down))
    monkeypatch.setattr(mobile_control, "_farm", lambda: farm)
    pid = c.post("/api/printer-profiles", json={"name": "App H2D", "engine": "orca", "wt_printer": "AppH2D",
                                                "machine_preset": "Bambu Lab H2D 0.4 nozzle"}).json()["id"]
    r = c.get(f"{M}/printers/apph2d/plate")                   # any case
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["installed"] is None and len(body["catalog"]) >= 40
    cfx = next(x for x in body["catalog"] if x["name"] == "Darkmoon CFX")
    r = c.put(f"{M}/printers/AppH2D/plate", json={"plate_id": cfx["id"]})
    assert r.status_code == 200 and r.json()["plate"] == "Darkmoon CFX"
    assert ("marker", "AppH2D", False) in farm.calls
    got = c.get(f"{M}/printers/AppH2D/plate").json()["installed"]
    assert got["name"] == "Darkmoon CFX" and got["slices_as"] == "High Temp Plate"
    assert c.post(f"{M}/printers/AppH2D/service", json={"down": True, "reason": "belt"}).status_code == 200
    assert calls == [("AppH2D", True)]
    assert c.get(f"{M}/printers/nobody/plate").status_code == 404
    assert pid


def test_print_options_and_order_from_the_app(shop_rig):
    from app.db import session_scope
    from app.models import Model
    from app.services import slicing
    c, w, f = shop_rig
    mid = _setup_model(c, "App Order", "ap1")
    with session_scope() as db:
        slicing.slice_model(db, mid, slicing.default_profile(db).id, {"approve": True})
        prod_id = db.get(Model, mid).product.id
    o = c.get(f"{M}/products/{prod_id}/print-options").json()
    assert o["printers"] and o["printers"][0]["time_s"] and o["price"]["source"] == "slice"
    r = c.post(f"{M}/products/{prod_id}/order", json={"customer": {"first_name": "Cy", "email": "cy@example.com"},
                                                       "total": 20, "send_invoice": True})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["invoice_sent"] and out["payment_url"]
    assert c.post(f"{M}/orders/{out['order_id']}/invoice").status_code == 200 and len(w.invoices) == 2
    assert c.post(f"{M}/products/{prod_id}/order", json={"customer": {}, "total": "x"}).status_code == 422
