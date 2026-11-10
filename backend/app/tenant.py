"""Team isolation.

Every tenant table carries team_id (TenantMixin). Each ORM SELECT / UPDATE / DELETE is filtered to the current
team automatically, and new rows get the current team on flush. The current team comes from the session
(session.info["team_id"], set by auth and the job runner) or from a context variable (set per request and per job).

A query on a tenant table with no team set raises NoTeamContext instead of silently returning every team's rows.
Platform code that must see all teams (job claiming, the scheduler, migrations) runs inside `unscoped()`.
"""
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import ForeignKey, event
from sqlalchemy.orm import Mapped, Session, declared_attr, mapped_column, with_loader_criteria

_team: ContextVar[int | None] = ContextVar("hub_team_id", default=None)
_unscoped: ContextVar[bool] = ContextVar("hub_unscoped", default=False)


class NoTeamContext(RuntimeError):
    pass


class CrossTeamWrite(RuntimeError):
    pass


class TenantMixin:
    """Rows that belong to one team."""

    @declared_attr
    def team_id(cls) -> Mapped[int]:
        return mapped_column(ForeignKey("team.id", ondelete="CASCADE"), index=True, nullable=False)


# ---------- context ----------

def set_team(team_id: int | None):
    """Set the team for the rest of the current context (request task or job thread)."""
    _team.set(team_id)


def current_team_id(session: Session | None = None) -> int | None:
    if session is not None and session.info.get("team_id"):
        return session.info["team_id"]
    return _team.get()


def require_team(session: Session | None = None) -> int:
    tid = current_team_id(session)
    if not tid:
        raise NoTeamContext("no team selected")
    return tid


@contextmanager
def use_team(team_id: int):
    tok = _team.set(team_id)
    tok2 = _unscoped.set(False)
    try:
        yield team_id
    finally:
        _unscoped.reset(tok2)
        _team.reset(tok)


@contextmanager
def unscoped():
    tok = _unscoped.set(True)
    try:
        yield
    finally:
        _unscoped.reset(tok)


def is_unscoped(session: Session | None = None) -> bool:
    return _unscoped.get() or bool(session is not None and session.info.get("unscoped"))


def bind_session(session: Session, team_id: int | None, unscoped_: bool = False):
    session.info["team_id"] = team_id
    session.info["unscoped"] = unscoped_


# ---------- enforcement ----------

def _touches_tenant(state) -> bool:
    try:
        mappers = state.all_mappers
    except Exception:  # noqa: BLE001
        return True
    return any(issubclass(m.class_, TenantMixin) for m in mappers)


@event.listens_for(Session, "do_orm_execute")
def _scope_queries(state):
    if not (state.is_select or state.is_update or state.is_delete):
        return
    if state.execution_options.get("hub_unscoped") or is_unscoped(state.session):
        return
    if not _touches_tenant(state) and not state.is_select:
        return
    tid = current_team_id(state.session)
    if not tid:
        if _touches_tenant(state):
            raise NoTeamContext("query on team data with no team selected")
        return
    state.statement = state.statement.options(
        with_loader_criteria(TenantMixin, lambda cls: cls.team_id == tid, include_aliases=True))


@event.listens_for(Session, "transient_to_pending")
def _stamp_on_add(session, obj):
    """Stamp the team when a row is added, so a later flush in another context can't change it."""
    if isinstance(obj, TenantMixin) and obj.team_id is None:
        tid = current_team_id(session)
        if tid:
            obj.team_id = tid


@event.listens_for(Session, "before_flush")
def _stamp_team(session, flush_context, instances):
    tid = current_team_id(session)
    free = is_unscoped(session)
    for obj in session.new:
        if not isinstance(obj, TenantMixin):
            continue
        if obj.team_id is None:
            parent_tid = _parent_team(obj)
            obj.team_id = tid or parent_tid
            if obj.team_id is None:
                raise NoTeamContext(f"new {type(obj).__name__} with no team selected")
        elif tid and obj.team_id != tid and not free:
            raise CrossTeamWrite(f"{type(obj).__name__} belongs to another team")
    if free:
        return
    for obj in session.dirty:
        if isinstance(obj, TenantMixin):
            hist = _history(obj, "team_id")
            if hist and hist.deleted and hist.added and hist.deleted[0] != hist.added[0]:
                raise CrossTeamWrite(f"moving a {type(obj).__name__} to another team is not allowed")
            if tid and obj.team_id != tid:
                raise CrossTeamWrite(f"{type(obj).__name__} belongs to another team")
    for obj in session.deleted:
        if isinstance(obj, TenantMixin) and tid and obj.team_id != tid:
            raise CrossTeamWrite(f"{type(obj).__name__} belongs to another team")


def _history(obj, attr):
    from sqlalchemy import inspect
    try:
        return inspect(obj).attrs[attr].history
    except Exception:  # noqa: BLE001
        return None


def _parent_team(obj):
    """A child row created through a relationship (model.files.append(...)) takes its parent's team."""
    from sqlalchemy import inspect
    for rel in inspect(type(obj)).relationships:
        if rel.direction.name != "MANYTOONE":
            continue
        parent = getattr(obj, rel.key, None)
        if parent is not None and isinstance(parent, TenantMixin) and parent.team_id:
            return parent.team_id
    return None
