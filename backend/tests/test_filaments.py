"""Requested filaments: reading notes, colour codes, matching to Bambu Lab, and applying to the materials list."""
import pytest

CAT = [
    {"family": "PLA Basic", "fila_id": "GFA00", "name": "Black", "code": "10101", "hex": "#000000", "multi": False, "clear": False, "colors": ["#000000"]},
    {"family": "PLA Basic", "fila_id": "GFA00", "name": "Jade White", "code": "10100", "hex": "#FFFFFF", "multi": False, "clear": False, "colors": ["#FFFFFF"]},
    {"family": "PLA Basic", "fila_id": "GFA00", "name": "Gray", "code": "10103", "hex": "#8E9089", "multi": False, "clear": False, "colors": ["#8E9089"]},
    {"family": "PLA Matte", "fila_id": "GFA01", "name": "Charcoal", "code": "11101", "hex": "#000000", "multi": False, "clear": False, "colors": ["#000000"]},
    {"family": "PLA Matte", "fila_id": "GFA01", "name": "Latte Brown", "code": "11800", "hex": "#D3B7A7", "multi": False, "clear": False, "colors": ["#D3B7A7"]},
    {"family": "PLA Silk+", "fila_id": "GFA06", "name": "Gold", "code": "13405", "hex": "#F4A925", "multi": False, "clear": False, "colors": ["#F4A925"]},
    {"family": "PETG HF", "fila_id": "GFG02", "name": "Green", "code": "33500", "hex": "#00AE42", "multi": False, "clear": False, "colors": ["#00AE42"]},
]
SDB = [{"manufacturer": "Prusament", "material": "PLA", "name": "Jet Black", "color_hex": "24292A"},
       {"manufacturer": "Prusament", "material": "PETG", "name": "Pistachio Green", "color_hex": "93A683"},
       {"manufacturer": "Sunlu", "material": "PLA+", "name": "White", "color_hex": "C7CDD7"}]

FC = [{"brand": "Sunlu", "name": "Klein Blue", "type": "PLA+", "parent": "PLA", "hex": "#264E9E", "td": 1.2, "slug": "sunlu-klein-blue-pla-1"},
      {"brand": "Sunlu", "name": "Silk Blue", "type": "Silk PLA", "parent": "PLA", "hex": "#3A6FD0", "td": None, "slug": "sunlu-silk-blue-2"},
      {"brand": "Bambu Lab", "name": "Black", "type": "PLA", "parent": "PLA", "hex": "#3D3D3C", "td": None, "slug": "bambu-black-3"},
      {"brand": "Bambu Lab", "name": "Matte Charcoal", "type": "PLA Matte", "parent": "PLA", "hex": "#424344", "td": None, "slug": "bambu-charcoal-4"},
      {"brand": "Bambu Lab", "name": "Grey", "type": "PLA Basic", "parent": "PLA", "hex": "#8B9398", "td": None, "slug": "bambu-grey-5"}]


@pytest.fixture
def cat(monkeypatch):
    from app.services import filaments
    monkeypatch.setattr(filaments, "bambu_catalog", lambda refresh=False: CAT)
    monkeypatch.setattr(filaments, "spoolmandb", lambda refresh=False: SDB)
    monkeypatch.setattr(filaments, "filamentcolors", lambda refresh=False: FC)
    return filaments


def test_parse_notes(cat):
    p = cat.parse_note('Sunlu PLA+ "Bone White" & "Latte Brown"')
    assert [(x["brand"], x["material"], x["name"]) for x in p] == [("sunlu", "PLA+", "Bone White"), ("sunlu", "PLA+", "Latte Brown")]
    p = cat.parse_note("Anycubic PLA+ White")
    assert p[0]["name"] == "White" and p[0]["material"] == "PLA+"
    p = cat.parse_note('Anycubic Silk PLA "White" & "Gold"')
    assert all("silk" in x["finish"] for x in p) and [x["name"] for x in p] == ["White", "Gold"]
    assert cat.parse_note("PLA #F99963")[0]["hex"] == "#F99963"
    assert cat.parse_note("View Filament") == []


def test_colour_codes_and_matching(cat):
    class M:
        print_settings = {"filament": 'Prusament PLA "Jet Black"'}
    r = cat.resolve(M(), [])
    s = r["slots"][0]
    assert s["hex"] == "#101010" and s["hex_source"] == "colour name"     # plain black: name wins over brand codes
    assert s["bambu"]["name"] == "Black" and s["bambu"]["family"] == "PLA Basic"
    M.print_settings = {"filament": 'Prusament PETG "Pistachio Green"'}
    s = cat.resolve(M(), [])["slots"][0]
    assert s["hex"] == "#93A683" and s["hex_source"] == "SpoolmanDB" and s["bambu"]["family"] == "PETG HF"
    M.print_settings = {"filament": 'Anycubic PLA Matte "Latte Brown"'}
    s = cat.resolve(M(), [])["slots"][0]
    assert s["bambu"]["name"] == "Latte Brown" and s["bambu"]["family"] == "PLA Matte"
    M.print_settings = {"filament": 'BambuLab Matte PLA "Black"'}
    assert cat.resolve(M(), [])["slots"][0]["bambu"]["name"] == "Charcoal"      # Bambu's matte black
    M.print_settings = {"filament": 'Anycubic Silk PLA "Gold"'}
    assert cat.resolve(M(), [])["slots"][0]["bambu"]["family"] == "PLA Silk+"
    # nothing in the notes: the project file's colour is used
    M.print_settings = {}
    s = cat.resolve(M(), [{"slot": 1, "preset": "Anycubic PLA @Anycubic Kobra S1", "vendor": "Anycubic", "type": "PLA", "hex": "#8E9089", "used": True}])["slots"][0]
    assert s["hex_source"] == "project file" and s["bambu"]["name"] == "Gray"
    assert cat.delta_e("#000000", "#000000") == 0 and 90 < cat.delta_e("#000000", "#FFFFFF") < 110


