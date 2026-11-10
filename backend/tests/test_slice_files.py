"""Sliced files chosen by hand: promote a file to a printer (approved and locked), lock and unlock, remove a
printer's slice, delete and replace files, and Print on one printer through Watchtower."""
import io
import json
import zipfile
from pathlib import Path

import pytest

from tests.test_plate_reslice import rig  # noqa: F401  (fixture)
from tests.test_print_now import PinFarm, _use
from tests.test_slicing import _setup_model


@pytest.fixture(autouse=True)
def _keep_printers():
    """These tests add printers and pick a default; put that back so later test modules see what they set up."""
    from app.db import session_scope
    from app.models import PrinterProfile
    with session_scope() as db:
        was = {p.id: p.is_default for p in db.query(PrinterProfile)}
    yield
    with session_scope() as db:
        for p in db.query(PrinterProfile):
            if p.id in was:
                p.is_default = was[p.id]
            else:
                db.delete(p)


def _zip_bytes(tag: str) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("3D/3dmodel.model", f'<model><metadata name="Application">BambuStudio-02.03</metadata><!-- {tag} --></model>')
        z.writestr("Metadata/plate_1.png", b"\x89PNG\r\n\x1a\n" + tag.encode())
        z.writestr("Metadata/plate_1.gcode", f"; {tag}\nG1 X0\n")
        z.writestr("Metadata/project_settings.config", json.dumps({
            "printer_model": "Bambu Lab H2D", "layer_height": "0.2", "filament_type": ["PLA"], "nozzle_diameter": ["0.4"]}))
        z.writestr("Metadata/slice_info.config", """<config><plate><metadata key="index" value="1"/>
<metadata key="prediction" value="1800"/><metadata key="weight" value="9.5"/>
<filament id="1" type="PLA" color="#FFFFFF" used_m="3" used_g="9.5"/></plate></config>""")
    return b.getvalue()


def _sliced(mid, name, sha, printer="Bambu Lab H2D", grams=12.5, secs=3600):
    from app.config import settings
    from app.db import session_scope
    from app.models import Model, ModelFile
    lib = Path(settings().library_root) / "test" / name
    lib.parent.mkdir(parents=True, exist_ok=True)
    lib.write_bytes(_zip_bytes(sha))
    with session_scope() as db:
        m = db.get(Model, mid)
        f = ModelFile(filename=name, kind="3mf_sliced", library_rel=f"test/{name}", sha256=sha, printer=printer,
                      print_seconds=secs, plates=1, slicer="BambuStudio", filament=[{"slot": 1, "type": "PLA", "grams": grams}])
        m.files.append(f)
        db.flush()
        return f.id


def _prof(c, name, default=False):
    from sqlalchemy import select, update
    from app.db import session_scope
    from app.models import PrinterProfile
    with session_scope() as db:
        pid = db.execute(select(PrinterProfile.id).where(PrinterProfile.name == name)).scalar()
    if not pid:
        pid = c.post("/api/printer-profiles", json={"name": name, "engine": "orca", "wt_printer": name,
                                                    "machine_preset": "Bambu Lab H2D 0.4 nozzle"}).json()["id"]
    if default:
        with session_scope() as db:
            db.execute(update(PrinterProfile).values(is_default=False))
            db.get(PrinterProfile, pid).is_default = True
    return pid


def _card(c, mid, pid):
    return next(p for p in c.get(f"/api/models/{mid}/slicing").json()["printers"] if p["profile_id"] == pid)


