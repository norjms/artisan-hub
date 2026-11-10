"""Postgres-backed job queue (SELECT ... FOR UPDATE SKIP LOCKED) plus a tiny scheduler.

Every sync action is a Job row, so the Sync page shows exactly what happened, what failed and why,
and failed jobs can be retried from the UI.
"""
import logging
import time
import traceback
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update, and_, or_, text
from sqlalchemy.orm import Session

from .db import SessionLocal, session_scope
from .models import Job, Event, Team
from . import tenant

log = logging.getLogger("hub.jobs")

HANDLERS: dict = {}
# Bulk background work yields to anything a person (or an order) is waiting on.
PRIORITY = {"import.folder": 8, "dam.sync_model": 7, "dam.refresh_metadata": 7, "library.scan_deltaprints": 6,
            "manyfold.link": 6, "manyfold.rescan": 6, "watchtower.mirror_model": 9}


def handler(kind: str):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


def utcnow():
    return datetime.now(timezone.utc)


def _naive(d):
    return d.replace(tzinfo=None) if d is not None and d.tzinfo else d


def enqueue(db: Session, kind: str, ref: str | None = None, payload: dict | None = None, *,
            delay: int = 0, dedupe: bool = True, max_attempts: int = 5, actor: str | None = None,
            priority: int | None = None) -> Job:
    """Queue a job. With dedupe, an identical queued job (same kind+ref) is reused."""
    if dedupe and ref:
        existing = db.execute(select(Job).where(Job.kind == kind, Job.ref == ref, Job.state == "queued")).scalar()
        if existing:
            if payload:
                existing.payload = {**(existing.payload or {}), **payload}
            # a more urgent request for the same work moves the queued one forward
            soon = utcnow() + timedelta(seconds=delay)
            if existing.run_after and _naive(existing.run_after) > _naive(soon):
                existing.run_after = soon
            if priority is not None and (existing.priority is None or priority < existing.priority):
                existing.priority = priority
            return existing
    if priority is None:
        priority = PRIORITY.get(kind, 5)
        if actor and not actor.startswith("periodic"):
            priority = min(priority, 2)
    j = Job(kind=kind, ref=ref, payload=payload or {}, run_after=utcnow() + timedelta(seconds=delay),
            max_attempts=max_attempts, created_by=actor, priority=priority)
    db.add(j)
    db.flush()
    return j


def event(db: Session, ref: str, message: str, level: str = "info", actor: str | None = None):
    db.add(Event(ref=ref, message=message, level=level, actor=actor))


def claim(db: Session) -> Job | None:
    row = db.execute(
        select(Job).where(Job.state == "queued", Job.run_after <= utcnow())
        .order_by(Job.priority, Job.run_after, Job.id).limit(1).with_for_update(skip_locked=True)
    ).scalar()
    if not row:
        return None
    row.state = "running"
    row.started_at = utcnow()
    row.attempts += 1
    db.commit()
    return row


def run_one() -> bool:
    db = SessionLocal()
    tenant.bind_session(db, None, True)       # claiming looks at every team's queue
    try:
        job = claim(db)
        if not job:
            return False
        tenant.bind_session(db, job.team_id)  # the handler only sees the job's team
        tok = tenant._team.set(job.team_id)
        fn = HANDLERS.get(job.kind)
        try:
            if not fn:
                raise RuntimeError(f"no handler for job kind {job.kind}")
            try:
                result = fn(db, job)
            except NotConnected as nc:
                db.rollback()
                job = db.get(Job, job.id)
                result = {"skipped": str(nc)}
            job.state = "done"
            job.result = result if isinstance(result, dict) else ({"result": result} if result is not None else None)
            job.error = None
            job.finished_at = utcnow()
            db.commit()
        except Defer as d:
            db.rollback()
            job = db.get(Job, job.id)
            job.state = "queued"
            job.attempts = max(0, job.attempts - 1)
            job.run_after = utcnow() + timedelta(seconds=d.seconds)
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            job = db.get(Job, job.id)
            msg = f"{type(e).__name__}: {e}"
            job.error = msg[:4000] + "\n" + traceback.format_exc()[-3000:]
            if job.attempts >= job.max_attempts or isinstance(e, PermanentError):
                job.state = "failed"
                job.finished_at = utcnow()
                if job.ref:
                    event(db, job.ref, f"{job.kind} failed: {msg[:500]}", level="error")
            else:
                job.state = "queued"
                job.run_after = utcnow() + timedelta(seconds=min(3600, 30 * 2 ** (job.attempts - 1)))
            db.commit()
            log.warning("job %s %s failed (attempt %s): %s", job.id, job.kind, job.attempts, msg)
        finally:
            tenant._team.reset(tok)
        return True
    finally:
        db.close()


