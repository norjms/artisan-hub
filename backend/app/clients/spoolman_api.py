from .base import BaseClient


class SpoolmanClient(BaseClient):
    service = "spoolman"

    def __init__(self, url):
        super().__init__(url.rstrip("/") + "/api/v1", timeout=30.0)

    def info(self):
        return self.get("/info")

    def spools(self, allow_archived=False):
        return self.get("/spool", params={"allow_archived": str(allow_archived).lower()})

    def filaments(self):
        return self.get("/filament")

    def vendors(self):
        return self.get("/vendor")

    def use(self, spool_id, grams):
        return self.put(f"/spool/{spool_id}/use", json={"use_weight": grams})

    def create_filament(self, data):
        return self.post("/filament", json=data)

    def spool(self, spool_id):
        return self.get(f"/spool/{spool_id}")

    def patch_spool(self, spool_id, data):
        return self.patch(f"/spool/{spool_id}", json=data)

    def create_spool(self, data):
        return self.post("/spool", json=data)

    def create_vendor(self, name):
        return self.post("/vendor", json={"name": name})
