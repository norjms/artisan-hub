"""/api/plugin/farm: the bundle the OrcaSlicer plugin polls. Parts that need the farm degrade to an error each."""
from fastapi.testclient import TestClient


def test_plugin_farm_bundle(monkeypatch):
    from app.main import app
    from app.services import schedule

    def boom(*a, **k):
        raise RuntimeError("The farm is not connected")
    monkeypatch.setattr(schedule, "build", boom)
    r = TestClient(app).get("/api/plugin/farm")
    assert r.status_code == 200
    d = r.json()
    assert d["api"] == 1
    for k in ("checks", "stagger", "maintenance", "filament", "report", "quotes_waiting", "look"):
        assert k in d, k
    assert d["errors"].get("timeline", "").startswith("The farm is not connected")
    assert d["stagger"]["minutes"] >= 0
