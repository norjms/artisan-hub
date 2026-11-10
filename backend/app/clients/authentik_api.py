"""Authentik, for giving invited people a login.

The hub uses a service account (svc-artisan-hub) that may only: look up users, add or remove people in the
hub-users group, and create or delete invitations for the artisan-hub-invite enrollment flow. It cannot create
users directly, change passwords, or touch any other group.
"""
from datetime import datetime, timedelta, timezone

from ..config import settings
from .base import ApiError, BaseClient


class AuthentikClient(BaseClient):
    service = "authentik"

    def __init__(self, url: str, token: str):
        super().__init__(url.rstrip("/") + "/api/v3", headers={"Authorization": f"Bearer {token}"}, timeout=30.0)
        self.ui = url.rstrip("/")
        self._group = None
        self._flow = None

    def user_by_email(self, email: str) -> dict | None:
        rows = (self.get("/core/users/", params={"email": email.strip().lower()}) or {}).get("results") or []
        rows = [r for r in rows if (r.get("email") or "").lower() == email.strip().lower()]
        return rows[0] if rows else None

    def group_pk(self) -> str:
        if not self._group:
            rows = (self.get("/core/groups/", params={"name": settings().authentik_group}) or {}).get("results") or []
            if not rows:
                raise ApiError(self.service, None, f"group {settings().authentik_group} not found or not visible")
            self._group = rows[0]["pk"]
        return self._group

    def flow_pk(self) -> str:
        if not self._flow:
            rows = (self.get("/flows/instances/", params={"slug": settings().authentik_invite_flow}) or {}).get("results") or []
            if not rows:
                raise ApiError(self.service, None, f"flow {settings().authentik_invite_flow} not found")
            self._flow = rows[0]["pk"]
        return self._flow

    def add_to_group(self, user_pk: int):
        self.request("POST", f"/core/groups/{self.group_pk()}/add_user/", json={"pk": user_pk}, expect_json=False)

    def remove_from_group(self, user_pk: int):
        self.request("POST", f"/core/groups/{self.group_pk()}/remove_user/", json={"pk": user_pk}, expect_json=False)

    def create_invitation(self, email: str, name: str | None, days: int = 14) -> tuple[str, str]:
        exp = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        tag = email.split("@")[0][:30].replace(".", "-")
        r = self.post("/stages/invitation/invitations/", json={
            "name": f"hub-{tag}-{int(datetime.now().timestamp())}", "single_use": True, "expires": exp,
            "flow": self.flow_pk(), "fixed_data": {"email": email.strip().lower(), "name": (name or "").strip() or email}})
        url = f"{self.ui}/if/flow/{settings().authentik_invite_flow}/?itoken={r['pk']}"
        return r["pk"], url

    def delete_invitation(self, pk: str):
        try:
            self.request("DELETE", f"/stages/invitation/invitations/{pk}/", expect_json=False)
        except ApiError as e:
            if e.status != 404:
                raise


def client() -> AuthentikClient | None:
    s = settings()
    if not (s.authentik_url and s.authentik_token):
        return None
    return AuthentikClient(s.authentik_url, s.authentik_token)
