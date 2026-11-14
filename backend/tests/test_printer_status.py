"""Printer codes (services/printer_status.py): descriptions, errors only while the farm's alarm is active, hidden
codes, and maintenance reminders as Upkeep tasks."""
import pytest

from app.services import printer_status as ps

H2D = {"name": "H2D", "state": "finish", "busy": False, "online": True, "error": "0501040000030002"}
H2C = {"name": "H2C", "state": "failed", "busy": False, "online": True, "error": "0500060000020005"}


@pytest.fixture
def kv(monkeypatch):
    store = {}
    monkeypatch.setattr(ps, "setting", lambda db, k, default=None: store.get(k, default))
    monkeypatch.setattr(ps, "set_setting", lambda db, k, v: store.__setitem__(k, v))
    monkeypatch.setattr(ps, "_load_codes", lambda: {"0500060000020005": "The Live View camera is not responding.",
                                                    "05008051": "Detected build plate is not the same as the Gcode file."})
    return store


@pytest.fixture
def tasks(monkeypatch):
    made = []

    class T:
        def __init__(self, n):
            self.id, self.created_by = n, "printer"

    def mx(db, printer, code, message):
        made.append((printer, code, message))
        return T(len(made))

    monkeypatch.setattr(ps, "_mx_task", mx)
    return made


def test_codes_in_every_spelling_are_one_code():
    assert ps.norm("HMS_0500_0600_0002_0005") == ps.norm("0500-0600-0002-0005") == "0500060000020005"
    assert ps.norm("PRINT_ERR_0500_8051") == "05008051"


def test_a_cleared_alarm_is_no_error_and_the_last_job_reads_idle(kv, tasks):
    (p,) = ps.annotate(None, [H2C])
    assert p["error"] is None and p["state"] == "idle" and p["last_result"] == "failed"
    assert p["reported_code"] == "0500060000020005"


def test_an_active_alarm_is_an_error_with_its_description(kv, tasks):
    ps.alarm_raised(None, "H2C", "HMS_0500_0600_0002_0005", "camera")
    (p,) = ps.annotate(None, [H2C])
    assert p["error"] == "0500060000020005" and p["error_message"] == "The Live View camera is not responding."
    assert p["state"] == "failed"
    ps.alarm_cleared(None, "h2c", "0500-0600-0002-0005")
    assert ps.annotate(None, [H2C])[0]["error"] is None


def test_clearing_without_a_code_clears_the_printer(kv, tasks):
    ps.alarm_raised(None, "H2C", "0500060000020005", "")
    ps.alarm_cleared(None, "H2C")
    assert kv["active_alarms"] == {}


def test_a_hidden_code_is_never_an_error(kv, tasks):
    ps.alarm_raised(None, "H2C", "0500060000020005", "")
    ps.hide(None, "HMS_0500_0600_0002_0005", "H2C", "james")
    p = ps.annotate(None, [H2C])[0]
    assert p["error"] is None and p["error_hidden"] is True
    other = ps.annotate(None, [{**H2C, "name": "H2S"}])[0]           # hidden for H2C only
    ps.alarm_raised(None, "H2S", "0500060000020005", "")
    assert ps.annotate(None, [{**H2C, "name": "H2S"}])[0]["error"] == "0500060000020005"
    assert other["error"] is None
    assert ps.unhide(None, "0500060000020005") is True and ps.hidden_list(None) == []


def test_a_maintenance_reminder_is_due_not_an_error(kv, tasks):
    ps.alarm_raised(None, "H2D", "0501040000030002", "")              # even with an alarm
    (p,) = ps.annotate(None, [H2D])
    assert p["error"] is None and p["state"] == "finish"
    assert p["mx_due"] == [{"code": "0501040000030002", "message": "Threaded rods need lubrication now.", "task_id": 1}]
    assert tasks == [("H2D", "0501040000030002", "Threaded rods need lubrication now.")]


def test_maintenance_wording_is_recognised():
    assert ps.is_maintenance("x", "Carbon rods need cleaning now.")
    assert ps.is_maintenance("x", "The nozzle wiper needs cleaning.")
    assert not ps.is_maintenance("x", "The factory data of AP board is abnormal; please replace the AP board")
    assert not ps.is_maintenance("x", "AMS A The power adapter voltage is too low")


def test_an_unknown_active_code_still_says_something(kv, tasks):
    ps.alarm_raised(None, "H2C", "0C00010000020099", "")
    p = ps.annotate(None, [{**H2C, "error": "0C00010000020099"}])[0]
    assert "no description from Bambu" in p["error_message"]