def test_promote_a_file_to_a_printer_locks_it(rig):
    c, fake, farm = rig
    mid = _setup_model(c, "Promote Me", "sf1")
    pid = _prof(c, "SF H2D", default=True)
    fid = _sliced(mid, "promote.gcode.3mf", "sf1-sliced")
    r = c.post(f"/api/models/{mid}/printers/{pid}/use-file", json={"file_id": fid})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["locked"] is True and out["production"] is True and out["warnings"] == []
    card = _card(c, mid, pid)
    assert card["state"] == "approved" and card["locked"] and card["source"] == "file"
    assert card["approved_file_ids"] == [fid] and card["stats"]["per_unit_g"] == 12.5 and card["stats"]["time_s"] == 3600
    assert card["previews"] and card["previews"][0].endswith("preview_plate_1.png")
    assert c.get(card["previews"][0]).status_code == 200
    files = {f["id"]: f for f in c.get(f"/api/models/{mid}").json()["files_all"]}
    assert files[fid]["is_print_file"] is True

    # locked: never stale, never auto-replaced; a re-slice waits for review and keeps the file in use
    from app.db import session_scope
    from app.models import SliceResult
    from app.services import slicing
    with session_scope() as db:
        assert slicing.check_stale(db, model_id=mid)["stale"] == 0
        db.execute(SliceResult.__table__.update().values(state="approved").where(SliceResult.id == card["id"]))
    with session_scope() as db:
        slicing.SETTINGS["slice_auto_approve_clean"] = True
        try:
            rep = slicing.slice_model(db, mid, pid, {})
        finally:
            slicing.SETTINGS["slice_auto_approve_clean"] = False
        assert rep.get("locked") and not rep.get("auto_approved")
    card = _card(c, mid, pid)
    assert card["state"] == "review" and card["approved_file_ids"] == [fid] and card["candidate_differs"]

    # the uploaded file is not deleted when a hub slice is approved over it
    assert c.post(f"/api/slice-results/{card['id']}/approve").status_code == 200
    ids = {f["id"] for f in c.get(f"/api/models/{mid}").json()["files_all"]}
    assert fid in ids


def test_mismatched_printer_warns_and_unsliced_refused(rig):
    c, fake, farm = rig
    mid = _setup_model(c, "Wrong Printer", "sf2")
    pid = _prof(c, "SF H2D")
    fid = _sliced(mid, "voron.gcode.3mf", "sf2-sliced", printer="voron01")
    out = c.post(f"/api/models/{mid}/printers/{pid}/use-file", json={"file_id": fid, "lock": False}).json()
    assert out["locked"] is False and "voron01" in out["warnings"][0]
    from app.db import session_scope
    from app.models import ModelFile
    with session_scope() as db:
        stl = next(f.id for f in db.query(ModelFile).filter(ModelFile.model_id == mid, ModelFile.kind == "stl"))
    r = c.post(f"/api/models/{mid}/printers/{pid}/use-file", json={"file_id": stl})
    assert r.status_code == 400 and "isn't sliced" in r.text


def test_lock_unlock_and_remove_a_slice(rig):
    c, fake, farm = rig
    mid = _setup_model(c, "Lock Me", "sf3")
    pid = _prof(c, "SF Lock")
    from app.db import session_scope
    from app.services import slicing
    with session_scope() as db:
        slicing.slice_model(db, mid, pid, {"approve": True})
    card = _card(c, mid, pid)
    assert card["locked"] is False and card["source"] == "hub"
    hub_files = list(card["approved_file_ids"])
    assert c.post(f"/api/slice-results/{card['id']}/lock", json={"locked": True}).json()["locked"] is True
    assert _card(c, mid, pid)["locked"] is True
    assert c.post(f"/api/slice-results/{card['id']}/lock", json={"locked": False}).json()["locked"] is False
    r = c.delete(f"/api/slice-results/{card['id']}")
    assert r.status_code == 200 and r.json()["state"] == "none"
    assert _card(c, mid, pid)["state"] == "none"
    ids = {f["id"] for f in c.get(f"/api/models/{mid}").json()["files_all"]}
    assert not ids & set(hub_files)            # the hub's own slice files went with it


