"""Mobile API v1: scoped, expiring, instance-bound keys; owner provisioning; curated calls; the raw relay."""
import json
import secrets as _s
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient


def _client():
    from app.main import app
    return TestClient(app, follow_redirects=False)


def _user(admin=False, team_id=1, role="owner"):
    from app.db import session_scope
    from app.models import Membership, User
    from app.tenant import unscoped
    tag = _s.token_hex(3)
    with session_scope() as db, unscoped():
        u = User(sub=f"m-{tag}", email=f"m-{tag}@x.example", name="M", platform_admin=admin)
        db.add(u)
        db.flush()
        db.add(Membership(team_id=team_id, user_id=u.id, role=role))
        return u.id, u.email


def _key(scopes, user_id=None, **kw):
    from app.auth import new_api_key
    from app.db import session_scope
    from app.models import ApiKey
    with session_scope() as db:
        raw = new_api_key(db, "t", scopes, team_id=1, user_id=user_id)
        k = db.query(ApiKey).filter(ApiKey.prefix == raw.split("_")[1]).one()
        for a, v in kw.items():
            setattr(k, a, v)
    return raw


def H(raw, inst=None):
    h = {"Authorization": f"Bearer {raw}"}
    if inst:
        h["X-Hub-Instance"] = inst
    return h


def test_me_and_scopes_by_permission_name():
    c = _client()
    raw = _key(["farm"])
    d = c.get("/api/mobile/v1/me", headers=H(raw)).json()
    assert d["api"] == 1 and set(d["perms"]) == {"view", "farm"} and d["relay"] == []
    # farm only: can't push products live
    assert c.post("/api/mobile/v1/products/1/live", json={"live": True}, headers=H(raw)).status_code == 403


