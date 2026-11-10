"""Teams, users, memberships and invites."""
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Invite, Membership, PERMS, ROLES, ROLE_PERMS, ROLE_SCOPES, SCOPE_PERMS, Team, User
from ..tenant import unscoped, use_team

INVITE_TTL = timedelta(days=14)


def _now():
    return datetime.now(timezone.utc)


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:60]
    return s or "team"


def unique_slug(db: Session, name: str) -> str:
    base = slugify(name)
    slug, n = base, 2
    while db.execute(select(Team.id).where(Team.slug == slug)).first():
        slug, n = f"{base}-{n}", n + 1
    return slug


def create_team(db: Session, name: str, owner: User | None = None, personal: bool = False) -> Team:
    t = Team(name=name.strip()[:120] or "Team", slug=unique_slug(db, name), personal=personal, source_dirs={})
    db.add(t)
    db.flush()
    t.storage_prefix = f"teams/{t.id}"
    if owner:
        db.add(Membership(team_id=t.id, user_id=owner.id, role="owner"))
    db.flush()
    return t


def membership(db: Session, team_id: int, user_id: int) -> Membership | None:
    return db.execute(select(Membership).where(Membership.team_id == team_id, Membership.user_id == user_id)).scalar()


def scopes_for(role: str) -> list[str]:
    return list(ROLE_SCOPES.get(role, []))


def owners(db: Session, team_id: int) -> list[User]:
    return list(db.execute(select(User).join(Membership, Membership.user_id == User.id)
                           .where(Membership.team_id == team_id, Membership.role == "owner")).scalars())


def upsert_user(db: Session, info: dict, groups: list[str]) -> User:
    """Create or update a person from their Authentik claims and make sure they have a team."""
    sub = str(info.get("sub") or "")
    u = db.execute(select(User).where(User.sub == sub)).scalar()
    if not u:
        u = User(sub=sub)
        db.add(u)
    u.email = info.get("email") or u.email
    u.username = info.get("preferred_username") or u.username
    u.name = info.get("name") or u.name
    u.platform_admin = bool(settings().admin_group and settings().admin_group in groups)
    u.last_login_at = _now()
    db.flush()
    if not u.memberships and u.platform_admin:
        first = db.get(Team, 1)
        if first and not owners(db, first.id):
            db.add(Membership(team_id=first.id, user_id=u.id, role="owner"))     # the existing workspace's owner
            db.flush()
            db.refresh(u)
    return u


def user_teams(db: Session, user: User) -> list[dict]:
    rows = db.execute(select(Membership, Team).join(Team, Team.id == Membership.team_id)
                      .where(Membership.user_id == user.id).order_by(Team.personal, Team.name)).all()
    return [{"id": t.id, "slug": t.slug, "name": t.name, "personal": t.personal, "role": m.role} for m, t in rows]


def pick_team(db: Session, user: User, wanted: str | int | None, session_team: int | None) -> tuple[Team, Membership] | None:
    """The team a request acts on: the asked-for one (id or slug), else the session's, else the last used, else any."""
    cands = []
    if wanted not in (None, ""):
        w = str(wanted)
        t = db.get(Team, int(w)) if w.isdigit() else db.execute(select(Team).where(Team.slug == w)).scalar()
        if not t:
            return None
        m = membership(db, t.id, user.id)
        return (t, m) if m else None
    for tid in (session_team, user.last_team_id):
        if tid:
            cands.append(tid)
    for tid in cands:
        m = membership(db, tid, user.id)
        if m:
            return db.get(Team, tid), m
    m = db.execute(select(Membership).where(Membership.user_id == user.id).order_by(Membership.id)).scalar()
    return (db.get(Team, m.team_id), m) if m else None


# ---------- permissions ----------

def perms_of(m: Membership | None) -> list[str]:
    return m.effective_perms if m else []


def perms_for_scopes(scopes: list[str]) -> list[str]:
    """Coarse scopes (read, write, admin) grant a role's permissions; a permission's own name grants just it.
    relay:<app>, clip and owner add no permissions of their own (see auth): a clip-only key does not even get view."""
    out = set()
    for sc in scopes or []:
        out |= set(SCOPE_PERMS.get(sc, []))
        if sc in PERMS:
            out.add(sc)
    if out or "owner" in (scopes or []):
        out.add("view")
    return [p for p in PERMS if p in out]


