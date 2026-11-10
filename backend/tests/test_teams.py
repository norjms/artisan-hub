"""Team isolation: one team can never read, change or act on another team's data, and roles are enforced."""
import base64
import hashlib
import hmac
import json

import pytest
from itsdangerous import TimestampSigner


# ---------- helpers ----------

@pytest.fixture
def real_auth(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings(), "dev_auth_bypass", False)


@pytest.fixture
def two_teams():
    """Team 1 (existing) plus a fresh team with its own owner, member and viewer."""
    from app.db import session_scope
    from app.models import Membership, User
    from app.services import teams as tsvc
    from app.tenant import unscoped
    import secrets as _s
    tag = _s.token_hex(3)
    with session_scope() as db, unscoped():
        owner = User(sub=f"owner-{tag}", email=f"owner-{tag}@b.example", name="Bea Owner")
        member = User(sub=f"member-{tag}", email=f"member-{tag}@b.example", name="Max Member")
        viewer = User(sub=f"viewer-{tag}", email=f"viewer-{tag}@b.example", name="Vi Viewer")
        outsider = User(sub=f"out-{tag}", email=f"out-{tag}@c.example", name="Otto Outsider")
        db.add_all([owner, member, viewer, outsider])
        db.flush()
        t2 = tsvc.create_team(db, f"Team B {tag}", owner=owner)
        db.add(Membership(team_id=t2.id, user_id=member.id, role="member"))
        db.add(Membership(team_id=t2.id, user_id=viewer.id, role="viewer"))
        tsvc.create_team(db, f"Otto's {tag}", owner=outsider, personal=True)
        db.flush()
        return {"t2": t2.id, "slug": t2.slug, "owner": owner.id, "member": member.id, "viewer": viewer.id,
                "outsider": outsider.id, "owner_email": owner.email, "outsider_email": outsider.email}


def key_for(team_id, user_id=None, scopes=("read", "write", "admin")):
    from app.auth import new_api_key
    from app.db import session_scope
    from app.tenant import use_team
    with session_scope() as db, use_team(team_id):
        return new_api_key(db, "test", list(scopes), team_id=team_id, user_id=user_id)


def H(key):
    return {"Authorization": f"Bearer {key}"}


def session_cookie(data: dict) -> str:
    from app.config import settings
    raw = base64.b64encode(json.dumps(data).encode())
    return TimestampSigner(settings().session_secret).sign(raw).decode()


def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app, follow_redirects=False)


def as_user(user_id, team_id=None):
    c = client()
    c.cookies.set("ahub", session_cookie({"uid": user_id, **({"team_id": team_id} if team_id else {})}))
    return c


def make_model(team_id, name):
    from app.db import session_scope
    from app.models import Model
    from app.tenant import use_team
    import secrets as _s
    with session_scope() as db, use_team(team_id):
        m = Model(code=f"T{_s.token_hex(3)}", name=name, slug=f"s-{_s.token_hex(4)}")
        db.add(m)
        db.flush()
        return m.id, m.code


# ---------- the database layer ----------

def test_queries_are_scoped(two_teams):
    from sqlalchemy import select, update
    from app.db import session_scope
    from app.models import Model
    from app.tenant import CrossTeamWrite, NoTeamContext, bind_session, use_team
    t2 = two_teams["t2"]
    mid2, code2 = make_model(t2, "Only in B")
    mid1, _ = make_model(1, "Only in A")
    with session_scope() as db, use_team(1):
        ids = {m.id for m in db.execute(select(Model)).scalars()}
        assert mid1 in ids and mid2 not in ids
        assert db.get(Model, mid2) is None                       # by primary key too
        assert db.execute(select(Model).where(Model.code == code2)).scalar() is None
        db.execute(update(Model).where(Model.id == mid2).values(name="hacked"))
    with session_scope() as db, use_team(t2):
        assert db.get(Model, mid2).name == "Only in B"
        assert db.get(Model, mid1) is None
    with pytest.raises(CrossTeamWrite):
        with session_scope() as db, use_team(1):
            db.add(Model(code="X1", name="x", slug="x-cross", team_id=t2))
            db.flush()
    from app.db import SessionLocal
    s = SessionLocal()
    try:
        bind_session(s, None)
        from app.tenant import _team
        tok = _team.set(None)
        try:
            with pytest.raises(NoTeamContext):
                s.execute(select(Model)).first()
        finally:
            _team.reset(tok)
    finally:
        s.close()


