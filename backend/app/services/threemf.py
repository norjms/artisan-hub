"""Read print metadata out of 3MF files (Bambu Studio / OrcaSlicer / PrusaSlicer / Creality / Anycubic).

Sliced Bambu/Orca 3MF files contain Metadata/plate_N.gcode and Metadata/slice_info.config with time,
weight and per-filament usage. Project (unsliced) files still carry project_settings.config with the
printer, layer height, walls, infill and filament types/colors.
"""
import json
import re
import zipfile
from lxml import etree

SLICER_HINTS = [
    ("bambu", "BambuStudio"), ("orca", "OrcaSlicer"), ("prusa", "PrusaSlicer"),
    ("creality", "CrealityPrint"), ("anycubic", "AnycubicSlicer"), ("elegoo", "ElegooSlicer"),
]


def guess_slicer(filename: str, app: str | None = None) -> str | None:
    hay = f"{filename} {app or ''}".lower()
    for k, v in SLICER_HINTS:
        if k in hay:
            return v
    return None


def _first(v):
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _num(v):
    try:
        return float(str(_first(v)).rstrip("%"))
    except (TypeError, ValueError):
        return None


def parse_3mf(path: str, filename: str | None = None) -> dict:
    out = {"sliced": False, "plates": None, "print_seconds": None, "grams": None, "filament": [],
           "settings": {}, "printer": None, "slicer": None, "app": None}
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        out["error"] = "not a zip/3mf"
        return out
    names = set(z.namelist())
    gcodes = sorted(n for n in names if re.match(r"Metadata/plate_\d+\.gcode$", n))
    out["sliced"] = bool(gcodes) or any(n.lower().endswith(".gcode") for n in names)
    out["plates"] = len(gcodes) or None

    # app name from 3D model metadata
    try:
        model_xml = z.read("3D/3dmodel.model")
        m = re.search(rb'name="Application"\s*>([^<]+)<', model_xml)
        if m:
            out["app"] = m.group(1).decode(errors="ignore").strip()
    except KeyError:
        pass
    out["slicer"] = guess_slicer(filename or path, out["app"])

    # project settings (Bambu/Orca JSON)
    for cand in ("Metadata/project_settings.config", "Metadata/Slic3r_PE.config", "Metadata/PrusaSlicer.config"):
        if cand in names:
            raw = z.read(cand)
            st = {}
            if raw.strip().startswith(b"{"):
                try:
                    st = json.loads(raw)
                except ValueError:
                    st = {}
            else:  # PrusaSlicer "; key = value"
                for line in raw.decode(errors="ignore").splitlines():
                    mm = re.match(r";\s*([\w\-]+)\s*=\s*(.*)$", line)
                    if mm:
                        st[mm.group(1)] = mm.group(2)
            out["printer"] = _first(st.get("printer_model")) or _first(st.get("printer_settings_id")) \
                or _first(st.get("printer_notes"))
            out["settings"] = {k: v for k, v in {
                "layer_height": _num(st.get("layer_height")),
                "nozzle": _num(st.get("nozzle_diameter")),
                "walls": _num(st.get("wall_loops") or st.get("perimeters")),
                "infill": _first(st.get("sparse_infill_density") or st.get("fill_density")),
                "infill_pattern": _first(st.get("sparse_infill_pattern") or st.get("fill_pattern")),
                "supports": _first(st.get("enable_support") or st.get("support_material")),
                "top_layers": _num(st.get("top_shell_layers") or st.get("top_solid_layers")),
                "bottom_layers": _num(st.get("bottom_shell_layers") or st.get("bottom_solid_layers")),
                "filament_types": st.get("filament_type"),
                "filament_colors": st.get("filament_colour") or st.get("extruder_colour"),
                "print_profile": _first(st.get("print_settings_id")),
            }.items() if v not in (None, "", [])}
            break

    # slice info (sliced Bambu/Orca)
    if "Metadata/slice_info.config" in names:
        try:
            root = etree.fromstring(z.read("Metadata/slice_info.config"))
            secs, grams = 0.0, 0.0
            fil: dict[str, dict] = {}
            plates = root.findall(".//plate")
            for plate in plates:
                meta = {m.get("key"): m.get("value") for m in plate.findall("metadata")}
                secs += float(meta.get("prediction") or 0)
                grams += float(meta.get("weight") or 0)
                for f in plate.findall("filament"):
                    fid = f.get("id")
                    e = fil.setdefault(fid, {"slot": int(fid) if fid and fid.isdigit() else None,
                                             "type": f.get("type"), "color": f.get("color"), "grams": 0.0})
                    e["grams"] += float(f.get("used_g") or 0)
            out["print_seconds"] = int(secs) or None
            out["grams"] = round(grams, 2) or None
            out["filament"] = sorted(({**v, "grams": round(v["grams"], 2)} for v in fil.values()),
                                     key=lambda x: x["slot"] or 0)
            out["plates"] = out["plates"] or len(plates) or None
            hdr = root.find(".//header")
            if hdr is not None:
                for h in hdr.findall("header_item"):
                    if h.get("key") == "X-BBL-Client-Type":
                        out["app"] = out["app"] or h.get("value")
        except etree.XMLSyntaxError:
            pass

    if not out["filament"] and out["settings"].get("filament_types"):
        types = out["settings"]["filament_types"]
        colors = out["settings"].get("filament_colors") or []
        out["filament"] = [{"slot": i + 1, "type": t, "color": colors[i] if i < len(colors) else None, "grams": None}
                           for i, t in enumerate(types)]
    return out


def parse_gcode_header(path: str) -> dict:
    """Pull time/weight from slicer comments at the top or bottom of a gcode file."""
    out = {"print_seconds": None, "grams": None}
    try:
        with open(path, "rb") as fh:
            head = fh.read(200_000)
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 200_000))
            tail = fh.read()
    except OSError:
        return out
    txt = (head + tail).decode(errors="ignore")
    m = re.search(r"total filament weight \[g\]\s*:\s*([\d.]+)", txt) or \
        re.search(r"filament used \[g\]\s*=\s*([\d.]+)", txt) or re.search(r"total filament used \[g\]\s*=\s*([\d.]+)", txt)
    if m:
        out["grams"] = float(m.group(1))
    m = re.search(r"estimated printing time \(normal mode\)\s*=\s*([\dhms ]+)", txt) or \
        re.search(r"model printing time:\s*([\dhms ]+)", txt)
    if m:
        secs = 0
        for val, unit in re.findall(r"(\d+)\s*([dhms])", m.group(1)):
            secs += int(val) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
        out["print_seconds"] = secs or None
    return out
