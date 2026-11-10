"""Archive, restore and permanent delete."""
import io


def test_archive_restore_delete(env):
    from fastapi.testclient import TestClient
    from PIL import Image
    from app.main import app
    from app.db import session_scope
    from app.models import Model
    from app.services import removal, images as imgsvc
    c = TestClient(app)
    mid = c.post("/api/models", json={"name": "Doomed Vase", "category": "Vases", "price": 12}).json()["id"]
    buf = io.BytesIO()
    Image.new("RGB", (600, 600), (10, 20, 230)).save(buf, "JPEG")
    assert c.post(f"/api/models/{mid}/images", files=[("files", ("v.jpg", buf.getvalue(), "image/jpeg"))]).status_code == 200
    d = c.get(f"/api/models/{mid}").json()
    code = d["code"]
    sha = None
    with session_scope() as db:
        sha = db.get(Model, mid).images[0].sha256
    assert imgsvc.variant_path(sha, "thumb").exists()

    # archive hides it from the default list, restore brings it back
    assert c.delete(f"/api/models/{mid}").json()["status"] == "archived"
    assert all(i["id"] != mid for i in c.get("/api/models?q=Doomed").json()["items"])
    assert any(i["id"] == mid for i in c.get("/api/models?status=archived&q=Doomed").json()["items"])
    assert c.post(f"/api/models/{mid}/restore").json()["status"] != "archived"

    chk = c.get(f"/api/models/{mid}/delete-check").json()
    assert chk["blockers"] == [] and chk["will_remove"]["photos"] == 1
    assert c.post(f"/api/models/{mid}/purge", json={"confirm": "nope"}).status_code == 400
    r = c.post(f"/api/models/{mid}/purge", json={"confirm": code})
    assert r.status_code == 200, r.text

    # the worker would run this; run it inline
    with session_scope() as db:
        rep = removal.purge(db, mid)
        db.commit()
    assert rep["code"] == code and rep["image_files"] >= 1
    assert c.get(f"/api/models/{mid}").status_code == 404
    assert not imgsvc.variant_path(sha, "thumb").exists()
    bl = c.get("/api/import-blocklist").json()
    assert code in bl["codes"]
    c.post("/api/import-blocklist/remove", json={"code": code})
    assert code not in c.get("/api/import-blocklist").json()["codes"]


def test_bulk_needs_confirmation(env):
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    mid = c.post("/api/models", json={"name": "Bulk Thing"}).json()["id"]
    assert c.post("/api/models/bulk", json={"model_ids": [mid], "action": "delete"}).status_code == 400
    r = c.post("/api/models/bulk", json={"model_ids": [mid], "action": "archive"}).json()
    assert r["done"] and not r["refused"]
