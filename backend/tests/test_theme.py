"""The supporting apps' shared look: /api/theme/look answers npmplus (shared key + Authentik email) and nobody else."""
import secrets as _s

from fastapi.testclient import TestClient


def _client():
    from app.main import app
    return TestClient(app, follow_redirects=False)


def _user(prefs):
    from app.db import session_scope
    from app.models import User
    from app.tenant import unscoped
    email = f"look-{_s.token_hex(3)}@x.example"
    with session_scope() as db, unscoped():
        db.add(User(sub=f"sub-{email}", email=email, name="Look", prefs=prefs))
    return email


def test_look_needs_proxy_key(monkeypatch):
    from app.config import settings
    email = _user({"skin": "openprinthq", "mode": "dark"})
    c = _client()
    monkeypatch.setattr(settings(), "theme_proxy_key", "")
    assert c.get("/api/theme/look", headers={"x-theme-user": email}).status_code == 204          # off when no key is set
    monkeypatch.setattr(settings(), "theme_proxy_key", "k" * 32)
    assert c.get("/api/theme/look", headers={"x-theme-user": email}).status_code == 204          # no key sent
    assert c.get("/api/theme/look", headers={"x-theme-user": email, "x-theme-proxy-key": "nope"}).status_code == 204
    r = c.get("/api/theme/look", headers={"x-theme-user": email.upper(), "x-theme-proxy-key": "k" * 32})
    assert r.status_code == 200 and r.json() == {"skin": "openprinthq", "mode": "dark"}


def test_look_defaults(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings(), "theme_proxy_key", "k" * 32)
    c = _client()
    h = {"x-theme-proxy-key": "k" * 32}
    r = c.get("/api/theme/look", headers={**h, "x-theme-user": "nobody@x.example"})
    assert r.json() == {"skin": "watchtower", "mode": "system"}
    email = _user({"skin": "bogus", "mode": "bogus"})
    assert c.get("/api/theme/look", headers={**h, "x-theme-user": email}).json() == {"skin": "watchtower", "mode": "system"}


def test_prefs_accept_openprinthq():
    from app.routers.ops import SKINS, PrefsIn
    assert "openprinthq" in SKINS
    assert PrefsIn(skin="openprinthq").skin == "openprinthq"
