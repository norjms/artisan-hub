"""Farm edge: queue mirror from farm webhooks and syncs, plate checks after each print."""
import pytest


class FakeFarm:
    def __init__(self):
        self.commands, self.submitted = [], []
        self.active, self.done = [], []
        self.public = {}
        self.printers_list = [{"name": "H2D", "online": True, "ready": False, "busy": False, "state": "Idle"},
                              {"name": "MK4S", "online": False, "ready": False, "busy": False, "state": "Offline"}]

    def command(self, printer, command, args=None, confirm=False):
        if printer == "OFF":
            from app.clients.base import ApiError
            raise ApiError("openprinthq", 409, "STATE_UNKNOWN")
        self.commands.append((printer, command))
        return {"accepted": True}

    def submit_job(self, file_ref=None, quantity=1, job_name=None, priority=None):
        self.submitted.append((file_ref, job_name, priority))
        return {"job_ids": [901]}

    def queue(self, statuses=None, limit=200, offset=0, sort=None):
        if statuses and "completed" in statuses:
            return {"jobs": self.done}
        return {"jobs": self.active}

    def job(self, jid):
        if jid in self.public:
            return self.public[jid]
        from app.clients.base import ApiError
        raise ApiError("openprinthq", 404, "NOT_FOUND")

    def printers(self):
        return {"printers": self.printers_list}

    def cancel_queued(self, job_id, reason):
        self.cancelled = getattr(self, "cancelled", []) + [job_id]
        return {}

    def snapshot(self, printer):
        return b"\xff\xd8fakejpeg"


def _priv(jid, status, printer=None, name="Widget_PLA_1h.gcode", **kw):
    return {"id": jid, "name": name, "status": status, "printer_id": printer, "priority": 50, "source": "queue_tab",
            "file_path": f"vfs://database/gcode/uuid-{jid}", "linked_orders": [], "material_type": "PLA",
            "metadata": {"filament_used_g": 12.5, "print_time_seconds": 3600, "quantity_required": 1,
                         **kw.pop("metadata", {})},
            "created_at": "2026-10-20T10:00:00", "started_at": kw.get("started_at"), "completed_at": kw.get("completed_at"),
            "progress": kw.get("progress"), "error_message": kw.get("error")}


@pytest.fixture
def farm_fake(monkeypatch):
    from app.services import farm
    from app.db import session_scope
    from app.kv import set_setting
    fake = FakeFarm()
    monkeypatch.setattr(farm, "_client", lambda: fake)
    with session_scope() as db:
        set_setting(db, "stagger_minutes", 0)
    return fake


def test_events_open_and_pass_check(env, farm_fake):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import session_scope
    from app.services import farm
    from app.models import PlateCheck
    with session_scope() as db:
        farm.ingest_event(db, "queue.job_submitted", {"job_id": 501, "job_name": "Widget_PLA_1h.gcode"})
        farm.ingest_event(db, "queue.job_printing", {"job_id": 501, "printer_name": "H2D", "timestamp": "2026-10-20T11:00:00Z"})
        assert not db.query(PlateCheck).count()
        out = farm.ingest_event(db, "queue.job_completed", {"job_id": 501, "printer_name": "H2D",
                                                            "timestamp": "2026-10-20T12:00:00Z"})
        assert out["status"] == "completed" and out["was"] == "printing"
        # a late or repeated event neither reopens the job nor opens a second check
        farm.ingest_event(db, "queue.job_printing", {"job_id": 501, "printer_name": "H2D"})
        farm.ingest_event(db, "queue.job_completed", {"job_id": 501, "printer_name": "H2D"})
        checks = db.query(PlateCheck).all()
        assert len(checks) == 1 and checks[0].printer_name == "H2D" and checks[0].state == "waiting"
        cid = checks[0].id
    c = TestClient(app)
    ov = c.get("/api/farm").json()
    assert [x["id"] for x in ov["checks"]] == [cid]
    assert c.get("/api/stats").json()["plate_checks"] == 1
    r = c.post(f"/api/plate-checks/{cid}/pass", json={"set_ready": True})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "passed" and r.json()["ready_set"]
    assert farm_fake.commands == [("H2D", "set_ready")]
    assert c.post(f"/api/plate-checks/{cid}/pass", json={}).status_code == 400     # only once
    assert c.get("/api/stats").json()["plate_checks"] == 0


