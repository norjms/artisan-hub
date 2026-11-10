"""Slicing phase 2: automation (auto-slice imports, auto-approve, nightly), store gating, cost per unit,
real-print records (manual and from Watchtower), the PrusaSlicer to OrcaSlicer preset converter, and settings."""
import json

from tests.test_slicing import FakeSlicer, _setup_model


def _printer(c, name, **kw):
    body = {"name": name, "engine": "orca", "machine_preset": "Bambu Lab H2D 0.4 nozzle", "is_default": True, **kw}
    r = c.post("/api/printer-profiles", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_auto_slice_auto_approve_and_nightly(env, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from app.main import app
    from app.db import session_scope
    from app.models import Job, Model, SliceResult
    from app.services import slicing
    from app import clients
    fake = FakeSlicer(tmp_path)
    monkeypatch.setattr(slicing, "client", lambda: fake)
    monkeypatch.setattr(clients, "slicer_configured", lambda: True)
    c = TestClient(app)
    pid = _printer(c, "P2-H2D")
    mid = _setup_model(c, "Auto Slice", "a1")
    with session_scope() as db:
        slicing.after_import(db, db.get(Model, mid))
        jobs = list(db.execute(select(Job).where(Job.kind == "slice.model", Job.ref == f"model:{mid}:slice:{pid}")).scalars())
        assert len(jobs) == 1 and jobs[0].created_by == "auto-slice"
        assert db.execute(select(Job).where(Job.kind == "slice.inspect", Job.ref == f"model:{mid}:inspect")).first()
        # a second import does not queue it again
        slicing.after_import(db, db.get(Model, mid))
        assert len(list(db.execute(select(Job).where(Job.kind == "slice.model", Job.ref == f"model:{mid}:slice:{pid}")).scalars())) == 1

    assert c.put("/api/settings", json={"slice_auto_new": "off"}).status_code == 200
    mid2 = _setup_model(c, "No Auto", "a2")
    with session_scope() as db:
        slicing.after_import(db, db.get(Model, mid2))
        assert not db.execute(select(Job).where(Job.kind == "slice.model", Job.ref == f"model:{mid2}:slice:{pid}")).first()

    # first slice is approved by hand; an identical re-slice is approved automatically when the team allows it
    with session_scope() as db:
        r = slicing.slice_model(db, mid, pid, {})
        db.commit()
        slicing.approve(db, db.get(SliceResult, r["result_id"]), "test")
        db.commit()
    assert c.put("/api/settings", json={"slice_auto_approve": True, "slice_auto_approve_pct": 5}).status_code == 200
    with session_scope() as db:
        r2 = slicing.slice_model(db, mid, pid, {})
        db.commit()
        assert r2.get("auto_approved") and db.get(SliceResult, r2["result_id"]).state == "approved"
    c.put("/api/settings", json={"slice_auto_approve": False})

    # "approve every slice that finishes without problems"
    mid3 = _setup_model(c, "Clean Slice", "a3")
    assert c.put("/api/settings", json={"slice_auto_approve_clean": True}).status_code == 200
    with session_scope() as db:
        r3 = slicing.slice_model(db, mid3, pid, {})
        db.commit()
        assert r3.get("auto_approved") and db.get(SliceResult, r3["result_id"]).state == "approved"
    c.put("/api/settings", json={"slice_auto_approve_clean": False})

    # nightly: a slicer upgrade makes it stale and the nightly run queues it again
    with session_scope() as db:
        fake.version = "v9"
        out = slicing.nightly(db, force=True)
        assert out["requeued"] >= 1
        assert db.get(SliceResult, r2["result_id"]).state == "queued"
        assert slicing.nightly(db)["skipped"]          # already ran today / not the hour
        fake.version = "v2.5.0-alpha"


def test_store_gate_cost_and_prints(env, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import session_scope
    from app.models import Model, Product, BomFilament, PrinterProfile, SliceResult
    from app.services import slicing, shop
    from app.jobs import HANDLERS
    from app import tasks  # noqa: F401
    fake = FakeSlicer(tmp_path)
    monkeypatch.setattr(slicing, "client", lambda: fake)
    c = TestClient(app)
    pid = _printer(c, "P2-Cost", hourly_rate=1.2)
    mid = _setup_model(c, "Costly", "c1")
    assert c.put("/api/settings", json={"material_prices": {"pla": 25}, "require_approved_slice": True}).status_code == 200
    with session_scope() as db:
        m = db.get(Model, mid)
        if not m.product:
            m.product = Product(sku="COST-1", title="costly")
        m.product.filaments = [BomFilament(slot=1, label="Body", material="PLA", waste_pct=0)]
        db.flush()
        problems = shop.validate_for_push(m.product, True, switching_live=True)
        assert any("no approved slice" in p for p in problems)
        assert not any("no approved slice" in p for p in shop.validate_for_push(m.product, True))   # re-push of a live product
        r = slicing.slice_model(db, mid, pid, {})
        db.commit()
        res = db.get(SliceResult, r["result_id"])
        cost = slicing.cost_of(db, m, db.get(PrinterProfile, pid), res.stats)
        # 42 g of PLA at 25/kg plus 1 h at 1.20/h
        assert cost == {"material": 1.05, "machine": 1.2, "total": 2.25, "rate": 1.2, "prices": ["team price"]}
        slicing.approve(db, res, "test")
        db.commit()
        assert not any("no approved slice" in p for p in shop.validate_for_push(db.get(Model, mid).product, True, switching_live=True))
        rid = res.id
        fname = [f for f in db.get(Model, mid).files if f.id in res.approved_file_ids][0].filename

    st = c.get(f"/api/models/{mid}/slicing").json()
    card = next(p for p in st["printers"] if p["profile_id"] == pid)
    assert card["cost"]["total"] == 2.25 and card["prints"]["ok"] == 0
    g = c.get("/api/slicing/grid?q=Costly").json()
    assert any(cell["cost"] == 2.25 for row in g["rows"] for cell in row["cells"])

    # by hand: two failures in a row raise a problem; a success verifies it
    for _ in range(2):
        assert c.post(f"/api/slice-results/{rid}/prints", json={"outcome": "failed", "note": "spaghetti"}).status_code == 200
    card = next(p for p in c.get(f"/api/models/{mid}/slicing").json()["printers"] if p["profile_id"] == pid)
    assert card["prints"]["failed"] == 2 and any(x["code"] == "prints" for x in card["problems"])
    assert c.post(f"/api/slice-results/{rid}/prints", json={"outcome": "bogus"}).status_code == 400

    # from Watchtower: the job name carries the sliced file name
    import app.clients as cl
    monkeypatch.setattr(cl, "watchtower", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    with session_scope() as db:
        from app.models import Job
        j = Job(kind="slicing.farm_job", payload={"event": "queue.job_completed",
                                                  "data": {"job_id": 777, "job_name": "COST-1__" + fname, "printer_name": "h2d-1"}})
        db.add(j)
        db.flush()
        out = HANDLERS["slicing.farm_job"](db, j)
        assert out["recorded"] and out["result_id"] == rid
        again = HANDLERS["slicing.farm_job"](db, j)          # the same job twice is recorded once
        assert not again["recorded"]
    card = next(p for p in c.get(f"/api/models/{mid}/slicing").json()["printers"] if p["profile_id"] == pid)
    assert card["prints"]["ok"] == 1 and card["prints"]["verified"]
    assert not any(x["code"] == "prints" for x in card["problems"])
    assert card["prints"]["recent"][0]["source"] == "watchtower"


def test_convert_ini_and_settings(env):
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    ini = ("bed_shape = 0x0,500x0,500x400,0x400\nmax_print_height = 300\nnozzle_diameter = 0.4,0.4\n"
           "start_gcode = M104 S[first_layer_temperature] ; temperature here\\nG28\nretract_length = 0.8,0.8\n"
           "fill_density = 15%\nprinter_model = COREONEL\nhost_type = prusalink\n")
    r = c.post("/api/slicer/convert-ini", json={"ini": ini, "base": "Prusa CORE One 0.4 nozzle"})
    assert r.status_code == 200, r.text
    out = r.json()
    cfg = json.loads(out["config"])
    assert cfg["inherits"] == "Prusa CORE One 0.4 nozzle" and cfg["type"] == "machine" and out["tools"] == 2
    assert cfg["printable_area"] == ["0x0", "500x0", "500x400", "0x400"] and cfg["printable_height"] == "300"
    assert "machine_start_gcode" not in cfg            # by default the G-code comes from the OrcaSlicer base printer
    kept = c.post("/api/slicer/convert-ini", json={"ini": ini + "nozzle_high_flow = 1,0\nend_gcode = {if nozzle_high_flow[1]}M1{endif}\n",
                                                   "base": "Prusa CORE One 0.4 nozzle", "keep_gcode": True}).json()
    kcfg = json.loads(kept["config"])
    assert kcfg["machine_start_gcode"] == "M104 S[nozzle_temperature_initial_layer] ; temperature here\nG28"
    assert kcfg["machine_end_gcode"] == "{if false}M1{endif}"
    assert cfg["retraction_length"] == ["0.8", "0.8"] and "fill_density" not in cfg and out["ignored"] == 1
    assert c.post("/api/slicer/convert-ini", json={"ini": "nothing here"}).status_code == 400
    # the imported Orca preset is accepted as a printer
    p = c.post("/api/printer-profiles", json={"name": "L+ via Orca", "engine": "orca", "machine_config": out["config"],
                                              "machine_preset": "Prusa CORE One 0.4 nozzle", "is_default": False})
    assert p.status_code == 200 and p.json()["imported"]
    assert c.put("/api/settings", json={"timezone": "Mars/Olympus"}).status_code == 400
    assert c.put("/api/settings", json={"slice_auto_new": "sometimes"}).status_code == 400
    s = c.put("/api/settings", json={"timezone": "America/Chicago", "slice_night_hour": 3}).json()
    assert s["timezone"] == "America/Chicago" and s["slice_night_hour"] == 3 and s["slice_auto_new"] in ("off", "default", "all")


def test_review_queue_bulk(env, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import session_scope
    from app.models import SliceResult
    from app.services import slicing
    fake = FakeSlicer(tmp_path)
    monkeypatch.setattr(slicing, "client", lambda: fake)
    c = TestClient(app)
    pid = _printer(c, "P2-Review")
    mids = [_setup_model(c, f"Review {i}", f"rv{i}") for i in range(3)]
    q = c.post("/api/slicing/slice-all", json={"profile_id": pid}).json()
    assert q["queued"] >= 3
    rids = []
    with session_scope() as db:
        for mid in mids:
            rids.append(slicing.slice_model(db, mid, pid, {})["result_id"])
        db.commit()
    rv = c.get(f"/api/slicing/review?profile_id={pid}").json()
    ids = {x["id"] for x in rv["items"]}
    assert set(rids) <= ids and rv["total"] >= 3
    item = next(x for x in rv["items"] if x["id"] == rids[0])
    assert item["previews"] and item["per_unit_g"] == 42.0 and item["cost"] and item["printer"] == "P2-Review"
    out = c.post("/api/slice-results/bulk-approve", json={"ids": rids[:2] + [999999]}).json()
    assert out["approved"] == 2
    again = c.post("/api/slice-results/bulk-approve", json={"ids": rids[:1]}).json()
    assert again["approved"] == 0 and again["skipped"]
    assert c.post("/api/slice-results/bulk-reject", json={"ids": [rids[2]]}).json()["discarded"] == 1
    with session_scope() as db:
        assert db.get(SliceResult, rids[0]).state == "approved" and db.get(SliceResult, rids[2]) is None
    left = {x["id"] for x in c.get(f"/api/slicing/review?profile_id={pid}").json()["items"]}
    assert not (set(rids) & left)
    # slicing the library again skips models that already have a result for that printer
    assert c.post("/api/slicing/slice-all", json={"profile_id": pid}).json()["queued"] < q["queued"]
