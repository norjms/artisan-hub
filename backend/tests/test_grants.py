"""Commercial license grants (memberships, purchases) gate COMMERCIAL models."""
from datetime import date, timedelta


def _m(grants):
    from app.models import Model, Designer, LicenseGrant
    d = Designer(name="Deltaprints", slug="dp")
    for g in grants:
        d.grants.append(LicenseGrant(**g))
    return Model(code="M1", name="Vase", slug="m1", source="deltaprints", license_code="COMMERCIAL", designer=d)


def test_no_grant_recorded_is_allowed():
    from app.services.licensing import sale_problems, commercial_cover
    m = _m([])
    assert sale_problems(m) == [] and commercial_cover(m)["recorded"] is False


def test_active_expiring_expired():
    from app.services.licensing import sale_problems, commercial_cover, grant_status
    t = date.today()
    m = _m([{"name": "Patreon tier", "expires_on": t + timedelta(days=40), "covers_all": True, "sales_need_active": True}])
    assert sale_problems(m) == [] and commercial_cover(m)["status"]["state"] == "active"
    m = _m([{"name": "Patreon tier", "expires_on": t + timedelta(days=5), "covers_all": True, "sales_need_active": True}])
    assert sale_problems(m) == [] and grant_status(m.designer.grants[0])["state"] == "expiring"
    m = _m([{"name": "Patreon tier", "expires_on": t - timedelta(days=1), "covers_all": True, "sales_need_active": True}])
    p = sale_problems(m)
    assert p and "ended" in p[0]


def test_purchase_survives_end_and_newest_grant_wins():
    from app.services.licensing import sale_problems
    t = date.today()
    m = _m([{"name": "One-time", "kind": "purchase", "expires_on": t - timedelta(days=100), "covers_all": True,
             "sales_need_active": False}])
    assert sale_problems(m) == []
    m = _m([{"name": "Old", "expires_on": t - timedelta(days=30), "covers_all": True, "sales_need_active": True},
            {"name": "Renewed", "expires_on": t + timedelta(days=300), "covers_all": True, "sales_need_active": True}])
    assert sale_problems(m) == []


def test_grant_scoped_to_models():
    from app.services.licensing import sale_problems, commercial_cover
    t = date.today()
    m = _m([{"name": "Just M9", "expires_on": t - timedelta(days=1), "covers_all": False, "model_codes": ["M9"],
             "sales_need_active": True}])
    assert commercial_cover(m)["recorded"] is False and sale_problems(m) == []


def test_api_grant_crud(env):
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    d = c.post("/api/designers", json={"name": "Grant Designer"}).json()
    g = c.post(f"/api/designers/{d['id']}/grants", json={"name": "Membership", "expires_on": "2030-01-01"}).json()
    assert g["state"] == "active"
    g = c.patch(f"/api/grants/{g['id']}", json={"expires_on": "2020-01-01"}).json()
    assert g["state"] == "expired"
    ov = c.get("/api/licenses").json()
    assert any(x["designer"] == "Grant Designer" and x["status"] == "expired" for x in ov["designers"])
    assert c.delete(f"/api/grants/{g['id']}").json()["ok"]
