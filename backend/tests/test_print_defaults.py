"""SpoolmanDB matching (services/filament_db.py) and print defaults per filament (services/print_defaults.py)."""
import pytest

from app.services import filament_db as fdb
from app.services import print_defaults as pd

ROWS = [
    {"id": "bambulab_pla+wood_claybrown_1000_175_n", "manufacturer": "Bambu Lab", "name": "Clay Brown", "material": "PLA+WOOD",
     "color_hex": "995F11", "weight": 1000, "diameter": 1.75, "spool_weight": 250, "extruder_temp": 220,
     "extruder_temp_range": [190, 240], "bed_temp": 45, "bed_temp_range": [35, 55], "density": 1.21},
    {"id": "bambulab_pla_black_1000_175_n", "manufacturer": "Bambu Lab", "name": "Black", "material": "PLA",
     "color_hex": "000000", "weight": 1000, "diameter": 1.75, "extruder_temp": 220, "bed_temp": 55, "finish": None},
    {"id": "bambulab_pla_charcoal_matte", "manufacturer": "Bambu Lab", "name": "Charcoal", "material": "PLA",
     "color_hex": "000000", "weight": 1000, "diameter": 1.75, "finish": "matte"},
    {"id": "bambulab_petg_black", "manufacturer": "Bambu Lab", "name": "Black", "material": "PETG",
     "color_hex": "000000", "weight": 1000, "diameter": 1.75, "extruder_temp": 255, "bed_temp": 70},
    {"id": "sunlu_pla+_black", "manufacturer": "Sunlu", "name": "Black", "material": "PLA+", "color_hex": "111111",
     "weight": 1000, "diameter": 1.75, "extruder_temp": 215, "bed_temp": 60},
]


@pytest.fixture(autouse=True)
def db_rows(monkeypatch):
    by = {}
    for x in ROWS:
        by.setdefault(fdb._maker_key(x["manufacturer"]), []).append(x)
    monkeypatch.setattr(fdb, "_load", lambda: ROWS)
    monkeypatch.setattr(fdb, "_by_maker", by)


@pytest.fixture
def kv(monkeypatch):
    store = {}
    monkeypatch.setattr(pd, "setting", lambda db, k, default=None: store.get(k, default))
    monkeypatch.setattr(pd, "set_setting", lambda db, k, v: store.__setitem__(k, v))
    return store


def test_finds_the_official_colour_from_what_the_label_said():
    hit = fdb.find("Bambu Lab", "PLA", "Bambu PLA Wood Clay Brown", "Clay Brown")
    assert hit["name"] == "Clay Brown" and hit["color_hex"] == "995f11" and hit["extruder_temp"] == 220


def test_brand_spellings_and_materials_steer_the_match():
    assert fdb.find("Bambu", "PETG", "PETG", "Black")["id"] == "bambulab_petg_black"
    assert fdb.find("Bambu", "PETG", "PETG HF", "Black") is None                   # SpoolmanDB lists "HF Black" apart
    assert fdb.find("BambuLab", "PLA", "PLA Basic", "Black")["id"] == "bambulab_pla_black_1000_175_n"
    assert fdb.find("SUNLU", "PLA+", "PLA Plus", "Black")["color_hex"] == "111111"


def test_line_words_must_agree():
    rows = ROWS + [{"id": "bambulab_pla_silkwhite", "manufacturer": "Bambu Lab", "name": "Silk White", "material": "PLA",
                    "color_hex": "ffffff", "weight": 1000, "diameter": 1.75},
                   {"id": "bambulab_pla_brown", "manufacturer": "Bambu Lab", "name": "Brown", "material": "PLA",
                    "color_hex": "9d432c", "weight": 1000, "diameter": 1.75}]
    fdb._by_maker[fdb._maker_key("Bambu Lab")] = [x for x in rows if x["manufacturer"] == "Bambu Lab"]
    assert fdb.find("Bambu Lab", "PLA", "PLA White Basic", "White") is None          # not Silk White
    assert fdb.find("Bambu Lab", "PLA", "PLA Brown Wood", "Brown") is None           # a wood, not plain Brown
    assert fdb.find("Bambu Lab", "PLA", "PLA Silk White", "")["id"] == "bambulab_pla_silkwhite"
    assert fdb.find("Bambu Lab", "PLA", "PLA Basic Brown", "Brown")["id"] == "bambulab_pla_brown"


def test_no_match_rather_than_a_wrong_colour():
    assert fdb.find("Bambu Lab", "PLA", "PLA Basic", "Jade White") is None          # not in the sample
    assert fdb.find("Unknown Maker", "PLA", "Black", "Black") is None
    assert fdb.find("Bambu Lab", "PLA", "", "") is None


def test_defaults_are_kept_and_hand_set_ones_win(kv):
    row = pd.set_for(None, 23, "Bambu Lab", "PLA", "PLA Wood Clay Brown", "Clay Brown")
    assert row["extruder_temp"] == 220 and row["bed_temp_range"] == [35, 55] and row["density"] == 1.21
    assert row["source"] == "SpoolmanDB" and pd.get(None, 23)["color_hex"] == "995f11"
    pd.set_manual(None, 23, {"extruder_temp": 230}, "james")
    again = pd.set_for(None, 23, "Bambu Lab", "PLA", "PLA Wood Clay Brown", "Clay Brown", replace=True)
    assert again["extruder_temp"] == 230 and again["source"] == "manual"


def test_backfill_reports_what_it_could_not_find(kv):
    out = pd.backfill(None, [{"id": 1, "vendor": "Bambu Lab", "material": "PLA", "name": "PLA Basic Black"},
                             {"id": 2, "vendor": {"name": "Polymaker"}, "material": "PLA", "name": "PolyLite Teal"}])
    assert out["found"] == 1 and out["missing"] == ["Polymaker PolyLite Teal"]
    assert pd.backfill(None, [{"id": 1, "vendor": "Bambu Lab", "material": "PLA", "name": "PLA Basic Black"}])["kept"] == 1
