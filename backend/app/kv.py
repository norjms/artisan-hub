"""Per-team key/value settings (the setting table is keyed by team and key)."""
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Setting

_memo: ContextVar[dict | None] = ContextVar("kv_memo", default=None)


@contextmanager
def read_cache():
    """Within the block, each setting is read from the database once (for long read-only reports)."""
    tok = _memo.set({})
    try:
        yield
    finally:
        _memo.reset(tok)


def setting_row(db: Session, key: str) -> Setting | None:
    return db.execute(select(Setting).where(Setting.key == key)).scalar()


def setting(db: Session, key: str, default=None):
    memo = _memo.get()
    if memo is not None:
        if key not in memo:
            row = setting_row(db, key)
            memo[key] = row.value if row else None
        return memo[key] if memo[key] is not None else default
    row = setting_row(db, key)
    return row.value if row else default


def set_setting(db: Session, key: str, value):
    row = setting_row(db, key)
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))
        db.flush()
    return value
