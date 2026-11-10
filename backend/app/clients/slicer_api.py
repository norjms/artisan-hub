"""Client for a slicer service (the artisan-slicer container: OrcaSlicer and/or PrusaSlicer behind HTTP).

The hub never links or bundles a slicer: it sends model files and preset names over HTTP and gets
sliced files back, so the slicer can be swapped or turned off (no URL configured) without code changes.
"""
import json
import time
from pathlib import Path

from .base import BaseClient, ApiError


class SlicerClient(BaseClient):
    service = "slicer"

    def __init__(self, url: str, token: str):
        if not url:
            raise ApiError("slicer", None, "no slicer configured (HUB_SLICER_URL or the team's Slicer connection)")
        super().__init__(url, headers={"Authorization": f"Bearer {token}"}, timeout=60.0)
        self._health = (0.0, None)

    def health(self) -> dict:
        h = self.get("/health", retries=1)
        self._health = (time.time(), h)
        return h

    def engines(self, max_age: float = 300.0) -> dict:
        """{engine: version} for the engines this service runs (cached a few minutes)."""
        at, h = self._health
        if not h or time.time() - at > max_age:
            h = self.health()
        eng = h.get("engines")
        if eng is None and h.get("orca"):        # 1.x service: OrcaSlicer only
            eng = {"orca": h["orca"]}
        return eng or {}

    def machines(self, q: str = "", engine: str = "orca") -> list[dict]:
        return self.get("/presets/machines", params={"q": q, "engine": engine})

    def presets(self, kind: str, machine: str, filament_type: str | None = None, engine: str = "orca",
                process: str | None = None) -> list[dict]:
        p = {"machine": machine, "engine": engine}
        if filament_type:
            p["filament_type"] = filament_type
        if process:
            p["process"] = process
        return self.get(f"/presets/{kind}", params=p)

    def resolve(self, kind: str, name: str, engine: str = "orca") -> dict:
        return self.get(f"/presets/resolve/{kind}", params={"name": name, "engine": engine})

    def slice(self, paths: list[Path], spec: dict, timeout: float = 2400.0) -> dict:
        handles = [open(p, "rb") for p in paths]
        try:
            files = [("file", (Path(p).name, h, "application/octet-stream")) for p, h in zip(paths, handles)]
            return self.request("POST", "/slice", files=files, data={"spec": json.dumps(spec)}, retries=1,
                                timeout=timeout)
        finally:
            for h in handles:
                h.close()

    def inspect(self, paths: list[Path], what: str, timeout: float = 400.0) -> dict:
        handles = [open(p, "rb") for p in paths]
        try:
            files = [("file", (Path(p).name, h, "application/octet-stream")) for p, h in zip(paths, handles)]
            return self.request("POST", "/inspect", files=files, data={"what": what}, retries=1, timeout=timeout)
        finally:
            for h in handles:
                h.close()

    def download(self, jid: str, name: str, dest: Path) -> Path:
        with self.client.stream("GET", f"/results/{jid}/{name}", timeout=600) as r:
            if r.status_code >= 400:
                raise ApiError(self.service, r.status_code, f"download {name} failed")
            with open(dest, "wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    fh.write(chunk)
        return dest

    def forget(self, jid: str):
        try:
            self.delete(f"/results/{jid}", retries=1)
        except ApiError:
            pass
