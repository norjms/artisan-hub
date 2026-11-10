"""Slicing workflow: designer settings per engine, tools, source choice, review and approval, stale detection,
and the API (grid, problems, previews) with a fake slicer service."""
import zipfile
from pathlib import Path

import pytest


def test_designer_overrides_orca_and_prusa():
    from app.models import Model
    from app.services.slicing import designer_overrides
    m = Model(code="X", name="x", slug="x", print_settings={"layer_height": "0.16", "walls": "3", "infill": "10% Gyroid",
                                                            "supports": "Yes"})
    o, lh = designer_overrides(m)
    assert lh == 0.16 and o == {"wall_loops": "3", "sparse_infill_density": "10%", "sparse_infill_pattern": "gyroid",
                                "enable_support": "1"}
    o, _ = designer_overrides(m, "prusa")
    assert o == {"perimeters": "3", "fill_density": "10%", "fill_pattern": "gyroid", "support_material": "1"}
    m.print_settings = {"infill": "15% Rectilinear", "supports": "No"}
    o, lh = designer_overrides(m)
    assert lh is None and o["sparse_infill_pattern"] == "zig-zag" and o["enable_support"] == "0"
    o, _ = designer_overrides(m, "prusa")
    assert o["fill_pattern"] == "rectilinear"


def test_choose_sources():
    from app.models import Model, ModelFile
    from app.services.slicing import choose_sources
    m = Model(code="X", name="x", slug="x")
    for fn, kind, slicer in [("a.stl", "stl", None), ("a-Prusa.3mf", "3mf_project", "PrusaSlicer"),
                             ("a-BambuStudio.3mf", "3mf_project", "BambuStudio"),
                             ("old.gcode.3mf", "3mf_sliced", "OrcaSlicer")]:
        m.files.append(ModelFile(filename=fn, kind=kind, slicer=slicer, library_rel=f"x/{fn}", sha256=fn, bytes=10))
    assert [f.filename for f in choose_sources(m)] == ["a-BambuStudio.3mf"]
    m2 = Model(code="Y", name="y", slug="y")
    for fn in ("box.stl", "lid.stl"):
        m2.files.append(ModelFile(filename=fn, kind="stl", library_rel=f"y/{fn}", sha256=fn))
    assert {f.filename for f in choose_sources(m2)} == {"box.stl", "lid.stl"}


def test_tools_and_problems():
    from app.models import Model, PrinterProfile, Product, BomFilament
    from app.services import slicing
    t = slicing.default_tools({"tools": 8, "nozzle": "0.4"}, "Prusa CORE One INDX 8T HF0.4 nozzle")
    assert len(t) == 8 and t[0]["flow"] == "hf" and t[7]["slot"] == 8 and t[0]["nozzle"] == 0.4
    assert len(slicing.default_tools({"extruders": 2, "nozzle": "0.4"}, "Bambu Lab H2D 0.4 nozzle")) == 2
    with pytest.raises(ValueError):
        slicing.clean_tools([{"color": "red"}])
    assert slicing.clean_tools([{"color": "ff0000", "material": "petg"}])[0] == {
        "slot": 1, "nozzle": 0.4, "flow": "standard", "material": "PETG", "color": "#FF0000", "preset": None}
    m = Model(code="X", name="x", slug="x", colors_needed=4)
    m.product = Product(sku="S", title="t")
    m.product.filaments = [BomFilament(slot=1, label="a", material="PLA"), BomFilament(slot=2, label="b", material="PETG")]
    prof = PrinterProfile(name="Mini", machine_preset="m", tools=[{"slot": 1, "material": "PLA"}])
    codes = [p["code"] for p in slicing.problems_for(m, prof)]
    assert codes == ["material"]          # a one-filament printer prints it in one colour, by choice
    prof = PrinterProfile(name="Duo", machine_preset="m", tools=[{"slot": 1, "material": "PLA"}, {"slot": 2, "material": "PLA"}])
    codes = [p["code"] for p in slicing.problems_for(m, prof)]
    assert codes == ["tools", "material"]
    prof.filament_slots = 4
    assert [p["code"] for p in slicing.problems_for(m, prof)] == ["material"]   # an AMS holds the four colours


