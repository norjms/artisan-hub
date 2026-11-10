"""MCP endpoint: handshake, tool list, model lookup and a slice through the job queue with a fake slicer."""
from pathlib import Path


def rpc(c, method, params=None, id_=1):
    r = c.post("/mcp", json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}})
    assert r.status_code == 200, r.text
    return r.json()


def call(c, name, args=None):
    out = rpc(c, "tools/call", {"name": name, "arguments": args or {}})
    assert "result" in out, out
    return out["result"]


def test_handshake_and_list(env):
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    init = rpc(c, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "t", "version": "1"}})["result"]
    assert init["protocolVersion"] == "2025-06-18" and init["capabilities"]["tools"]
    assert c.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202
    names = {t["name"] for t in rpc(c, "tools/list")["result"]["tools"]}
    assert {"slice_model", "list_printers", "find_models", "slice_job_status", "set_production_file", "slice_results",
            "approve_slice"} <= names
    assert rpc(c, "nope")["error"]["code"] == -32601
    assert c.get("/mcp").status_code == 405
    bad = call(c, "model_slicing_info", {"model": "NOPE-1"})
    assert bad["isError"] and "no model" in bad["content"][0]["text"]


def test_slice_via_mcp(env, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.config import settings
    from app.db import session_scope
    from app.jobs import HANDLERS
    from app import tasks  # noqa: F401  (registers slice.model)
    from app.models import Job, Model, ModelFile, PrinterProfile
    from app.services import slicing
    from tests.test_slicing import FakeSlicer
    fake = FakeSlicer(tmp_path)
    monkeypatch.setattr(slicing, "client", lambda: fake)
    c = TestClient(app)
    mid = c.post("/api/models", json={"name": "Mcp Slice", "grams": 0}).json()["id"]
    with session_scope() as db:
        m = db.get(Model, mid)
        code = m.code
        lib = Path(settings().library_root) / "mcp" / "part.stl"
        lib.parent.mkdir(parents=True, exist_ok=True)
        lib.write_bytes(b"solid x\nendsolid x\n")
        m.files.append(ModelFile(filename="part.stl", kind="stl", library_rel="mcp/part.stl", sha256="mcp1"))
        db.add(PrinterProfile(name="MCP-H2D", wt_printer="mcp h2d", machine_preset="Bambu Lab H2D 0.4 nozzle"))
    printers = call(c, "list_printers")
    assert "MCP-H2D" in printers["content"][0]["text"]
    found = call(c, "find_models", {"query": "mcp slice"})
    assert code in found["content"][0]["text"]
    r = call(c, "slice_model", {"model": code, "printer": "mcp h2d", "walls": 4, "infill_percent": 20,
                                "infill_pattern": "gyroid", "approve": True, "wait_seconds": 0})
    sc = r["structuredContent"]
    assert sc["state"] == "queued" and sc["printer"] == "MCP-H2D"
    jid = sc["job_id"]
    with session_scope() as db:
        j = db.get(Job, jid)
        assert j.payload["opts"]["process_overrides"] == {"wall_loops": "4", "sparse_infill_density": "20%",
                                                          "sparse_infill_pattern": "gyroid"}
        j.result = HANDLERS["slice.model"](db, j)
        j.state = "done"
    st = call(c, "slice_job_status", {"job_id": jid})["structuredContent"]
    assert st["state"] == "done" and st["result"]["grams"] == 42.0 and st["result"]["print_time"] == "1h 0m"
    info = call(c, "model_slicing_info", {"model": code, "printer": "MCP-H2D"})["structuredContent"]
    sliced = [f for f in info["files"] if f["kind"] == "3mf_sliced"]
    assert len(sliced) == 1 and sliced[0]["production"] and sliced[0]["grams"] == 42.0
    sr = call(c, "slice_results", {"model": code})["structuredContent"]
    assert sr["printers"][0]["state"] == "approved" and sr["printers"][0]["grams_per_unit"] == 42.0
    res = call(c, "set_production_file", {"file_id": sliced[0]["file_id"]})
    assert not res.get("isError")
    bad = call(c, "set_production_file", {"file_id": [f for f in info["files"] if f["kind"] == "stl"][0]["file_id"]})
    assert bad["isError"]