def test_delete_and_replace_files(rig):
    c, fake, farm = rig
    mid = _setup_model(c, "Replace Me", "sf4")
    a, b = _prof(c, "SF A", default=True), _prof(c, "SF B")
    fid = _sliced(mid, "both.gcode.3mf", "sf4-sliced", grams=10)
    for pid in (a, b):
        assert c.post(f"/api/models/{mid}/printers/{pid}/use-file", json={"file_id": fid}).status_code == 200

    # replacing with an unsliced file is refused (and the old file is untouched)
    r = c.post(f"/api/files/{fid}/replace", files={"file": ("both.stl", b"solid y\nendsolid y\n")})
    assert r.status_code == 400
    # a new version under the same name takes its place on both printers and in production
    r = c.post(f"/api/files/{fid}/replace", files={"file": ("both.gcode.3mf", _zip_bytes("v2"))})
    assert r.status_code == 200, r.text
    out = r.json()
    new = out["file_id"]
    assert set(out["printers"]) == {"SF A", "SF B"} and out["production"]
    files = {f["id"]: f for f in c.get(f"/api/models/{mid}").json()["files_all"]}
    assert fid not in files and files[new]["is_print_file"]
    from app.config import settings
    assert (Path(settings().library_root) / "test" / "both.gcode.3mf").exists() or files[new]["download"]
    for pid in (a, b):
        card = _card(c, mid, pid)
        assert card["approved_file_ids"] == [new] and card["locked"]

    # deleting it leaves both printers with nothing approved
    r = c.delete(f"/api/files/{new}")
    assert r.status_code == 200 and set(r.json()["printers"]) == {"SF A", "SF B"} and r.json()["was_production"]
    for pid in (a, b):
        assert _card(c, mid, pid)["state"] == "none"


def test_upload_a_sliced_file_for_a_printer(rig):
    c, fake, farm = rig
    mid = _setup_model(c, "Upload For", "sf5")
    pid = _prof(c, "SF Up")
    r = c.post(f"/api/models/{mid}/printers/{pid}/upload", files={"file": ("up.stl", b"solid z\nendsolid z\n")})
    assert r.status_code == 400
    assert not any(f["filename"] == "up.stl" for f in c.get(f"/api/models/{mid}").json()["files_all"])


def test_print_on_a_chosen_file_with_start_now(rig, monkeypatch):
    c, fake, base = rig
    seen = []

    class StartFarm(PinFarm):
        def submit_job(self, file_ref=None, quantity=1, job_name=None, printer=None, start=False, **kw):
            seen.append((printer, start))
            return {**super().submit_job(file_ref=file_ref, quantity=quantity, job_name=job_name, printer=printer),
                    "started": [1] if start else []}

    _use(monkeypatch, StartFarm())
    mid = _setup_model(c, "Print Chosen", "sf6")
    pid = _prof(c, "SF Print")
    fid = _sliced(mid, "chosen.gcode.3mf", "sf6-sliced")
    c.post(f"/api/models/{mid}/printers/{pid}/use-file", json={"file_id": fid})
    r = c.post(f"/api/models/{mid}/print", json={"profile_id": pid, "quantity": 1, "start_now": True})
    assert r.status_code == 200, r.text
    assert seen[-1] == ("SF Print", True) and r.json()["started"]


def test_watchtower_pins_with_print_now(monkeypatch):
    from app.clients.watchtower_api import WatchtowerClient
    w = WatchtowerClient("http://wt.test", "k", "u", "p")
    calls = []
    monkeypatch.setattr(w, "_pub", lambda method, path, **kw: calls.append(("pub", path, kw.get("json"))) or {"job_ids": [7, 8]})
    monkeypatch.setattr(w, "_priv", lambda method, path, **kw: calls.append(("priv", path, kw.get("json"))) or {})
    assert w.has("pin_printer")
    out = w.submit_job(file_ref="f1", quantity=2, job_name="x", printer="H2D", start=True)
    assert calls[0] == ("pub", "/jobs", {"quantity": 2, "file_ref": "f1", "job_name": "x", "preferred_printer": "H2D", "priority": 100})
    assert calls[1] == ("priv", "/api/queue/job/7/print-now", {"printer_name": "H2D", "set_ready": True})
    assert out["started"] == [7] and out["job_ids"] == [7, 8]

    def busy(*a, **kw):
        raise RuntimeError("printer busy")
    monkeypatch.setattr(w, "_priv", busy)
    out = w.submit_job(file_ref="f1", printer="H2D")
    assert out["started"] == [] and "queued at the top" in out["notes"][0]
    calls.clear()
    monkeypatch.setattr(w, "_priv", lambda *a, **kw: calls.append("priv"))
    w.submit_job(file_ref="f1", quantity=1)               # no printer: no pin, no print-now
    assert "priv" not in calls and "preferred_printer" not in calls[0][2]
