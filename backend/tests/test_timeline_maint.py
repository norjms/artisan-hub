"""Farm timeline (per-printer schedule, order due dates) and maintenance work orders."""
from datetime import datetime, timedelta, timezone


def test_timeline_orders_and_prioritise(env, monkeypatch):
    from app.db import session_scope
    from app.services import farm, schedule
    from app.models import FarmJob, Order, OrderLine, PlateCheck
    t0 = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
    printers = [{"name": "H2D", "online": True, "busy": True, "eta_minutes": 60, "ready": False},
                {"name": "H2C", "online": True, "busy": False, "ready": True},
                {"name": "MK4S", "online": False, "busy": False, "ready": False}]
    prios = []

    class W:
        def set_priority(self, jid, p):
            prios.append((jid, p))
    monkeypatch.setattr(farm, "_client", lambda: W())
    with session_scope() as db:
        for x in db.query(FarmJob).all():
            db.delete(x)
        for x in db.query(PlateCheck).filter_by(state="waiting").all():
            x.state = "skipped"
        db.flush()
        db.add_all([
            FarmJob(external_id="t1", name="now printing", status="printing", printer_name="H2D", started_at=t0 - timedelta(hours=1)),
            FarmJob(external_id="t2", name="big", status="queued", est_time_s=5 * 3600, priority=50, created_ext=t0 - timedelta(hours=3)),
            FarmJob(external_id="t3", name="urgent small", status="queued", est_time_s=3600, priority=100, created_ext=t0),
            FarmJob(external_id="t4", name="pinned to offline", status="assigned", printer_name="MK4S", est_time_s=600, created_ext=t0),
            FarmJob(external_id="t5", name="needs PETG", status="waiting_filament", waiting_reason="No printers have PETG", created_ext=t0),
            FarmJob(external_id="t6", name="order part", status="queued", est_time_s=2 * 3600, priority=50,
                    created_ext=t0 - timedelta(hours=1), order_refs=["7001"]),
        ])
        o = Order(woo_order_id=7001, number="7001", status="processing", placed_at=t0 - timedelta(days=3, hours=1),
                  routed_at=t0)
        o.lines.append(OrderLine(sku="X", qty=1, wt_job_ids=["t6"]))
        db.add(o)
        db.flush()
        tl = schedule.build(db, printers=printers, at=t0)
        lanes = {l["printer"]: l for l in tl["lanes"]}
        h2c = [b["job"] for b in lanes["H2C"]["bars"]]
        h2d = [b["job"] for b in lanes["H2D"]["bars"]]
        assert h2c[0] == "t3"                       # highest priority first, on the printer free soonest
        assert h2d[0] == "t1" and "t2" in h2d + h2c and "t6" in h2d + h2c
        why = {u["job"]: u["why"] for u in tl["unscheduled"]}
        assert why["t4"] == "MK4S is offline" and why["t5"] == "No printers have PETG"
        od = next(x for x in tl["orders"] if x["number"] == "7001")
        assert od["state"] == "late"                 # placed 3 days and 1 hour ago, 3-day default: already past due
        # an explicit later due date puts it on time
        o.due_at = t0 + timedelta(days=2)
        tl = schedule.build(db, printers=printers, at=t0)
        assert next(x for x in tl["orders"] if x["number"] == "7001")["state"] == "on_time"
        out = schedule.prioritise(db, o.id, 100)
        assert out["raised"] == ["t6"] and prios == [("t6", 100)]
        assert db.query(FarmJob).filter_by(external_id="t6").one().priority == 100


