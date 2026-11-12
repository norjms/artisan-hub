"""Shared folders for OrcaSlicer in the browser: the hub keeps the list and their LLDAP groups, the owner gives each
person none, read or write on People, and the installation reads the list with its own key. Removing one marks it;
only the installation forgets it, once it has removed the files. LLDAP is a fake that keeps logins and groups.
"""
import pytest

from app.clients.base import ApiError
from tests.test_orcaslicer_access import FakeLldap, GROUP, _events, orca  # noqa: F401  (fixtures)
from tests.test_setup_api import new_install  # noqa: F401  (fixture)
from tests.test_teams import as_user, people, real_auth  # noqa: F401  (fixtures)


class GroupsLldap(FakeLldap):
    def __init__(self):
        super().__init__()
        self.groups = {GROUP, "hub-users", "hub-admins"}

    def group_id(self, name=None):
        self._check()
        if name not in self.groups:
            raise ApiError("lldap", None, f"group {name} not found in LLDAP")
        return 1

    def create_group(self, name):
        self._check()
        self.calls.append(("create", name))
        self.groups.add(name)

    def delete_group(self, name):
        self._check()
        self.group_id(name)
        self.calls.append(("delete", name))
        self.groups.discard(name)
        for u in self.logins.values():
            u["groups"].discard(name)


def _clear():
    from app.db import session_scope
    from app.models import OrcaShare
    from app.tenant import unscoped
    with session_scope() as db, unscoped():
        db.query(OrcaShare).delete()
        db.commit()


@pytest.fixture
def shares(orca, monkeypatch):
    from app.clients import lldap_api
    fake = GroupsLldap()
    fake.logins = orca["lldap"].logins
    monkeypatch.setattr(lldap_api, "client", lambda: fake)
    _clear()
    yield {**orca, "lldap": fake}
    _clear()


def test_only_the_hubs_owner_lists_or_changes_shared_folders(shares, real_auth):
    for who in ("owner", "member"):
        c = as_user(shares[who])
        assert c.get("/api/platform/shares").status_code == 403
        assert c.post("/api/platform/shares", json={"name": "Production"}).status_code == 403
    assert shares["lldap"].calls == []


def test_without_orcaslicer_in_the_browser_there_are_none(shares, real_auth, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings(), "orcaslicer_group", "")
    hub = as_user(shares["boss"])
    assert hub.get("/api/platform/shares").json() == {"available": False, "shares": []}
    assert hub.post("/api/platform/shares", json={"name": "Production"}).status_code == 404


def test_a_new_folder_gets_a_slug_and_both_groups_and_nobody_but_owners(shares, real_auth):
    hub = as_user(shares["boss"])
    r = hub.post("/api/platform/shares", json={"name": "  Production   jigs "})
    assert r.status_code == 201, r.text
    made = r.json()
    assert made["slug"] == "production-jigs" and made["name"] == "Production jigs"
    assert made["read_group"] == "share-production-jigs-read" and made["write_group"] == "share-production-jigs-write"
    assert {"share-production-jigs-read", "share-production-jigs-write"} <= shares["lldap"].groups
    seen = hub.get("/api/platform/shares?members=1").json()
    assert seen["available"] is True and seen["error"] is None
    assert [s["slug"] for s in seen["shares"]] == ["production-jigs"]
    assert seen["access"][str(shares["boss"])] == {"production-jigs": "write"}
    assert seen["access"][str(shares["member"])] == {}
    assert "Shared folder Production jigs added" in _events("Production jigs")


def test_names_are_unique_whatever_the_case_and_never_a_path(shares, real_auth):
    hub = as_user(shares["boss"])
    assert hub.post("/api/platform/shares", json={"name": "Jigs"}).status_code == 201
    r = hub.post("/api/platform/shares", json={"name": "jigs"})
    assert r.status_code == 409 and r.json()["detail"] == "There is already a shared folder called jigs"
    for bad in ("a/b", "..", "   ", "x" * 61, "back\\slash"):
        assert hub.post("/api/platform/shares", json={"name": bad}).status_code == 400, bad
    # Two names that make the same slug get different ones.
    assert hub.post("/api/platform/shares", json={"name": "Jigs!"}).json()["slug"] == "jigs-2"


def test_the_owner_gives_read_or_write_and_write_takes_the_place_of_read(shares, real_auth):
    hub = as_user(shares["boss"])
    hub.post("/api/platform/shares", json={"name": "Production"})
    path = f"/api/platform/users/{shares['member']}/shares/production"
    r = hub.put(path, json={"access": "read"})
    assert r.status_code == 200 and r.json()["changed"] is True
    groups = shares["lldap"].logins["mo"]["groups"]
    assert "share-production-read" in groups and "share-production-write" not in groups
    assert hub.put(path, json={"access": "write"}).json()["changed"] is True
    assert "share-production-write" in groups and "share-production-read" not in groups
    assert hub.put(path, json={"access": "write"}).json()["changed"] is False
    seen = hub.get("/api/platform/shares?members=1").json()
    assert seen["access"][str(shares["member"])] == {"production": "write"}
    assert hub.put(path, json={"access": "none"}).json()["changed"] is True
    assert not any(g.startswith("share-") for g in groups)
    assert GROUP not in groups and "hub-users" in groups, "only the folder's groups change"
    assert hub.put(path, json={"access": "admin"}).status_code == 400
    assert len(_events(f"{shares['member_name']}: shared folder Production")) == 3


def test_owners_always_write_and_their_access_cannot_change(shares, real_auth):
    hub = as_user(shares["boss"])
    hub.post("/api/platform/shares", json={"name": "Production"})
    r = hub.put(f"/api/platform/users/{shares['boss']}/shares/production", json={"access": "none"})
    assert r.status_code == 400 and r.json()["detail"] == "Hub owners can always read and write shared folders"


