"""Supply math: what can be built right now, what open orders need, and what to buy.

Pools:
  parts     InvenTree in_stock minus InvenTree allocations minus open-order demand
  filament  Spoolman remaining grams per matching filament minus open-order demand
A product's buildable units = min over its BOM lines of floor(pool / per-unit need).
"""
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import Product, Variant, BomFilament, BomPart, PartCache, SpoolCache, FilamentCatalog, Order, OrderLine

OPEN_STATUSES = ("pending", "processing", "on-hold")
HEX_TOLERANCE = 48.0


def norm_material(m: str | None) -> str:
    m = (m or "PLA").upper().replace(" ", "")
    m = m.replace("SILKPLA", "PLA-SILK")
    return m


def norm_hex(h: str | None) -> str | None:
    if not h:
        return None
    h = h.strip().lstrip("#")
    if len(h) >= 6 and re.fullmatch(r"[0-9a-fA-F]{6,8}", h):
        return "#" + h[:6].upper()
    return None


def hex_dist(a: str, b: str) -> float:
    a, b = a.lstrip("#"), b.lstrip("#")
    ra, ga, ba = int(a[0:2], 16), int(a[2:4], 16), int(a[4:6], 16)
    rb, gb, bb = int(b[0:2], 16), int(b[2:4], 16), int(b[4:6], 16)
    rm = (ra + rb) / 2
    return math.sqrt((2 + rm / 256) * (ra - rb) ** 2 + 4 * (ga - gb) ** 2 + (2 + (255 - rm) / 256) * (ba - bb) ** 2)


@dataclass
class FilamentNeed:
    material: str
    color_name: str | None
    color_hex: str | None
    filament_id: int | None

    @property
    def key(self) -> str:
        if self.filament_id:
            return f"fid:{self.filament_id}"
        return f"{norm_material(self.material)}|{(norm_hex(self.color_hex) or '')}|{(self.color_name or '').strip().lower()}"

    @property
    def label(self) -> str:
        return " ".join(x for x in [self.color_name, self.material] if x) or self.key


def effective_need(line: BomFilament, variant: Variant | None) -> FilamentNeed:
    if line.follows_variant and variant:
        return FilamentNeed(variant.material or line.material, variant.color_name, variant.color_hex,
                            variant.spoolman_filament_id)
    return FilamentNeed(line.material, line.color_name, line.color_hex, line.spoolman_filament_id)


def same_material(need: str | None, spool: str | None) -> bool:
    """A Bambu Lab line in the materials list ("PLA Basic", "PLA Metal", "PETG HF") is its filament type ("PLA",
    "PETG") for matching spools, which Spoolman files under the type; the colour then decides."""
    if norm_material(need) == norm_material(spool):
        return True
    if "SUPPORT" in (need or "").upper() or "SUPPORT" in (spool or "").upper():
        return False
    from .slicing import material_type
    return material_type(need) == material_type(spool)


def spool_matches(need: FilamentNeed, s: SpoolCache) -> bool:
    if s.archived:
        return False
    if need.filament_id:
        return s.filament_id == need.filament_id
    if not same_material(need.material, s.material):
        return False
    nh, sh = norm_hex(need.color_hex), norm_hex(s.color_hex)
    if nh and sh:
        return hex_dist(nh, sh) <= HEX_TOLERANCE
    if need.color_name:
        return need.color_name.strip().lower() in (s.name or "").lower()
    return True   # material only: any color of that material


