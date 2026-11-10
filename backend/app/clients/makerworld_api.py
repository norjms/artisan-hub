"""MakerWorld + Bambu Lab account client.

Public model data (details, search) needs no account. Downloading model files needs the
owner's Bambu Lab account token, obtained through the normal Bambu login flow (password,
then an emailed code or a 2FA code when the account asks for one). The password is never stored.

Requests are paced and cached so the hub behaves like one person browsing.
"""
import threading
import time
from urllib.parse import urlencode

from curl_cffi import requests as cffi

from .base import ApiError

MW = "https://makerworld.com"
BAMBU_API = "https://api.bambulab.com"
_LOCK = threading.Lock()
_LAST = [0.0]
GAP = 1.0
_CACHE: dict[str, tuple[float, object]] = {}
_SESSION: list = [None]
_BLOCKED_UNTIL = [0.0]


def _session():
    # one long-lived browser-like session so MakerWorld's cookies (including the bot-check pass) are kept
    with _LOCK:
        if _SESSION[0] is None:
            _SESSION[0] = cffi.Session(impersonate="chrome", timeout=60)
        return _SESSION[0]


def _pace():
    with _LOCK:
        wait = _LAST[0] + GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        _LAST[0] = time.time()


class MakerWorldClient:
    service = "makerworld"

    def __init__(self, token: str | None = None):
        self.token = token
        self.s = _session()

    def _headers(self, auth=False, json_body=False):
        h = {"Accept": "application/json, text/plain, */*", "Referer": f"{MW}/en", "Origin": MW}
        if json_body:
            h["Content-Type"] = "application/json"
        if auth and self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _get(self, url, auth=False, cache_s=0, raw=False):
        if cache_s and url in _CACHE and time.time() - _CACHE[url][0] < cache_s:
            return _CACHE[url][1]
        if time.time() < _BLOCKED_UNTIL[0]:
            raise ApiError(self.service, 429, f"MakerWorld asked us to slow down; try again in {int(_BLOCKED_UNTIL[0] - time.time()) + 1}s")
        cookies = {"token": self.token} if (auth and self.token) else None
        for attempt in range(2):
            _pace()
            r = self.s.get(url, headers=self._headers(auth), cookies=cookies)
            challenged = r.status_code in (403, 429, 503) and ("Just a moment" in r.text[:2000] or "cf-" in r.text[:2000])
            if not challenged:
                break
            if attempt == 0:
                time.sleep(4)
                try:   # visit the site like a browser would, then retry once
                    self.s.get(f"{MW}/en", headers={"Accept": "text/html"})
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(2)
        if r.status_code >= 400:
            msg = r.text[:300]
            if challenged:
                _BLOCKED_UNTIL[0] = time.time() + 90
                msg = "MakerWorld's bot check is active; the hub will pause MakerWorld requests for 90 seconds"
            raise ApiError(self.service, r.status_code, msg)
        out = r if raw else r.json()
        if cache_s:
            _CACHE[url] = (time.time(), out)
            if len(_CACHE) > 500:
                for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[:100]:
                    _CACHE.pop(k, None)
        return out

    # ---------- public data ----------
    def design(self, design_id: int) -> dict:
        return self._get(f"{MW}/api/v1/design-service/design/{int(design_id)}", cache_s=60)

    def page_html(self, design_id: int, slug: str = "") -> str:
        r = self._get(f"{MW}/en/models/{int(design_id)}" + (f"-{slug}" if slug else ""), raw=True)
        return r.text

    def search(self, keyword: str = "", licenses: list[str] | None = None, offset: int = 0, limit: int = 40,
               order_by: str | None = None) -> dict:
        p = {"keyword": keyword, "limit": limit, "offset": offset}
        if licenses:
            p["licenses"] = ",".join(licenses)
        if order_by:
            p["orderBy"] = order_by
        return self._get(f"{MW}/api/v1/search-service/select/design2?{urlencode(p)}", cache_s=600)

    def fetch_bytes(self, url: str) -> bytes:
        _pace()
        r = self.s.get(url, headers={"Referer": f"{MW}/en"})
        if r.status_code >= 400:
            raise ApiError(self.service, r.status_code, f"download failed: {url[:120]}")
        return r.content

    # ---------- account ----------
    def instance_download(self, instance_id: int) -> dict:
        """Returns {"name": ..., "url": signed download url} for a print profile's 3MF."""
        if not self.token:
            raise ApiError(self.service, 401, "connect your MakerWorld account in Settings to download files")
        return self._get(f"{MW}/api/v1/design-service/instance/{int(instance_id)}/f3mf?type=download", auth=True)

    def profile(self) -> dict:
        _pace()
        r = self.s.get(f"{BAMBU_API}/v1/user-service/my/profile", headers=self._headers(auth=True))
        if r.status_code >= 400:
            raise ApiError(self.service, r.status_code, r.text[:200])
        return r.json()

    def login(self, email: str, password: str | None = None, code: str | None = None) -> dict:
        body = {"account": email}
        if code:
            body["code"] = code
        else:
            body["password"] = password
        _pace()
        r = self.s.post(f"{BAMBU_API}/v1/user-service/user/login", json=body, headers=self._headers(json_body=True))
        try:
            data = r.json()
        except ValueError:
            raise ApiError(self.service, r.status_code, r.text[:200])
        if r.status_code >= 400:
            raise ApiError(self.service, r.status_code, str(data.get("message") or data)[:200])
        return data

    def send_email_code(self, email: str):
        _pace()
        r = self.s.post(f"{BAMBU_API}/v1/user-service/user/sendemail/code", json={"email": email, "type": "codeLogin"},
                        headers=self._headers(json_body=True))
        if r.status_code >= 400:
            raise ApiError(self.service, r.status_code, r.text[:200])

    def tfa(self, tfa_key: str, code: str) -> str | None:
        _pace()
        r = self.s.post("https://bambulab.com/api/sign-in/tfa", json={"tfaKey": tfa_key, "tfaCode": code},
                        headers=self._headers(json_body=True))
        if r.status_code >= 400:
            raise ApiError(self.service, r.status_code, r.text[:200])
        tok = r.cookies.get("token")
        if not tok:
            try:
                tok = r.json().get("accessToken")
            except ValueError:
                tok = None
        return tok


def parse_design_id(url_or_id: str) -> int:
    import re
    s = str(url_or_id).strip()
    if s.isdigit():
        return int(s)
    m = re.search(r"/models/(\d+)", s)
    if not m:
        raise ValueError("Paste a MakerWorld model link like https://makerworld.com/en/models/123456-name")
    return int(m.group(1))


def parse_profile_id(url: str) -> int | None:
    import re
    m = re.search(r"profileId-(\d+)", url or "")
    return int(m.group(1)) if m else None
