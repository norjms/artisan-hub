"""Farm reports: failures and waste (from the Watchtower job mirror and plate checks), printer use, and profit per
SKU and per printer-hour (from approved slices, BOM parts and prices).

Grams per job are Watchtower's estimate from the sliced file; a failed or cancelled job counts the share it got
through (its progress, else its run time against the estimate). Costs use the same prices as the slicing cost
(Spoolman or team material prices, printer hourly rates). Revenue is units sold at list price."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import FarmJob, Model, Order, PartCache, PlateCheck, PrinterProfile, Product
from . import slicing
from .farm import FAIL_REASONS, _aware

SOLD = ("processing", "completed", "on-hold")


def now():
    return datetime.now(timezone.utc)


def _rates(db: Session) -> tuple[dict, float]:
    by_name = {p.name: p.hourly_rate for p in db.execute(select(PrinterProfile)).scalars() if p.hourly_rate is not None}
    return by_name, float(slicing.cfg(db, "printer_hourly_rate") or 0)


def _job_numbers(fj: FarmJob) -> dict:
    st, en = _aware(fj.started_at), _aware(fj.completed_at)
    ran_h = (en - st).total_seconds() / 3600 if st and en and en > st else None
    est_h = (fj.est_time_s or 0) / 3600 or None
    grams = float(fj.filament_g or 0)
    measured = fj.used_g if fj.used_source in ("printer", "weighed") else None
    if fj.status == "completed":
        share = 1.0
    elif fj.progress:
        share = min(1.0, max(0.0, float(fj.progress) / 100))
    elif ran_h and est_h:
        share = min(1.0, ran_h / est_h)
    else:
        share = 1.0 if fj.status == "failed" else 0.0
    hours = ran_h if ran_h is not None else (est_h or 0) * share
    return {"hours": hours, "grams": measured if measured is not None else grams * share, "share": share,
            "measured": measured is not None}


def farm(db: Session, days: int = 30) -> dict:
    from ..kv import read_cache
    with read_cache():
        return _farm(db, days)


def _farm(db: Session, days: int) -> dict:
    since = now() - timedelta(days=days)
    rates, default_rate = _rates(db)
    jobs = list(db.execute(select(FarmJob).where(FarmJob.status.in_(("completed", "failed", "cancelled")),
                                                 FarmJob.completed_at >= since)).scalars())
    checks = {c.external_job_id: c for c in db.execute(select(PlateCheck).where(PlateCheck.created_at >= since - timedelta(days=1))).scalars()}
    models = {m.id: m for m in db.execute(select(Model).where(Model.id.in_({j.model_id for j in jobs if j.model_id}))).scalars()} \
        if jobs else {}
    ppk_cache = {}

    def ppg(material):
        k = (material or "PLA").upper()
        if k not in ppk_cache:
            ppk_cache[k] = slicing.price_per_kg(db, k)[0] / 1000
        return ppk_cache[k]

    tot = defaultdict(float)
    printers = defaultdict(lambda: defaultdict(float))
    reasons = defaultdict(lambda: {"count": 0, "grams": 0.0, "hours": 0.0})
    by_model = defaultdict(lambda: defaultdict(float))
    for fj in jobs:
        n = _job_numbers(fj)
        chk = checks.get(fj.external_id)
        failed = fj.status != "completed" or (chk is not None and chk.state == "failed")
        if fj.status == "cancelled" and n["share"] == 0 and not (chk and chk.state == "failed"):
            continue              # cancelled before it started: no print, no waste
        rate = rates.get(fj.printer_name, default_rate)
        cost = n["grams"] * ppg(fj.material) + n["hours"] * rate
        p = printers[fj.printer_name or "unknown"]
        p["jobs"] += 1
        p["hours"] += n["hours"]
        tot["jobs"] += 1
        tot["hours"] += n["hours"]
        tot["grams"] += n["grams"]
        if n["measured"]:
            tot["measured_jobs"] += 1
            tot["measured_g"] += n["grams"]
        m = by_model[fj.model_id] if fj.model_id else None
        if m is not None:
            m["jobs"] += 1
        if failed:
            if chk is not None and chk.state == "failed":
                key = FAIL_REASONS.get(chk.reason, chk.reason or "Other")
            elif fj.status == "cancelled":
                key = "Cancelled mid-print"
            else:
                key = "Failed in Watchtower" + (f": {fj.error[:60]}" if fj.error else "")
            r = reasons[key]
            r["count"] += 1
            r["grams"] += n["grams"]
            r["hours"] += n["hours"]
            for d in (tot, p):
                d["failed"] += 1
                d["waste_g"] += n["grams"]
                d["waste_h"] += n["hours"]
                d["waste_cost"] += cost
            if m is not None:
                m["failed"] += 1
                m["waste_g"] += n["grams"]
                m["waste_cost"] += cost
        else:
            tot["ok"] += 1
            p["ok"] += 1
    period_h = days * 24
    rnd = lambda v, k=1: round(v, k)  # noqa: E731
    out_printers = []
    for name, p in sorted(printers.items()):
        out_printers.append({"printer": name, "jobs": int(p["jobs"]), "ok": int(p["ok"]), "failed": int(p["failed"]),
                             "success_rate": rnd(100 * p["ok"] / p["jobs"]) if p["jobs"] else None,
                             "print_hours": rnd(p["hours"]), "utilisation": rnd(100 * p["hours"] / period_h),
                             "waste_g": rnd(p["waste_g"]), "waste_hours": rnd(p["waste_h"]), "waste_cost": rnd(p["waste_cost"], 2)})
    out_models = []
    for mid, m in by_model.items():
        if not m["failed"]:
            continue
        mm = models.get(mid)
        out_models.append({"model_id": mid, "code": mm.code if mm else None, "name": mm.name if mm else None,
                           "jobs": int(m["jobs"]), "failed": int(m["failed"]), "failure_rate": rnd(100 * m["failed"] / m["jobs"]),
                           "waste_g": rnd(m["waste_g"]), "waste_cost": rnd(m["waste_cost"], 2)})
    out_models.sort(key=lambda x: (-x["waste_cost"], -x["failed"]))
    return {"days": days, "since": since.isoformat(),
            "totals": {"jobs": int(tot["jobs"]), "ok": int(tot["ok"]), "failed": int(tot["failed"]),
                       "success_rate": rnd(100 * tot["ok"] / tot["jobs"]) if tot["jobs"] else None,
                       "print_hours": rnd(tot["hours"]), "filament_g": rnd(tot["grams"]), "waste_g": rnd(tot["waste_g"]),
                       "measured_jobs": int(tot["measured_jobs"]), "measured_g": rnd(tot["measured_g"]),
                       "waste_hours": rnd(tot["waste_h"]), "waste_cost": rnd(tot["waste_cost"], 2)},
            "printers": out_printers,
            "reasons": sorted(({"reason": k, "count": v["count"], "waste_g": rnd(v["grams"]), "waste_hours": rnd(v["hours"])}
                               for k, v in reasons.items()), key=lambda x: -x["count"]),
            "models": out_models[:25]}


def _part_cost(db: Session, cache: dict, part_id: int) -> float | None:
    if part_id not in cache:
        pc = db.execute(select(PartCache).where(PartCache.inventree_part_id == part_id)).scalar()
        best = None
        for s in (pc.suppliers if pc else []) or []:
            try:
                unit = float(s.get("price")) / max(1.0, float(s.get("pack_qty") or 1))
            except (TypeError, ValueError):
                continue
            best = unit if best is None else min(best, unit)
        cache[part_id] = best
    return cache[part_id]


def profit(db: Session, days: int = 30) -> dict:
    from ..kv import read_cache
    with read_cache():
        return _profit(db, days)


def _profit(db: Session, days: int) -> dict:
    from sqlalchemy.orm import selectinload
    from ..models import SliceResult
    since = now() - timedelta(days=days)
    prof = slicing.default_profile(db)
    results = {r.model_id: r for r in db.execute(select(SliceResult).where(SliceResult.profile_id == prof.id)).scalars()} \
        if prof else {}
    sold = defaultdict(int)
    for o in db.execute(select(Order).where(Order.status.in_(SOLD), Order.is_test.is_(False),
                                            Order.placed_at >= since)).scalars():
        for ln in o.lines:
            if ln.sku:
                sold[ln.sku] += ln.qty
    from .filament_use import model_ratios
    ratios = model_ratios(db)
    pcache, rows, unpriced, uncosted = {}, [], 0, 0
    q = select(Product).options(selectinload(Product.model).selectinload(Model.product), selectinload(Product.variants),
                                selectinload(Product.parts),
                                selectinload(Product.filaments))
    for p in db.execute(q).scalars():
        m = p.model
        if not m or m.status == "archived":
            continue
        r = results.get(m.id)
        stats = (r.approved_stats if r and r.approved_file_ids else None) or None
        c = slicing.cost_of(db, m, prof, stats) if stats and prof else None
        copies = max(1, int((stats or {}).get("copies") or 1))
        hours = (stats or {}).get("time_s", 0) / 3600 / copies if stats else None
        targets = [(v.sku, v.price, f"{p.title} ({v.color_name})") for v in p.variants if v.active] \
            if p.color_mode == "options" and any(v.active for v in p.variants) else [(p.sku, None, p.title)]
        for sku, vprice, title in targets:
            price = vprice or p.sale_price or p.price
            parts = 0.0
            missing_part_price = False
            vid = next((v.id for v in p.variants if v.sku == sku), None)
            for b in p.parts:
                if b.variant_id and b.variant_id != vid:
                    continue          # a part only some colours use
                u = _part_cost(db, pcache, b.inventree_part_id)
                if u is None:
                    missing_part_price = True
                else:
                    parts += u * b.qty
            rt = ratios.get(m.id)
            mat = c["material"] if c else None
            if c and rt and rt["prints"] >= 1:
                mat = round(c["material"] * rt["ratio"], 2)     # what the printer or the scale says it really uses
            cost = (c["total"] - c["material"] + mat + parts) if c else None
            margin = (price - cost) if price is not None and cost is not None else None
            units = sold.get(sku, 0)
            if price is None:
                unpriced += 1
            if cost is None:
                uncosted += 1
            rows.append({"sku": sku, "title": title, "model_id": m.id, "live": p.live, "price": price,
                         "material_cost": mat, "material_estimate": c["material"] if c else None,
                         "material_basis": f"measured ({rt['prints']} prints, {round(100 * rt['ratio'])}% of estimate)"
                                           if c and rt else "estimate",
                         "machine_cost": c["machine"] if c else None,
                         "parts_cost": round(parts, 2) if p.parts else 0, "parts_price_missing": missing_part_price,
                         "cost": round(cost, 2) if cost is not None else None,
                         "margin": round(margin, 2) if margin is not None else None,
                         "margin_pct": round(100 * margin / price, 1) if margin is not None and price else None,
                         "print_hours": round(hours, 2) if hours else None,
                         "per_printer_hour": round(margin / hours, 2) if margin is not None and hours else None,
                         "units_sold": units, "revenue": round(units * price, 2) if price is not None else None,
                         "profit": round(units * margin, 2) if margin is not None else None})
    rows.sort(key=lambda x: (-(x["profit"] or 0), -(x["margin"] if x["margin"] is not None else -1e9), x["sku"] or ""))
    sold_rows = [x for x in rows if x["units_sold"]]
    return {"days": days, "printer": prof.name if prof else None, "rows": rows,
            "totals": {"units": sum(x["units_sold"] for x in rows), "revenue": round(sum(x["revenue"] or 0 for x in sold_rows), 2),
                       "profit": round(sum(x["profit"] or 0 for x in sold_rows), 2),
                       "priced": len(rows) - unpriced, "unpriced": unpriced, "uncosted": uncosted}}


CSV = {
    "printers": ("printers", ["printer", "jobs", "ok", "failed", "success_rate", "print_hours", "utilisation", "waste_g",
                              "waste_hours", "waste_cost"]),
    "reasons": ("reasons", ["reason", "count", "waste_g", "waste_hours"]),
    "models": ("models", ["code", "name", "jobs", "failed", "failure_rate", "waste_g", "waste_cost"]),
    "profit": ("rows", ["sku", "title", "live", "price", "material_cost", "material_estimate", "material_basis", "machine_cost", "parts_cost", "cost", "margin",
                        "margin_pct", "print_hours", "per_printer_hour", "units_sold", "revenue", "profit"]),
}


def csv_text(db: Session, kind: str, days: int) -> str:
    import csv
    import io
    key, cols = CSV[kind]
    data = profit(db, days) if kind == "profit" else farm(db, days)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in data[key]:
        w.writerow(["" if r.get(c) is None else r.get(c) for c in cols])
    return buf.getvalue()