def test_fail_with_reprint_and_ready_error(env, farm_fake):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import session_scope
    from app.services import farm
    farm_fake.active = [_priv(601, "printing", "OFF", started_at="2026-10-21T00:00:00")]
    with session_scope() as db:
        farm.sync(db)
        farm_fake.active = []
        farm_fake.done = [_priv(601, "failed", "OFF", started_at="2026-10-21T00:00:00", completed_at="2026-10-21T01:00:00",
                         error="spaghetti detected")]
        out = farm.sync(db)
        assert out["changed"] == 1
        from app.models import PlateCheck, FarmJob
        chk = db.query(PlateCheck).filter_by(external_job_id="601").one()
        assert chk.job_outcome == "failed"
        fj = db.query(FarmJob).filter_by(external_id="601").one()
        assert fj.file_ref == "uuid-601" and fj.filament_g == 12.5 and fj.error == "spaghetti detected"
        cid = chk.id
    c = TestClient(app)
    assert c.post(f"/api/plate-checks/{cid}/fail", json={"reason": "nope"}).status_code == 400
    r = c.post(f"/api/plate-checks/{cid}/fail", json={"reason": "spaghetti", "note": "came loose at layer 20",
                                                      "reprint": True, "set_ready": True}).json()
    assert r["state"] == "failed" and r["reprint_job_id"] == "901"
    assert farm_fake.submitted == [("uuid-601", "Widget_PLA_1h.gcode", 50)]
    assert not r["ready_set"] and "STATE_UNKNOWN" in r["ready_error"]      # printer offline: Ready not set, said so
    assert c.post(f"/api/plate-checks/{cid}/retry-ready").json()["ready_set"] is False


def test_sync_history_blocked_and_skips(env, farm_fake):
    from app.db import session_scope
    from app.services import farm
    from app.models import PlateCheck, FarmJob
    # long-finished history seen for the first time opens no check
    farm_fake.done = [_priv(700, "completed", "H2D", completed_at="2020-01-01T00:00:00")]
    farm_fake.active = [_priv(701, "waiting_filament", None, metadata={"filament_rejection": "No printers have PETG #FF0000"}),
                 _priv(702, "queued", None), _priv(703, "printing", "MK4S", started_at="2026-10-21T00:00:00")]
    with session_scope() as db:
        farm.sync(db)
        assert not db.query(PlateCheck).filter_by(external_job_id="700").count()
        ov = farm.overview(db)
        why = {q["external_id"]: q["blocked"] for q in ov["queue"]}
        assert why["701"] == "No printers have PETG #FF0000"
        assert why["702"].startswith("No printer is Ready") and "offline: MK4S" in why["702"]
        # a job that vanished from the queue and from the farm is marked deleted
        farm_fake.active = [_priv(701, "waiting_filament", None), _priv(703, "printing", "MK4S")]
        farm.sync(db)
        assert db.query(FarmJob).filter_by(external_id="702").one().status == "deleted"
        # a waiting check is skipped once its printer starts another print (its plate was cleared in the farm)
        farm.ingest_event(db, "queue.job_completed", {"job_id": 703, "printer_name": "MK4S"})
        chk = db.query(PlateCheck).filter_by(external_job_id="703").one()
        farm.ingest_event(db, "queue.job_printing", {"job_id": 704, "printer_name": "MK4S"})
        assert chk.state == "skipped"
        # plate checks off: nothing opens
        from app.kv import set_setting
        set_setting(db, "plate_checks", False)
        farm.ingest_event(db, "queue.job_completed", {"job_id": 704, "printer_name": "MK4S"})
        assert not db.query(PlateCheck).filter_by(external_job_id="704").count()
        set_setting(db, "plate_checks", True)


