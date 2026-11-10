"""Watchtower client.

Public API v1 (/api/public/v1, bearer key) for files, jobs, orders, spools, webhooks.
Private web API (/api/..., session cookie + CSRF) for shop SKU mappings and storefronts,
which the public API does not expose yet.
"""
import os
import threading
import time

from .base import BaseClient, ApiError
from .farm_base import FarmBackend


_WRITE_LOCK = threading.Lock()
# The client is shared by all worker threads and the private API ties the CSRF token to the
# session cookie, so login, token fetch and each private call run one at a time.
_PRIV_LOCK = threading.RLock()
_LAST_WRITE = [0.0]
WRITE_GAP = 2.2   # public API write budget is 30/minute per key


def _pace():
    with _WRITE_LOCK:
        wait = _LAST_WRITE[0] + WRITE_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        _LAST_WRITE[0] = time.time()


class WatchtowerClient(BaseClient, FarmBackend):
    """The Watchtower driver of the farm interface (clients/farm_base.py)."""
    service = "watchtower"
    kind = "watchtower"
    label = "Watchtower"
    CAPABILITIES = frozenset({"files", "jobs", "queue", "priority", "cancel", "printers", "commands", "snapshot",
                              "filament", "spools", "webhooks", "sku_mapping", "storefronts", "maintenance",
                              "out_of_service", "maintenance_log", "library_folders", "pin_printer"})

    def request(self, method, path, retries: int = 5, **kw):
        if method != "GET":
            _pace()
        return super().request(method, path, retries=retries, **kw)

    def __init__(self, url, api_key, username, password):
        super().__init__(url, timeout=600.0)
        self.api_key = api_key
        self.username = username
        self.password = password
        self._csrf = None
        self._csrf_at = 0.0
        self._priv_lock = threading.RLock()   # this team's session cookie + CSRF token

    # ---- public v1 ----
    def _pub(self, method, path, **kw):
        h = kw.pop("headers", {})
        h["Authorization"] = f"Bearer {self.api_key}"
        r = self.request(method, "/api/public/v1" + path, headers=h, **kw)
        return r.get("data", r) if isinstance(r, dict) else r

    def ping(self):
        return self._pub("GET", "/ping")

    def list_files(self):
        return self._pub("GET", "/files").get("files", [])

    def upload_file(self, path, filename=None):
        filename = filename or os.path.basename(path)
        with open(path, "rb") as fh:
            return self._pub("POST", "/files", files={"file": (filename, fh, "application/octet-stream")}, retries=2)

    def submit_job(self, file_ref=None, sku=None, quantity=1, job_name=None, priority=None, max_retries=None,
                   printer=None, start=False):
        """With printer: the jobs go in at the top with that printer preferred (public API), then the first is moved
        onto it with the web API's Print now (what Watchtower's own queue page does), so it prints there next.
        start also marks that printer's bed clear, so it starts as soon as the printer is idle."""
        body = {"quantity": quantity}
        if printer:
            body["preferred_printer"] = printer
            if priority is None:
                priority = 100
        if file_ref:
            body["file_ref"] = file_ref
        if sku:
            body["sku"] = sku
        if job_name:
            body["job_name"] = job_name
        if priority:
            body["priority"] = priority
        res = self._pub("POST", "/jobs", json=body)
        if not printer or not isinstance(res, dict):
            return res
        ids = [i.get("id") if isinstance(i, dict) else i for i in (res.get("job_ids") or res.get("jobs") or [])]
        started, notes = [], []
        if ids:
            try:
                self.print_now(ids[0], printer, set_ready=start)
                started.append(ids[0])
            except Exception as e:  # noqa: BLE001  (busy, or no web sign-in: the job still waits, preferring it)
                notes.append(f"queued at the top for {printer}; Watchtower didn't move it there yet "
                             f"({str(getattr(e, 'message', None) or e)[:160]})")
        return {**res, "job_ids": ids, "started": started, "notes": notes}

    def print_now(self, job_id, printer: str, set_ready: bool = False):
        """Watchtower's Print now on a queued job: send it to this printer; set_ready marks the bed clear first."""
        return self._priv("POST", f"/api/queue/job/{job_id}/print-now",
                          json={"printer_name": printer, "set_ready": bool(set_ready)})

    def job(self, job_id):
        return self._pub("GET", f"/jobs/{job_id}")

    def order(self, order_id):
        return self._pub("GET", f"/orders/{order_id}")

    def printers(self):
        return self._pub("GET", "/printers")

    def printer(self, name: str):
        from urllib.parse import quote
        return self._pub("GET", "/printers/" + quote(name, safe=""))

    def cancel_job(self, job_id):
        """Stop a job that is printing (public API)."""
        return self._pub("POST", f"/jobs/{job_id}/cancel")

    def spools(self):
        return self._pub("GET", "/spools")

    def create_spool(self, data):
        return self._pub("POST", "/spools", json=data)

    def patch_spool(self, spool_id, data):
        return self._pub("PATCH", f"/spools/{spool_id}", json=data)

    def webhooks(self):
        return self._pub("GET", "/webhooks").get("webhooks", [])

    def add_webhook(self, url, events, secret):
        return self._pub("POST", "/webhooks", json={"url": url, "events": events, "secret": secret})

    def delete_webhook(self, hook_id):
        return self._pub("DELETE", f"/webhooks/{hook_id}")

    def command(self, printer: str, command: str, args: dict | None = None, confirm: bool = False):
        """A unified control verb on one printer (set_ready, clear_ready, pause, ...). Needs a 'control' key."""
        from urllib.parse import quote
        body = {"command": command}
        if args:
            body["args"] = args
        if confirm:
            body["confirm"] = True
        return self._pub("POST", f"/printers/{quote(printer, safe='')}/commands", json=body, retries=2)

    # ---- private web API ----
    def _login(self):
        self.client.cookies.clear()
        r = self.request("POST", "/api/auth/login",
                         json={"username": self.username, "password": self.password, "remember_me": True})
        if not (isinstance(r, dict) and r.get("success")):
            raise ApiError(self.service, None, "login failed")
        self._csrf = None

    def _token(self):
        if not self._csrf or time.time() - self._csrf_at > 1800:
            r = self.request("GET", "/api/auth/csrf-token")
            self._csrf = r["data"]["token"]
            self._csrf_at = time.time()
        return self._csrf

    def _priv(self, method, path, **kw):
        with self._priv_lock:
            return self._priv_locked(method, path, **kw)

    def _priv_locked(self, method, path, **kw):
        if not self.client.cookies.get("wt_session"):
            self._login()
        base_headers = dict(kw.pop("headers", {}))
        for attempt in range(2):
            h = dict(base_headers)
            if method != "GET":
                h["X-CSRF-Token"] = self._token()
            try:
                r = self.request(method, path, headers=h, **kw)
            except ApiError as e:
                if e.status in (401, 403) and attempt == 0:
                    self._login()
                    continue
                raise
            if isinstance(r, dict) and r.get("success") is False:
                raise ApiError(self.service, None, str(r.get("error"))[:300])
            return r.get("data", r) if isinstance(r, dict) else r

    def storefronts(self):
        return self._priv("GET", "/api/shops/storefronts").get("storefronts", [])

    def sku_mappings(self):
        return self._priv("GET", "/api/shops/sku-mappings").get("mappings", [])

    def create_sku_mapping(self, data):
        return self._priv("POST", "/api/shops/sku-mappings", json=data)

    def update_sku_mapping(self, mid, data):
        return self._priv("PUT", f"/api/shops/sku-mappings/{mid}", json=data)

    def delete_sku_mapping(self, mid):
        return self._priv("DELETE", f"/api/shops/sku-mappings/{mid}")

    def set_mapping_parts(self, mid, parts):
        return self._priv("POST", f"/api/shops/sku-mappings/{mid}/parts", json={"parts": parts})

    def sync_products(self):
        return self._priv("POST", "/api/shops/products/sync", json={})

    def shop_orders(self):
        return self._priv("GET", "/api/shops/orders")

    def printers_full(self):
        """Printers with their capabilities (build volume, tools, AMS units)."""
        return self._priv("GET", "/api/printers")

    def filament_assignments(self):
        """What each printer has loaded, slot by slot (AMS, AMS HT, external spools, tools)."""
        return self._priv("GET", "/api/filament/assignments")

    def set_priority(self, job_id, priority: int):
        """Watchtower priorities: 25 low, 50 normal, 75 high, 100 urgent."""
        return self._priv("POST", f"/api/queue/job/{job_id}/priority", json={"priority": int(priority)})

    # ---- maintenance (private) ----
    def maint_schedules(self):
        return self._priv("GET", "/api/maintenance/schedule") or []

    def maint_complete(self, schedule_id: int, performed_by: str, notes: str = "", minutes: int | None = None):
        return self._priv("POST", "/api/maintenance/complete", json={"schedule_id": schedule_id, "performed_by": performed_by,
                                                                     "duration_minutes": minutes, "notes": notes})

    def maint_log(self, printer: str, task: str, category: str, performed_by: str, notes: str = "", minutes: int | None = None):
        return self._priv("POST", "/api/maintenance/log", json={"printer_name": printer, "task_name": task, "category": category,
                                                                "performed_by": performed_by, "notes": notes,
                                                                "duration_minutes": minutes})

    def maint_down(self) -> list:
        return (self._priv("GET", "/api/maintenance/down") or {}).get("down") or []

    def set_down(self, printer: str, down: bool, reason: str = ""):
        """Out of service: Watchtower sends no new jobs to it; a print in progress keeps running."""
        from urllib.parse import quote
        return self._priv("PUT", f"/api/maintenance/printer/{quote(printer, safe='')}/down",
                          json={"is_down": bool(down), "reason": reason})

    def cancel_queued(self, job_id, reason: str):
        """Cancel a job that hasn't started (the public cancel only stops a print in progress)."""
        return self._priv("POST", f"/api/queue/job/{job_id}/cancel", json={"reason": reason})

    def camera_sources(self, printer: str) -> list[str]:
        """The printer's live camera streams (go2rtc names), a chamber camera first."""
        streams = (self._priv("GET", "/api/system/streams") or {}).get("streams") or []
        mine = [s for s in streams if s.get("printer_name") == printer and s.get("enabled", True)
                and str(s.get("state", "")).upper() == "ONLINE"]
        mine.sort(key=lambda s: 0 if "chamber" in str(s.get("camera_name", "")).lower() else 1)
        return [s.get("stream_name") or s.get("name") for s in mine if s.get("stream_name") or s.get("name")]

    def frame(self, source: str, timeout: float = 15) -> bytes | None:
        """One JPEG from a go2rtc stream (Watchtower's proxy passes single frames, not its live streams)."""
        from urllib.parse import quote
        r = self.client.get(f"/api/go2rtc/frame.jpeg?src={quote(source, safe='')}", timeout=timeout)
        return r.content if r.status_code == 200 and r.content[:2] == b"\xff\xd8" else None

    def snapshot(self, printer: str) -> bytes | None:
        """One camera frame for a printer, preferring a chamber camera; None when it has no live camera."""
        from urllib.parse import quote
        streams = (self._priv("GET", "/api/system/streams") or {}).get("streams") or []
        mine = [s for s in streams if s.get("printer_name") == printer and s.get("enabled", True)
                and str(s.get("state", "")).upper() == "ONLINE"]
        if not mine:
            return None
        mine.sort(key=lambda s: 0 if "chamber" in str(s.get("camera_name", "")).lower() else 1)
        with self._priv_lock:
            for s in mine[:2]:
                name = s.get("stream_name") or s.get("name")
                try:
                    r = self.client.get(f"/api/go2rtc/frame.jpeg?src={quote(name, safe='')}", timeout=20)
                except Exception:  # noqa: BLE001
                    continue
                if r.status_code == 200 and r.content[:2] == b"\xff\xd8":
                    return r.content
        return None

    def queue(self, statuses=None, limit: int = 200, offset: int = 0, sort: str | None = None):
        """The farm queue (private web API). statuses filters (queued, waiting_filament, assigned, printing, paused,
        resuming, held, failed, completed, cancelled); answers {"jobs": [...], "count": n, "total": n}."""
        from urllib.parse import urlencode
        q = [("limit", str(limit)), ("offset", str(offset))]
        q += [("status", x) for x in (statuses or [])]
        if sort:
            q.append(("sort", sort))
        return self._priv("GET", "/api/queue?" + urlencode(q))

    # ---- library folders (private) ----
    def library_folder(self, name: str, parent=None) -> dict:
        return self._priv("POST", "/api/library/folders", json={"name": name[:120], "parent_folder_id": parent,
                                                                "reuse_existing": True})

    def move_library_file(self, file_id, folder_id):
        return self._priv("POST", f"/api/library/files/{file_id}/move", json={"folder_id": folder_id})

    def delete_library_file(self, file_id):
        return self._priv("DELETE", f"/api/library/files/gcode/{file_id}")

    def delete_library_folder(self, folder_id):
        return self._priv("DELETE", f"/api/library/folders/{folder_id}")
