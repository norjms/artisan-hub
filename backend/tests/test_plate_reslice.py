"""Plate catalog, the plate on each printer, and plate-aware reslicing of farm jobs."""
import pytest

from tests.test_slicing import FakeSlicer, _setup_model

PEI_HOLD = "needs the Textured PEI Plate (has High Temp Plate)"


class FakeFarm:
    kind = "openprinthq"
    label = "OpenPrintHQ"

    def __init__(self):
        self.calls = []
        self.rows = None          # preflight printer rows; None = default plate-only hold on H2D (id 6)
        self.types = ["PLA"]
        self.uploads = 0
        self.swapped = set()      # jobs already pointed at a file for the fitted plate

    def has(self, cap):
        return cap in ("maintenance_log", "out_of_service")

    def preflight(self, job_id):
        if str(job_id) in self.swapped:      # the engine now sees a file for the fitted plate
            return {"item_id": int(job_id), "status": "pending", "job_plate": "High Temp Plate",
                    "filament_types": self.types, "printers": [
                        {"printer_id": 6, "name": "H2D", "eligible": True, "reasons": [],
                         "has_plate": "High Temp Plate", "plate_mismatch": False}]}
        rows = self.rows if self.rows is not None else [
            {"printer_id": 6, "name": "H2D", "model": "H2D", "eligible": False, "reasons": [PEI_HOLD],
             "has_plate": "High Temp Plate", "plate_mismatch": True}]
        return {"item_id": int(job_id), "status": "pending", "job_plate": "Textured PEI Plate",
                "filament_types": self.types, "printers": rows}

    def swap_job_file(self, job_id, ref, printer_id):
        self.calls.append(("swap", str(job_id), str(ref), printer_id))
        self.swapped.add(str(job_id))
        return {"id": int(job_id)}

    def set_installed_plate(self, printer, slicer_type):
        self.calls.append(("plate", printer, slicer_type))

    def set_plate_detection(self, printer, enabled):
        self.calls.append(("marker", printer, enabled))

    def maint_log(self, printer, task, category, performed_by, notes="", minutes=None):
        self.calls.append(("log", printer, task))

    def upload_file(self, path, filename=None):
        self.uploads += 1
        return {"uuid": str(900 + self.uploads)}


