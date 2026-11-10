"""Two-way spool sync between Spoolman and Legacy farm."""


class FakeSM:
    def __init__(self):
        self.spools_ = {}
        self.fil = {1: {"id": 1, "name": "Black Basic", "material": "ABS", "color_hex": "000000", "density": 1.04,
                        "diameter": 1.75, "weight": 1000, "vendor": {"id": 1, "name": "Polymaker"}}}
        self.vend = [{"id": 1, "name": "Polymaker"}]
        self.used = []

    def add(self, sid, g, comment="", archived=False, fid=1):
        self.spools_[sid] = {"id": sid, "remaining_weight": g, "initial_weight": 1000, "comment": comment,
                             "archived": archived, "filament": self.fil[fid], "location": "Shelf"}

    def spools(self, allow_archived=False):
        return [dict(s) for s in self.spools_.values() if allow_archived or not s["archived"]]

    def use(self, sid, g):
        self.used.append((sid, g))
        self.spools_[sid]["remaining_weight"] -= g

    def patch_spool(self, sid, data):
        self.spools_[sid].update(data)

    def vendors(self):
        return self.vend

    def filaments(self):
        return list(self.fil.values())

    def create_vendor(self, name):
        v = {"id": len(self.vend) + 1, "name": name}
        self.vend.append(v)
        return v

    def create_filament(self, body):
        fid = max(self.fil) + 1
        self.fil[fid] = {"id": fid, **body, "vendor": next(v for v in self.vend if v["id"] == body["vendor_id"])}
        return self.fil[fid]

    def create_spool(self, body):
        sid = max(self.spools_ or {0: 0}) + 1
        self.add(sid, body["remaining_weight"], body.get("comment", ""), fid=body["filament_id"])
        return {"id": sid}


class FakeWT:
    def __init__(self):
        self.s = {}
        self.patches = []

    def add(self, wid, g, qty=1, **kw):
        self.s[wid] = {"id": wid, "remaining_weight": g, "initial_weight": 1000, "quantity": qty, "material_type": "PLA",
                       "color": "Pink", "variant": "Matte", "manufacturer": "Sunlu", "color_hex": "#FFAAAA", **kw}

    def spools(self):
        return {"spools": [dict(x) for x in self.s.values()]}

    def patch_spool(self, wid, data):
        self.patches.append((wid, data))
        self.s[wid].update(data)

    def create_spool(self, body):
        wid = max(self.s) + 1
        self.add(wid, body["remaining_weight"])
        self.created = body
        return {"spool_ids": [wid]}


def test_two_way(env, monkeypatch):
    from app.db import session_scope
    from app.services import spoolsync
    from app.models import SpoolLink
    sm, wt = FakeSM(), FakeWT()
    monkeypatch.setattr(spoolsync, "_clients", lambda: (sm, wt))
    sm.add(1, 800.0, "Imported from Legacy farm spool #4 by Artisan Hub")
    wt.add(4, 800.0, material_type="ABS")
    wt.add(5, 500.0)                      # only in Legacy farm
    wt.add(6, 900.0, qty=3)               # a batch row: not synced
    sm.add(2, 300.0)                      # only in Spoolman
    with session_scope() as db:
        dry = spoolsync.sync(db, dry_run=True)
        assert dry["linked"] == 1 and [x["legacy_farm"] for x in dry["created_in_spoolman"]] == [5]
        assert dry["created_in_legacy_farm"] and not db.query(SpoolLink).count()
        out = spoolsync.sync(db)
        assert out["linked"] == 1
        assert [x["legacy_farm"] for x in out["created_in_spoolman"]] == [5]
        assert [x["spoolman"] for x in out["created_in_legacy_farm"]] == [2]
        assert out["skipped"][0]["legacy_farm"] == 6
        assert wt.created["remaining_weight"] == 300.0 and wt.created["color"] == "Black Basic"
        new_sm = next(l.spoolman_id for l in db.query(SpoolLink).filter_by(wt_spool_id=5))
        assert sm.spools_[new_sm]["remaining_weight"] == 500.0
        assert sm.vend[-1]["name"] == "Sunlu"
        # a print in Legacy farm uses 42.5 g: recorded as use in Spoolman, Legacy farm untouched
        wt.s[4]["remaining_weight"] = 757.5
        wt.patches.clear()
        out = spoolsync.sync(db)
        assert sm.used == [(1, 42.5)] and abs(sm.spools_[1]["remaining_weight"] - 757.5) < 0.01 and not wt.patches
        # weighed in Spoolman: Legacy farm follows
        sm.spools_[1]["remaining_weight"] = 700.0
        out = spoolsync.sync(db)
        assert wt.s[4]["remaining_weight"] == 700.0 and out["to_legacy_farm"][0]["to"] == 700.0
        # both changed: Legacy farm printed 20 g while Spoolman was corrected to 650: result 630 on both
        sm.spools_[1]["remaining_weight"] = 650.0
        wt.s[4]["remaining_weight"] = 680.0
        spoolsync.sync(db)
        assert abs(sm.spools_[1]["remaining_weight"] - 630.0) < 0.01 and wt.s[4]["remaining_weight"] == 630.0
        # weighed higher in Legacy farm only: Spoolman takes the reading
        wt.s[4]["remaining_weight"] = 640.0
        spoolsync.sync(db)
        assert sm.spools_[1]["remaining_weight"] == 640.0
        # emptied in Legacy farm: archived in Spoolman, then left alone
        wt.s[4]["remaining_weight"] = 0.0
        out = spoolsync.sync(db)
        assert out["archived"] == [1] and sm.spools_[1]["archived"] is True
        assert db.query(SpoolLink).filter_by(spoolman_id=1).one().state == "archived"
        # deleted in Legacy farm: link marked broken, nothing recreated
        del wt.s[5]
        out = spoolsync.sync(db)
        assert out["broken"] == [new_sm] and not out["created_in_legacy_farm"]