def test_a_rename_keeps_the_slug_and_the_people(shares, real_auth):
    hub = as_user(shares["boss"])
    hub.post("/api/platform/shares", json={"name": "Production"})
    hub.put(f"/api/platform/users/{shares['member']}/shares/production", json={"access": "read"})
    r = hub.patch("/api/platform/shares/production", json={"name": "Customer jobs"})
    assert r.status_code == 200 and r.json()["slug"] == "production" and r.json()["name"] == "Customer jobs"
    assert hub.get("/api/platform/shares?members=1").json()["access"][str(shares["member"])] == {"production": "read"}


def test_removing_only_marks_it_until_the_installation_forgets_it(shares, real_auth):
    hub = as_user(shares["boss"])
    hub.post("/api/platform/shares", json={"name": "Production"})
    hub.put(f"/api/platform/users/{shares['member']}/shares/production", json={"access": "write"})
    # Not marked: it can't be forgotten.
    assert hub.delete("/api/platform/shares/production").status_code == 409
    marked = hub.post("/api/platform/shares/production/remove").json()
    assert marked["deleting"] is True
    assert "share-production-write" in shares["lldap"].groups, "the groups stay until the files go"
    seen = hub.get("/api/platform/shares?members=1").json()
    assert seen["shares"][0]["deleting"] is True
    assert seen["access"][str(shares["member"])] == {}, "a folder marked for removal is in nobody's People"
    assert hub.put(f"/api/platform/users/{shares['member']}/shares/production",
                   json={"access": "read"}).status_code == 409
    # Change of mind, then for good.
    assert hub.post("/api/platform/shares/production/keep").json()["deleting"] is False
    assert hub.get("/api/platform/shares?members=1").json()["access"][str(shares["member"])] == {"production": "write"}
    hub.post("/api/platform/shares/production/remove")
    r = hub.delete("/api/platform/shares/production")
    assert r.status_code == 200 and r.json() == {"slug": "production", "removed": True}
    assert not any(g.startswith("share-production") for g in shares["lldap"].groups)
    assert not any(g.startswith("share-") for g in shares["lldap"].logins["mo"]["groups"])
    assert hub.get("/api/platform/shares").json()["shares"] == []
    assert hub.delete("/api/platform/shares/production").status_code == 404


def test_the_installations_key_reads_the_list_and_forgets_a_removed_folder_and_no_other_key_may(
        new_install, monkeypatch):
    """The installation's own key (the one setup made) reads the list when it starts a desktop, and forgets a
    folder once it has removed its files. Another key with the same scopes may not."""
    from fastapi.testclient import TestClient
    from app.clients import lldap_api
    from app.config import settings
    from app.main import app
    from tests.test_setup_api import TOKEN, _set
    fake = GroupsLldap()
    monkeypatch.setattr(lldap_api, "client", lambda: fake)
    monkeypatch.setattr(settings(), "orcaslicer_group", GROUP)
    monkeypatch.setattr(settings(), "orcaslicer_desktops", "per-person")
    client = TestClient(app)
    try:
        made = client.post("/api/setup/v1/initialize", json={"farm": "openprinthq", "team_name": "Lab"},
                           headers={"Authorization": f"Bearer {TOKEN}"}).json()
        key = {"Authorization": f"Bearer {made['api_key']}"}
        from app.db import session_scope
        from app.models import OrcaShare
        from app.tenant import unscoped
        _clear()
        with session_scope() as db, unscoped():
            db.add(OrcaShare(slug="production", name="Production", deleting=False))
            db.add(OrcaShare(slug="old", name="Old", deleting=True))
            db.commit()
        fake.groups |= {"share-old-read", "share-old-write"}
        seen = client.get("/api/platform/shares", headers=key)
        assert seen.status_code == 200, seen.text
        body = seen.json()
        assert body["available"] is True and body["desktops"] == "per-person"
        assert [(s["slug"], s["name"], s["read_group"], s["write_group"], s["deleting"]) for s in body["shares"]] == [
            ("old", "Old", "share-old-read", "share-old-write", True),
            ("production", "Production", "share-production-read", "share-production-write", False)]
        # It can't change anything else.
        assert client.post("/api/platform/shares", json={"name": "Mine"}, headers=key).status_code == 403
        assert client.delete("/api/platform/shares/production", headers=key).status_code == 409
        other = client.post("/api/mobile/v1/keys", json={"name": "other", "scopes": ["admin"], "bind_instance": False},
                            headers=key)
        assert other.status_code == 200, other.text
        bad = {"Authorization": f"Bearer {other.json()['key']}"}
        assert client.get("/api/platform/shares", headers=bad).status_code == 403
        assert client.delete("/api/platform/shares/old", headers=bad).status_code == 403
        assert client.delete("/api/platform/shares/old", headers=key).json() == {"slug": "old", "removed": True}
        assert "share-old-read" not in fake.groups
    finally:
        _clear()
        _set(installer_key_id=None)


def test_a_directory_that_does_not_answer_is_said_plainly(shares, real_auth):
    hub = as_user(shares["boss"])
    hub.post("/api/platform/shares", json={"name": "Production"})
    shares["lldap"].fail = True
    seen = hub.get("/api/platform/shares?members=1").json()
    assert seen["error"].startswith("The hub's user directory (LLDAP) did not take the change")
    assert seen["shares"][0]["slug"] == "production", "the list is the hub's own and still there"
    r = hub.put(f"/api/platform/users/{shares['member']}/shares/production", json={"access": "read"})
    assert r.status_code == 502