def test_staggered_ready(env, farm_fake):
    from datetime import datetime, timezone
    from app.db import session_scope
    from app.services import farm
    from app.kv import set_setting
    from app.models import PlateCheck, Job
    with session_scope() as db:
        set_setting(db, "stagger_minutes", 4)
        set_setting(db, "farm_last_start", None)
        set_setting(db, "farm_last_ready", None)
        a = PlateCheck(printer_name="H2D", external_job_id="s1", state="waiting", job_outcome="completed")
        b = PlateCheck(printer_name="H2C", external_job_id="s2", state="waiting", job_outcome="completed")
        c = PlateCheck(printer_name="voron01", external_job_id="s3", state="waiting", job_outcome="completed")
        db.add_all([a, b, c])
        db.flush()
        farm.pass_check(db, a, "t")
        farm.pass_check(db, b, "t")
        farm.pass_check(db, c, "t")
        assert a.ready_set and not b.ready_set and not c.ready_set
        gap_b = (b.ready_due - datetime.now(timezone.utc)).total_seconds()
        gap_c = (c.ready_due - b.ready_due).total_seconds()
        assert 200 < gap_b <= 241 and abs(gap_c - 240) < 2           # one printer every 4 minutes
        assert farm_fake.commands == [("H2D", "set_ready")]
        jobs = db.query(Job).filter(Job.kind == "farm.set_ready").all()
        assert len(jobs) == 2
        assert farm.ready_due(db, b.id)["ready"] and b.ready_set and b.ready_due is None
        set_setting(db, "stagger_minutes", 0)


def test_alert_rules_and_quiet_hours(env, monkeypatch):
    from datetime import datetime, timezone
    from app.db import session_scope
    from app.services import alerts
    from app.kv import set_setting
    from app.models import Job
    monkeypatch.setattr(alerts, "configured", lambda: ["email", "ntfy", "slack"])
    with session_scope() as db:
        r = alerts.rules(db)
        assert "email" in r["events"]["print_failed"] and "email" not in r["events"]["print_done"]
        # quiet 22:00 to 07:00 Chicago; 04:00 UTC is 23:00 there
        assert alerts.quiet_now(r, datetime(2026, 10, 21, 4, 0, tzinfo=timezone.utc))
        assert not alerts.quiet_now(r, datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc))
        with pytest.raises(ValueError):
            alerts.clean_rules({"quiet_start": "late"})
        set_setting(db, "alert_rules", {**r, **alerts.clean_rules({"quiet_mode": "off", "events": {"print_done": ["ntfy", "bogus"]}})})
        assert alerts.rules(db)["events"]["print_done"] == ["ntfy"]
        assert alerts.notify(db, "print_done", "H2D finished", "x") == ["ntfy"]
        set_setting(db, "alert_rules", {**alerts.rules(db), "quiet_mode": "none", "quiet_start": "00:00", "quiet_end": "23:59"})
        assert alerts.notify(db, "print_failed", "H2D failed", "x") == []
        set_setting(db, "alert_rules", None)
    sent = []

    class R:
        def raise_for_status(self):
            pass
    monkeypatch.setattr(alerts.httpx, "post", lambda url, **kw: sent.append(("post", url, kw.get("headers"))) or R())
    monkeypatch.setattr(alerts.httpx, "put", lambda url, **kw: sent.append(("put", url, kw.get("headers"))) or R())
    monkeypatch.setattr(alerts, "_conn", lambda: {"ntfy_url": "https://ntfy.example/farm", "ntfy_token": "tk",
                                                  "slack_webhook_url": "https://hooks.example/x"})
    with session_scope() as db:
        out = alerts.deliver(db, {"title": "H2D failed", "message": "spaghetti", "channels": ["ntfy", "slack"], "urgent": True})
    assert out == {"ntfy": "sent", "slack": "sent"}
    assert sent[0][2]["Priority"] == "high" and sent[0][2]["Authorization"] == "Bearer tk"


