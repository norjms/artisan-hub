"""Print now: a model's approved slice for one printer goes to the farm pinned to that printer."""
from tests.test_plate_reslice import FakeFarm, rig  # noqa: F401  (fixture)
from tests.test_slicing import _setup_model


class PinFarm(FakeFarm):
    def __init__(self, pin=True):
        super().__init__()
        self.pin = pin
        self.jobs = []

    def has(self, cap):
        return (cap == "pin_printer" and self.pin) or (cap != "pin_printer" and super().has(cap))

    def submit_job(self, file_ref=None, quantity=1, job_name=None, printer=None, **kw):
        self.jobs.append((str(file_ref), quantity, job_name, printer))
        return {"job_ids": [100 + len(self.jobs) + i for i in range(quantity)]}


def _use(monkeypatch, f):
    from app import clients
    from app.services import farm as farmsvc, shop
    monkeypatch.setattr(clients, "farm", lambda: f)
    monkeypatch.setattr(shop, "farm", lambda: f)
    monkeypatch.setattr(farmsvc, "_client", lambda: f)


def _approved(c, mid, name="Print H2D"):
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import PrinterProfile
    from app.services import slicing
    with session_scope() as db:
        pid = db.execute(select(PrinterProfile.id).where(PrinterProfile.name == name)).scalar()
    if not pid:
        pid = c.post("/api/printer-profiles", json={"name": name, "engine": "orca", "machine_preset": "Bambu Lab H2D 0.4 nozzle",
                                                    "farm_printer": "H2D"}).json()["id"]
    with session_scope() as db:
        slicing.slice_model(db, mid, pid, {"approve": True})
    return pid


def test_print_now_sends_the_approved_file_pinned(rig, monkeypatch):
    c, fake, base = rig
    f = PinFarm()
    _use(monkeypatch, f)
    mid = _setup_model(c, "Print Me", "pn1")
    pid = _approved(c, mid)
    st = c.get(f"/api/models/{mid}/slicing").json()
    assert st["can_print_now"] is True
    assert next(p for p in st["printers"] if p["profile_id"] == pid)["has_approved"]
    r = c.post(f"/api/models/{mid}/print", json={"profile_id": pid, "quantity": 2})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["printer"] == "Print H2D" and len(out["jobs"]) == 2 and out["quantity"] == 2
    ref, qty, name, printer = f.jobs[-1]
    assert printer == "H2D" and qty == 2 and "Print Me" in name and ref.startswith("9")


def test_print_now_refuses_without_approval_or_pinning(rig, monkeypatch):
    c, fake, base = rig
    f = PinFarm(pin=False)
    _use(monkeypatch, f)
    mid = _setup_model(c, "Print Me Not", "pn2")
    pid = c.post("/api/printer-profiles", json={"name": "Print Unsliced", "engine": "orca",
                                                "machine_preset": "Bambu Lab H2D 0.4 nozzle"}).json()["id"]
    r = c.post(f"/api/models/{mid}/print", json={"profile_id": pid})
    assert r.status_code == 400 and "nothing approved" in r.text
    pid2 = _approved(c, mid)
    assert c.get(f"/api/models/{mid}/slicing").json()["can_print_now"] is False
    r = c.post(f"/api/models/{mid}/print", json={"profile_id": pid2})
    assert r.status_code == 400 and "one printer" in r.text
    assert f.jobs == []