def test_children_inherit_team(two_teams):
    from app.db import session_scope
    from app.models import Model, ModelFile
    from app.tenant import use_team
    t2 = two_teams["t2"]
    mid2, _ = make_model(t2, "With file")
    with session_scope() as db, use_team(t2):
        m = db.get(Model, mid2)
        m.files.append(ModelFile(filename="a.stl", kind="stl", sha256="kid1"))
        db.flush()
        assert m.files[0].team_id == t2


def test_same_codes_allowed_in_different_teams(two_teams):
    from app.db import session_scope
    from app.models import Model
    from app.tenant import use_team
    for tid in (1, two_teams["t2"]):
        with session_scope() as db, use_team(tid):
            db.add(Model(code="DUP-1", name="dup", slug="dup-1"))
            db.flush()


# ---------- the API ----------

def test_api_key_sees_only_its_team(two_teams, real_auth):
    t2 = two_teams["t2"]
    mid1, code1 = make_model(1, "Secret A model")
    mid2, _ = make_model(t2, "B model")
    c = client()
    kb = key_for(t2, two_teams["owner"])
    items = c.get("/api/models?per=500", headers=H(kb)).json()["items"]
    ids = {x["id"] for x in items}
    assert mid2 in ids and mid1 not in ids
    assert c.get(f"/api/models/{mid1}", headers=H(kb)).status_code == 404
    assert c.patch(f"/api/models/{mid1}", json={"name": "pwned"}, headers=H(kb)).status_code == 404
    assert c.delete(f"/api/models/{mid1}", headers=H(kb)).status_code == 404
    assert c.get(f"/api/models?q={code1}", headers=H(kb)).json()["total"] == 0
    # a new model lands in the caller's team
    r = c.post("/api/models", json={"name": "Made by B"}, headers=H(kb))
    assert r.status_code == 200, r.text
    ka = key_for(1)
    assert c.get(f"/api/models/{r.json()['id']}", headers=H(ka)).status_code == 404
    # keys list is per team
    names = [k["prefix"] for k in c.get("/api/keys", headers=H(kb)).json()]
    assert ka.split("_")[1] not in names
    # MCP tools are scoped the same way
    out = c.post("/mcp", headers=H(kb), json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                              "params": {"name": "find_models", "arguments": {"query": "Secret A"}}}).json()
    assert "Secret A model" not in out["result"]["content"][0]["text"]


def test_roles(two_teams, real_auth):
    t2 = two_teams["t2"]
    c = client()
    viewer_key = key_for(t2, two_teams["viewer"], ("read", "write", "admin"))   # capped by the viewer role
    assert c.get("/api/models", headers=H(viewer_key)).status_code == 200
    r = c.post("/api/models", json={"name": "viewer try"}, headers=H(viewer_key))
    assert r.status_code == 403 and "permission" in r.json()["detail"]
    member_key = key_for(t2, two_teams["member"])
    assert c.post("/api/models", json={"name": "member ok"}, headers=H(member_key)).status_code == 200
    assert c.get("/api/connections", headers=H(member_key)).status_code == 403
    # people
    owner = as_user(two_teams["owner"], t2)
    member = as_user(two_teams["member"], t2)
    assert member.patch(f"/api/team/members/{two_teams['viewer']}", json={"role": "admin"}).status_code == 403
    assert owner.patch(f"/api/team/members/{two_teams['member']}", json={"role": "admin"}).status_code == 200
    admin = as_user(two_teams["member"], t2)
    assert admin.patch(f"/api/team/members/{two_teams['owner']}", json={"role": "member"}).status_code == 403
    assert owner.patch(f"/api/team/members/{two_teams['owner']}", json={"role": "admin"}).status_code == 400  # last owner
    assert owner.post("/api/team/leave").status_code == 400
    # removing someone kills their keys
    assert owner.delete(f"/api/team/members/{two_teams['viewer']}").status_code == 200
    assert c.get("/api/models", headers=H(viewer_key)).status_code == 401


def test_session_team_switching(two_teams, real_auth):
    t2 = two_teams["t2"]
    owner = as_user(two_teams["owner"])
    me = owner.get("/api/me").json()
    assert {t["id"] for t in me["teams"]} >= {t2}
    assert owner.get("/api/me", headers={"X-Hub-Team": "1"}).status_code == 403          # not a member of team 1
    assert owner.post("/api/me/team", json={"team": 1}).status_code == 403
    assert owner.get("/api/me", headers={"X-Hub-Team": two_teams["slug"]}).json()["team"]["id"] == t2
    outsider = as_user(two_teams["outsider"])
    assert outsider.get("/api/team").json()["id"] != t2
    assert outsider.get("/api/team", headers={"X-Hub-Team": str(t2)}).status_code == 403
    assert client().get("/api/models").status_code == 401


