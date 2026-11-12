"""The person's look is a named theme (glass-dark) and a reduce-transparency choice. Saved skins from before themes
migrate to glass-dark. /api/theme/look answers npmplus (shared key + signed-in email) and nobody else."""
import secrets as _s

from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.test_teams import as_user, real_auth  # noqa: F401  (fixture)


def _client():
    from app.main import app
    return TestClient(app, follow_redirects=False)


def _user(prefs, team_role=None):
    from app.db import session_scope
    from app.models import Membership, User
    from app.tenant import unscoped
    email = f"look-{_s.token_hex(3)}@x.example"
    with session_scope() as db, unscoped():
        u = User(sub=f"sub-{email}", email=email, name="Look", prefs=prefs)
        db.add(u)
        db.flush()
        if team_role:
            db.add(Membership(team_id=1, user_id=u.id, role=team_role))
        uid = u.id
    return email, uid


def _prefs(uid):
    from app.db import session_scope
    from app.models import User
    from app.tenant import unscoped
    with session_scope() as db, unscoped():
        return db.get(User, uid).prefs


def test_normalize_maps_every_old_look_to_glass_dark():
    from app.services.themes import normalize
    for old in ({"skin": "watchtower", "mode": "light"}, {"skin": "openprinthq", "mode": "dark"},
                {"skin": "classic", "mode": "system"}, {"skin": "bogus"}, {"theme": "neon"}, {}, None):
        assert normalize(old) == {"theme": "glass-dark", "reduce_transparency": "auto"}
    assert normalize({"skin": "classic", "other": 1, "reduce_transparency": "on"}) == \
        {"other": 1, "theme": "glass-dark", "reduce_transparency": "on"}


def test_saved_skins_migrate_at_startup():
    """THEME-01: every saved skin and mode becomes the glass-dark theme when the hub starts; other choices stay."""
    from app.db import THEME_MIGRATION, engine, init_db
    _, a = _user({"skin": "openprinthq", "mode": "dark"})
    _, b = _user({"skin": "classic", "mode": "light", "keep": "me"})
    _, c = _user(None)
    with engine().begin() as conn:
        conn.execute(text("DELETE FROM schema_version WHERE v = :v"), {"v": THEME_MIGRATION})
    init_db()
    assert _prefs(a) == {"theme": "glass-dark", "reduce_transparency": "auto"}
    assert _prefs(b) == {"keep": "me", "theme": "glass-dark", "reduce_transparency": "auto"}
    assert _prefs(c) is None
    with engine().begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM schema_version WHERE v = :v"), {"v": THEME_MIGRATION}).scalar() == 1


def test_prefs_api_saves_theme_and_transparency(real_auth):  # noqa: F811
    _, uid = _user({"skin": "watchtower", "mode": "dark"}, team_role="member")
    c = as_user(uid, 1)
    assert c.get("/api/me").json()["prefs"] == {"theme": "glass-dark", "reduce_transparency": "auto"}
    r = c.put("/api/me/prefs", json={"theme": "glass-dark", "reduce_transparency": "on"})
    assert r.status_code == 200 and r.json()["prefs"] == {"theme": "glass-dark", "reduce_transparency": "on"}
    assert _prefs(uid) == {"theme": "glass-dark", "reduce_transparency": "on"}
    assert c.put("/api/me/prefs", json={"theme": "watchtower"}).status_code == 400
    assert c.put("/api/me/prefs", json={"reduce_transparency": "sometimes"}).status_code == 400
    # an app from before themes still sends a skin: accepted, and the look stays a theme
    r = c.put("/api/me/prefs", json={"skin": "classic", "mode": "light"})
    assert r.status_code == 200 and _prefs(uid) == {"theme": "glass-dark", "reduce_transparency": "on"}


def test_look_needs_proxy_key(monkeypatch):
    from app.config import settings
    email, _ = _user({"skin": "openprinthq", "mode": "dark"})
    c = _client()
    monkeypatch.setattr(settings(), "theme_proxy_key", "")
    assert c.get("/api/theme/look", headers={"x-theme-user": email}).status_code == 204          # off when no key is set
    monkeypatch.setattr(settings(), "theme_proxy_key", "k" * 32)
    assert c.get("/api/theme/look", headers={"x-theme-user": email}).status_code == 204          # no key sent
    assert c.get("/api/theme/look", headers={"x-theme-user": email, "x-theme-proxy-key": "nope"}).status_code == 204
    r = c.get("/api/theme/look", headers={"x-theme-user": email.upper(), "x-theme-proxy-key": "k" * 32})
    assert r.status_code == 200 and r.json() == {"theme": "glass-dark", "reduce_transparency": "auto"}


def test_look_defaults(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings(), "theme_proxy_key", "k" * 32)
    c = _client()
    h = {"x-theme-proxy-key": "k" * 32}
    r = c.get("/api/theme/look", headers={**h, "x-theme-user": "nobody@x.example"})
    assert r.json() == {"theme": "glass-dark", "reduce_transparency": "auto"}
    email, _ = _user({"theme": "bogus", "reduce_transparency": "bogus"})
    assert c.get("/api/theme/look", headers={**h, "x-theme-user": email}).json() == \
        {"theme": "glass-dark", "reduce_transparency": "auto"}