def test_expired_key_refused():
    raw = _key(["read"], expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    r = _client().get("/api/mobile/v1/me", headers=H(raw))
    assert r.status_code == 401 and "expired" in r.text


def test_instance_binding():
    c = _client()
    raw = _key(["read"], bind_instance=True)
    assert c.get("/api/mobile/v1/me", headers=H(raw)).status_code == 401          # must say which install
    assert c.get("/api/mobile/v1/me", headers=H(raw, "phone-A")).status_code == 200  # binds to phone-A
    assert c.get("/api/mobile/v1/me", headers=H(raw, "phone-A")).status_code == 200
    r = c.get("/api/mobile/v1/me", headers=H(raw, "phone-B"))
    assert r.status_code == 401 and "another app instance" in r.text
    fixed = _key(["read"], instance_id="tablet-1")
    assert c.get("/api/mobile/v1/me", headers=H(fixed, "tablet-1")).status_code == 200
    assert c.get("/api/mobile/v1/me", headers=H(fixed)).status_code == 401


def test_make_key_never_exceeds_maker():
    c = _client()
    uid, _ = _user(role="member")
    maker = _key(["write", "api_keys"], user_id=uid)          # a member: no store, no connections
    # api_keys isn't a member permission, so the maker's key is trimmed to the member's own permissions
    assert c.post("/api/mobile/v1/keys", json={"name": "x", "scopes": ["read"]}, headers=H(maker)).status_code == 403
    admin_uid, _ = _user(role="owner")
    owner_key = _key(["admin"], user_id=admin_uid)
    r = c.post("/api/mobile/v1/keys", json={"name": "phone", "scopes": ["farm", "inventory"], "expires_in_days": 30},
               headers=H(owner_key))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["key"].startswith("ahk_") and d["instance"] == "binds on first use" and d["expires_at"]
    assert c.post("/api/mobile/v1/keys", json={"name": "x", "scopes": ["owner"]}, headers=H(owner_key)).status_code == 403
    assert c.post("/api/mobile/v1/keys", json={"name": "x", "scopes": ["bogus"]}, headers=H(owner_key)).status_code == 400
    farm_key = _key(["farm", "api_keys"], user_id=admin_uid)
    assert c.post("/api/mobile/v1/keys", json={"name": "x", "scopes": ["relay:*"]}, headers=H(farm_key)).status_code == 403
    keys = c.get("/api/mobile/v1/keys", headers=H(owner_key)).json()["keys"]
    kid = next(k["id"] for k in keys if k["name"] == "phone")
    assert c.delete(f"/api/mobile/v1/keys/{kid}", headers=H(owner_key)).json() == {"ok": True}
    assert c.get("/api/mobile/v1/me", headers=H(d["key"], "p1")).status_code == 401


def test_owner_provisioning():
    c = _client()
    owner_uid, _ = _user(admin=True)
    not_owner_uid, _ = _user(admin=False)
    okey = _key(["owner", "admin"], user_id=owner_uid)
    plain = _key(["owner", "admin"], user_id=not_owner_uid)      # owner scope from someone who isn't the owner: ignored
    assert c.get("/api/mobile/v1/owner/teams", headers=H(plain)).status_code == 403
    assert c.get("/api/mobile/v1/owner/teams", headers=H(okey)).status_code == 200
    _, member_email = _user(admin=False)
    # An installation has one team (B25): there is none to make, and the owner works with the one there is.
    r = c.post("/api/mobile/v1/owner/teams", json={"name": "Shop B " + _s.token_hex(2), "owner_email": member_email}, headers=H(okey))
    assert r.status_code == 410 and "one team" in r.json()["detail"]
    assert [t["id"] for t in c.get("/api/mobile/v1/owner/teams", headers=H(okey)).json()["teams"]][:1] == [1]
    m = c.get("/api/mobile/v1/owner/teams/1/members", headers=H(okey)).json()["members"]
    assert m and all("role" in x for x in m)
    k = c.post("/api/mobile/v1/owner/teams/1/keys", json={"name": "A app", "scopes": ["write"], "expires_in_days": 7},
               headers=H(okey)).json()
    me = c.get("/api/mobile/v1/me", headers=H(k["key"], "b1")).json()
    assert me["team"]["id"] == 1 and "farm" in me["perms"]
    assert c.post("/api/mobile/v1/owner/teams/1/keys", json={"name": "x", "scopes": ["owner"]}, headers=H(okey)).status_code == 400


class _Spool(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def _go(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        _Spool.seen.append((self.command, self.path, body, dict(self.headers)))
        out = json.dumps({"ok": True, "path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Set-Cookie", "secret=1")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_GET = do_POST = do_PATCH = _go


@pytest.fixture
def spoolman_mock(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Spool)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    from app.services import connections
    real = connections.get

    def fake(kind, team_id=None, required=True):
        if kind == "spoolman":
            return {"url": "http://127.0.0.1:%d" % srv.server_address[1], "_version": 1}
        return real(kind, team_id, required)
    monkeypatch.setattr(connections, "get", fake)
    _Spool.seen.clear()
    yield
    srv.shutdown()


def test_relay(spoolman_mock):
    c = _client()
    uid, _ = _user(role="owner")
    no_relay = _key(["admin"], user_id=uid)
    assert c.get("/api/mobile/v1/relay/spoolman/v1/spool", headers=H(no_relay)).status_code == 403
    wrong_app = _key(["admin", "relay:woo"], user_id=uid)
    assert c.get("/api/mobile/v1/relay/spoolman/v1/spool", headers=H(wrong_app)).status_code == 403
    no_conn = _key(["farm", "relay:spoolman"], user_id=uid)            # relay scope without the connections permission
    assert c.get("/api/mobile/v1/relay/spoolman/v1/spool", headers=H(no_conn)).status_code == 403
    k = _key(["admin", "relay:spoolman"], user_id=uid)
    r = c.get("/api/mobile/v1/relay/spoolman/v1/spool?limit=2", headers=H(k))
    assert r.status_code == 200 and r.json()["path"] == "/api/v1/spool?limit=2"
    assert "set-cookie" not in r.headers
    method, path, body, hdrs = _Spool.seen[-1]
    assert "Authorization" not in hdrs and "authorization" not in {h.lower() for h in hdrs if h.lower() == "authorization"}
    r = c.patch("/api/mobile/v1/relay/spoolman/v1/spool/3", json={"location": "Shelf"}, headers=H(k))
    assert r.status_code == 200 and _Spool.seen[-1][2] == b'{"location":"Shelf"}'
    assert c.get("/api/mobile/v1/relay/spoolman/..%2F..%2Fetc", headers=H(k)).status_code in (400, 404)
    assert c.get("/api/mobile/v1/relay/spoolman/../x", headers=H(k)).status_code in (400, 404)
    me = c.get("/api/mobile/v1/me", headers=H(k)).json()
    assert me["relay"] == ["spoolman"]
    from app.db import session_scope
    from app.models import Event
    with session_scope() as db:
        assert db.query(Event).filter(Event.ref == "relay", Event.message.like("spoolman PATCH%")).count() >= 1


def test_overview_survives_watchtower_down(monkeypatch):
    import app.routers.mobile as mob

    def boom():
        raise RuntimeError("Watchtower is not connected")
    monkeypatch.setattr(mob, "_wt", boom)
    d = _client().get("/api/mobile/v1/overview").json()
    assert d["printers"] is None and "printers" in d["errors"] and "plate_checks_waiting" in d
