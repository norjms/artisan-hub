"""Products that share a scarce part or the same spools: one cart can't promise the same supply twice, and two
filament needs drawing on the same spools see each other's open-order demand."""
from types import SimpleNamespace as NS


def _prod(parts=(), fils=()):
    return NS(parts=[NS(variant_id=None, inventree_part_id=p, qty=q, name=f"part {p}") for p, q in parts],
              filaments=[NS(grams=g, waste_pct=0, follows_variant=False, material=m, color_name=None, color_hex=h,
                            spoolman_filament_id=fid) for m, h, g, fid in fils],
              variants=[], color_mode="single")


def _spool(i, mat, hexv, g, fid=None):
    return NS(spool_id=i, material=mat, color_hex=hexv, name="", filament_id=fid, remaining_g=g, archived=False)


def test_cart_checked_as_a_whole():
    from app.services import availability as av
    magnet = NS(in_stock=4, allocated=0)
    snap = av.Snapshot(parts={7: magnet}, spools=[])
    a, b = _prod(parts=[(7, 2)]), _prod(parts=[(7, 2)])
    assert av.buildable(snap, a, None)["units"] == 2
    assert av.buildable(snap, b, None)["units"] == 2      # each alone could take both magnets' worth
    snap.reserve(a, None, 2)                               # the cart's first line takes them
    assert av.buildable(snap, b, None)["units"] == 0


def test_overlapping_filament_needs_share_demand():
    from app.services import availability as av
    spools = [_spool(1, "PLA", "#101010", 300, fid=55), _spool(2, "PLA", "#121212", 300, fid=56)]
    snap = av.Snapshot(parts={}, spools=spools)
    any_black = _prod(fils=[("PLA", "#111111", 100, None)])     # matches both spools
    bambu_black = _prod(fils=[("PLA", None, 100, 55)])           # only spool 1
    assert av.buildable(snap, bambu_black, None)["units"] == 3
    snap.reserve(any_black, None, 4)                              # 400 g of "any black", spread over both spools
    # half of that demand falls on spool 1 (300 of its 600 g of matching filament): 300 - 200 = 100 g left
    assert av.buildable(snap, bambu_black, None)["units"] == 1
    assert av.buildable(snap, any_black, None)["units"] == 2      # 600 - 400


def test_availability_endpoint_qty(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import availability as av
    c = TestClient(app)
    # without qty: per SKU as before (unknown SKUs answer null)
    r = c.get("/api/v1/availability", params={"sku": "NOPE-1"})
    assert r.status_code == 200 and r.json()["items"] == {"NOPE-1": None}
    calls = []
    real = av.Snapshot.reserve
    monkeypatch.setattr(av.Snapshot, "reserve", lambda self, p, v, n: (calls.append(n), real(self, p, v, n)))
    c.get("/api/v1/availability", params={"sku": "NOPE-1,NOPE-2", "qty": "2,x"})
    assert calls == []     # nothing to reserve for unknown products
