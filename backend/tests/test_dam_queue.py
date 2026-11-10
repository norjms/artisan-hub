"""DAM uploads: a failed upload keeps its resource for the retry, and only one upload runs at a time."""
from pathlib import Path


class FakeDam:
    def __init__(self):
        self.created = 0
        self.uploads = 0
        self.fail_next = True
        self.files = set()

    def create_resource(self, rtype, meta, archive=0):
        self.created += 1
        return 1000 + self.created

    def upload(self, ref, path, filename):
        self.uploads += 1
        if self.fail_next:
            self.fail_next = False
            raise TimeoutError("timed out")
        self.files.add(ref)

    def has_file(self, ref):
        return ref in self.files

    def update_field(self, ref, field, value):
        pass

    def add_to_collection(self, ref, coll):
        pass


def test_retry_reuses_resource(env, monkeypatch):
    import pytest
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import session_scope
    from app.config import settings
    from app.models import Image
    from app.services import dam as damsvc
    fake = FakeDam()
    monkeypatch.setattr(damsvc, "dam", lambda: fake)
    monkeypatch.setattr(damsvc, "ensure_fields", lambda db: {k: 1 for k in
                        ("sku", "model_code", "model", "designer", "category", "role", "hub_url", "brand")})
    monkeypatch.setattr(damsvc, "ensure_collection", lambda db, m: 7)
    mid = TestClient(app).post("/api/models", json={"name": "Dam Me", "grams": 0}).json()["id"]
    src = Path(settings().data_dir) / "test-dam" / "a.jpg"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"jpg")
    with session_scope() as db:
        db.add(Image(model_id=mid, sha256="dam-a", filename="a.jpg", role="gallery", store_rel="test-dam/a.jpg"))
    with session_scope() as db:
        with pytest.raises(RuntimeError):
            damsvc.sync_model(db, mid)
    with session_scope() as db:
        img = db.query(Image).filter_by(sha256="dam-a").one()
        assert img.dam_ref == 1001 and img.dam_error
    with session_scope() as db:
        assert damsvc.sync_model(db, mid) == {"uploaded": 1}
    with session_scope() as db:
        img = db.query(Image).filter_by(sha256="dam-a").one()
        assert img.dam_ref == 1001 and not img.dam_error
    assert fake.created == 1 and fake.uploads == 2


def test_busy_upload_defers_without_using_attempt(env, monkeypatch):
    from app import jobs
    from app.db import session_scope
    from app.models import Job
    calls = []

    @jobs.handler("test.defer")
    def _h(db, job):
        calls.append(1)
        raise jobs.Defer(60)

    with session_scope() as db:
        j = jobs.enqueue(db, "test.defer", ref="test:defer", priority=0)
        db.flush()
        jid = j.id
    assert jobs.run_one()
    with session_scope() as db:
        j = db.get(Job, jid)
        assert j.state == "queued" and j.attempts == 0 and not j.error
        db.delete(j)
    assert calls == [1]