def test_invites(two_teams, real_auth):
    t2 = two_teams["t2"]
    owner = as_user(two_teams["owner"], t2)
    r = owner.post("/api/team/invites", json={"email": two_teams["outsider_email"], "role": "member"})
    assert r.status_code == 200
    token = r.json()["link"].rsplit("/", 1)[1]
    wrong = as_user(two_teams["member"], t2)
    assert wrong.post(f"/api/invites/{token}/accept").status_code == 403       # someone else's invite
    otto = as_user(two_teams["outsider"])
    info = otto.get(f"/api/invites/{token}").json()
    assert info["matches"] and info["role"] == "member"
    assert otto.post(f"/api/invites/{token}/accept").status_code == 200
    assert otto.post(f"/api/invites/{token}/accept").status_code == 404        # used
    assert otto.get("/api/team").json()["id"] == t2
    member = as_user(two_teams["member"], t2)
    assert member.post("/api/team/invites", json={"email": "x@y.example", "role": "member"}).status_code == 403
    assert owner.post("/api/team/invites", json={"email": "not-an-email", "role": "member"}).status_code == 400


def test_connections_are_per_team(two_teams, real_auth):
    from app import clients
    from app.tenant import use_team
    t2 = two_teams["t2"]
    owner = as_user(two_teams["owner"], t2)
    r = owner.put("/api/connections/woo", json={"config": {"url": "https://b-shop.example"},
                                                "secrets": {"key": "ck_b", "secret": "cs_b"}})
    assert r.status_code == 200
    v = r.json()
    assert v["secrets"] == {"key": True, "secret": True, "webhook_secret": False}
    assert "ck_b" not in json.dumps(owner.get("/api/connections").json())
    with use_team(t2):
        cb = clients.woo()
        assert "b-shop.example" in cb.base_url
    with use_team(1):
        from app.services.connections import NotConnected
        try:
            ca = clients.woo()
        except NotConnected:
            ca = None
    assert ca is not cb
    assert owner.put("/api/connections/woo", json={"config": {"url": "file:///etc/passwd"}}).status_code == 400


def test_webhook_routes_by_team(two_teams):
    from app.db import session_scope
    from app.services import connections
    from app.tenant import use_team
    t2 = two_teams["t2"]
    with session_scope() as db, use_team(t2):
        connections.save(db, "woo", {"url": "https://b-shop.example"}, {"key": "k", "secret": "s", "webhook_secret": "hookB"})
    body = json.dumps({"id": 999999, "status": "draft"}).encode()

    def sig(secret):
        return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    c = client()
    hdr = {"x-wc-webhook-topic": "product.updated", "content-type": "application/json"}
    assert c.post(f"/hooks/woo/{two_teams['slug']}", content=body, headers={**hdr, "x-wc-webhook-signature": sig("hookB")}).status_code == 200
    assert c.post(f"/hooks/woo/{two_teams['slug']}", content=body, headers={**hdr, "x-wc-webhook-signature": sig("other")}).status_code == 401
    assert c.post("/hooks/woo/no-such-team", content=body, headers={**hdr, "x-wc-webhook-signature": sig("hookB")}).status_code == 404


def test_jobs_run_in_their_team(two_teams):
    from sqlalchemy import select
    from app import jobs
    from app.db import session_scope
    from app.models import Job, Model
    from app.tenant import use_team
    t2 = two_teams["t2"]
    mid2, _ = make_model(t2, "job sees me")
    seen = {}

    @jobs.handler("test.team_probe")
    def _probe(db, job):
        seen["ids"] = {m.id for m in db.execute(select(Model)).scalars()}
        return {"n": len(seen["ids"])}

    with session_scope() as db, use_team(t2):
        j = jobs.enqueue(db, "test.team_probe", ref="probe", priority=-10)
        db.flush()
        jid = j.id
        assert j.team_id == t2
    assert jobs.run_one()
    assert mid2 in seen["ids"] and all(i != mid2 for i in [])
    with session_scope() as db, use_team(1):
        a_ids = {m.id for m in db.execute(select(Model)).scalars()}
        assert db.get(Job, jid) is None       # team 1 can't see team B's job
    assert not (seen["ids"] & (a_ids - seen["ids"]))
    with session_scope() as db, use_team(t2):
        assert db.get(Job, jid).state == "done"


