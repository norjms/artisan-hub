"""The installation: its one print farm, chosen explicitly, and first-run setup.

Invariant 1: exactly one print farm per installation, chosen explicitly and never inferred from which
connections happen to exist. That inference is what allowed the November 2026 double-tracking bug.

Invariants 3 and 4: the hub is complete without the installer, and the setup API lives in the hub. First-run
setup goes through /api/setup/v1, used both by the hub's own /setup page and by the installer from outside.

Installations that predate the explicit choice are migrated at startup: if every team with a farm connected
agrees on the same farm, that farm is written as the explicit choice ("migrated"). If they do not agree, or no
farm is connected, nothing is guessed: the installation keeps the old per-team inference ("legacy") and the
hub logs a warning, so nothing changes for it until an admin chooses.
"""

import hashlib
import hmac
import logging
import secrets
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..tenant import unscoped
from .connections import NotConnected

log = logging.getLogger("hub.installation")
FARM_KINDS = ("watchtower", "openprinthq")
TTL = 20.0   # web and worker are separate processes; a short cache, as for connections
_cache: dict = {}
_lock = threading.Lock()


class FarmNotChosen(NotConnected):
    def __init__(self):
        RuntimeError.__init__(self, "No print farm has been chosen for this installation yet. Finish first-run setup at /setup.")
        self.kind = "farm"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def state() -> dict:
    """{"farm", "farm_source", "setup_complete"} for the installation, cached for a few seconds."""
    t = time.time()
    with _lock:
        hit = _cache.get("state")
    if hit and t - hit[0] < TTL:
        return hit[1]
    from ..db import SessionLocal
    from ..models import Installation
    s = SessionLocal()
    try:
        row = s.get(Installation, 1)
        st = {"farm": row.farm if row else None, "farm_source": row.farm_source if row else None,
              "setup_complete": bool(row and row.setup_completed_at)}
    finally:
        s.close()
    # Cache only settled states. "Not chosen yet" ends at setup, which runs in one process; the others must
    # see the change at once rather than up to TTL later, so that state is always read fresh.
    if st["farm"] or st["farm_source"] == "legacy":
        with _lock:
            _cache["state"] = (t, st)
    return st


def forget() -> None:
    with _lock:
        _cache.clear()


def chosen_farm() -> str | None:
    return state()["farm"]


def _inferred(rows: list) -> str | None:
    """The old per-team rule, for one team's connection rows: OpenPrintHQ when it is the only farm connected, or is
    marked use_as_farm; Watchtower when it is connected; otherwise none."""
    have = {r.kind: r for r in rows if r.kind in FARM_KINDS and r.enabled and (r.config or {}).get("url")}
    if "openprinthq" in have and ("watchtower" not in have or (have["openprinthq"].config or {}).get("use_as_farm")):
        return "openprinthq"
    return "watchtower" if "watchtower" in have else None


def decide(kinds: set[str], fresh: bool) -> tuple[str | None, str | None]:
    """(farm, farm_source) for an installation seen for the first time, from the farms its teams' connections imply.
    One farm everywhere: that farm, migrated. Anything else on an existing installation: legacy, nothing guessed.
    A new installation with no farm connected: nothing, until first-run setup chooses."""
    if len(kinds) == 1:
        return next(iter(kinds)), "migrated"
    if not fresh:
        return None, "legacy"
    return None, None


def bootstrap(db: Session, fresh: bool) -> None:
    """At startup: make sure the installation row exists, migrate an older installation once, and on a new
    installation that has no setup token from the environment, generate one and log it once."""
    from ..models import Connection, Installation, User
    row = db.get(Installation, 1)
    if row is None:
        row = Installation(id=1)
        db.add(row)
        db.flush()
        by_team: dict[int, list] = {}
        with unscoped():   # every team's connections, across teams
            for c in db.execute(select(Connection).where(Connection.kind.in_(FARM_KINDS))).scalars():
                by_team.setdefault(c.team_id, []).append(c)
        kinds = {k for k in (_inferred(rows) for rows in by_team.values()) if k}
        row.farm, row.farm_source = decide(kinds, fresh)
        if row.farm_source == "migrated":
            log.info("Installation farm set to %s from its existing connections", row.farm)
        elif row.farm_source == "legacy":
            log.warning("Installation farm not chosen: %s. The hub keeps choosing per team, as before, until an "
                        "admin chooses one farm for the installation.",
                        "teams use different farms" if kinds else "no farm is connected")
        if not fresh and db.execute(select(func.count()).select_from(User)).scalar():
            row.setup_completed_at = _now()   # an installation already in use never runs first-run setup
    if row.setup_completed_at is None and not settings().setup_token and not row.setup_token_hash:
        token = secrets.token_urlsafe(24)
        row.setup_token_hash = _hash(token)
        log.warning("First-run setup: open %s/setup and enter this setup token: %s",
                    settings().public_url.rstrip("/"), token)
    db.flush()
    forget()


def token_ok(row, token: str) -> bool:
    expected = settings().setup_token
    if expected:
        return hmac.compare_digest(token, expected)
    return bool(row.setup_token_hash) and hmac.compare_digest(_hash(token), row.setup_token_hash)


def connect_allowed(kind: str, existing: bool) -> None:
    """Refuse a new connection to a farm other than the installation's own (invariant 1). An existing connection
    row can still be edited, so an installation that predates the choice keeps working as it did."""
    if kind not in FARM_KINDS or existing:
        return
    st = state()
    if st["farm"] and kind != st["farm"]:
        raise ValueError(f"This installation's print farm is {st['farm']}. Connecting {kind} as well is refused: "
                         "one installation runs exactly one farm.")
    if not st["farm"] and st["farm_source"] != "legacy":
        raise ValueError("Choose this installation's print farm first (first-run setup at /setup).")
