"""Shared folders for OrcaSlicer in the browser.

A shared folder is a named folder that each person's own OrcaSlicer desktop can open under Files, Shared:
read-only for the people in its LLDAP group `share-<slug>-read`, read and write for those in `share-<slug>-write`,
and read and write for the hub's owners always. The hub keeps the list and the groups. The installation reads the
list (GET /api/platform/shares, with its own key) each time it starts someone's desktop, makes the folder's volume
and mounts what that person may use, so a change here takes effect the next time their desktop starts.

Removing one takes two steps, because its files can't come back. Here the owner marks it for removal: it leaves
People and every desktop started after. Its files go only when the owner confirms on the installer's summary by
typing its name; the installer then tells the hub to forget it (DELETE /api/platform/shares/<slug>).

With one desktop shared by everyone, no shared folder is opened in it (one desktop can't tell people apart); the
list can still be kept ready here.
"""
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import Principal, current, need
from ..config import settings
from ..db import get_db
from ..jobs import event
from ..models import Installation, OrcaShare, User
from ..tenant import set_team

router = APIRouter(prefix="/api")

MAX_SHARES = 50
NAME_MAX = 60
# A share's volume is ahi-<install>-share-<slug>: Docker keeps names short, with room for the rest.
SLUG_MAX = 32
LEVELS = ("none", "read", "write")
NOT_RUN = "This hub doesn't run OrcaSlicer in the browser"


def read_group(slug: str) -> str:
    return f"share-{slug}-read"


def write_group(slug: str) -> str:
    return f"share-{slug}-write"


def owner_or_installer(db: Session = Depends(get_db), p: Principal = Depends(current)) -> Principal:
    """The hub's owner, or the installation's own key (the one setup made, like the password link): the
    installation reads the list when it starts a desktop and forgets a folder once it removed its files."""
    row = db.get(Installation, 1)
    if not p.get("platform_admin") and not (row and row.installer_key_id and p.get("key_id") == row.installer_key_id):
        raise HTTPException(403, "only the hub owner or the installation can do this")
    set_team(p["team_id"])
    return p


def _directory():
    """The LLDAP client when this hub runs OrcaSlicer in the browser, or None."""
    from .teams import _orcaslicer
    group, ld = _orcaslicer()
    return ld if group else None


def _need_directory():
    ld = _directory()
    if ld is None:
        raise HTTPException(404, NOT_RUN)
    return ld


def _directory_error(e: Exception) -> str:
    from .teams import _directory_error as why
    return why(e)


def _per_person() -> bool:
    return (settings().orcaslicer_desktops or "").strip() == "per-person"


def clean_name(raw: str | None) -> str:
    name = " ".join((raw or "").split())
    if not name:
        raise HTTPException(400, "Give the folder a name")
    if len(name) > NAME_MAX:
        raise HTTPException(400, f"Keep the name to {NAME_MAX} characters")
    if "/" in name or "\\" in name or name in (".", "..") or any(ord(c) < 32 for c in name):
        raise HTTPException(400, "A folder name can't contain / or \\")
    return name


def slug_for(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:SLUG_MAX].strip("-") or "folder"
    slug, n = base, 1
    while slug in taken:
        n += 1
        suffix = f"-{n}"
        slug = base[: SLUG_MAX - len(suffix)].rstrip("-") + suffix
    return slug


def _view(s: OrcaShare) -> dict:
    return {"slug": s.slug, "name": s.name, "read_group": read_group(s.slug), "write_group": write_group(s.slug),
            "deleting": bool(s.deleting), "created_at": s.created_at.isoformat() if s.created_at else None}


def _all(db: Session) -> list[OrcaShare]:
    return list(db.execute(select(OrcaShare).order_by(func.lower(OrcaShare.name))).scalars())


def _get(db: Session, slug: str) -> OrcaShare:
    s = db.execute(select(OrcaShare).where(OrcaShare.slug == slug)).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "No such shared folder")
    return s


def _name_free(db: Session, name: str, but: int | None = None) -> None:
    q = select(OrcaShare).where(func.lower(OrcaShare.name) == name.lower())
    if but is not None:
        q = q.where(OrcaShare.id != but)
    if db.execute(q).scalar_one_or_none():
        raise HTTPException(409, f"There is already a shared folder called {name}")


def _ensure_group(ld, name: str) -> None:
    from ..clients.base import ApiError
    try:
        ld.group_id(name)
    except ApiError:
        ld.create_group(name)


def _drop_group(ld, name: str) -> None:
    from ..clients.base import ApiError
    try:
        ld.delete_group(name)
    except ApiError as e:
        if "not found" not in (e.message or ""):
            raise


@router.get("/platform/shares")
def list_shares(members: bool = False, db: Session = Depends(get_db), p: Principal = Depends(owner_or_installer)):
    """The shared folders. The installation reads this when it starts a person's desktop. With members, also who
    may read or write each one, by hub user (People's choices)."""
    ld = _directory()
    if ld is None:
        return {"available": False, "shares": []}
    rows = _all(db)
    out = {"available": True, "desktops": "per-person" if _per_person() else "shared",
           "shares": [_view(s) for s in rows]}
    if not members:
        return out
    users = list(db.execute(select(User).order_by(User.id)).scalars())
    by_email = {(u.email or "").strip().lower(): u for u in users if u.email}
    access: dict[int, dict[str, str]] = {u.id: {} for u in users}
    try:
        for s in rows:
            if s.deleting:
                continue
            for level, group in (("read", read_group(s.slug)), ("write", write_group(s.slug))):
                for m in ld.group_members(group):
                    u = by_email.get((m.get("email") or "").strip().lower())
                    if u and access[u.id].get(s.slug) != "write":
                        access[u.id][s.slug] = level
    except Exception as e:  # noqa: BLE001
        return {**out, "access": {}, "error": _directory_error(e)}
    for u in users:
        if u.platform_admin:
            access[u.id] = {s.slug: "write" for s in rows if not s.deleting}
    return {**out, "access": access, "error": None}