def clean_perms(perms: list[str] | None, role: str, granter: list[str]) -> list[str] | None:
    """A custom permission set, limited to what the granter holds. None means "the role's defaults"."""
    if perms is None or role == "owner":
        return None
    want = [p for p in PERMS if p in set(perms) | {"view"}]
    over = [p for p in want if p not in granter]
    if over:
        raise PermissionError(f"you can't grant permissions you don't have: {', '.join(over)}")
    return None if set(want) == set(ROLE_PERMS.get(role, [])) else want


# ---------- invites ----------

def _h(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _valid_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("enter a valid email address")
    return email


def create_invite(db: Session, team_id: int | None, email: str, role: str, by: User | None, *, kind: str = "join",
                  team_name: str | None = None, name: str | None = None, perms: list[str] | None = None) -> tuple[str, Invite]:
    if kind not in ("join", "new_team", "individual"):
        raise ValueError("unknown invite kind")
    if kind == "join":
        if role not in ROLES:
            raise ValueError("unknown role")
        if not team_id:
            raise ValueError("pick a team")
    else:
        team_id, role, perms = None, "owner", None
        if kind == "new_team" and not (team_name or "").strip():
            raise ValueError("name the new team")
    email = _valid_email(email)
    if kind == "join":
        u = db.execute(select(User).where(func.lower(User.email) == email)).scalar()
        if u and membership(db, team_id, u.id):
            raise ValueError(f"{email} is already in this team")
    raw = secrets.token_urlsafe(24)
    inv = Invite(team_id=team_id, kind=kind, team_name=(team_name or "").strip()[:120] or None, name=(name or "").strip()[:200] or None,
                 email=email, role=role, perms=perms, token_hash=_h(raw), invited_by=by.id if by else None,
                 expires_at=_now() + INVITE_TTL)
    db.add(inv)
    db.flush()
    return raw, inv


def provision_login(inv: Invite) -> dict:
    """Make sure the person can sign in: an existing Authentik account joins the hub-users group; otherwise an
    Authentik invitation lets them create one. Returns {"account": "existing"|"invited"|"manual", ...}."""
    from ..clients import authentik_api
    ak = authentik_api.client()
    if not ak:
        return {"account": "manual", "note": "Authentik is not connected; add them to the hub-users group by hand."}
    u = ak.user_by_email(inv.email)
    if u:
        ak.add_to_group(u["pk"])
        return {"account": "existing"}
    pk, url = ak.create_invitation(inv.email, inv.name, days=INVITE_TTL.days)
    inv.ak_invitation, inv.ak_url = pk, url
    return {"account": "invited", "signup_url": url}


def find_invite(db: Session, raw: str) -> Invite | None:
    inv = db.execute(select(Invite).where(Invite.token_hash == _h(raw or ""))).scalar()
    if not inv or inv.revoked or inv.accepted_at:
        return None
    exp = inv.expires_at if inv.expires_at.tzinfo else inv.expires_at.replace(tzinfo=timezone.utc)
    return inv if exp > _now() else None


def pending_invites(db: Session, email: str | None) -> list[Invite]:
    if not email:
        return []
    now = _now()
    rows = db.execute(select(Invite).where(func.lower(Invite.email) == email.lower(), Invite.accepted_at.is_(None),
                                           Invite.revoked.is_(False))).scalars()
    return [i for i in rows if (i.expires_at if i.expires_at.tzinfo else i.expires_at.replace(tzinfo=timezone.utc)) > now]


def invite_target(inv: Invite) -> str:
    if inv.kind == "new_team":
        return inv.team_name or "a new team"
    if inv.kind == "individual":
        return "your own workspace"
    return inv.team.name if inv.team else "a team"


def accept_invite(db: Session, inv: Invite, user: User) -> Membership:
    if (user.email or "").lower() != inv.email.lower():
        raise PermissionError(f"this invite is for {inv.email}; you are signed in as {user.email or 'an account with no email'}")
    if inv.kind == "join":
        m = membership(db, inv.team_id, user.id)
        if m:
            if ROLES.index(inv.role) > ROLES.index(m.role):
                m.role = inv.role
                m.perms = inv.perms
        else:
            m = Membership(team_id=inv.team_id, user_id=user.id, role=inv.role, perms=inv.perms)
            db.add(m)
        tid = inv.team_id
    else:
        label = inv.team_name if inv.kind == "new_team" else f"{(user.name or user.username or 'My').split(' ')[0]}'s workspace"
        t = create_team(db, label, owner=user, personal=inv.kind == "individual")
        inv.team_id = t.id
        tid = t.id
        m = membership(db, t.id, user.id)
    inv.accepted_at = _now()
    inv.accepted_by = user.id
    user.last_team_id = tid
    db.flush()
    return m


def drop_hub_access_if_unused(db: Session, user: User):
    """Someone left with no team loses hub sign-in (they are taken out of the Authentik hub-users group)."""
    db.flush()
    left = db.execute(select(func.count()).select_from(Membership).where(Membership.user_id == user.id)).scalar()
    if left or user.platform_admin or not user.email:
        return False
    from ..clients import authentik_api
    ak = authentik_api.client()
    if not ak:
        return False
    try:
        u = ak.user_by_email(user.email)
        if u:
            ak.remove_from_group(u["pk"])
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


# ---------- startup ----------

def bootstrap(conn, fresh: bool = False):
    """Make sure the first team exists, and give it the folders and services that used to come from env. Then make
    sure the installation row exists (services.installation)."""
    from ..models import Connection
    from . import connections
    db = Session(bind=conn)
    try:
        with unscoped():
            t = db.get(Team, 1)
            if not t:
                t = Team(id=1, slug=slugify(settings().first_team_name), name=settings().first_team_name,
                         storage_prefix="", source_dirs={})
                db.add(t)
                db.flush()
            if not t.source_dirs:
                dirs = {"deltaprints": settings().deltaprints_dir, "inbox": settings().inbox_dir}
                t.source_dirs = {k: v for k, v in dirs.items() if v}
            has = db.execute(select(func.count()).select_from(Connection).where(Connection.team_id == 1)).scalar()
        if not has:
            with use_team(1):
                for kind, (cfg, sec) in connections.env_seed().items():
                    db.add(Connection(team_id=1, kind=kind, config=cfg,
                                      secrets_enc=connections.encrypt({k: v for k, v in sec.items() if v}) if any(sec.values()) else None))
        db.flush()
        from . import installation
        installation.bootstrap(db, fresh)
        if conn.dialect.name == "postgresql":
            from sqlalchemy import text
            db.execute(text("SELECT setval(pg_get_serial_sequence('team', 'id'), (SELECT max(id) FROM team))"))
        db.commit()
    finally:
        db.close()


# ---------- per-team folders and alert recipients ----------

def current_team(db: Session) -> Team:
    from ..tenant import require_team
    return db.get(Team, require_team(db))


def team_dir(db: Session, key: str, team: Team | None = None):
    """A server folder a platform admin assigned to the team (deltaprints, inbox), only if it sits under source_root."""
    from pathlib import Path
    team = team or current_team(db)
    raw = (team.source_dirs or {}).get(key)
    if not raw or not settings().source_root:
        return None   # no server folders without a source_root (an empty root would resolve to the working directory)
    p = Path(raw).resolve()
    root = Path(settings().source_root).resolve()
    if p != root and root not in p.parents:
        return None
    return p


def alert_recipients(db: Session) -> list[str]:
    from ..kv import setting
    from ..tenant import require_team
    raw = setting(db, "alert_emails")
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.replace(";", ",").split(",")]
    out = [x for x in (raw or []) if x and "@" in x]
    if out:
        return out
    tid = require_team(db)
    rows = db.execute(select(User.email).join(Membership, Membership.user_id == User.id)
                      .where(Membership.team_id == tid, Membership.role.in_(("owner", "admin")))).scalars()
    out = sorted({e for e in rows if e})
    if not out and tid == 1 and settings().alert_to:
        out = [settings().alert_to]
    return out


def library_prefix(db: Session) -> str:
    t = current_team(db)
    return (t.storage_prefix or "").strip("/")
