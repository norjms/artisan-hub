"""OAuth for the MCP connector: discovery, registration, PKCE code flow, refresh rotation, token scope, revocation."""
import base64
import hashlib
import re
import secrets
from urllib.parse import parse_qs, urlparse

import pytest


def pkce():
    v = secrets.token_urlsafe(48)
    c = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    return v, c


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app, follow_redirects=False)


def connect(c, scope="read write"):
    reg = c.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                                          "token_endpoint_auth_method": "none"})
    assert reg.status_code == 201, reg.text
    cid = reg.json()["client_id"]
    v, ch = pkce()
    q = {"response_type": "code", "client_id": cid, "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
         "code_challenge": ch, "code_challenge_method": "S256", "state": "st1", "scope": scope,
         "resource": "http://test/mcp"}
    page = c.get("/oauth/authorize", params=q)
    assert page.status_code == 200 and "Allow Claude" in page.text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    form = {k: v_ for k, v_ in q.items() if k != "response_type"}
    team_id = re.search(r'name="team_id" value="(\d+)"', page.text).group(1)
    r = c.post("/oauth/authorize", data={**form, "csrf": csrf, "decision": "allow", "team_id": team_id})
    assert r.status_code == 302
    loc = urlparse(r.headers["location"])
    qs = parse_qs(loc.query)
    assert loc.netloc == "claude.ai" and qs["state"] == ["st1"]
    return cid, v, qs["code"][0]


def test_discovery(client):
    pr = client.get("/.well-known/oauth-protected-resource").json()
    assert pr["resource"] == "http://test/mcp" and pr["authorization_servers"] == ["http://test"]
    asm = client.get("/.well-known/oauth-authorization-server").json()
    assert asm["code_challenge_methods_supported"] == ["S256"] and asm["registration_endpoint"].endswith("/oauth/register")


def test_registration_rejects_bad_redirects(client):
    assert client.post("/oauth/register", json={"redirect_uris": ["http://evil.example/cb"]}).status_code == 400
    assert client.post("/oauth/register", json={"redirect_uris": []}).status_code == 400
    ok = client.post("/oauth/register", json={"redirect_uris": ["http://localhost:5555/callback"]})
    assert ok.status_code == 201


def test_authorize_rejects_unregistered_redirect(client):
    cid = client.post("/oauth/register", json={"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]}).json()["client_id"]
    _, ch = pkce()
    r = client.get("/oauth/authorize", params={"response_type": "code", "client_id": cid, "redirect_uri": "https://evil.example/cb",
                                               "code_challenge": ch, "code_challenge_method": "S256"})
    assert r.status_code == 400 and "location" not in r.headers


def test_code_flow_and_mcp(client, monkeypatch):
    from app.config import settings
    cid, verifier, code = connect(client)
    bad = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid,
                                            "redirect_uri": "https://claude.ai/api/mcp/auth_callback", "code_verifier": "wrong"})
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_grant"
    tok = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid,
                                            "redirect_uri": "https://claude.ai/api/mcp/auth_callback", "code_verifier": verifier})
    assert tok.status_code == 200, tok.text
    t = tok.json()
    assert t["access_token"].startswith("aht_") and t["scope"] == "read write"
    # real auth for the rest of the test
    monkeypatch.setattr(settings(), "dev_auth_bypass", False)
    h = {"Authorization": "Bearer " + t["access_token"]}
    no = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert no.status_code == 401 and "resource_metadata=" in no.headers["www-authenticate"]
    ok = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=h)
    assert ok.status_code == 200 and ok.json()["result"]["tools"]
    # an MCP token is no good on the rest of the API
    assert client.get("/api/models", headers=h).status_code == 401
    # the code can't be used twice, and replaying it revokes the grant
    again = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid,
                                              "redirect_uri": "https://claude.ai/api/mcp/auth_callback", "code_verifier": verifier})
    assert again.status_code == 400
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=h).status_code == 401


def test_refresh_rotation_and_revoke(client, monkeypatch):
    from app.config import settings
    cid, verifier, code = connect(client, scope="read")
    t = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid,
                                          "redirect_uri": "https://claude.ai/api/mcp/auth_callback", "code_verifier": verifier}).json()
    assert t["scope"] == "read"
    t2 = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": t["refresh_token"], "client_id": cid}).json()
    assert t2["access_token"] != t["access_token"]
    monkeypatch.setattr(settings(), "dev_auth_bypass", False)
    h = {"Authorization": "Bearer " + t2["access_token"]}
    w = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": "bulk_slice", "arguments": {"models": ["X"]}}}, headers=h).json()
    assert w["result"]["isError"] and "slice permission" in w["result"]["content"][0]["text"]
    # reusing the old refresh token is treated as theft
    reuse = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": t["refresh_token"], "client_id": cid})
    assert reuse.status_code == 400
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=h).status_code == 401


def test_connections_list_and_disconnect(client):
    cid, verifier, code = connect(client)
    t = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid,
                                          "redirect_uri": "https://claude.ai/api/mcp/auth_callback", "code_verifier": verifier}).json()
    conns = client.get("/api/oauth/connections").json()
    mine = [x for x in conns if x["app"] == "Claude"]
    assert mine
    from app.db import session_scope
    from app.models import OAuthToken
    from app.routers.oauth import principal_for, _h
    with session_scope() as db:
        assert principal_for(db, t["access_token"])
        g = db.query(OAuthToken).filter_by(hash=_h(t["access_token"])).one().grant
    assert g in {x["grant"] for x in mine}
    assert client.delete(f"/api/oauth/connections/{g}").json()["ok"]
    with session_scope() as db:
        assert principal_for(db, t["access_token"]) is None
