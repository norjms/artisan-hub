"""The hub links to the model library (gyroidhub) when it has one: from its menu, and from each model to that
model in the library."""


def test_a_model_links_to_its_folder_in_the_model_library(monkeypatch):
    from app.config import settings
    from app.models import Model
    from app.serialize import _models_url

    m = Model(code="C1", name="Cube", slug="c1-cube", library_rel="functional/C1 - Cube")
    monkeypatch.setattr(settings(), "models_url", "")
    assert _models_url(m) is None                       # no model library
    monkeypatch.setattr(settings(), "models_url", "https://hub.workshop.lan/apps/models/")
    assert _models_url(m) == "https://hub.workshop.lan/apps/models/#/models?search=C1%20-%20Cube"
    m.library_rel = None
    assert _models_url(m) is None                       # no folder yet


def test_the_menu_links_to_the_model_library(monkeypatch):
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.main import app

    monkeypatch.setattr(settings(), "models_url", "https://hub.workshop.lan/apps/models/")
    me = TestClient(app).get("/api/me").json()
    assert me["links"]["models"] == "https://hub.workshop.lan/apps/models/"
    monkeypatch.setattr(settings(), "models_url", "")
    assert TestClient(app).get("/api/me").json()["links"]["models"] is None