@pytest.fixture
def rig(env, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app import clients
    from app.main import app
    from app.services import shop, slicing
    fake, farm = FakeSlicer(tmp_path), FakeFarm()
    monkeypatch.setattr(slicing, "client", lambda: fake)
    monkeypatch.setattr(clients, "farm", lambda: farm)
    monkeypatch.setattr(clients, "farm_kind", lambda team_id=None: "openprinthq")
    monkeypatch.setattr(clients, "farm_connected", lambda team_id=None: True)
    monkeypatch.setattr(shop, "farm", lambda: farm)
    c = TestClient(app)
    from app.db import session_scope
    from app.models import FarmJob
    with session_scope() as db:          # each test starts with no waiting farm jobs
        for fj in db.query(FarmJob).filter(FarmJob.status.in_(("queued", "assigned"))):
            fj.status = "completed"
    return c, fake, farm


def _profile(c, name="Plate H2D"):
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import PrinterProfile
    with session_scope() as db:
        got = db.execute(select(PrinterProfile.id).where(PrinterProfile.name == name)).scalar()
        if got:
            return got
    return c.post("/api/printer-profiles", json={"name": name, "engine": "orca", "machine_preset": "Bambu Lab H2D 0.4 nozzle",
                                                 "bed_type": "Textured PEI Plate", "farm_printer": "H2D"}).json()["id"]


def _plate_id(c, name):
    return next(p["id"] for p in c.get("/api/plates").json()["plates"] if p["name"] == name)


def _farm_job(mid, ext, ref="500"):
    from app.db import session_scope
    from app.models import FarmJob
    with session_scope() as db:
        fj = FarmJob(external_id=str(ext), name=f"job {ext}", status="queued", model_id=mid, file_ref=ref)
        db.add(fj)
        db.flush()
        return fj.id


def _last_job_payload(kind):
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import Job
    with session_scope() as db:
        j = db.execute(select(Job).where(Job.kind == kind).order_by(Job.id.desc())).scalar()
        return dict(j.payload) if j else None


def test_family_and_suits():
    from app.models import BuildPlate
    from app.services import plates
    assert [plates.family(t) for t in ("PA6-CF", "PETG-HF", "PLA+", "PAHT-CF", "PPA-CF", "pp")] == \
        ["PA", "PETG", "PLA", "PA", "PPA", "PP"]
    cfx = BuildPlate(name="CFX", slicer_type="High Temp Plate", materials=["PLA", "PA", "PP"])
    assert plates.suits(cfx, ["PLA", "PA6-CF"]) == (True, None)
    ok, why = plates.suits(cfx, ["PVA"])
    assert not ok and "PVA" in why
    empty = BuildPlate(name="SuperTack", slicer_type="Supertack Plate", materials=[])
    assert not plates.suits(empty, ["PLA"])[0]
    assert plates.plate_key("Cool Plate (SuperTack)") == plates.plate_key("Supertack Plate")


def test_catalog_seed_and_setting_a_plate(rig):
    c, fake, farm = rig
    body = c.get("/api/plates").json()
    names = {p["name"]: p for p in body["plates"]}
    assert {"Darkmoon CFX", "Bambu SuperTack", "Bambu Textured PEI"} <= set(names)
    cfx = names["Darkmoon CFX"]
    assert cfx["slicer_type"] == "High Temp Plate" and not cfx["bambu_marker"] and "PP" in cfx["materials"]
    assert names["Bambu SuperTack"]["materials"] == ["PLA", "PETG"]
    assert "PA" in names["Bambu Textured PEI"]["materials"] and all(p["ready"] for p in names.values())
    pid = _profile(c)
    out = c.put(f"/api/printer-profiles/{pid}/plate", json={"plate_id": cfx["id"]}).json()
    assert out["plate"] == "Darkmoon CFX" and not out["errors"]
    assert ("plate", "H2D", "High Temp Plate") in farm.calls
    assert ("marker", "H2D", False) in farm.calls                 # no Bambu marker: detection off
    assert any(x[0] == "log" for x in farm.calls)
    rows = c.get("/api/printer-profiles/plates").json()["printers"]
    assert next(r for r in rows if r["profile_id"] == pid)["installed_plate"]["name"] == "Darkmoon CFX"
    farm.calls.clear()
    c.put(f"/api/printer-profiles/{pid}/plate", json={"plate_id": names["Bambu SuperTack"]["id"]})
    assert ("marker", "H2D", True) in farm.calls                  # Bambu plate: detection back on
    assert c.patch(f"/api/plates/{cfx['id']}", json={"slicer_type": "Glass"}).status_code == 400
    assert c.delete(f"/api/plates/{names['Bambu SuperTack']['id']}").status_code == 409   # it's on a printer


def test_plate_hold_is_resliced_swapped_and_cached(rig):
    from app.db import session_scope
    from app.models import FarmJob, Model, PlateSlice, SliceResult
    from app.services import plate_reslice
    c, fake, farm = rig
    mid = _setup_model(c, "Plate Me", "pl1")
    pid = _profile(c)
    c.put(f"/api/printer-profiles/{pid}/plate", json={"plate_id": _plate_id(c, "Darkmoon CFX")})
    fj1 = _farm_job(mid, 11)
    with session_scope() as db:
        assert plate_reslice.scan(db) == {"enqueued": 1}
        assert db.get(FarmJob, fj1).plate_reslice["state"] == "slicing"
    payload = _last_job_payload("farm.plate_reslice")
    assert payload["plate"] == "High Temp Plate" and payload["printer_id"] == 6
    with session_scope() as db:
        out = plate_reslice.run_reslice(db, payload)
        assert out["swapped"] is True
        assert fake.spec["bed_type"] == "High Temp Plate"
        fj = db.get(FarmJob, fj1)
        assert fj.plate_reslice["state"] == "swapped" and fj.printer_name == "H2D" and fj.file_ref == "901"
        ps = db.get(PlateSlice, out["plate_slice_id"])
        assert ps.state == "approved" and ps.plate == "High Temp Plate"
        f = next(x for x in db.get(Model, mid).files if x.id == ps.approved_file_ids[0])
        assert not f.is_print_file and "High Temp Plate" in f.filename   # production files untouched
        assert db.query(SliceResult).filter(SliceResult.model_id == mid).count() == 0
    assert ("swap", "11", "901", 6) in farm.calls
    # the next job of the same model on the same plate uses the cache: no slicing
    calls = fake.calls
    fj2 = _farm_job(mid, 12)
    with session_scope() as db:
        assert plate_reslice.scan(db) == {"swapped": 1, "not a plate hold": 1}   # job 11 is already swapped
        assert db.get(FarmJob, fj2).plate_reslice["state"] == "swapped"
    assert fake.calls == calls and ("swap", "12", "901", 6) in farm.calls


def test_unsuitable_no_hold_and_printer_taken(rig):
    from app.db import session_scope
    from app.models import FarmJob
    from app.services import plate_reslice
    c, fake, farm = rig
    mid = _setup_model(c, "Plate Two", "pl2")
    pid = _profile(c)
    c.put(f"/api/printer-profiles/{pid}/plate", json={"plate_id": _plate_id(c, "Darkmoon CFX")})
    fj = _farm_job(mid, 21)
    farm.types = ["PVA"]
    with session_scope() as db:
        assert plate_reslice.scan(db) == {"unsuitable": 1}
        st = db.get(FarmJob, fj).plate_reslice
        assert st["state"] == "unsuitable" and "PVA" in st["msg"]
    farm.types = ["PLA"]
    farm.rows = [{"printer_id": 6, "name": "H2D", "eligible": False, "reasons": [PEI_HOLD, "busy"],
                  "has_plate": "High Temp Plate", "plate_mismatch": True}]
    with session_scope() as db:
        assert plate_reslice.scan(db) == {"not a plate hold": 1}     # busy too: not this watcher's business
    # sliced while free, taken before the swap: the slice is cached and the job waits
    farm.rows = None
    with session_scope() as db:
        assert plate_reslice.scan(db) == {"enqueued": 1}
    payload = _last_job_payload("farm.plate_reslice")
    farm.rows = [{"printer_id": 6, "name": "H2D", "eligible": False, "reasons": ["busy"],
                  "has_plate": "High Temp Plate", "plate_mismatch": False}]
    with session_scope() as db:
        out = plate_reslice.run_reslice(db, payload)
        assert out["swapped"] is False
        assert db.get(FarmJob, fj).plate_reslice["state"] == "waiting_printer"
    assert not any(x[0] == "swap" for x in farm.calls)


def test_problems_go_to_review_then_approve(rig, monkeypatch):
    from app.db import session_scope
    from app.models import FarmJob, PlateSlice
    from app.services import plate_reslice, slicing
    c, fake, farm = rig
    mid = _setup_model(c, "Plate Three", "pl3")
    pid = _profile(c)
    c.put(f"/api/printer-profiles/{pid}/plate", json={"plate_id": _plate_id(c, "Darkmoon CFX")})
    fj = _farm_job(mid, 31)
    monkeypatch.setattr(slicing, "problems_for", lambda m, p: [{"code": "material", "msg": "needs 2 colours"}])
    with session_scope() as db:
        plate_reslice.scan(db)
    payload = _last_job_payload("farm.plate_reslice")
    with session_scope() as db:
        out = plate_reslice.run_reslice(db, payload)
        assert "review" in out
        assert db.get(FarmJob, fj).plate_reslice["state"] == "review"
        ps_id = out["review"]
    assert any(x["id"] == ps_id for x in c.get("/api/plate-slices?state=review").json()["plate_slices"])
    assert not any(x[0] == "swap" for x in farm.calls)
    assert c.post(f"/api/plate-slices/{ps_id}/approve").json()["state"] == "approved"
    monkeypatch.setattr(slicing, "problems_for", lambda m, p: [])
    with session_scope() as db:
        assert plate_reslice.scan(db) == {"swapped": 1}
        assert db.get(PlateSlice, ps_id).state == "approved"
    assert ("swap", "31", "901", 6) in farm.calls
    assert c.post(f"/api/plate-slices/{ps_id}/approve").status_code == 409


def test_skipped_without_a_connected_farm(rig, monkeypatch):
    from app import clients
    from app.db import session_scope
    from app.services import plate_reslice
    monkeypatch.setattr(clients, "farm_connected", lambda team_id=None: False)
    with session_scope() as db:
        assert "skipped" in plate_reslice.scan(db)


def test_profile_lookup_ignores_case(rig):
    from app.db import session_scope
    from app.services import plate_reslice
    c, fake, farm = rig
    pid = _profile(c)
    with session_scope() as db:
        assert plate_reslice.profile_for(db, "h2d").id == pid
        assert plate_reslice.profile_for(db, " H2D ").id == pid
        assert plate_reslice.profile_for(db, "nope") is None


def test_catalog_has_vendors_and_maps_for_the_h2c(rig):
    from app.db import session_scope
    from app.models import BuildPlate, PrinterProfile
    from app.services import plates
    c, fake, farm = rig
    cat = {p["name"]: p for p in c.get("/api/plates").json()["plates"]}
    for name in ("Darkmoon G10 Pro", "Garolite G10 Sheet", "Wham Bam PEX", "Bambu Engineering Plate",
                 "Prusa Satin Sheet", "BIQU CryoGrip Pro Glacier", "Energetic PEO"):
        assert name in cat, name
    assert cat["Darkmoon G10 Pro"]["slicer_type"] == "High Temp Plate" and "PA" in cat["Darkmoon G10 Pro"]["materials"]
    assert "PA" not in cat["Darkmoon G10 Hobby"]["materials"]
    assert all(p["slicer_type"] in c.get("/api/plates").json()["slicer_types"] for p in cat.values())
    textured = [p for p in cat.values() if p["slicer_type"] == "Textured PEI Plate"]
    assert all("textured" in p["name"].lower() or "satin" in p["name"].lower() for p in textured)
    with session_scope() as db:
        g10 = db.query(BuildPlate).filter(BuildPlate.name == "Darkmoon G10 Pro").one()
        h2c = PrinterProfile(name="x", machine_preset="Bambu Lab H2C 0.4 nozzle")
        h2d = PrinterProfile(name="y", machine_preset="Bambu Lab H2D 0.4 nozzle")
        assert plates.slicer_type_for(g10, h2c) == "Engineering Plate"
        assert plates.slicer_type_for(g10, h2d) == "High Temp Plate"
        # re-seeding keeps edits
        g10.materials = ["PA"]
        db.flush()
        plates.ensure_seed(db)
        assert g10.materials == ["PA"] and db.query(BuildPlate).filter(BuildPlate.name == "Darkmoon G10 Pro").count() == 1