def test_orders_routed_cancelled_and_printed(env, farm_fake, monkeypatch):
    from app.db import session_scope
    from app.services import farm, shop
    from app.kv import set_setting
    from app.models import Model, Product, ModelFile, Order, Job, FarmJob
    notes = []

    class FakeWoo:
        def add_order_note(self, oid, note, customer_note=False):
            notes.append((oid, note))
    import app.clients as cl
    monkeypatch.setattr(cl, "woo", lambda: FakeWoo())
    with session_scope() as db:
        m = Model(code="RT-1", name="Routed", slug="routed-rt-1")
        db.add(m)
        db.flush()
        f = ModelFile(model_id=m.id, sha256="a" * 64, filename="Routed_H2D.gcode.3mf", kind="3mf_sliced", is_print_file=True,
                      farm_file_ref="file-1", farm_kind="openprinthq", library_rel="x/Routed_H2D.gcode.3mf")
        p = Product(model_id=m.id, sku="RT-1", title="Routed")
        m2 = Model(code="RT-2", name="Second", slug="second-rt-2")
        db.add(m2)
        db.flush()
        f2 = ModelFile(model_id=m2.id, sha256="b" * 64, filename="Second_H2D.gcode.3mf", kind="3mf_sliced",
                       is_print_file=True, farm_file_ref="file-2", farm_kind="openprinthq",
                       library_rel="x/Second_H2D.gcode.3mf")
        p2 = Product(model_id=m2.id, sku="RT-2", title="Second")
        db.add_all([f, f2, p, p2])
        db.flush()
        set_setting(db, "order_routing", "hub")
        order = {"id": 9001, "number": "9001", "status": "processing", "total": "10", "billing": {"first_name": "A"},
                 "line_items": [{"id": 1, "sku": "RT-1", "name": "Routed", "quantity": 2},
                                {"id": 2, "sku": "RT-2", "name": "Second", "quantity": 1},
                                {"id": 3, "sku": "NOPE", "name": "Mystery", "quantity": 1}]}
        o = shop.upsert_order(db, order)
        assert db.query(Job).filter(Job.kind == "farm.route_order").count() == 1
        calls = []
        farm_fake.submit_job = lambda **kw: calls.append(kw) or {"job_ids": [100 + len(calls)]}
        out = farm.route_order(db, o.id)
        assert out["jobs"] == 2 and len(out["problems"]) == 1 and "NOPE" in out["problems"][0]
        assert calls[0]["file_ref"] == "file-1" and calls[0]["quantity"] == 2
        assert calls[1]["file_ref"] == "file-2"
        assert o.routed_at is None                       # the mystery line keeps it from counting as routed
        ln = {x.sku: x for x in o.lines}
        assert ln["RT-1"].farm_job_ids == [101] and ln["NOPE"].farm_error
        # drop the unknown line: now routed; then both jobs complete, so the Woo order gets a note
        o.lines.remove(ln["NOPE"])
        db.flush()
        farm.route_order(db, o.id)
        assert o.routed_at is not None
        for jid in (101, 102):
            farm.ingest_event(db, "queue.job_printing", {"job_id": jid, "printer_name": "H2D"})
            farm.ingest_event(db, "queue.job_completed", {"job_id": jid, "printer_name": "H2D"})
        assert o.printed_at is not None
        assert farm.order_printed(db, o.id)["noted"] and notes[0][0] == 9001
        # a second order cancelled before printing pulls its queued job
        o2 = shop.upsert_order(db, {**order, "id": 9002, "number": "9002",
                                    "line_items": [{"id": 5, "sku": "RT-1", "name": "Routed", "quantity": 1}]})
        farm.route_order(db, o2.id)
        jid = o2.lines[0].farm_job_ids[0]
        shop.upsert_order(db, {**order, "id": 9002, "number": "9002", "status": "cancelled",
                               "line_items": [{"id": 5, "sku": "RT-1", "name": "Routed", "quantity": 1}]})
        assert db.query(Job).filter(Job.kind == "farm.cancel_order").count() == 1
        out = farm.cancel_order(db, o2.id)
        assert out["cancelled"] == [jid] and farm_fake.cancelled == [jid]
        assert db.query(FarmJob).filter_by(external_id=str(jid)).one().status == "cancelled"
        set_setting(db, "order_routing", "shim")


