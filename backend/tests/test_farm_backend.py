"""The farm interface: the hub reaches its print farm only through clients.farm() (Watchtower driver today)."""
import pytest


def test_watchtower_is_a_farm_backend_with_everything_it_had():
    from app.clients.farm_base import CAPABILITIES, FarmBackend
    from app.clients.watchtower_api import WatchtowerClient
    w = WatchtowerClient("http://wt.example", "k", "u", "p")
    assert isinstance(w, FarmBackend) and w.kind == "watchtower"
    assert set(w.CAPABILITIES) <= set(CAPABILITIES) and w.has("sku_mapping") and w.has("maintenance")
    d = w.describe()
    assert d["kind"] == "watchtower" and "queue" in d["capabilities"]
    # Every method of the interface is implemented by the driver itself, not left to the base's "unsupported",
    # except the ones for capabilities only OpenPrintHQ has (Watchtower has no preflight, queue statistics or
    # alarm feed).
    only_ophq = ("preflight", "queue_stats", "alarms", "alarm_summary", "ack_alarm", "ack_printer_alarms")
    for name, fn in vars(FarmBackend).items():
        if callable(fn) and not name.startswith("_") and name not in ("has", "describe", *only_ophq):
            assert getattr(type(w), name) is not fn, f"Watchtower driver lacks {name}"


def test_unsupported_raises_an_api_error_callers_already_handle():
    from app.clients.base import ApiError
    from app.clients.farm_base import FarmBackend, FarmUnsupported

    class Tiny(FarmBackend):
        kind = "tiny"
        CAPABILITIES = frozenset({"printers"})

        def printers(self):
            return []

    t = Tiny()
    assert t.printers() == [] and not t.has("sku_mapping")
    with pytest.raises(FarmUnsupported) as e:
        t.sku_mappings()
    assert isinstance(e.value, ApiError) and e.value.capability == "sku_mapping"


def test_farm_is_the_teams_watchtower_for_now(monkeypatch):
    from app import clients
    sentinel = object()
    monkeypatch.setattr(clients, "watchtower", lambda: sentinel)
    assert clients.farm() is sentinel and clients.farm_kind() == "watchtower"


def test_named_library_calls_hit_the_same_watchtower_paths(monkeypatch):
    from app.clients.watchtower_api import WatchtowerClient
    calls = []
    w = WatchtowerClient("http://wt.example", "k", "u", "p")
    monkeypatch.setattr(w, "_priv", lambda m, p, **kw: calls.append((m, p, kw.get("json"))) or {"id": 7})
    monkeypatch.setattr(w, "_pub", lambda m, p, **kw: calls.append((m, p, None)) or {})
    w.library_folder("Benchy", 3)
    w.move_library_file("u1", 7)
    w.delete_library_file("u1")
    w.delete_library_folder(7)
    w.printer("Voron 2.4")
    w.cancel_job(12)
    assert calls == [
        ("POST", "/api/library/folders", {"name": "Benchy", "parent_folder_id": 3, "reuse_existing": True}),
        ("POST", "/api/library/files/u1/move", {"folder_id": 7}),
        ("DELETE", "/api/library/files/gcode/u1", None),
        ("DELETE", "/api/library/folders/7", None),
        ("GET", "/printers/Voron%202.4", None),
        ("POST", "/jobs/12/cancel", None),
    ]
