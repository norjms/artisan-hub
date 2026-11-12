"""OrcaSlicer in the browser: who may open it (an installation that runs it behind Authelia, HUB_ORCASLICER_GROUP).

The hub's owners always may. Anyone else may when they are in its LLDAP group, which the owner turns on or off per
person on People. LLDAP is a fake that keeps logins and groups the way LLDAP does, and can fail.
"""

import secrets

import pytest

from app.clients.base import ApiError
from tests.test_teams import as_user, people, real_auth  # noqa: F401  (fixtures)

GROUP = "orcaslicer-users"


class FakeLldap:
    def __init__(self):
        self.logins = {}
        self.calls = []
        self.fail = False

    def login(self, uid, email, groups=()):
        self.logins[uid] = {"id": uid, "email": email.lower(), "groups": set(groups)}

    def _check(self):
        if self.fail:
            raise ApiError("lldap", None, "LLDAP did not answer: ConnectError")

    def user_by_email(self, email):
        self._check()
        for u in self.logins.values():
            if u["email"] == email.strip().lower():
                return {"id": u["id"], "email": u["email"], "groups": [{"id": 9, "displayName": g} for g in u["groups"]]}
        return None

    def group_members(self, name):
        self._check()
        return [{"id": u["id"], "email": u["email"]} for u in self.logins.values() if name in u["groups"]]

    def add_to_group(self, uid, group=None):
        self._check()
        self.calls.append(("add", uid, group))
        self.logins[uid]["groups"].add(group)

    def remove_from_group(self, uid, group=None):
        self._check()
        self.calls.append(("remove", uid, group))
        self.logins[uid]["groups"].discard(group)


@pytest.fixture
def orca(monkeypatch, people):
    """A hub that runs OrcaSlicer in the browser, its owner, and logins in LLDAP for the team's people."""
    from app.clients import lldap_api
    from app.config import settings
    from app.db import session_scope
    from app.models import Membership, User
    from app.tenant import unscoped
    fake = FakeLldap()
    monkeypatch.setattr(lldap_api, "client", lambda: fake)
    monkeypatch.setattr(settings(), "orcaslicer_group", GROUP)
    tag = secrets.token_hex(3)
    with session_scope() as db, unscoped():
        boss = User(sub=f"boss-o-{tag}", email=f"boss-o-{tag}@shop.example", name="Hub Owner", platform_admin=True)
        db.add(boss)
        db.flush()
        db.add(Membership(team_id=1, user_id=boss.id, role="owner"))
        member = db.get(User, people["member"])
        member.name = f"Mo Member {tag}"     # its own name, so its log lines are its own
        fake.login("boss", boss.email, ("hub-admins", "hub-users"))
        fake.login("mo", member.email, ("hub-users",))
        ids = {"boss": boss.id, "member": member.id, "member_name": member.label}
    return {**people, **ids, "lldap": fake}


def _events(text):
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import Event
    from app.tenant import unscoped
    with session_scope() as db, unscoped():
        return [e.message for e in db.execute(select(Event).where(Event.message.contains(text))).scalars()]


def test_only_the_hubs_owner_reads_or_changes_it(orca, real_auth):
    for who in ("owner", "member"):        # a team owner who is not the hub's owner, and a member
        c = as_user(orca[who])
        assert c.get("/api/platform/apps/orcaslicer").status_code == 403
        r = c.put(f"/api/platform/users/{orca['member']}/orcaslicer", json={"allowed": True})
        assert r.status_code == 403
    assert orca["lldap"].calls == []


