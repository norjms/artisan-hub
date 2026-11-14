"""Farm reports: waste and failures, printer use, profit per SKU and per printer-hour."""
from datetime import datetime, timedelta, timezone


def test_farm_and_profit_reports(env, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import session_scope
    from app.kv import set_setting
    from app.models import FarmJob, PlateCheck, Model, Product, BomPart, PartCache, Order, OrderLine, PrinterProfile, SliceResult
    from app.services import reports, slicing
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        for x in db.query(FarmJob).all():
            db.delete(x)
        db.flush()
        set_setting(db, "material_prices", {"PLA": 20})
        set_setting(db, "printer_hourly_rate", 0.5)
        m = Model(code="RP-1", name="Report Vase", slug="report-vase-rp-1")
        db.add(m)
        db.flush()
        mk = lambda i, st, g, hrs, **kw: FarmJob(external_id=f"r{i}", name=f"job {i}", status=st, printer_name=kw.pop("p", "H2D"),  # noqa: E731
                                                 model_id=m.id, filament_g=g, est_time_s=int(hrs * 3600), material="PLA",
                                                 started_at=now - timedelta(hours=hrs + 1), completed_at=now - timedelta(hours=1), **kw)
        db.add_all([mk(1, "completed", 100, 2), mk(2, "completed", 100, 2),
                    mk(3, "failed", 100, 2, progress=50, error="spaghetti"),
                    mk(4, "completed", 100, 2, p="MK4S"),
                    mk(5, "cancelled", 100, 2, progress=0, p="MK4S")])
        db.add(PlateCheck(printer_name="MK4S", external_job_id="r4", state="failed", reason="adhesion", job_outcome="completed"))
        db.flush()
    r = TestClient(app).get("/api/reports/farm?days=7").json()
    t = r["totals"]
    # r5 cancelled with no progress but it ran 2 h: counts as failed (its share from run time = 1)
    assert t["jobs"] == 5 and t["ok"] == 2 and t["failed"] == 3
    reasons = {x["reason"]: x["count"] for x in r["reasons"]}
    assert reasons["Lifted or warped"] == 1 and reasons["Failed in the farm: spaghetti"] == 1
    h2d = next(p for p in r["printers"] if p["printer"] == "H2D")
    assert h2d["jobs"] == 3 and h2d["ok"] == 2 and h2d["waste_g"] == 50.0       # half of the failed job's 100 g
    assert r["models"][0]["code"] == "RP-1" and r["models"][0]["failed"] == 3

    # profit: approved slice 40 g PLA, 2 h at 0.5/h; a part at 2.40 per pack of 4; price 12
    with session_scope() as db:
        prof = slicing.default_profile(db)
        if not prof:
            prof = PrinterProfile(name="P-rep", engine="orca", machine_preset="Bambu Lab H2D 0.4 nozzle", is_default=True)
            db.add(prof)
            db.flush()
        prof.hourly_rate = 0.5
        m = db.query(Model).filter_by(code="RP-1").one()
        r0 = slicing.result_for(db, m.id, prof.id)
        st = {"grams": 40, "time_s": 7200, "tools": [{"slot": 1, "grams": 40, "material": "PLA"}]}
        r0.state, r0.approved_file_ids, r0.approved_stats, r0.stats = "approved", [1], st, st
        p = Product(model_id=m.id, sku="RP-1", title="Report Vase", price=12.0)
        db.add(p)
        db.flush()
        db.add(PartCache(inventree_part_id=901, name="Magnet", suppliers=[{"price": 2.4, "pack_qty": 4}]))
        db.add(BomPart(product_id=p.id, inventree_part_id=901, name="Magnet", qty=2))
        o = Order(woo_order_id=8801, number="8801", status="completed", placed_at=now - timedelta(days=1))
        o.lines.append(OrderLine(sku="RP-1", qty=3))
        db.add(o)
        db.flush()
        pr = reports.profit(db, 30)
        row = next(x for x in pr["rows"] if x["sku"] == "RP-1")
        # material 40 g x 20/kg = 0.80, machine 2 h x 0.5 = 1.00, parts 2 x 0.60 = 1.20: cost 3.00, margin 9.00
        assert row["cost"] == 3.0 and row["margin"] == 9.0 and row["margin_pct"] == 75.0
        assert row["per_printer_hour"] == 4.5 and row["units_sold"] == 3 and row["profit"] == 27.0
    csv = TestClient(app).get("/api/reports/profit.csv?days=30")
    assert csv.status_code == 200 and "RP-1" in csv.text and csv.text.startswith("sku,title")
    assert TestClient(app).get("/api/reports/bogus.csv").status_code == 404
