"""What a printer's error code means, and whether it is still an error.

Bambu printers keep reporting the last HMS or print-error code until their next job, long after the problem was dealt
with. The hub turns that raw code into what people should see:

- the code's description, from Bambu's published list (https://e.bambulab.com/query.php, the list Bambu Studio and
  Handy show; cached for a day, with the maintenance reminders built in for when Bambu cannot be reached);
- an error only while Watchtower has an active alarm for it (alarm.raised until alarm.cleared, from its webhooks);
  once Watchtower clears it, the printer shows idle;
- never an error for a code someone hid as a nuisance (Settings, or Hide this error in the app);
- a maintenance reminder ("Threaded rods need lubrication now") is not an error at all: it opens an Upkeep task once
  per printer and code, the printer reports idle with maintenance due, and marking the task done clears it (it does
  not reopen for 14 days even if the printer keeps saying it).

annotate() adds to each printer dict from the farm: error (the active code, or None), error_message, reported_code,
error_hidden, mx_due ([{code, message, task_id}]) and last_result; state "failed"/"cancelled"/"error" becomes "idle"
when nothing is active.
"""
import logging
import re
import threading
import time
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..kv import set_setting, setting
from ..models import now

log = logging.getLogger("hub.printer_status")

CODES_URL = "https://e.bambulab.com/query.php?lang=en"
CODES_TTL = 24 * 3600
REOPEN_AFTER = timedelta(days=14)
_codes: dict[str, str] = {}
_codes_at = 0.0
_codes_lock = threading.Lock()

# Bambu's own maintenance reminders, so they are recognised even without the downloaded list.
BUILT_IN = {
    "0501040000030001": "Carbon rods need cleaning now.",
    "0501040000030002": "Threaded rods need lubrication now.",
}
MAINTENANCE_RE = re.compile(r"\bneeds? (?:\w+ )?(?:cleaning|lubrication|lubricating|maintenance|greasing)\b|"
                            r"\bneeds? to be (?:cleaned|lubricated|greased)\b|\bmaintenance is due\b", re.I)
LAST_JOB_STATES = {"failed", "cancelled", "canceled", "error", "stopped"}


def norm(code) -> str:
    """HMS_0500_0600_0002_0005, 0500-0600-0002-0005 and 0500060000020005 are the same code; PRINT_ERR_0500_8051 is
    05008051."""
    h = re.sub(r"[^0-9A-Fa-f]", "", re.sub(r"^(HMS|PRINT_ERR|ERR)[_-]?", "", str(code or "").strip(), flags=re.I))
    return h.upper()


def _load_codes() -> dict[str, str]:
    global _codes, _codes_at
    with _codes_lock:
        if _codes and time.time() - _codes_at < CODES_TTL:
            return _codes
        try:
            data = httpx.get(CODES_URL, timeout=20).json().get("data") or {}
            got = {}
            for group in ("device_error", "device_hms"):
                for x in ((data.get(group) or {}).get("en") or []):
                    if x.get("ecode") and x.get("intro"):
                        got[norm(x["ecode"])] = str(x["intro"]).strip()
            if got:
                _codes, _codes_at = got, time.time()
        except Exception as e:  # noqa: BLE001
            log.info("Bambu code list: %s", e)
            _codes_at = time.time() - CODES_TTL + 600          # try again in ten minutes
        return _codes


def describe(code) -> str | None:
    c = norm(code)
    if not c:
        return None
    return _load_codes().get(c) or BUILT_IN.get(c)


def is_maintenance(code, text: str | None = None) -> bool:
    c = norm(code)
    if c in BUILT_IN:
        return True
    t = text if text is not None else describe(c)
    return bool(t and MAINTENANCE_RE.search(t))


# ---------------------------------------------------------------- active alarms (Watchtower webhooks)

def _key(printer) -> str:
    return str(printer or "").strip().lower()


def alarm_raised(db: Session, printer: str, code, message: str | None):
    if not printer or not norm(code):
        return
    act = dict(setting(db, "active_alarms") or {})
    mine = dict(act.get(_key(printer)) or {})
    mine[norm(code)] = {"message": (message or "")[:300], "at": now().isoformat()}
    act[_key(printer)] = mine
    set_setting(db, "active_alarms", act)