def test_check_photo_and_alert(env, farm_fake, monkeypatch, tmp_path):
    from app.db import session_scope
    from app.services import farm, alerts
    from app.models import PlateCheck
    monkeypatch.setattr(alerts, "configured", lambda: ["ntfy"])
    from app.kv import set_setting
    with session_scope() as db:
        set_setting(db, "alert_rules", {"quiet_mode": "off"})
        farm.ingest_event(db, "queue.job_printing", {"job_id": 801, "printer_name": "voron01", "job_name": "Thing.gcode"})
        farm.ingest_event(db, "queue.job_completed", {"job_id": 801, "printer_name": "voron01"})
        c = db.query(PlateCheck).filter_by(external_job_id="801").one()
        out = farm.check_opened(db, c.id)
        assert out["photo"] and c.photo and out["alerted"] == ["ntfy"]
        cid = c.id
        set_setting(db, "alert_rules", None)
    from fastapi.testclient import TestClient
    from app.main import app
    r = TestClient(app).get(f"/api/plate-checks/{cid}/photo")
    assert r.status_code == 200 and r.content.startswith(b"\xff\xd8")


def test_order_uploads_a_file_the_farm_has_not_got(env, farm_fake, monkeypatch):
    """A print file the farm's library has no id for yet (or one with an id of another kind) is uploaded when its
    order is routed, and that id is sent; a second order reuses it."""
    from app.db import session_scope
    from app.services import farm, shop
    from app.kv import set_setting
    from app.models import Model, Product, ModelFile
    uploads, calls = [], []
    farm_fake.upload_file = lambda path, filename=None: uploads.append(filename) or {"uuid": "77", "id": 77}
    farm_fake.submit_job = lambda **kw: calls.append(kw) or {"job_ids": [500 + len(calls)]}
    with session_scope() as db:
        m = Model(code="SW-1", name="Switched", slug="switched-sw-1")
        db.add(m)
        db.flush()
        f = ModelFile(model_id=m.id, sha256="b" * 64, filename="Switched_H2D.gcode.3mf", kind="3mf_sliced",
                      is_print_file=True, farm_file_ref="0c1d2e3f-0000-4000-8000-000000000001", farm_kind="other",
                      library_rel="x/Switched_H2D.gcode.3mf")
        p = Product(model_id=m.id, sku="SW-1", title="Switched")
        db.add_all([f, p])
        db.flush()
        assert shop.farm_ref(f) is None
        set_setting(db, "order_routing", "hub")
        order = {"id": 9101, "number": "9101", "status": "processing", "total": "10", "billing": {"first_name": "A"},
                 "line_items": [{"id": 1, "sku": "SW-1", "name": "Switched", "quantity": 1}]}
        o = shop.upsert_order(db, order)
        out = farm.route_order(db, o.id)
        assert out["jobs"] == 1 and not out["problems"]
        assert uploads == ["SW-1__Switched_H2D.gcode.3mf"] and calls[0]["file_ref"] == "77"
        assert f.farm_file_ref == "77" and f.farm_kind == "openprinthq" and shop.farm_ref(f) == "77"
        o2 = shop.upsert_order(db, {**order, "id": 9102, "number": "9102"})
        farm.route_order(db, o2.id)
        assert len(uploads) == 1 and calls[1]["file_ref"] == "77"