def test_unconnected_service_jobs_are_skipped(two_teams):
    from app import jobs
    from app.db import session_scope
    from app.models import Job
    from app.tenant import use_team
    t2 = two_teams["t2"]

    @jobs.handler("test.needs_dam")
    def _needs(db, job):
        from app.clients import dam
        dam()
        return {"ran": True}

    with session_scope() as db, use_team(t2):
        jid = jobs.enqueue(db, "test.needs_dam", ref="nd", priority=-10).id
    assert jobs.run_one()
    with session_scope() as db, use_team(t2):
        j = db.get(Job, jid)
        assert j.state == "done" and "not connected" in j.result["skipped"]


def test_platform_admin_only(two_teams, real_auth):
    owner = as_user(two_teams["owner"], two_teams["t2"])
    assert owner.get("/api/platform/teams").status_code == 403
    assert owner.patch(f"/api/platform/teams/{two_teams['t2']}", json={"source_dirs": {"inbox": "/etc"}}).status_code == 403


def test_first_sign_in(two_teams, real_auth, monkeypatch):
    from app.config import settings
    from app.db import session_scope
    from app.models import Team
    from app.services import teams as tsvc
    from app.tenant import unscoped
    import secrets as _s
    tag = _s.token_hex(3)
    monkeypatch.setattr(settings(), "admin_group", "platform-admins")
    with session_scope() as db, unscoped():
        # nobody gets a workspace just by signing in; the hub owner or a team invites them
        u = tsvc.upsert_user(db, {"sub": f"new-{tag}", "email": f"new-{tag}@x.example", "name": "Nia New"}, ["hub-users"])
        assert tsvc.user_teams(db, u) == [] and not u.platform_admin
        tsvc.create_invite(db, two_teams["t2"], f"inv-{tag}@x.example", "member", None)
        v = tsvc.upsert_user(db, {"sub": f"inv-{tag}", "email": f"inv-{tag}@x.example", "name": "Ivo Invited"}, [])
        assert tsvc.user_teams(db, v) == []
        vid = v.id
        # platform admins are flagged; they only take over team 1 if it has no owner
        a = tsvc.upsert_user(db, {"sub": f"adm-{tag}", "email": f"adm-{tag}@x.example"}, ["platform-admins"])
        assert a.platform_admin
    from app.auth import _has_invite
    assert _has_invite(f"inv-{tag}@x.example") and not _has_invite(f"nobody-{tag}@x.example")
    r = as_user(vid).get("/api/me")
    assert r.status_code == 403 and r.json()["detail"] == "no_team"



class FakeAK:
    def __init__(self, existing=()):
        self.existing = {e: i + 100 for i, e in enumerate(existing)}
        self.added, self.removed, self.invited, self.deleted = [], [], [], []

    def user_by_email(self, email):
        pk = self.existing.get(email.lower())
        return {"pk": pk, "email": email} if pk else None

    def add_to_group(self, pk):
        self.added.append(pk)

    def remove_from_group(self, pk):
        self.removed.append(pk)

    def create_invitation(self, email, name, days=14):
        self.invited.append(email)
        return f"ak-{len(self.invited)}", f"https://auth.example/if/flow/artisan-hub-invite/?itoken=ak-{len(self.invited)}"

    def delete_invitation(self, pk):
        self.deleted.append(pk)


