"""What the slicer presets should be: the library filaments with their print defaults, and the farm's printers.

    docker exec hub-web python -m app.slicer_specs [--team N]

prints JSON for a sync script that writes OrcaSlicer and Bambu Studio user filament presets from it on the
machine that runs the slicers. Read over SSH, so no API key lives on that machine.
"""
import argparse
import json
import sys


def specs(team_id: int | None = None) -> dict:
    from sqlalchemy import text

    from .db import SessionLocal
    from .tenant import bind_session, use_team
    from .services import print_defaults, spool_library as lib

    db = SessionLocal()
    if team_id is None:
        team_id = db.execute(text("select min(id) from team")).scalar()
    bind_session(db, team_id)
    with use_team(team_id):
        filaments = []
        for f in lib.library_filaments(db):
            vendor = f.get("vendor") if isinstance(f.get("vendor"), str) else (f.get("vendor") or {}).get("name")
            filaments.append({"id": f.get("id"), "vendor": vendor or "", "name": f.get("name") or "",
                              "material": f.get("material") or "", "color_hex": (f.get("color_hex") or "").lstrip("#"),
                              "weight_g": f.get("weight_g") or f.get("weight"),
                              "defaults": print_defaults.get(db, f.get("id"))})
        printers = []
        try:
            from .routers.mobile import _wt
            for p in _wt().printers().get("printers", []):
                printers.append({"name": p.get("name"), "model": p.get("model") or "", "family": p.get("family") or ""})
        except Exception as e:  # noqa: BLE001
            print(f"printers: {e}", file=sys.stderr)
    db.close()
    return {"team_id": team_id, "filaments": filaments, "printers": printers}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", type=int)
    a = ap.parse_args()
    json.dump(specs(a.team), sys.stdout, indent=1, default=str)