def test_maintenance_plans_alarms_and_parts(env, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import session_scope
    from app.services import maintenance, inventory, farm
    from app.models import FarmJob, MaintPlan, MaintTask
    removed = []

    class Inv:
        def stock(self, part, in_stock):
            return [{"pk": 1, "quantity": 3}, {"pk": 2, "quantity": 5}]

        def remove_stock(self, batch, note):
            removed.append(batch)
    monkeypatch.setattr(inventory, "inventree", lambda: Inv())
    c = TestClient(app)
    old = (datetime.now(timezone.utc) - timedelta(days=1)).replace(tzinfo=None).isoformat()
    r = c.post("/api/maintenance/plans", json={"printer": "H2C", "title": "Clean and lube rods", "every_hours": 10,
                                                "parts": [{"part_id": 42, "qty": 4, "name": "Lube sachet"}], "last_done_at": old})
    assert r.status_code == 200, r.text
    assert r.json()["due"] is False and r.json()["hours_since"] == 0
    assert c.post("/api/maintenance/plans", json={"printer": "H2C", "title": "x"}).status_code == 400
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        db.add(FarmJob(external_id="m1", name="long", status="completed", printer_name="H2C",
                       started_at=now - timedelta(hours=12), completed_at=now - timedelta(hours=1)))
        db.flush()
        out = maintenance.check_plans(db)
        assert len(out["opened"]) == 1
        assert maintenance.check_plans(db)["opened"] == []          # one open task per plan
        tid = out["opened"][0]
        # an alarm opens a task once per printer and code
        farm.ingest_event(db, "alarm.raised", {"printer_name": "H2D", "code": "0300-8003", "message": "Nozzle clog"})
        farm.ingest_event(db, "alarm.raised", {"printer_name": "H2D", "code": "0300-8003", "message": "Nozzle clog"})
        assert db.query(MaintTask).filter_by(source="alarm", printer_name="H2D").count() == 1
    ov = c.get("/api/maintenance").json()
    assert {t["source"] for t in ov["open"]} >= {"plan", "alarm"}
    r = c.post(f"/api/maintenance/tasks/{tid}/done", json={"notes": "done"}).json()
    assert r["state"] == "done" and removed == [[{"pk": 1, "quantity": 3}, {"pk": 2, "quantity": 1}]]
    assert not r["parts_error"]
    with session_scope() as db:
        p = db.query(MaintPlan).one()
        assert p.last_done_at is not None and maintenance.plan_status(db, p)["due"] is False
    # by hand, more parts than stock: done, and the shortfall is reported
    t = c.post("/api/maintenance/tasks", json={"printer": "voron01", "title": "Swap nozzle",
                                               "parts": [{"part_id": 7, "qty": 10, "name": "0.6 nozzle"}]}).json()
    r = c.post(f"/api/maintenance/tasks/{t['id']}/done", json={}).json()
    assert r["state"] == "done" and "2 not in stock" in r["parts_error"]
    assert c.post(f"/api/maintenance/tasks/{t['id']}/done", json={}).status_code == 400


def test_maintenance_linked_with_legacy_farm(env, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import session_scope
    from app.services import maintenance, inventory, schedule
    from app.models import MaintPlan, MaintTask
    calls = []

    class W:
        rows = [{"id": 91, "printer_name": "MK4S", "task_name": "Clean print bed", "interval_hours": 50.0, "interval_days": None,
                 "description": "IPA wipe", "category": "cleaning", "status": "overdue", "hours_since_last": 61.0,
                 "last_completed_at": "2026-10-01 10:00:00", "last_completed_by": "james", "enabled": 1},
                {"id": 92, "printer_name": "MK4S", "task_name": "Check belt tension", "interval_hours": 300.0, "interval_days": None,
                 "description": "", "category": "inspection", "status": "no_data", "hours_since_last": None,
                 "last_completed_at": None, "enabled": 1}]
        down = []

        def maint_schedules(self):
            return self.rows

        def maint_complete(self, sid, by, notes="", minutes=None):
            calls.append(("complete", sid, by))

        def maint_log(self, printer, task, cat, by, notes="", minutes=None):
            calls.append(("log", printer, task, cat))

        def maint_down(self):
            return [{"printer_name": n} for n in self.down]

        def set_down(self, printer, down, reason=""):
            calls.append(("down", printer, down))
            self.down = [printer] if down else []
    w = W()
    monkeypatch.setattr(maintenance, "_wt", lambda: w)
    monkeypatch.setattr(maintenance, "_wt_on", lambda: True)
    monkeypatch.setattr(inventory, "inventree", lambda: type("I", (), {"stock": lambda self, part, in_stock: [{"pk": 1, "quantity": 9}],
                                                                      "remove_stock": lambda self, b, n: None})())
    c = TestClient(app)
    out = c.post("/api/maintenance/sync").json()
    assert out["legacy_farm"]["added"] == 2 and len(out["opened"]) == 1          # the overdue one opens a task
    with session_scope() as db:
        bed = db.query(MaintPlan).filter_by(wt_schedule_id=91).one()
        belts = db.query(MaintPlan).filter_by(wt_schedule_id=92).one()
        assert maintenance.plan_status(db, bed)["hours_since"] == 61.0
        assert maintenance.plan_status(db, belts)["due"] is False
        tid = db.query(MaintTask).filter_by(plan_id=bed.id).one().id
        bid, beltid = bed.id, belts.id
    # the hub keeps parts on a linked plan, but its interval stays Legacy farm's; it can't be removed here
    r = c.put(f"/api/maintenance/plans/{bid}", json={"printer": "MK4S", "title": "x", "every_hours": 5,
                                                      "parts": [{"part_id": 3, "qty": 1, "name": "IPA"}], "active": True})
    assert r.json()["every_hours"] == 50.0 and r.json()["parts"][0]["name"] == "IPA" and r.json()["legacy_farm"]
    assert c.delete(f"/api/maintenance/plans/{bid}").status_code == 400
    # out of service, then done: Legacy farm schedule completed and the printer comes back
    assert c.post(f"/api/maintenance/tasks/{tid}/service", json={"out_of_service": True}).json()["out_of_service"]
    with session_scope() as db:
        lanes = schedule.build(db, printers=[{"name": "MK4S", "online": True, "busy": False, "ready": True}])["lanes"]
        assert next(l for l in lanes if l["printer"] == "MK4S")["down"]
    r = c.post(f"/api/maintenance/tasks/{tid}/done", json={"notes": "wiped"}).json()
    assert r["wt_note"] == "Completed in Legacy farm" and not r["out_of_service"]
    assert ("complete", 91, "test") in [(a, b, "test") for (a, b, *_) in calls if a == "complete"] and ("down", "MK4S", False) in calls
    # a hand-made task is logged in Legacy farm's maintenance log
    t = c.post("/api/maintenance/tasks", json={"printer": "MK4S", "title": "Replace PTFE tube"}).json()
    assert c.post(f"/api/maintenance/tasks/{t['id']}/done", json={}).json()["wt_note"] == "Logged in Legacy farm"
    assert ("log", "MK4S", "Replace PTFE tube", "inspection") in calls
    # completed in Legacy farm: the hub's open task closes, without taking parts
    with session_scope() as db:
        t2 = MaintTask(printer_name="MK4S", title="Check belt tension", source="plan", plan_id=beltid)
        db.add(t2)
        db.flush()
        t2id = t2.id
    w.rows[1] = {**w.rows[1], "status": "ok", "hours_since_last": 0.0, "last_completed_at": "2099-01-01 00:00:00",
                 "last_completed_by": "james"}
    c.post("/api/maintenance/sync")
    with session_scope() as db:
        t2 = db.get(MaintTask, t2id)
        assert t2.state == "done" and t2.done_by == "james" and "not taken" in t2.wt_note
