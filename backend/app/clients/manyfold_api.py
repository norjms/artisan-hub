"""Manyfold integration.

Manyfold's library is a folder shared by the hub (HUB_LIBRARY_ROOT, /srv/library by default) and the
Manyfold container (HUB_MANYFOLD_LIBRARY_PATH, the same folder as Manyfold sees it). The hub writes model
folders there and touches `.rescan`; a watcher next to Manyfold runs the scan and writes `.manyfold-index.json`
(path -> public_id). No API credentials are needed for the main flow.
"""
import json
import os
from pathlib import Path

from ..config import settings


class ManyfoldClient:
    def __init__(self, url, client_id, client_secret, public_url=None):
        self.url = url
        self.public_url = (public_url or url or "").rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret

    @property
    def root(self) -> Path:
        return Path(settings().library_root)

    def request_rescan(self):
        p = self.root / ".rescan"
        p.write_text("1")

    def index(self) -> dict[str, dict]:
        p = self.root / ".manyfold-index.json"
        if not p.exists():
            return {}
        try:
            rows = json.loads(p.read_text() or "[]")
        except ValueError:
            return {}
        return {r["path"].strip("/"): r for r in rows}

    def public_id_for(self, library_rel: str) -> str | None:
        row = self.index().get(library_rel.strip("/"))
        return row["public_id"] if row else None

    def model_url(self, public_id: str) -> str:
        return f"{self.public_url}/models/{public_id}"

    def healthy(self) -> bool:
        return self.root.exists() and os.access(self.root, os.W_OK)
