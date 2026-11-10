"""Instant quotes: upload through the store's key, slice, price, order, review, farm."""
from tests.test_slicing import FakeSlicer

STL = b"solid t\n" + b"facet normal 0 0 1\n" * 10 + b"endsolid t\n"


def test_quote_flow(env, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import session_scope
    from app.jobs import HANDLERS
    from app.models import Job, Quote, PrinterProfile, OrderLine
    from app.services import slicing, quotes, shop, farm
    from app import tasks  # noqa: F401
    fake = FakeSlicer(tmp_path)
    monkeypatch.setattr(slicing, "client", lambda: fake)
    c = TestClient(app)
    with session_scope() as db:
        has_default = slicing.default_profile(db) is not None
    if not has_default:
        assert c.post("/api/printer-profiles", json={"name": "QH2D", "engine": "orca", "machine_preset": "Bambu Lab H2D 0.4 nozzle",
                                                     "is_default": True, "hourly_rate": 0.5}).status_code == 200
    # switched off: refused
    r = c.post("/api/store/quotes", files={"file": ("part.stl", STL)})
    assert r.status_code == 403
    s = c.put("/api/quotes/settings", json={"enabled": True, "markup": 2, "setup_fee": 3, "minimum": 10,
                                            "materials": [{"label": "PLA", "material": "PLA", "price_per_kg": 20,
                                                           "colours": [{"name": "Black", "hex": "#000000"}]},
                                                          {"label": "PETG", "material": "PETG", "price_per_kg": 30,
                                                           "colours": [{"name": "Blue", "hex": "#0000FF"}]}]})
    assert s.status_code == 200, s.text
    assert c.post("/api/store/quotes", files={"file": ("part.exe", STL)}).status_code == 400
    r = c.post("/api/store/quotes", files={"file": ("part.stl", STL)}, data={"customer_ip": "203.0.113.9"})
    assert r.status_code == 200, r.text
    tok = r.json()["token"]
    assert r.json()["status"] == "slicing"
    with session_scope() as db:
        j = db.query(Job).filter(Job.kind == "quote.slice").order_by(Job.id.desc()).first()
        out = HANDLERS["quote.slice"](db, j)
        assert out.get("ready"), out
    q = c.get(f"/api/store/quotes/{tok}").json()
    assert q["status"] == "ready" and "grams" in q["options"][0] and q["previews"]
    pla = next(o for o in q["options"] if o["key"] == "pla")
    with session_scope() as db:
        prof = db.get(PrinterProfile, db.query(Quote).filter_by(token=tok).one().printer_profile_id)
        rate = prof.hourly_rate if prof.hourly_rate is not None else 0
    # 42 g PLA at 20/kg = 0.84, 1 h machine; markup 2
    assert pla["unit_price"] == round((0.84 + rate) * 2, 2)
    r = c.post(f"/api/store/quotes/{tok}/choose", json={"material": "petg", "colour": "Blue", "quantity": 4}).json()
    unit = r["choice"]["unit_price"]
    assert r["choice"]["total"] == round(max(10, unit * 4 + 3), 2)
    assert c.post(f"/api/store/quotes/{tok}/choose", json={"material": "petg", "colour": "Pink"}).status_code == 400
    assert c.get(f"/api/store/quotes/{tok}/preview/{q['previews'][0].split('/')[-1]}").status_code == 200
    assert "material_cost" not in str(r)                      # no costs leak to the store

    # the paid order arrives with the quote on its line: waits for review, not routed
    calls = []

    class W:
        def upload_file(self, path, name):
            calls.append(("upload", name))
            return {"uuid": "wt-q-file"}

        def submit_job(self, **kw):
            calls.append(("submit", kw))
            return {"job_ids": [5501]}
    monkeypatch.setattr(farm, "_client", lambda: W())
    with session_scope() as db:
        from app.kv import set_setting
        set_setting(db, "order_routing", "hub")
        o = shop.upsert_order(db, {"id": 9901, "number": "9901", "status": "processing", "total": "20",
                                   "line_items": [{"id": 77, "sku": "AP-CUSTOM", "name": "Custom 3D print", "quantity": 1,
                                                   "meta_data": [{"key": "_artisan_quote", "value": tok}]}]})
        db.flush()
        qrow = db.query(Quote).filter_by(token=tok).one()
        assert qrow.status == "ordered" and qrow.order_id == o.id
        assert farm.route_order(db, o.id)["problems"] == []      # the quote line isn't treated as an unknown SKU
        qid = qrow.id
    lst = c.get("/api/quotes").json()
    assert [x["id"] for x in lst["waiting"]] == [qid] and lst["waiting"][0]["breakdown"]["material_cost"] is not None
    assert c.post(f"/api/quotes/{qid}/refuse", json={"reason": ""}).status_code == 400
    assert c.post(f"/api/quotes/{qid}/approve").json()["status"] == "approved"
    with session_scope() as db:
        j = db.query(Job).filter(Job.kind == "quote.produce").order_by(Job.id.desc()).first()
        out = HANDLERS["quote.produce"](db, j)
        assert out["jobs"] == [5501]
        assert calls[-1][1]["file_ref"] == "wt-q-file" and calls[-1][1]["quantity"] == 4
        assert fake.spec["filament_overrides"][0]["filament_colour"] == ["#0000FF"]
        ln = db.query(OrderLine).filter_by(woo_line_id=77).one()
        assert ln.wt_job_ids == [5501] and ln.order.routed_at is not None
        set_setting(db, "order_routing", "shim")
    assert c.post(f"/api/quotes/{qid}/approve").status_code == 400
