"""The Sync health badge counts only failures that still need a look."""


def test_badge_counts_unresolved_failures_only(env):
    from app.db import session_scope
    from app.models import Job
    from app.routers.ops import unresolved_failures
    with session_scope() as db:
        base = unresolved_failures(db)
        db.add_all([
            Job(kind="farm.sync", ref="badge:a", state="failed", error="ApiError: 522"),
            Job(kind="farm.sync", ref="badge:a", state="done"),                      # resolved by a later success
            Job(kind="slice.model", ref="badge:b", state="failed",
                error="PermanentError: the model doesn't fit on voron01's bed"),     # a slicing outcome, not sync
            Job(kind="library.scan_inbox", ref="badge:c", state="failed", error="PermissionError: denied"),
        ])
        db.flush()
        assert unresolved_failures(db) == base + 1