def test_the_owner_turns_it_on_and_off_and_each_change_is_logged(orca, real_auth):
    hub = as_user(orca["boss"])
    seen = hub.get("/api/platform/apps/orcaslicer").json()
    assert seen["available"] is True and seen["group"] == GROUP and seen["error"] is None
    assert orca["boss"] in seen["allowed"] and orca["boss"] in seen["owners"]
    assert orca["member"] not in seen["allowed"]

    r = hub.put(f"/api/platform/users/{orca['member']}/orcaslicer", json={"allowed": True})
    assert r.status_code == 200, r.text
    assert r.json() == {"user_id": orca["member"], "allowed": True, "changed": True}
    assert GROUP in orca["lldap"].logins["mo"]["groups"]
    assert orca["member"] in hub.get("/api/platform/apps/orcaslicer").json()["allowed"]

    r = hub.put(f"/api/platform/users/{orca['member']}/orcaslicer", json={"allowed": False})
    assert r.status_code == 200 and r.json()["changed"] is True
    assert GROUP not in orca["lldap"].logins["mo"]["groups"]
    assert orca["lldap"].logins["mo"]["groups"] == {"hub-users"}, "only the OrcaSlicer group changes"
    assert orca["member"] not in hub.get("/api/platform/apps/orcaslicer").json()["allowed"]
    name = orca["member_name"]
    assert f"{name}: OrcaSlicer in the browser on" in _events(name)
    assert f"{name}: OrcaSlicer in the browser off" in _events(name)


def test_asking_twice_changes_nothing_the_second_time(orca, real_auth):
    hub = as_user(orca["boss"])
    path = f"/api/platform/users/{orca['member']}/orcaslicer"
    assert hub.put(path, json={"allowed": True}).json()["changed"] is True
    again = hub.put(path, json={"allowed": True})
    assert again.status_code == 200 and again.json() == {"user_id": orca["member"], "allowed": True, "changed": False}
    assert [c[0] for c in orca["lldap"].calls] == ["add"]
    assert hub.put(path, json={"allowed": False}).json()["changed"] is True
    assert hub.put(path, json={"allowed": False}).json()["changed"] is False
    assert [c[0] for c in orca["lldap"].calls] == ["add", "remove"]
    assert len(_events(f"{orca['member_name']}: OrcaSlicer")) == 2


def test_owners_always_have_it_and_their_switch_cannot_change(orca, real_auth):
    hub = as_user(orca["boss"])
    r = hub.put(f"/api/platform/users/{orca['boss']}/orcaslicer", json={"allowed": False})
    assert r.status_code == 400 and r.json()["detail"] == "Hub owners can always open OrcaSlicer in the browser"
    # Allowed whether or not they are in the group.
    assert GROUP not in orca["lldap"].logins["boss"]["groups"]
    assert orca["boss"] in hub.get("/api/platform/apps/orcaslicer").json()["allowed"]
    assert orca["lldap"].calls == []


def test_a_directory_failure_is_a_plain_error(orca, real_auth):
    hub = as_user(orca["boss"])
    orca["lldap"].fail = True
    r = hub.put(f"/api/platform/users/{orca['member']}/orcaslicer", json={"allowed": True})
    assert r.status_code == 502
    assert r.json()["detail"] == ("The hub's user directory (LLDAP) did not take the change: "
                                  "LLDAP did not answer: ConnectError")
    seen = hub.get("/api/platform/apps/orcaslicer")
    assert seen.status_code == 200 and seen.json()["error"].startswith("The hub's user directory (LLDAP)")
    assert seen.json()["allowed"] == seen.json()["owners"]
    assert _events(f"{orca['member_name']}: OrcaSlicer") == []


def test_someone_without_a_login_is_told_so(orca, real_auth):
    hub = as_user(orca["boss"])
    r = hub.put(f"/api/platform/users/{orca['viewer']}/orcaslicer", json={"allowed": True})
    assert r.status_code == 400 and r.json()["detail"].endswith("has no login in the hub's user directory")


def test_a_hub_without_it_shows_no_switch(orca, real_auth, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings(), "orcaslicer_group", "")
    hub = as_user(orca["boss"])
    assert hub.get("/api/platform/apps/orcaslicer").json() == {"available": False}
    r = hub.put(f"/api/platform/users/{orca['member']}/orcaslicer", json={"allowed": True})
    assert r.status_code == 404 and orca["lldap"].calls == []