class FakeSlicer:
    def __init__(self, tmp, version="v2.5.0-alpha"):
        self.tmp = tmp
        self.spec = None
        self.version = version
        self.calls = 0

    def engines(self, max_age=300.0):
        return {"orca": self.version, "prusa": "2.9.6"}

    def health(self):
        return {"ok": True, "engines": self.engines()}

    def presets(self, kind, machine, ftype=None, engine="orca", process=None):
        if engine == "prusa":
            if kind == "process":
                return [{"name": "0.20mm Balanced @COREONEINDX HF0.4", "layer_height": "0.2"}]
            return [{"name": "Prusament PETG @COREONEINDX HF0.4"}, {"name": "Generic PETG @COREONEINDX HF0.4"}]
        if kind == "process":
            return [{"name": "0.16mm Standard @BBL H2D", "layer_height": "0.16"}, {"name": "0.20mm Standard @BBL H2D", "layer_height": "0.2"}]
        return [{"name": "Bambu PLA Basic @BBL H2D"}, {"name": "Generic PLA @BBL H2D"}]

    def machines(self, q="", engine="orca"):
        if engine == "prusa":
            return [{"name": "Prusa CORE One INDX 8T HF0.4 nozzle", "tools": 8, "nozzle": "0.4"}]
        return [{"name": "Bambu Lab H2D 0.4 nozzle", "default_filament": "Bambu PLA Basic @BBL H2D", "extruders": 2, "nozzle": "0.4"}]

    def slice(self, paths, spec):
        self.spec = spec
        self.calls += 1
        if spec["engine"] == "prusa":
            return {"ok": True, "id": "p%d" % self.calls, "engine": "prusa", "engine_version": "2.9.6", "sliced_file": "X.bgcode",
                    "files": ["X.bgcode", "preview_plate_1.png"], "previews": ["preview_plate_1.png"],
                    "plates": [{"plate": 1, "time_s": 1800, "filaments": [{"slot": 1, "grams": 10.0}, {"slot": 2, "grams": 5.0}]}]}
        return {"ok": True, "id": "abc%d" % self.calls, "orca": self.version, "sliced_3mf": "X.gcode.3mf",
                "files": ["X.gcode.3mf", "plate_1.gcode", "preview_plate_1.png"], "previews": ["preview_plate_1.png"],
                "plates": [{"plate": 1, "time_s": 3600, "weight_g": 42.0, "filaments": [{"slot": 1, "grams": 42.0}]}], "warnings": []}

    def download(self, jid, name, dest):
        if name.endswith(".png"):
            dest.write_bytes(b"\x89PNG\r\n\x1a\n" + jid.encode())
            return dest
        with zipfile.ZipFile(dest, "w") as z:     # unique per job, so each slice is a new file
            z.writestr("Metadata/plate_1.gcode", "; sliced " + jid)
            z.writestr("Metadata/slice_info.config", '<config><plate><metadata key="index" value="1"/><metadata key="prediction" value="3600"/><metadata key="weight" value="42"/></plate></config>')
        return dest

    def forget(self, jid):
        pass


def _setup_model(c, name, sha):
    from app.db import session_scope
    from app.models import Model, ModelFile
    from app.config import settings
    mid = c.post("/api/models", json={"name": name, "grams": 0}).json()["id"]
    with session_scope() as db:
        m = db.get(Model, mid)
        m.print_settings = {"layer_height": "0.16", "walls": "4", "infill": "20%"}
        lib = Path(settings().library_root) / "test" / f"{sha}.stl"
        lib.parent.mkdir(parents=True, exist_ok=True)
        lib.write_bytes(b"solid x\nendsolid x\n")
        m.files.append(ModelFile(filename=f"{sha}.stl", kind="stl", library_rel=f"test/{sha}.stl", sha256=sha))
    return mid