def test_apply_keeps_the_original(env, cat):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import session_scope
    from app.models import Model
    c = TestClient(app)
    mid = c.post("/api/models", json={"name": "Fil Test", "grams": 10}).json()["id"]
    with session_scope() as db:
        m = db.get(Model, mid)
        m.print_settings = {"filament": 'Sunlu PLA+ "White" & Prusament PLA "Jet Black"', "layer_height": "0.2"}
    assert c.put("/api/settings", json={"filament_brand": "Bambu Lab"}).status_code == 200
    out = c.post(f"/api/models/{mid}/filaments/match").json()
    assert out["slots"] == 2 and out["request"]["note"] == 'Sunlu PLA+ "White" & Prusament PLA "Jet Black"'
    with session_scope() as db:
        m = db.get(Model, mid)
        bom = sorted(m.product.filaments, key=lambda b: b.slot)
        assert [(b.material, b.color_name, b.color_hex) for b in bom] == [("PLA Basic", "Jade White", "#FFFFFF"), ("PLA Basic", "Black", "#000000")]
        assert m.print_settings["filament"] == 'Sunlu PLA+ "White" & Prusament PLA "Jet Black"'   # never overwritten
        req = m.extra["filament_request"]["slots"]
        assert req[0]["brand_label"] == "Sunlu" and req[1]["name"] == "Jet Black"
    r = c.put(f"/api/models/{mid}/filaments/1", json={"family": "PLA Matte", "name": "Latte Brown"})
    assert r.status_code == 200
    got = c.get(f"/api/models/{mid}/filaments").json()
    assert got["request"]["slots"][0]["bambu"]["name"] == "Latte Brown" and got["request"]["slots"][0]["name"] == "White"
    assert c.put(f"/api/models/{mid}/filaments/1", json={"family": "PLA Basic", "name": "Nope"}).status_code == 400
    c.put("/api/settings", json={"filament_brand": None})


def test_measured_colours(cat):
    """FilamentColors.xyz measurements win over published codes, and are compared with measured Bambu colours."""
    m = cat.lookup_measured("sunlu", "PLA+", "Klein Blue")
    assert m["hex"] == "#264E9E" and m["url"].endswith("sunlu-klein-blue-pla-1") and m["td"] == 1.2
    assert cat.lookup_measured("sunlu", "PLA+", "Blue") is None              # the silk swatch is not plain blue
    assert cat.lookup_measured("sunlu", "PLA", "Blue", ["silk"])["hex"] == "#3A6FD0"
    meas = cat.with_measured(CAT)
    by = {(c["family"], c["name"]): c.get("measured_hex") for c in meas}
    assert by[("PLA Basic", "Black")] == "#3D3D3C" and by[("PLA Matte", "Charcoal")] == "#424344"
    assert by[("PLA Basic", "Gray")] == "#8B9398" and by[("PLA Basic", "Jade White")] is None

    class M:
        print_settings = {"filament": 'Sunlu PLA+ "Klein Blue"'}
    s = cat.resolve(M(), [])["slots"][0]
    assert s["hex_source"] == "measured" and s["catalog_url"] and s["td"] == 1.2
    # a measured near-black matches Bambu Black through its measured code, not a grey
    FC.append({"brand": "Sunlu", "name": "Black", "type": "PLA+", "parent": "PLA", "hex": "#383838", "td": None, "slug": "sunlu-black-6"})
    try:
        M.print_settings = {"filament": 'Sunlu PLA+ "Black"'}
        s = cat.resolve(M(), [])["slots"][0]
        assert s["hex_source"] == "measured" and s["bambu"]["name"] == "Black" and s["bambu"]["delta_e"] < 3
    finally:
        FC.pop()


def test_photo_colour_when_nothing_recorded(cat):
    """A model with no designer filament uses the approved colour read from its photos; notes still win."""
    class M:
        print_settings = {}
        extra = {"filament_photo": {"main": {"name": "Charcoal", "hex": "#333333", "finish": []},
                                    "accents": [{"name": "White", "hex": "#F2F2F2"}], "approved_by": "James"}}
    r = cat.resolve(M(), [])
    s = r["slots"][0]
    assert s["hex_source"] == "product photo" and s["name"] == "Charcoal" and s["photo_accents"][0]["name"] == "White"
    assert s["bambu"]["family"] == "PLA Matte" and s["bambu"]["name"] == "Charcoal" and r["photo"]["approved_by"] == "James"
    M.print_settings = {"filament": 'Prusament PLA "Jet Black"'}
    assert cat.resolve(M(), [])["slots"][0]["hex_source"] != "product photo"