@dataclass
class Snapshot:
    parts: dict[int, PartCache]
    spools: list[SpoolCache]
    part_demand: dict[int, float] = field(default_factory=lambda: defaultdict(float))
    fil_demand: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    fil_needs: dict[str, FilamentNeed] = field(default_factory=dict)

    def filament_on_hand(self, need: FilamentNeed) -> float:
        return sum(s.remaining_g for s in self.spools if spool_matches(need, s))

    def part_available(self, pid: int) -> float:
        p = self.parts.get(pid)
        if not p:
            return 0.0
        return p.in_stock - p.allocated - self.part_demand.get(pid, 0)

    def filament_available(self, need: FilamentNeed) -> float:
        """Filament on hand for this need less what open orders want from the same spools. Two needs can draw on
        the same spools (say "any black PLA" and "Bambu black PLA"); another need's demand counts here in
        proportion to how much of its matching filament is also this need's."""
        mine = {s.spool_id: s.remaining_g for s in self.spools if spool_matches(need, s)}
        on_hand = sum(mine.values())
        taken = 0.0
        for key, g in self.fil_demand.items():
            if not g:
                continue
            if key == need.key:
                taken += g
                continue
            other = self.fil_needs.get(key)
            if other is None:
                continue
            theirs = {s.spool_id: s.remaining_g for s in self.spools if spool_matches(other, s)}
            tot = sum(theirs.values())
            shared = sum(v for k, v in theirs.items() if k in mine)
            if tot > 0 and shared > 0:
                taken += g * shared / tot
        return on_hand - taken

    def reserve(self, product: Product, variant: Variant | None, units: float):
        """Count `units` of a product as wanted, so later checks see what is left (a cart checked as a whole)."""
        for kind, ref, qty, _ in unit_requirements(product, variant):
            if kind == "part":
                self.part_demand[ref] += qty * units
            else:
                self.fil_demand[ref.key] += qty * units
                self.fil_needs[ref.key] = ref


def unit_requirements(product: Product, variant: Variant | None):
    """Yield ('part', part_id, qty, name) and ('filament', FilamentNeed, grams, label) for one unit."""
    for bp in product.parts:
        if bp.variant_id and (not variant or bp.variant_id != variant.id):
            continue
        yield "part", bp.inventree_part_id, bp.qty, bp.name
    for bf in product.filaments:
        g = (bf.grams or 0) * (1 + (bf.waste_pct or 0) / 100)
        if g <= 0:
            continue
        need = effective_need(bf, variant)
        yield "filament", need, g, need.label


def load_snapshot(db: Session) -> Snapshot:
    parts = {p.inventree_part_id: p for p in db.execute(select(PartCache)).scalars()}
    spools = list(db.execute(select(SpoolCache).where(SpoolCache.archived.is_(False))).scalars())
    snap = Snapshot(parts=parts, spools=spools)
    lines = db.execute(
        select(OrderLine).join(Order).where(Order.status.in_(OPEN_STATUSES), Order.consumed.is_(False))
        .options(selectinload(OrderLine.order))
    ).scalars().all()
    for ln in lines:
        if not ln.product_id:
            continue
        product = db.get(Product, ln.product_id)
        variant = db.get(Variant, ln.variant_id) if ln.variant_id else None
        for kind, ref, qty, _ in unit_requirements(product, variant):
            if kind == "part":
                snap.part_demand[ref] += qty * ln.qty
            else:
                snap.fil_demand[ref.key] += qty * ln.qty
                snap.fil_needs[ref.key] = ref
    return snap


def buildable(snap: Snapshot, product: Product, variant: Variant | None) -> dict:
    lines = []
    units = None
    for kind, ref, qty, label in unit_requirements(product, variant):
        if kind == "part":
            avail = snap.part_available(ref)
            on_hand = snap.parts[ref].in_stock if ref in snap.parts else 0
        else:
            avail = snap.filament_available(ref)
            on_hand = snap.filament_on_hand(ref)
        can = max(0, math.floor(avail / qty + 1e-9)) if qty > 0 else None
        lines.append({"kind": kind, "label": label, "per_unit": round(qty, 2), "on_hand": round(on_hand, 2),
                      "available": round(avail, 2), "units": can,
                      "ref": ref if kind == "part" else ref.key})
        if can is not None:
            units = can if units is None else min(units, can)
    limiting = None
    if lines and units is not None:
        limiting = min((ln for ln in lines if ln["units"] is not None), key=lambda ln: ln["units"])["label"]
    return {"units": units, "has_bom": bool(lines), "limiting": limiting, "lines": lines}


def product_availability(db: Session, product: Product, snap: Snapshot | None = None) -> dict:
    snap = snap or load_snapshot(db)
    active = [v for v in product.variants if v.active]
    if product.color_mode == "options" and active:
        per = {v.id: buildable(snap, product, v) for v in active}
        total = sum((r["units"] or 0) for r in per.values())
        return {"mode": "options", "variants": per, "units": total}
    r = buildable(snap, product, None)
    return {"mode": "single", **r}


