"""Real filament use per job: printer history, progress, weigh-ins."""
import secrets as _s
from datetime import datetime, timedelta, timezone

import pytest


def _job(db, **kw):
    from app.models import FarmJob
    t = datetime.now(timezone.utc) - timedelta(hours=2)
    d = dict(external_id="fu-" + _s.token_hex(4), name="Widget", status="completed", printer_name="voron01",
             material="PLA", filament_g=50.0, started_at=t, completed_at=t + timedelta(hours=1), est_time_s=3600)
    d.update(kw)
    fj = FarmJob(**d)
    db.add(fj)
    db.flush()
    return fj


def test_mm_to_g():
    from app.services.filament_use import mm_to_g
    # 1 m of 1.75 mm PLA is about 2.98 g
    assert mm_to_g(1000, "PLA") == pytest.approx(2.982, rel=0.01)
    assert mm_to_g(1000, "PETG") > mm_to_g(1000, "ABS")


def test_printer_history_wins(monkeypatch):
    from app.db import session_scope
    from app.services import filament_use as fu
    with session_scope() as db:
        fj = _job(db)
        end = fj.completed_at.timestamp()
        hist = [{"start_time": end - 3500, "end_time": end + 120, "filament_used": 15000.0},     # 2 minutes off: match
                {"start_time": end - 90000, "end_time": end - 86000, "filament_used": 999.0}]
        monkeypatch.setattr(fu, "moonraker_history", lambda url, since, limit=200: hist)
        src = fu.measure(db, fj, hosts={"voron01": "http://x:7125"})
        assert src == "printer" and fj.used_g == pytest.approx(fu.mm_to_g(15000, "PLA"), rel=1e-3)
        # nothing within the window: falls back to the estimate
        other = _job(db, printer_name="voron01", started_at=fj.started_at - timedelta(days=3),
                     completed_at=fj.completed_at - timedelta(days=3))
        assert fu.measure(db, other, hist_cache={"http://x:7125": hist}, hosts={"voron01": "http://x:7125"}) == "estimate"
        assert other.used_g == 50.0


def test_progress_for_failed():
    from app.db import session_scope
    from app.services import filament_use as fu
    with session_scope() as db:
        fj = _job(db, status="failed", progress=40.0, printer_name="MK4S")
        assert fu.measure(db, fj, hosts={}) == "progress" and fj.used_g == 20.0


def test_weigh_in_spreads_difference():
    from app.db import session_scope
    from app.services import filament_use as fu
    sm = 900000 + int(_s.token_hex(2), 16)
    with session_scope() as db:
        a = _job(db, printer_name="MK3S", spools=[{"sm": sm, "wt": 1, "g": 40.0}], filament_g=40.0, used_g=40.0, used_source="estimate")
        b = _job(db, printer_name="MK3S", spools=[{"sm": sm, "wt": 1, "g": 30.0}, {"sm": sm + 1, "wt": 2, "g": 30.0}],
                 filament_g=60.0, used_g=60.0, used_source="estimate")
        c = _job(db, printer_name="voron01", spools=[{"sm": sm, "wt": 1, "g": 10.0}], filament_g=10.0, used_g=11.0,
                 used_source="printer")
        row = fu.weigh_in(db, sm, extra_used_g=7.0, remaining_g=500)     # the scale says 7 g more went
        # adjustable grams on this spool: 40 + 30 = 70, so factor 77/70 = 1.1
        assert row.factor == pytest.approx(1.1, rel=1e-3)
        assert a.used_g == pytest.approx(44.0) and a.used_source == "weighed"
        assert b.used_g == pytest.approx(63.0)               # only its 30 g from this spool scaled
        assert c.used_g == 11.0 and c.used_source == "printer"
        # the next weigh-in only looks at prints since this one
        assert fu.weigh_in(db, sm, extra_used_g=3, remaining_g=497).note == "no prints on this spool to adjust"
        # a huge change is a correction, not a weigh-in
        _job(db, printer_name="MK3S", spools=[{"sm": sm + 5, "wt": 9, "g": 40.0}], filament_g=40.0, used_g=40.0)
        row2 = fu.weigh_in(db, sm + 5, extra_used_g=-800, remaining_g=1000)
        assert row2.factor is None and "too large" in (row2.note or "")


def test_model_ratio_feeds_profit():
    from app.db import session_scope
    from app.models import Model
    from app.services import filament_use as fu
    with session_scope() as db:
        m = db.query(Model).first()
        if m is None:
            pytest.skip("no model in the test database")
        _job(db, model_id=m.id, filament_g=100.0, used_g=110.0, used_source="printer")
        r = fu.model_ratios(db)
        assert m.id in r and r[m.id]["prints"] >= 1


def test_plan_and_snapshot_split(monkeypatch):
    from app.db import session_scope
    from app.models import SpoolLink
    from app.services import filament_use as fu
    plan = fu.plan_from_metadata({"filaments": [{"type": "PLA", "color": "#FF0000", "used_g": 20},
                                                {"type": "PLA", "color": "#0000FF", "used_g": 5}]})
    assert plan == [{"type": "PLA", "color": "ff0000", "g": 20.0}, {"type": "PLA", "color": "0000ff", "g": 5.0}]

    class W:
        def printers_full(self):
            return {"printers": [{"id": "p1", "name": "H2D"}]}

        def filament_assignments(self):
            return {"printers": {"p1": {"assignments": [
                {"spool_id": 71, "material_type": "PLA", "spool_color_hex": "#EE1111"},
                {"spool_id": 72, "material_type": "PLA", "spool_color_hex": "#1111EE"},
                {"spool_id": None, "material_type": "PLA"}]}}}
    import app.clients as clients
    monkeypatch.setattr(clients, "legacy_farm", lambda: W())
    with session_scope() as db:
        link = SpoolLink(spoolman_id=8071, wt_spool_id=71, last_g=500)
        db.add(link)
        db.flush()
        fj = _job(db, printer_name="H2D", fil_plan=plan, filament_g=25.0)
        out = fu.snapshot_spools(db, fj)
        by = {s["wt"]: s for s in out}
        assert by[71]["g"] == 20.0 and by[71]["sm"] == 8071
        assert by[72]["g"] == 5.0 and by[72]["sm"] is None
        db.delete(link)          # leave no links behind for the spool sync tests
