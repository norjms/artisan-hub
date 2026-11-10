"""Filament sets: a chosen production file's colours become the materials list, Match again follows it, each
printer's slice keeps its own set, and spools match a Bambu Lab line by its filament type."""
import io
import json
import zipfile
from pathlib import Path

import pytest

from tests.test_plate_reslice import rig  # noqa: F401  (fixture)
from tests.test_slice_files import _prof, _card
from tests.test_slicing import _setup_model

CATALOG = [
    {"family": "PLA Metal", "name": "Iridium Gold Metallic", "hex": "#B39B84", "clear": False, "multi": False, "fila_id": "GFA02", "code": "13700"},
    {"family": "PLA Metal", "name": "Oxide Green Metallic", "hex": "#1D7C6A", "clear": False, "multi": False, "fila_id": "GFA02", "code": "13500"},
    {"family": "PLA Basic", "name": "Black", "hex": "#000000", "clear": False, "multi": False, "fila_id": "GFA00", "code": "10101"},
]


@pytest.fixture(autouse=True)
def _keep_default():
    """These tests choose a default printer; put the flags back so later test modules see what they set up."""
    from app.db import session_scope
    from app.models import PrinterProfile
    with session_scope() as db:
        was = {p.id: p.is_default for p in db.query(PrinterProfile)}
    yield
    with session_scope() as db:
        for p in db.query(PrinterProfile):
            if p.id in was:
                p.is_default = was[p.id]
            else:                      # printers made here go, or they become the fallback default for later modules
                db.delete(p)


@pytest.fixture
def cat(monkeypatch):
    from app.services import filaments
    monkeypatch.setattr(filaments, "bambu_catalog", lambda refresh=False: CATALOG)
    monkeypatch.setattr(filaments, "with_measured", lambda c: c)
    return CATALOG


def _two_tone(mid, name, sha):
    """A Bambu Studio two-tone file: filaments 1 and 6 of six in the project, both Bambu PLA Metal."""
    from app.config import settings
    from app.db import session_scope
    from app.models import Model, ModelFile
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("3D/3dmodel.model", f'<model><metadata name="Application">BambuStudio-02.03</metadata><!-- {sha} --></model>')
        z.writestr("Metadata/plate_1.png", b"\x89PNG\r\n\x1a\n" + sha.encode())
        z.writestr("Metadata/project_settings.config", json.dumps({
            "printer_model": "Bambu Lab H2D", "filament_type": ["PLA"] * 6,
            "filament_settings_id": ["Bambu PLA Metal @BBL H2D", "Generic PLA @BBL H2D", "Bambu PLA Wood @BBL H2D",
                                     "Bambu PLA Basic @BBL H2D", "Generic PETG @BBL H2D", "Bambu PLA Metal @BBL H2D"],
            "filament_colour": ["#B39B84", "#161616", "#3F231C", "#FFFFFF", "#2850E0", "#1D7C6A"]}))
    lib = Path(settings().library_root) / "test" / name
    lib.parent.mkdir(parents=True, exist_ok=True)
    lib.write_bytes(b.getvalue())
    with session_scope() as db:
        m = db.get(Model, mid)
        f = ModelFile(filename=name, kind="3mf_sliced", library_rel=f"test/{name}", sha256=sha, printer="Bambu Lab H2D",
                      print_seconds=12000, plates=1, slicer="BambuStudio",
                      filament=[{"slot": 1, "type": "PLA", "color": "#B39B84", "grams": 26.27},
                                {"slot": 6, "type": "PLA", "color": "#1D7C6A", "grams": 31.59}])
        m.files.append(f)
        db.flush()
        return f.id


def _with_bom(mid, rows):
    from app.db import session_scope
    from app.models import Model, BomFilament
    from app.services import importer
    with session_scope() as db:
        m = db.get(Model, mid)
        p = m.product or importer.ensure_product(db, m)
        for b in list(p.filaments):
            p.filaments.remove(b)
        for r in rows:
            p.filaments.append(BomFilament(**r))


def _bom(mid):
    from app.db import session_scope
    from app.models import Model
    with session_scope() as db:
        return [(b.slot, b.label, b.material, b.color_name, b.color_hex, b.grams)
                for b in sorted(db.get(Model, mid).product.filaments, key=lambda b: b.slot)]