from .services.connections import NotConnected  # noqa: E402


class PermanentError(Exception):
    """Raise from a handler to fail a job without retrying (bad input, missing data)."""


class Defer(Exception):
    """Raise from a handler to put the job back in the queue for later without using up an attempt."""

    def __init__(self, seconds: int = 30, reason: str = ""):
        super().__init__(reason or f"deferred {seconds}s")
        self.seconds = seconds


# ---------------- scheduler ----------------
PERIODIC: list[tuple[str, int, object]] = []   # (kind, seconds, needs)


def every(kind: str, seconds: int, needs=None):
    """Run `kind` every `seconds` for each team. `needs` is a connection kind, or a callable(team) -> bool,
    that says whether the team uses this job at all (no store connected: no stock sync)."""
    PERIODIC.append((kind, seconds, needs))


def _team_wants(team, needs) -> bool:
    if needs is None:
        return True
    if callable(needs):
        return bool(needs(team))
    from .services.connections import is_connected
    return is_connected(needs, team.id)


def schedule_periodic(last_run: dict):
    now = time.time()
    db = SessionLocal()
    try:
        tenant.bind_session(db, None, True)
        teams = list(db.execute(select(Team).order_by(Team.id)).scalars())
        for team in teams:
            for kind, secs, needs in PERIODIC:
                key = (team.id, kind)
                if now - last_run.get(key, 0) < secs:
                    continue
                last_run[key] = now
                try:
                    if not _team_wants(team, needs):
                        continue
                except Exception:  # noqa: BLE001
                    log.exception("periodic check %s for team %s", kind, team.id)
                    continue
                tenant.bind_session(db, team.id)
                enqueue(db, kind, ref=f"periodic:{kind}", max_attempts=1)
                db.commit()
                tenant.bind_session(db, None, True)
    finally:
        db.close()


def recover_stuck(minutes: int = 30):
    """Jobs left 'running' by a crashed or restarted worker go back to the queue."""
    with session_scope() as db:
        tenant.bind_session(db, None, True)
        db.execute(update(Job).where(Job.state == "running", Job.started_at < utcnow() - timedelta(minutes=minutes))
                   .values(state="queued", run_after=utcnow()))


def prune(days: int = 30):
    with session_scope() as db:
        db.execute(text("DELETE FROM job WHERE state='done' AND finished_at < now() - make_interval(days => :d)"),
                   {"d": days})


def _runner(stop):
    while not stop():
        try:
            worked = run_one()
        except Exception:  # noqa: BLE001
            log.exception("runner error")
            worked = False
            time.sleep(5)
        if not worked:
            time.sleep(1)


def worker_loop(stop=lambda: False, threads: int = 3):
    import threading
    from . import tasks  # noqa: F401  (registers handlers + periodic)
    last_run: dict = {}
    recover_stuck(0)   # one worker process: anything 'running' at startup was interrupted
    for n in range(threads - 1):
        threading.Thread(target=_runner, args=(stop,), daemon=True, name=f"runner-{n}").start()
    tick = 0
    while not stop():
        try:
            if tick % 10 == 0:
                schedule_periodic(last_run)
            if tick % 3600 == 0:
                prune()
            worked = run_one()
        except Exception:  # noqa: BLE001
            log.exception("worker loop error")
            worked = False
            time.sleep(5)
        tick += 1
        if not worked:
            time.sleep(1)