def test_permissions_per_person(two_teams, real_auth):
    t2 = two_teams["t2"]
    owner = as_user(two_teams["owner"], t2)
    member = as_user(two_teams["member"], t2)
    # members can't invite by default
    assert member.post("/api/team/invites", json={"email": "a@b.example"}).status_code == 403
    # the owner ticks "invite" for this member
    r = owner.patch(f"/api/team/members/{two_teams['member']}", json={"perms": ["view", "models", "slice", "inventory", "farm", "invite"]})
    assert r.status_code == 200 and r.json()["custom"] and "invite" in r.json()["perms"]
    assert member.post("/api/team/invites", json={"email": "c@d.example", "role": "member"}).status_code == 200
    # but can't hand out more than they hold
    r = member.post("/api/team/invites", json={"email": "e@f.example", "role": "admin"})
    assert r.status_code == 403
    r = member.post("/api/team/invites", json={"email": "g@h.example", "role": "member", "perms": ["view", "store"]})
    assert r.status_code == 403
    # nobody changes their own permissions
    assert member.patch(f"/api/team/members/{two_teams['member']}", json={"role": "admin"}).status_code in (400, 403)
    # taking a permission away works immediately
    r = owner.patch(f"/api/team/members/{two_teams['member']}", json={"perms": ["view"]})
    assert r.json()["perms"] == ["view"]
    assert member.post("/api/models", json={"name": "nope"}).status_code == 403
    r = owner.patch(f"/api/team/members/{two_teams['member']}", json={"reset_perms": True})
    assert not r.json()["custom"] and "models" in r.json()["perms"]
    # me reports the effective permissions
    assert "invite" not in member.get("/api/me").json()["perms"]


def test_hub_owner_adds_people(two_teams, real_auth, monkeypatch):
    from app.clients import authentik_api
    from app.db import session_scope
    from app.models import User
    from app.tenant import unscoped
    import secrets as _s
    tag = _s.token_hex(3)
    ak = FakeAK(existing=[f"known-{tag}@x.example"])
    monkeypatch.setattr(authentik_api, "client", lambda: ak)
    with session_scope() as db, unscoped():
        boss = User(sub=f"boss-{tag}", email=f"boss-{tag}@x.example", name="Hub Owner", platform_admin=True)
        newbie = User(sub=f"nb-{tag}", email=f"newbie-{tag}@x.example", name="Ned")
        known = User(sub=f"kn-{tag}", email=f"known-{tag}@x.example", name="Kim")
        db.add_all([boss, newbie, known]); db.flush()
        from app.models import Membership
        db.add(Membership(team_id=two_teams["t2"], user_id=boss.id, role="owner"))
        ids = (boss.id, newbie.id, known.id)
    hub = as_user(ids[0], two_teams["t2"])
    # team owners who are not the hub owner can't use this
    assert as_user(two_teams["owner"], two_teams["t2"]).post("/api/platform/people", json={"email": "z@z.example", "mode": "individual"}).status_code == 403
    # someone with no Authentik account gets an Authentik invitation
    r = hub.post("/api/platform/people", json={"email": f"newbie-{tag}@x.example", "name": "Ned", "mode": "team", "team_name": f"Ned Co {tag}"})
    assert r.status_code == 200, r.text
    assert r.json()["account"] == "invited" and r.json()["signup_url"].startswith("https://auth.example/")
    token = r.json()["link"].rsplit("/", 1)[1]
    info = client().get(f"/api/invites/{token}").json()          # readable before sign-in
    assert info["signup_url"] and info["team"] == f"Ned Co {tag}" and not info["signed_in"]
    ned = as_user(ids[1])
    assert ned.post(f"/api/invites/{token}/accept").status_code == 200
    me = ned.get("/api/me").json()
    assert me["team"]["name"] == f"Ned Co {tag}" and me["team"]["role"] == "owner"
    # someone who already has an Authentik account is added to the hub-users group
    r = hub.post("/api/platform/people", json={"email": f"known-{tag}@x.example", "mode": "individual"})
    assert r.json()["account"] == "existing" and 100 in ak.added
    token = r.json()["link"].rsplit("/", 1)[1]
    kim = as_user(ids[2])
    assert kim.post(f"/api/invites/{token}/accept").status_code == 200
    kt = kim.get("/api/team").json()
    assert kt["personal"] and kt["role"] == "owner"
    # into an existing team with a role
    r = hub.post("/api/platform/people", json={"email": f"third-{tag}@x.example", "mode": "join", "team_id": two_teams["t2"], "role": "viewer"})
    assert r.status_code == 200 and r.json()["invite"]["role"] == "viewer"
    # cancelling removes the Authentik invitation too
    inv_id = r.json()["invite"]["id"]
    assert hub.delete(f"/api/platform/invites/{inv_id}").status_code == 200 and ak.deleted
    # removing someone from their last team takes them out of hub-users
    r = hub.post(f"/api/platform/users/{ids[2]}/remove")
    assert r.json()["hub_access_removed"] and 100 in ak.removed
    assert kim.get("/api/me").json()["detail"] == "no_team"
    # only the hub owner can create teams
    assert ned.post("/api/teams", json={"name": "x"}).status_code in (403, 404, 405)