def alarm_cleared(db: Session, printer: str, code=None):
    """Watchtower cleared an alarm: that code, or all of the printer's when the event names none."""
    if not printer:
        return
    act = dict(setting(db, "active_alarms") or {})
    if _key(printer) not in act:
        return
    if norm(code):
        mine = dict(act[_key(printer)])
        mine.pop(norm(code), None)
        act[_key(printer)] = mine
    else:
        act.pop(_key(printer))
    set_setting(db, "active_alarms", {k: v for k, v in act.items() if v})


def active(db: Session, printer: str) -> dict:
    return dict((setting(db, "active_alarms") or {}).get(_key(printer)) or {})


# ---------------------------------------------------------------- hidden (nuisance) codes

def hidden(db: Session) -> dict:
    """{code: {printer (None for every printer), message, by, at}}."""
    return dict(setting(db, "hidden_printer_codes") or {})


def is_hidden(db: Session, printer: str, code) -> bool:
    h = hidden(db).get(norm(code))
    return bool(h) and (not h.get("printer") or _key(h["printer"]) == _key(printer))


def hide(db: Session, code, printer: str | None, by: str) -> dict:
    c = norm(code)
    if not c:
        raise ValueError("no code")
    h = hidden(db)
    h[c] = {"printer": printer or None, "message": describe(c), "by": by, "at": now().isoformat()}
    set_setting(db, "hidden_printer_codes", h)
    return h[c]


def unhide(db: Session, code) -> bool:
    h = hidden(db)
    gone = h.pop(norm(code), None) is not None
    set_setting(db, "hidden_printer_codes", h)
    return gone


def hidden_list(db: Session) -> list[dict]:
    return [{"code": c, **v} for c, v in sorted(hidden(db).items())]


# ---------------------------------------------------------------- maintenance reminders as Upkeep tasks

def _mx_task(db: Session, printer: str, code: str, message: str):
    """The open Upkeep task for this reminder (opened if needed), or None when one was done recently."""
    from ..models import MaintTask
    key = f"printer:{code}"[:80]
    rows = list(db.execute(select(MaintTask).where(MaintTask.printer_name == printer, MaintTask.alarm_code == key)
                           .order_by(MaintTask.id.desc()).limit(5)).scalars())
    for t in rows:
        if t.state == "open":
            return t
    if any(t.done_at and t.done_at > now() - REOPEN_AFTER for t in rows):
        return None
    t = MaintTask(printer_name=printer, title=f"{printer}: {message}"[:300], source="printer", alarm_code=key,
                  due_at=now(), created_by="printer")
    db.add(t)
    db.flush()
    return t


# ---------------------------------------------------------------- the view

def annotate(db: Session, printers: list[dict] | None) -> list[dict] | None:
    if printers is None:
        return None
    out, opened = [], False
    for x in printers:
        p = dict(x)
        name = str(p.get("name") or "")
        raw = p.get("error")
        code = norm(raw)
        msg = describe(code) if code else None
        p.update(reported_code=code or None, error_message=None, error_hidden=False, mx_due=[], last_result=None)
        if code:
            alarms = active(db, name)
            if is_maintenance(code, msg):
                try:
                    t = _mx_task(db, name, code, msg or "maintenance due")
                    opened = opened or (t is not None and t.id is not None and t.created_by == "printer")
                    if t is not None:
                        p["mx_due"] = [{"code": code, "message": msg or "Maintenance due", "task_id": t.id}]
                except Exception as e:  # noqa: BLE001
                    log.info("maintenance task for %s: %s", name, e)
                p["error"] = None
            elif is_hidden(db, name, code):
                p.update(error=None, error_hidden=True)
            elif code in alarms:
                p.update(error=code, error_message=msg or alarms[code].get("message") or None)
            else:
                p["error"] = None                                    # Watchtower has cleared it
        st = str(p.get("state") or "").strip().lower()
        if not p.get("error") and not p.get("busy") and st in LAST_JOB_STATES:
            p.update(last_result=st, state="idle")
        if p.get("error") and not p.get("error_message"):
            p["error_message"] = f"Printer code {code}: no description from Bambu for it yet; check the printer's screen."
        out.append(p)
    if opened and db is not None:
        try:
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
    return out
