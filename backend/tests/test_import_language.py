"""English and a category on import (services/import_language.py)."""
import json
import types

import pytest

from app.services import import_language as il


def test_the_english_check():
    assert not il.needs_english("Articulated Dragon with print-in-place joints for the desk")
    assert not il.needs_english("Planter")
    assert il.needs_english("Ein gemütlicher Herbst-Wichtel mit einer Mütze in Kürbisform")
    assert il.needs_english("이글루매니아 v5 브릭")
    assert il.needs_english("会飞舞的蝴蝶")
    assert not il.needs_english("Low poly catch everything bowl")              # short English names pass
    assert il.needs_english("Verleihe deiner Halloween-Dekoration eine besonders düstere Atmosphäre")
    assert not il.needs_english("")


@pytest.fixture
def kv(monkeypatch):
    store = {}
    monkeypatch.setattr(il, "setting", lambda db, k, default=None: store.get(k, default))
    monkeypatch.setattr(il, "set_setting", lambda db, k, v: store.__setitem__(k, v))
    return store


class FakeDB:
    def __init__(self, names):
        self.names = names

    def execute(self, q):
        cats = [types.SimpleNamespace(name=n) for n in self.names]
        return types.SimpleNamespace(scalars=lambda: cats)

    def flush(self):
        pass


def test_category_suggestions_use_yours_and_remember_choices(kv):
    db = FakeDB(["Art", "Christmas", "Fidget Toys", "Other Toys & Games", "Plants and Planters", "Vases"])
    assert il.suggest_category(db, "printables", "Other Art & Designs") == "Art"      # not "Other Toys & Games"
    assert il.suggest_category(db, "printables", "Christmas Decorations") == "Christmas"
    assert il.suggest_category(db, "printables", "Planters & Pots") == "Plants and Planters"
    assert il.suggest_category(db, "printables", "Engineering") is None          # never a made-up category
    il.remember_category(db, "printables", "Christmas Decorations", "Vases")
    assert il.suggest_category(db, "printables", "christmas decorations") == "Vases"


def test_translation_uses_the_teams_connection_and_skips_english(kv, monkeypatch):
    sent = {}

    def ask(kind, cfg, prompt, photo, timeout=None):
        sent["kind"], sent["items"] = kind, json.loads(prompt[prompt.index("{"):])
        return json.dumps({k: f"EN {v}" for k, v in sent["items"].items()})

    from app.services import connections, vision
    monkeypatch.setattr(vision, "order", lambda db: ["ai_ollama", "ai_gemini"])
    monkeypatch.setattr(vision, "ask", ask)
    monkeypatch.setattr(connections, "get", lambda k, required=False: {"url": "x", "model": "m"} if k == "ai_ollama" else None)
    got, note = il.translate(None, {"name": "Herbst-Wichtel mit Kürbismütze und Bart", "summary": "A cozy autumn gnome for the desk"})
    assert sent["kind"] == "ai_ollama" and list(sent["items"]) == ["name"] and got == {"name": "EN Herbst-Wichtel mit Kürbismütze und Bart"}
    kv[il.SETTINGS_KEY] = {"translate": False}
    assert il.translate(None, {"name": "会飞舞的蝴蝶"}) == ({}, "translation is off in Settings")
    kv[il.SETTINGS_KEY] = {"translate": True, "connection": "ai_gemini"}
    assert "no AI connection" in il.translate(None, {"name": "会飞舞的蝴蝶"})[1]


def test_finish_uses_phone_text_translates_files_and_moves_the_folder(kv, monkeypatch, tmp_path):
    from app.services import connections, importer, vision
    monkeypatch.setattr(vision, "order", lambda db: ["ai_ollama"])
    monkeypatch.setattr(connections, "get", lambda k, required=False: {"url": "x", "model": "m"})
    monkeypatch.setattr(vision, "ask", lambda kind, cfg, prompt, photo, timeout=None: json.dumps(
        {k: v.replace("이글루매니아", "Igloo Mania").replace("브릭", "Brick") for k, v in json.loads(prompt[prompt.index("{"):]).items()}))
    monkeypatch.setattr(il, "suggest_category", lambda db, plat, src: None)
    lib = tmp_path / "lib"
    (lib / "toys" / "AP-1 - 이글루매니아").mkdir(parents=True)
    (lib / "toys" / "AP-1 - 이글루매니아" / "obj_1_이글루매니아 v5_브릭.stl").write_text("solid")
    monkeypatch.setattr("app.config.settings", lambda: types.SimpleNamespace(library_root=str(lib)))
    f = types.SimpleNamespace(filename="obj_1_이글루매니아 v5_브릭.stl", library_rel="toys/AP-1 - 이글루매니아/obj_1_이글루매니아 v5_브릭.stl")
    prod = types.SimpleNamespace(title="이글루매니아", short_description=None, description=None)
    m = types.SimpleNamespace(name="이글루매니아", summary="", description="", tags=[], notes=None, category=None,
                              files=[f], library_rel="toys/AP-1 - 이글루매니아", code="AP-1", product=prod)
    monkeypatch.setattr(importer, "library_folder", lambda model: f"toys/{model.code} - {model.name}")
    rep = il.finish(FakeDB([]), m, "printables", None, overrides={"name": "Igloo Mania", "tags": "igloo, toy"})
    assert m.name == "Igloo Mania" and m.tags == ["igloo", "toy"] and "Original wording" in m.notes
    assert prod.title == "Igloo Mania"                                  # the draft product follows the model
    assert f.filename == "obj_1_Igloo Mania v5_Brick.stl" and rep["renamed_files"] == 1
    assert m.library_rel == "toys/AP-1 - Igloo Mania" and f.library_rel == "toys/AP-1 - Igloo Mania/obj_1_Igloo Mania v5_Brick.stl"
    assert (lib / f.library_rel).exists() and not (lib / "toys" / "AP-1 - 이글루매니아").exists()