def test_farm_refs(env):
    from app.models import ModelFile
    from app.services import shop
    f = ModelFile(filename="a.gcode", kind="gcode", farm_file_ref="u-1")
    assert shop.farm_ref(f) is None                    # no kind: not an id the farm knows
    shop.set_farm_ref(f, "9")
    assert (f.farm_file_ref, f.farm_kind) == ("9", "openprinthq") and shop.farm_ref(f) == "9"
    shop.set_farm_ref(f, None)
    assert f.farm_kind is None


def test_order_jobs_carry_max_retries(env, farm_fake, monkeypatch):
    from app.db import session_scope
    from app.services import farm, shop
    from app.kv import set_setting
    from app.models import Model, Product, ModelFile
    calls = []
    farm_fake.has = lambda cap: cap == "retries"
    farm_fake.upload_file = lambda path, filename=None: {"uuid": "88"}
    farm_fake.submit_job = lambda **kw: calls.append(kw) or {"job_ids": [700 + len(calls)]}
    with session_scope() as db:
        m = Model(code="RTY-1", name="Retry", slug="retry-rty-1")
        db.add(m)
        db.flush()
        db.add_all([ModelFile(model_id=m.id, sha256="c" * 64, filename="R.gcode.3mf", kind="3mf_sliced",
                              is_print_file=True, library_rel="x/R.gcode.3mf"),
                    Product(model_id=m.id, sku="RTY-1", title="Retry")])
        db.flush()
        set_setting(db, "order_routing", "hub")
        assert farm.max_retries(db) == 1            # default
        base = {"status": "processing", "total": "1", "billing": {"first_name": "A"}}
        o = shop.upsert_order(db, {**base, "id": 9201, "number": "9201",
                                   "line_items": [{"id": 1, "sku": "RTY-1", "name": "Retry", "quantity": 1}]})
        farm.route_order(db, o.id)
        assert calls[-1]["max_retries"] == 1
        set_setting(db, "farm_max_retries", 0)
        o2 = shop.upsert_order(db, {**base, "id": 9202, "number": "9202",
                                    "line_items": [{"id": 1, "sku": "RTY-1", "name": "Retry", "quantity": 1}]})
        farm.route_order(db, o2.id)
        assert "max_retries" not in calls[-1]


def test_periodic_farm_sync_slows_when_webhooks_arrive(env, farm_fake, monkeypatch):
    import time
    from types import SimpleNamespace
    from app.db import session_scope
    from app import tasks
    from app.services import farm
    from app.kv import set_setting
    ran = []
    monkeypatch.setattr(farm, "sync", lambda db: ran.append(1) or {"jobs": 0})
    periodic = SimpleNamespace(ref="periodic:farm.sync", payload={})
    hooked = SimpleNamespace(ref="farm:sync", payload={})
    with session_scope() as db:
        set_setting(db, "farm_webhook_at", 0)
        set_setting(db, "farm_periodic_sync_at", 0)
        tasks._farm_sync(db, periodic)
        tasks._farm_sync(db, periodic)              # no webhooks seen: every periodic run syncs
        assert len(ran) == 2
        set_setting(db, "farm_webhook_at", time.time())
        out = tasks._farm_sync(db, periodic)        # webhooks arriving and a periodic sync just ran: skipped
        assert out.get("skipped") and len(ran) == 2
        tasks._farm_sync(db, hooked)                # webhook-triggered syncs always run
        assert len(ran) == 3
        set_setting(db, "farm_periodic_sync_at", time.time() - 700)
        tasks._farm_sync(db, periodic)              # ten minutes on: the safety-net pull runs
        assert len(ran) == 4