def test_production_file_sets_the_materials_list(rig, cat):
    c, fake, farm = rig
    mid = _setup_model(c, "Two Tone", "fs1")
    _with_bom(mid, [{"slot": 1, "label": "Body", "material": "PLA Basic", "color_name": "Black", "color_hex": "#000000",
                     "grams": 20, "waste_pct": 5}])
    pid = _prof(c, "FS H2D", default=True)
    fid = _two_tone(mid, "two-tone.gcode.3mf", "fs1-sliced")
    r = c.post(f"/api/models/{mid}/printers/{pid}/use-file", json={"file_id": fid})
    assert r.status_code == 200, r.text
    assert r.json()["bom_updated"]
    assert _bom(mid) == [(1, "Body", "PLA Metal", "Iridium Gold Metallic", "#B39B84", 26.27),
                         (2, "Color 2", "PLA Metal", "Oxide Green Metallic", "#1D7C6A", 31.59)]
    tools = _card(c, mid, pid)["stats"]["tools"]
    assert [(t["slot"], t["file_slot"], t["color_hex"]) for t in tools] == [(1, 1, "#B39B84"), (2, 6, "#1D7C6A")]

    # Match again reads the production file, not the designer's request, and keeps the list as printed
    r = c.post(f"/api/models/{mid}/filaments/match")
    assert r.status_code == 200, r.text
    assert r.json()["production"]["source"] == "file"
    assert [x[4] for x in _bom(mid)] == ["#B39B84", "#1D7C6A"]
    # picking a colour by hand is refused while a file sets them
    r = c.put(f"/api/models/{mid}/filaments/1", json={"family": "PLA Basic", "name": "Black"})
    assert r.status_code == 400 and "file" in r.text

    sets = c.get(f"/api/models/{mid}/filament-sets").json()
    assert sets["production"]["printer"] == "FS H2D"
    s = sets["sets"][0]
    assert s["production"] and s["source"] == "file" and s["grams"] == 57.86 and not s["differs"]
    assert [x["color_name"] for x in s["set"]] == ["Iridium Gold Metallic", "Oxide Green Metallic"]


def test_other_printers_keep_their_own_set(rig, cat):
    c, fake, farm = rig
    mid = _setup_model(c, "Two Tone Other", "fs2")
    _with_bom(mid, [{"slot": 1, "label": "Body", "material": "PLA Basic", "color_name": "Black", "color_hex": "#000000",
                     "grams": 20, "waste_pct": 5}])
    pid = _prof(c, "FS H2D", default=True)
    other = _prof(c, "FS Other")
    from app.db import session_scope
    from app.services import slicing
    with session_scope() as db:
        slicing.slice_model(db, mid, other, {"approve": True})     # sliced here from the list: black
    before = _bom(mid)
    assert before[0][4] == "#000000" and before[0][5] == 20        # another printer's approval leaves the list alone
    fid = _two_tone(mid, "two-tone-2.gcode.3mf", "fs2-sliced")
    c.post(f"/api/models/{mid}/printers/{pid}/use-file", json={"file_id": fid})
    sets = {s["printer"]: s for s in c.get(f"/api/models/{mid}/filament-sets").json()["sets"]}
    assert sets["FS H2D"]["production"] and not sets["FS Other"]["production"]
    assert sets["FS Other"]["set"][0]["color_hex"] == "#000000"   # what it was sliced with, not today's list
    # a slice from before tools kept their colour: the material comes from its preset, not today's list
    from app.models import SliceResult
    with session_scope() as db:
        r = db.query(SliceResult).filter(SliceResult.model_id == mid, SliceResult.profile_id == other).one()
        st = dict(r.approved_stats)
        st["tools"] = [{k: v for k, v in x.items() if k in ("slot", "tool", "grams", "per_unit_g")}
                       | {"preset": "Bambu PLA Basic @BBL H2D"} for x in st["tools"]]
        r.approved_stats = st
    old = {s["printer"]: s for s in c.get(f"/api/models/{mid}/filament-sets").json()["sets"]}["FS Other"]["set"][0]
    assert (old["material"], old["color_hex"], old["color_name"]) == ("PLA Basic", "#000000", "Black")
    assert sets["FS Other"]["differs"] is True


def test_hub_production_slice_updates_grams_without_going_stale(rig, cat):
    c, fake, farm = rig
    mid = _setup_model(c, "Hub Prod", "fs3")
    _with_bom(mid, [{"slot": 1, "label": "Body", "material": "PLA Basic", "color_name": "Black", "color_hex": "#000000",
                     "grams": 1, "waste_pct": 5}])
    pid = _prof(c, "FS H2D", default=True)
    from app.db import session_scope
    from app.services import slicing
    with session_scope() as db:
        out = slicing.slice_model(db, mid, pid, {"approve": True})
        assert out.get("bom_updated")
    b = _bom(mid)
    assert b[0][1:5] == ("Body", "PLA Basic", "Black", "#000000") and b[0][5] > 1
    with session_scope() as db:
        assert slicing.check_stale(db, model_id=mid)["stale"] == 0


def test_spools_match_a_bambu_line_by_type():
    from app.models import SpoolCache
    from app.services.availability import FilamentNeed, spool_matches, same_material
    assert same_material("PLA Metal", "PLA") and same_material("PLA Basic", "PLA") and same_material("PETG HF", "PETG")
    assert not same_material("PLA-CF", "PLA") and not same_material("PA", "SUPPORT FOR PA/PET")
    gold = SpoolCache(spool_id=1, material="PLA", color_hex="#B39B84", name="Iridium Gold Metallic", remaining_g=940, archived=False)
    assert spool_matches(FilamentNeed("PLA Metal", "Iridium Gold Metallic", "#B39B84", None), gold)
    assert not spool_matches(FilamentNeed("PLA Metal", "Oxide Green Metallic", "#1D7C6A", None), gold)
