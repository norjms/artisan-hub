"""One-time: fill tools on existing printer profiles and turn sliced files made before the review workflow
into approved slice results, so they show on the Print tab and in the Slicing grid.

Run inside the web container:  python -m scripts.migrate_slices [--dry-run]
Safe to run again: printers that already have tools and (model, printer) pairs that already have a result are skipped.
"""
import sys
from collections import defaultdict

from sqlalchemy import select

from app.db import SessionLocal
from app import tenant
from app.models import Team, PrinterProfile, ModelFile, SliceResult, Model
from app.services import slicing


def main(dry: bool):
    db = SessionLocal()
    tenant.bind_session(db, None, True)
    teams = list(db.execute(select(Team)).scalars())
    for team in teams:
        tenant.bind_session(db, team.id)
        tok = tenant._team.set(team.id)
        try:
            profs = {p.id: p for p in db.execute(select(PrinterProfile)).scalars()}
            for p in profs.values():
                if not p.tools:
                    slicing.ensure_tools(p)
                    print(f"[{team.slug}] {p.name}: {len(p.tools)} tool(s) from {p.machine_preset}")
            groups = defaultdict(list)
            for f in db.execute(select(ModelFile).where(ModelFile.kind.in_(("3mf_sliced", "gcode")))).scalars():
                pid = (f.settings or {}).get("printer_profile_id")
                if pid in profs:
                    groups[(f.model_id, pid)].append(f)
            made = 0
            for (mid, pid), files in groups.items():
                if slicing.result_for(db, mid, pid, create=False):
                    continue
                prof, model = profs[pid], db.get(Model, mid)
                files.sort(key=lambda f: ((f.settings or {}).get("plate_of") or [1])[0])
                f0 = files[0]
                by_slot = {}
                for f in files:             # one file per plate: add the plates up
                    for x in f.filament or []:
                        if x.get("slot"):
                            by_slot[x["slot"]] = by_slot.get(x["slot"], 0) + (x.get("grams") or 0)
                n = max(1, (f0.settings or {}).get("copies") or 1)
                tools = [{"slot": s, "tool": s, "grams": round(g, 2), "per_unit_g": round(g / n, 2), "preset": None}
                         for s, g in sorted(by_slot.items())]
                grams = round(sum(t["grams"] or 0 for t in tools), 2)
                copies = max(1, (f0.settings or {}).get("copies") or 1)
                opts = {}
                ver = slicing.engine_version(prof)
                stats = {"time_s": sum(f.print_seconds or 0 for f in files), "grams": grams, "per_unit_g": round(grams / copies, 2),
                         "copies": copies, "plates": f0.plates or len(files), "tools": tools, "tower_g": 0,
                         "process": (f0.settings or {}).get("process"), "warnings": [], "opts": opts, "migrated": True}
                h = slicing.inputs_hash(model, prof, opts, ver)
                r = SliceResult(model_id=mid, profile_id=pid, state="approved", engine=slicing.engine_of(prof),
                                engine_version=ver, inputs_hash=h, approved_hash=h, spec={"_opts": opts},
                                stats=stats, approved_stats=dict(stats), file_ids=[f.id for f in files],
                                approved_file_ids=[f.id for f in files], previews=[],
                                problems=slicing.problems_for(model, prof), requested_by="migration",
                                approved_by="migration (sliced before review existed)")
                db.add(r)
                made += 1
            print(f"[{team.slug}] {made} slice result(s) from existing sliced files")
            if dry:
                db.rollback()
            else:
                db.commit()
        finally:
            tenant._team.reset(tok)
    db.close()


if __name__ == "__main__":
    main("--dry-run" in sys.argv)
