import logging
import time
import httpx

log = logging.getLogger("hub.clients")


class ApiError(Exception):
    def __init__(self, service: str, status: int | None, message: str):
        super().__init__(f"{service}: {status} {message}")
        self.service = service
        self.status = status
        self.message = message


class BaseClient:
    service = "api"

    def __init__(self, base_url: str, headers: dict | None = None, auth=None, verify=True, timeout=60.0):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(base_url=self.base_url, headers=headers or {}, auth=auth, verify=verify,
                                   timeout=timeout, follow_redirects=False)

    def request(self, method: str, path: str, retries: int = 3, expect_json: bool = True, **kw):
        last = None
        for attempt in range(retries):
            try:
                r = self.client.request(method, path, **kw)
            except (httpx.TransportError,) as e:
                last = ApiError(self.service, None, f"{type(e).__name__}: {e}")
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code in (429, 502, 503, 504) and attempt < retries - 1:
                try:
                    wait = float(r.headers.get("retry-after") or 0)
                except ValueError:
                    wait = 0
                time.sleep(min(60.0, max(wait, 2 * (attempt + 1))))
                last = ApiError(self.service, r.status_code, r.text[:300])
                continue
            if r.status_code >= 400:
                raise ApiError(self.service, r.status_code, r.text[:800])
            if not expect_json:
                return r
            if not r.content:
                return None
            try:
                return r.json()
            except ValueError:
                raise ApiError(self.service, r.status_code, f"non-JSON response: {r.text[:200]}")
        raise last or ApiError(self.service, None, "request failed")

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, **kw):
        return self.request("POST", path, **kw)

    def put(self, path, **kw):
        return self.request("PUT", path, **kw)

    def patch(self, path, **kw):
        return self.request("PATCH", path, **kw)

    def delete(self, path, **kw):
        return self.request("DELETE", path, **kw)