def test_review_approve_and_stale(env, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import session_scope
    from app.models import Model, PrinterProfile, SliceResult
    from app.services import slicing
    fake = FakeSlicer(tmp_path)
    monkeypatch.setattr(slicing, "client", lambda: fake)
    c = TestClient(app)
    mid = _setup_model(c, "Slice Me", "p1")
    pid = c.post("/api/printer-profiles", json={"name": "H2D", "engine": "orca", "machine_preset": "Bambu Lab H2D 0.4 nozzle",
                                                "is_default": True}).json()["id"]
    with session_scope() as db:
        prof = db.get(PrinterProfile, pid)
        assert len(prof.tools) == 2            # filled from the catalog
        r = slicing.slice_model(db, mid, pid, {})
        db.commit()
        assert fake.spec["process"] == "0.16mm Standard @BBL H2D"
        assert fake.spec["process_overrides"] == {"wall_loops": "4", "sparse_infill_density": "20%"}
        assert fake.spec["filaments"] == ["Bambu PLA Basic @BBL H2D"]
        assert fake.spec["metadata"]["hub_printer"] == "H2D" and fake.spec["metadata"]["hub_model"]
        assert r["state"] == "review" and r["time_s"] == 3600 and r["grams"] == 42.0
        res = db.get(SliceResult, r["result_id"])
        rid = res.id
        m = db.get(Model, mid)
        first = [f for f in m.files if f.kind == "3mf_sliced"]
        assert len(first) == 1 and not first[0].is_print_file      # nothing in production before approval

    # previews are served to the team, and the grid and problems show the result
    st = c.get(f"/api/models/{mid}/slicing").json()
    pv = st["printers"][0]["previews"]
    assert st["printers"][0]["state"] == "review" and pv and c.get(pv[0]).status_code == 200
    assert c.get(f"/api/slice-results/{rid}/preview/../../etc/passwd").status_code == 404
    g = c.get("/api/slicing/grid").json()
    row = next(x for x in g["rows"] if x["id"] == mid)
    assert row["cells"][0]["state"] == "review"
    probs = c.get("/api/slicing/problems").json()["items"]
    assert not any(x["model_id"] == mid for x in probs)      # waiting for review is not a problem
    assert any(x["id"] == rid for x in c.get("/api/slicing/review").json()["items"])

    out = c.post(f"/api/slice-results/{rid}/approve").json()
    assert out["approved"] and out["production"] and out["bom_updated"]
    with session_scope() as db:
        m = db.get(Model, mid)
        approved = [f for f in m.files if f.kind == "3mf_sliced"]
        assert len(approved) == 1 and approved[0].is_print_file and m.product.filaments[0].grams == 42.0
        approved_id = approved[0].id
        # a re-slice goes to review and leaves the approved file in place
        slicing.slice_model(db, mid, pid, {"layer_height": 0.2})
        db.commit()
        files = {f.id: f for f in db.get(Model, mid).files if f.kind == "3mf_sliced"}
        assert len(files) == 2 and files[approved_id].is_print_file
        r = db.get(SliceResult, rid)
        assert r.state == "review" and r.approved_file_ids == [approved_id]
    # rejecting the candidate keeps the approved one
    assert c.post(f"/api/slice-results/{rid}/reject").json()["state"] == "approved"
    with session_scope() as db:
        assert [f.id for f in db.get(Model, mid).files if f.kind == "3mf_sliced"] == [approved_id]
        assert slicing.check_stale(db, model_id=mid) == {"stale": 0, "fresh": 0}
        # a new slicer version makes it stale; so does a printer change
        fake.version = "v2.6.0"
        assert slicing.check_stale(db, model_id=mid)["stale"] == 1
        assert db.get(SliceResult, rid).state == "stale"
        fake.version = "v2.5.0-alpha"
        assert slicing.check_stale(db, model_id=mid)["fresh"] == 1
        db.get(PrinterProfile, pid).process_overrides = {"brim_type": "no_brim"}
        db.flush()
        assert slicing.check_stale(db, model_id=mid)["stale"] == 1
    assert c.post("/api/slicing/reslice", json={"states": ["stale"], "profile_id": pid}).json()["queued"] == 1


def test_prusa_toolchanger_spec(env, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import session_scope
    from app.models import Model, Product, BomFilament
    from app.services import slicing
    fake = FakeSlicer(tmp_path)
    monkeypatch.setattr(slicing, "client", lambda: fake)
    c = TestClient(app)
    mid = _setup_model(c, "Two Colour", "p2")
    tools = [{"material": "PETG", "color": "#000000"}] * 8
    r = c.post("/api/printer-profiles", json={"name": "INDX", "engine": "prusa", "machine_preset": "Prusa CORE One INDX 8T HF0.4 nozzle",
                                              "tools": tools})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    assert len(r.json()["tools"]) == 8 and r.json()["engine"] == "prusa"
    with session_scope() as db:
        m = db.get(Model, mid)
        if not m.product:
            m.product = Product(sku="TC-1", title="tc")
        m.product.filaments = [BomFilament(slot=1, label="Black", material="PETG", color_hex="#111111"),
                               BomFilament(slot=2, label="White", material="PETG", color_hex="#EEEEEE")]
        db.flush()
        out = slicing.slice_model(db, mid, pid, {"orientation": {"mode": "largest_face"}})
        db.commit()
        sp = fake.spec
        assert sp["engine"] == "prusa" and sp["process"] == "0.20mm Balanced @COREONEINDX HF0.4"
        assert sp["filaments"] == ["Prusament PETG @COREONEINDX HF0.4"] * 2 and sp["tool_of_color"] == [1, 2]
        assert sp["process_overrides"] == {"perimeters": "4", "fill_density": "20%"}
        assert sp["machine_overrides"]["extruder_colour"] == "#111111;#EEEEEE" and "metadata" not in sp
        assert not sp.get("ground_largest_face") and any("reorient" in w for w in out["warnings"])
        assert [t["grams"] for t in out["tools"]] == [10.0, 5.0] and out["files"][0].endswith(".bgcode")


def test_import_preset_and_isolation(env, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import slicing
    monkeypatch.setattr(slicing, "client", lambda: FakeSlicer(tmp_path))
    c = TestClient(app)
    ini = "inherits = Prusa CORE One INDX 8T HF0.4 nozzle\nbed_shape = 0x0,500x0,500x400,0x400\n"
    r = c.post("/api/printer-profiles", json={"name": "L+INDX", "engine": "prusa", "machine_config": ini})
    assert r.status_code == 200, r.text
    assert r.json()["machine_preset"] == "Prusa CORE One INDX 8T HF0.4 nozzle" and r.json()["imported"]
    bad = c.post("/api/printer-profiles", json={"name": "Bad", "engine": "orca", "machine_config": "not json"})
    assert bad.status_code == 400
    assert c.post("/api/printer-profiles", json={"name": "X", "engine": "cura", "machine_preset": "a"}).status_code == 400


def test_material_type():
    from app.services.slicing import material_type as t
    assert [t(x) for x in ("PLA+", "Silk PLA", "PLA Plus", "petg cf", "PETG", "TPU 95A", "Nylon", None)] == \
        ["PLA", "PLA", "PLA", "PETG-CF", "PETG", "TPU", "PA", "PLA"]


def test_project_settings_are_the_base_layer():
    from app.models import Model, ModelFile
    from app.services.slicing import designer_overrides
    proj = ModelFile(filename="x-BambuStudio.3mf", kind="3mf_project", library_rel="x/p.3mf", sha256="p",
                     settings={"layer_height": 0.16, "walls": 3.0, "infill": "12%", "infill_pattern": "gyroid",
                               "supports": "1", "top_layers": 6.0, "bottom_layers": 4.0})
    m = Model(code="X", name="x", slug="x", print_settings={})
    o, lh = designer_overrides(m, "orca", [proj])
    assert lh == 0.16 and o == {"wall_loops": "3", "sparse_infill_density": "12%", "sparse_infill_pattern": "gyroid",
                                "enable_support": "1", "top_shell_layers": "6", "bottom_shell_layers": "4"}
    # recorded recommendations win over the project file
    m.print_settings = {"layer_height": "0.2", "infill": "20% Grid", "supports": "No"}
    o, lh = designer_overrides(m, "orca", [proj])
    assert lh == 0.2 and o["sparse_infill_density"] == "20%" and o["sparse_infill_pattern"] == "grid"
    assert o["enable_support"] == "0" and o["wall_loops"] == "3" and o["top_shell_layers"] == "6"
    proj.settings["infill_pattern"] = "zig-zag"
    m.print_settings = {}
    o, _ = designer_overrides(m, "prusa", [proj])
    assert o["fill_pattern"] == "rectilinear" and o["perimeters"] == "3" and o["top_solid_layers"] == "6"


def test_project_filament_cap(monkeypatch):
    """A materials list longer than the project's filament list is cut to the project's count."""
    from types import SimpleNamespace as NS
    from app.services import slicing, filaments
    monkeypatch.setattr(filaments, "project_slots_of", lambda p: [{"slot": 1}])
    assert slicing.project_filament_count([NS(kind="stl", library_rel="a.stl")]) is None
    assert slicing.project_filament_count([NS(kind="3mf_project", library_rel="a.3mf")]) == 1
