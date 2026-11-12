"""The sidebar as data (/api/nav/v1), shared with ArtisanHub Farm, and the redirects from pages that moved."""
import pytest
from fastapi.testclient import TestClient

from app.routers.nav import NAV_PATHS

# The list is frontend/static/nav.json; a run that has only the backend folder cannot check it.
needs_list = pytest.mark.skipif(not any(p.is_file() for p in NAV_PATHS), reason="needs frontend/static/nav.json")

APPROVED = {
    "Print": ["Farm overview", "Printers", "Cameras", "Print queue", "Filament", "Slicing"],
    "Library": ["Models", "Add model", "Find models", "Licenses"],
    "Shop": ["Orders", "Quotes", "Inventory", "Shopping list", "Reports"],
    "Admin": ["Sync health", "People", "Connections", "Devices", "Settings"],
}


def _client():
    from app.main import app
    return TestClient(app, follow_redirects=False)


@needs_list
def test_nav_is_the_approved_list():
    r = _client().get("/api/nav/v1")
    assert r.status_code == 200
    d = r.json()
    assert d["version"] == 1 and d["product"] == "ArtisanHub"
    assert {g["label"]: [i["label"] for i in g["items"]] for g in d["groups"]} == APPROVED
    for g in d["groups"]:
        for it in g["items"]:
            assert it["href"].startswith("/") and it["icon"] and it["app"] in ("hub", "farm")


@needs_list
def test_farm_items_link_to_an_installed_farm():
    from app.routers.nav import load, resolve
    nav = load()
    hub = resolve(nav, {"app": None})
    farm = resolve(nav, {"app": "https://hub.example/apps/farm"})
    by_id = lambda d: {i["id"]: i for g in d["groups"] for i in g["items"]}  # noqa: E731
    assert by_id(hub)["printers"] == {**by_id(hub)["printers"], "href": "/farm?view=printers", "app": "hub"}
    assert by_id(farm)["printers"]["href"] == "https://hub.example/apps/farm/app/printers"
    assert by_id(farm)["printers"]["app"] == "farm"
    assert by_id(farm)["models"]["href"] == "/" and by_id(farm)["models"]["app"] == "hub"


def test_moved_pages_redirect():
    c = _client()
    for old, new in (("/team", "/people"), ("/platform", "/people?view=hub"), ("/add-on", "/devices"),
                     ("/tv?code=ABCD-EFGH", "/devices?code=ABCD-EFGH"), ("/printers", "/slicing?tab=printers")):
        r = c.get(old)
        assert r.status_code == 301 and r.headers["location"] == new, old
