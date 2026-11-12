"""Files sent to the hub from OrcaSlicer in the browser (services/orcaslicer_outbox.py)."""
import os
import time
from pathlib import Path

import pytest

from tests.test_core import make_sliced_3mf


@pytest.fixture()
def outbox(env, monkeypatch):
    base = env / "outbox"
    (base / "shared").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HUB_ORCASLICER_OUTBOX", str(base))
    from app.config import settings
    settings.cache_clear()
    yield base
    monkeypatch.delenv("HUB_ORCASLICER_OUTBOX")
    settings.cache_clear()


def _old(path: Path):
    t = time.time() - 60
    os.utime(path, (t, t))


def _pass():
    from app.db import session_scope
    from app.services import orcaslicer_outbox
    with session_scope() as db:
        return orcaslicer_outbox.take_in(db)


def _events(code_prefix=""):
    from app.db import session_scope
    from app.models import Event
    from sqlalchemy import select
    with session_scope() as db:
        return [e.message for e in db.execute(select(Event)).scalars() if "OrcaSlicer" in e.message]


def test_a_finished_file_becomes_a_draft_model_and_leaves_the_outbox(outbox):
    from app.db import session_scope
    from app.models import Model
    from sqlalchemy import select
    f = outbox / "shared" / "Cable clip.gcode.3mf"
    make_sliced_3mf(f)
    _old(f)
    assert _pass()["imported"] == 1
    assert not f.exists() and not (outbox / "shared" / "Not sent").exists()
    with session_scope() as db:
        m = db.execute(select(Model).where(Model.name == "Cable clip")).scalar_one()
        assert m.status == "draft" and m.license_code == "UNKNOWN"
        assert [x.kind for x in m.files] == ["3mf_sliced"]
        assert (Path(os.environ["HUB_LIBRARY_ROOT"]) / m.files[0].library_rel).is_file()
    assert "Sent from OrcaSlicer in the browser by OrcaSlicer in the browser" in _events()


def test_a_file_still_being_written_waits(outbox):
    f = outbox / "shared" / "fresh.stl"
    f.write_bytes(b"solid x\nendsolid x\n")
    assert _pass() == {"imported": 0, "refused": 0, "waiting": 1}
    assert f.exists()


def test_other_files_go_to_not_sent_with_the_reason(outbox):
    shared = outbox / "shared"
    bad = shared / "notes.txt"
    bad.write_text("hello")
    empty = shared / "empty.stl"
    empty.write_bytes(b"")
    for p in (bad, empty):
        _old(p)
    r = _pass()
    assert r["imported"] == 0 and r["refused"] == 2
    ns = shared / "Not sent"
    assert "3MF, STL, STEP, OBJ and G-code" in (ns / "notes.txt.txt").read_text()
    assert "empty" in (ns / "empty.stl.txt").read_text()
    # Nothing in Not sent is tried again.
    assert _pass()["refused"] == 0


def test_a_file_the_hub_cannot_read_goes_back_with_the_reason(outbox, monkeypatch):
    from app.services import importer
    def boom(*a, **k):
        raise ValueError("unreadable")
    monkeypatch.setattr(importer, "import_generic_folder", boom)
    f = outbox / "shared" / "broken.3mf"
    f.write_bytes(b"PK broken")
    _old(f)
    assert _pass()["refused"] == 1
    assert "could not read" in (outbox / "shared" / "Not sent" / "broken.3mf.txt").read_text()
    assert (outbox / "shared" / "Not sent" / "broken.3mf").read_bytes() == b"PK broken"


def test_a_persons_folder_credits_that_person_and_unknown_folders_are_left_alone(outbox):
    from app.db import session_scope
    from app.models import User
    with session_scope() as db:
        db.add(User(sub="sub-alex", username="alex", email="alex@example.com"))
    people = outbox / "people"
    (people / "alex").mkdir(parents=True)
    (people / "mallory").mkdir()
    mine = people / "alex" / "bracket.stl"
    mine.write_bytes(b"solid b\nendsolid b\n")
    stray = people / "mallory" / "x.stl"
    stray.write_bytes(b"solid m\nendsolid m\n")
    _old(mine)
    _old(stray)
    assert _pass()["imported"] == 1
    assert not mine.exists() and stray.exists()
    assert "Sent from OrcaSlicer in the browser by alex" in _events()


def test_without_the_setting_nothing_happens(env):
    from app.config import settings
    settings.cache_clear()
    assert _pass() == {"skipped": True}