class ShareIn(BaseModel):
    name: str


@router.post("/platform/shares", status_code=201)
def create_share(body: ShareIn, db: Session = Depends(get_db), p: Principal = Depends(need("platform"))):
    """A new shared folder and its two groups. Nobody but the owners can use it until they are given it."""
    ld = _need_directory()
    name = clean_name(body.name)
    _name_free(db, name)
    rows = _all(db)
    if len(rows) >= MAX_SHARES:
        raise HTTPException(400, f"A hub keeps up to {MAX_SHARES} shared folders")
    slug = slug_for(name, {s.slug for s in rows})
    try:
        _ensure_group(ld, read_group(slug))
        _ensure_group(ld, write_group(slug))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, _directory_error(e))
    s = OrcaShare(slug=slug, name=name, deleting=False)
    db.add(s)
    event(db, "team", f"Shared folder {name} added", actor=p.actor)
    db.commit()
    return _view(s)


@router.patch("/platform/shares/{slug}")
def rename_share(slug: str, body: ShareIn, db: Session = Depends(get_db), p: Principal = Depends(need("platform"))):
    """A new name. Its folder in each desktop takes it the next time that desktop starts; its files stay."""
    _need_directory()
    s = _get(db, slug)
    name = clean_name(body.name)
    if name == s.name:
        return _view(s)
    _name_free(db, name, but=s.id)
    old, s.name = s.name, name
    event(db, "team", f"Shared folder {old} renamed {name}", actor=p.actor)
    db.commit()
    return _view(s)


@router.post("/platform/shares/{slug}/remove")
def mark_share(slug: str, db: Session = Depends(get_db), p: Principal = Depends(need("platform"))):
    """Mark a shared folder for removal. Its files stay until the owner confirms on the installer's summary."""
    _need_directory()
    s = _get(db, slug)
    if not s.deleting:
        s.deleting = True
        event(db, "team", f"Shared folder {s.name} marked for removal", actor=p.actor)
        db.commit()
    return _view(s)


@router.post("/platform/shares/{slug}/keep")
def keep_share(slug: str, db: Session = Depends(get_db), p: Principal = Depends(need("platform"))):
    """Change your mind before the files go: the folder is back, with the same people."""
    _need_directory()
    s = _get(db, slug)
    if s.deleting:
        s.deleting = False
        event(db, "team", f"Shared folder {s.name} kept", actor=p.actor)
        db.commit()
    return _view(s)


@router.delete("/platform/shares/{slug}")
def forget_share(slug: str, db: Session = Depends(get_db), p: Principal = Depends(owner_or_installer)):
    """The installer removed the folder's files: forget it and its groups. Only a folder marked for removal."""
    ld = _need_directory()
    s = _get(db, slug)
    if not s.deleting:
        raise HTTPException(409, f"{s.name} is not marked for removal")
    try:
        _drop_group(ld, read_group(slug))
        _drop_group(ld, write_group(slug))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, _directory_error(e))
    event(db, "team", f"Shared folder {s.name} removed", actor=p.actor)
    db.delete(s)
    db.commit()
    return {"slug": slug, "removed": True}


class AccessIn(BaseModel):
    access: str


@router.put("/platform/users/{user_id}/shares/{slug}")
def set_access(user_id: int, slug: str, body: AccessIn, db: Session = Depends(get_db),
               p: Principal = Depends(need("platform"))):
    """None, read or write for one person on one shared folder: they join or leave its groups. Asking for what is
    already so changes nothing."""
    if body.access not in LEVELS:
        raise HTTPException(400, "Choose none, read or write")
    ld = _need_directory()
    s = _get(db, slug)
    if s.deleting:
        raise HTTPException(409, f"{s.name} is marked for removal")
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404)
    if u.platform_admin:
        raise HTTPException(400, "Hub owners can always read and write shared folders")
    if not u.email:
        raise HTTPException(400, f"{u.label} has no email, so their login can't be found")
    r, w = read_group(slug), write_group(slug)
    try:
        login = ld.user_by_email(u.email)
        if not login:
            raise HTTPException(400, f"{u.label} has no login in the hub's user directory")
        has = {g.get("displayName") for g in login.get("groups") or []}
        before = "write" if w in has else "read" if r in has else "none"
        want = {"none": set(), "read": {r}, "write": {w}}[body.access]
        for group in (r, w):
            if group in want and group not in has:
                ld.add_to_group(login["id"], group)
            elif group in has and group not in want:
                ld.remove_from_group(login["id"], group)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, _directory_error(e))
    changed = before != body.access
    if changed:
        what = {"none": "no access", "read": "read only", "write": "read and write"}[body.access]
        event(db, "team", f"{u.label}: shared folder {s.name}, {what}", actor=p.actor)
        db.commit()
    return {"user_id": u.id, "slug": slug, "access": body.access, "changed": changed}
