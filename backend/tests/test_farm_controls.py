"""Printer and queue controls on the Farm page."""
import pytest


class CtlFarm:
    kind, label = "openprinthq", "OpenPrintHQ"
    CAPABILITIES = frozenset({"queue", "priority", "commands", "out_of_service"})

    def __init__(self):
        self.calls = []

    def has(self, cap):
        return cap in self.CAPABILITIES

    def printers(self):
        return {"printers": [{"name": "Voron01", "model": "Voron 2.4", "online": True, "ready": False, "busy": False,
                              "state": "finish", "nozzle_temp": 31.0, "bed_temp": 28.0},
                             {"name": "H2D", "online": True, "ready": True, "busy": False, "state": "idle"}]}

    def maint_down(self):
        return [{"printer_name": "H2D", "reason": "new hotend", "since": None}]

    def command(self, name, command, args=None, confirm=False):
        self.calls.append(("command", name, command, args, confirm))
        return {"ok": True}

    def set_down(self, name, down, reason=""):
        self.calls.append(("down", name, down, reason))

    def set_priority(self, job_id, priority):
        self.calls.append(("priority", str(job_id), priority))

    def cancel_job(self, job_id):
        self.calls.append(("cancel_printing", str(job_id)))

    def cancel_queued(self, job_id, reason):
        self.calls.append(("cancel_queued", str(job_id)))


@pytest.fixture
def ctl(env, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import farm
    f = CtlFarm()
    monkeypatch.setattr(farm, "_client", lambda: f)
    return TestClient(app), f


def test_overview_has_controls_data(ctl):
    c, f = ctl
    d = c.get("/api/farm").json()
    assert d["farm_label"] == "OpenPrintHQ"
    ps = {p["name"]: p for p in d["printers"]}
    assert ps["Voron01"]["ready"] is False and ps["Voron01"]["nozzle_temp"] == 31.0
    assert ps["H2D"]["out_of_service"] is True and ps["H2D"]["out_of_service_reason"] == "new hotend"
    assert ps["Voron01"]["out_of_service"] is False


def test_printer_commands_and_service(ctl):
    c, f = ctl
    assert c.post("/api/farm/printers/Voron01/command", json={"command": "set_ready"}).status_code == 200
    assert ("command", "Voron01", "set_ready", None, False) in f.calls
    assert c.post("/api/farm/printers/Voron01/command", json={"command": "preheat", "args": {"material": "PETG"}}).status_code == 200
    assert c.post("/api/farm/printers/Voron01/command", json={"command": "rm -rf"}).status_code == 400
    assert c.post("/api/farm/printers/H2D/service", json={"down": False}).status_code == 200
    assert ("down", "H2D", False, f.calls[-1][3]) == f.calls[-1]


def test_job_priority_and_cancel(ctl):
    from app.db import session_scope
    from app.models import FarmJob
    c, f = ctl
    with session_scope() as db:
        db.add(FarmJob(external_id="fc-1", name="queued one", status="queued", priority=50))
        db.add(FarmJob(external_id="fc-2", name="printing one", status="printing"))
    assert c.post("/api/farm/jobs/fc-1/priority", json={"priority": 75}).status_code == 200
    assert ("priority", "fc-1", 75) in f.calls
    assert c.post("/api/farm/jobs/fc-1/priority", json={"priority": 0}).status_code == 400
    assert c.post("/api/farm/jobs/fc-1/cancel").status_code == 200
    assert ("cancel_queued", "fc-1") in f.calls
    with session_scope() as db:
        assert db.query(FarmJob).filter(FarmJob.external_id == "fc-1").one().priority == 75