def stock_for_woo(product: Product, result: dict) -> dict:
    """Map availability to Woo stock quantities, honouring the per-product cap."""
    cap = product.stock_cap or 10

    def clamp(r):
        if not r["has_bom"]:
            return cap      # no BOM yet: don't block sales, but the UI flags it
        return max(0, min(cap, r["units"] or 0))

    if result["mode"] == "options":
        return {str(vid): clamp(r) for vid, r in result["variants"].items()}
    return {"p": clamp(result)}


def all_products(db: Session, live_only=False):
    q = select(Product).options(selectinload(Product.variants), selectinload(Product.filaments),
                                selectinload(Product.parts), selectinload(Product.model))
    if live_only:
        q = q.where(Product.live.is_(True))
    return db.execute(q).scalars().all()


def planner(db: Session, include: str = "live") -> dict:
    """Supplies needed to cover open orders plus each live product's target buffer."""
    snap = load_snapshot(db)
    products = all_products(db, live_only=(include == "live"))
    part_req: dict[int, float] = defaultdict(float)
    fil_req: dict[str, float] = defaultdict(float)
    fil_info: dict[str, FilamentNeed] = dict(snap.fil_needs)
    users: dict[str, set] = defaultdict(set)
    for p in products:
        variants = [v for v in p.variants if v.active] if p.color_mode == "options" else [None]
        for v in variants or [None]:
            for kind, ref, qty, label in unit_requirements(p, v):
                need = qty * (p.target_buffer or 0)
                if kind == "part":
                    part_req[ref] += need
                    users[f"part:{ref}"].add(p.sku)
                else:
                    fil_req[ref.key] += need
                    fil_info[ref.key] = ref
                    users[f"fil:{ref.key}"].add(v.sku if v else p.sku)
    for pid, q in snap.part_demand.items():
        part_req[pid] += q
    for k, q in snap.fil_demand.items():
        fil_req[k] += q

    parts_out = []
    for pid, req in sorted(part_req.items()):
        pc = snap.parts.get(pid)
        on_hand = (pc.in_stock - pc.allocated) if pc else 0
        on_order = pc.on_order if pc else 0
        short = max(0.0, req - on_hand)
        sup = (pc.suppliers or [None])[0] if pc and pc.suppliers else None
        pack = (sup or {}).get("pack_qty") or 1
        parts_out.append({"part_id": pid, "name": pc.name if pc else f"Part {pid}", "ipn": pc.ipn if pc else None,
                          "required": round(req, 2), "on_hand": round(on_hand, 2), "on_order": round(on_order, 2),
                          "short": round(short, 2), "buy_packs": math.ceil(max(0, short - on_order) / pack) if short else 0,
                          "supplier": sup, "used_by": sorted(users.get(f"part:{pid}", []))})

    cat = list(db.execute(select(FilamentCatalog)).scalars())
    fil_out = []
    for key, req in sorted(fil_req.items(), key=lambda kv: -kv[1]):
        need = fil_info.get(key)
        if not need:
            continue
        on_hand = snap.filament_on_hand(need)
        short = max(0.0, req - on_hand)
        match = None
        for f in cat:
            fake = SpoolCache(spool_id=0, filament_id=f.filament_id, material=f.material, color_hex=f.color_hex,
                              name=f.name, remaining_g=0, archived=False)
            if spool_matches(need, fake):
                match = f
                break
        spool_g = (match.weight_g if match and match.weight_g else 1000)
        fil_out.append({"key": key, "label": need.label, "material": need.material, "color_name": need.color_name,
                        "color_hex": norm_hex(need.color_hex), "required_g": round(req), "on_hand_g": round(on_hand),
                        "short_g": round(short), "buy_spools": math.ceil(short / spool_g) if short else 0,
                        "catalog": {"vendor": match.vendor, "name": match.name, "url": match.url, "price": match.price}
                        if match else None, "used_by": sorted(users.get(f"fil:{key}", []))})
    return {"parts": parts_out, "filament": fil_out}
