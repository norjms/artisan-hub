"""Mesh geometry for STL and 3MF files: volume, surface area, bounding box, and a filament estimate.

Used when a model site gives no filament weight. The estimate treats the part as solid perimeter shells
(surface area x shell thickness) plus sparse infill inside, which lands within about 20 percent of a slicer
for typical decor prints. It is labelled as an estimate everywhere it is shown.
"""
import math
import re
import struct
import zipfile
from pathlib import Path

from lxml import etree

DENSITY = {"PLA": 1.24, "PETG": 1.27, "ABS": 1.04, "ASA": 1.07, "TPU": 1.21, "PA": 1.14, "PC": 1.20, "PVA": 1.23,
           "HIPS": 1.04, "PLA-CF": 1.29, "PETG-CF": 1.30}
SHELL_MM = 1.2          # about three 0.4 mm perimeters / top-bottom layers
INFILL = 0.15
MAX_TRIANGLES = 6_000_000


class Mesh:
    def __init__(self):
        self.vol6 = 0.0       # 6 x signed volume
        self.area2 = 0.0      # 2 x area
        self.lo = [math.inf] * 3
        self.hi = [-math.inf] * 3
        self.tris = 0

    def add(self, a, b, c):
        ax, ay, az = a
        bx, by, bz = b
        cx, cy, cz = c
        self.vol6 += ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        self.area2 += math.sqrt(nx * nx + ny * ny + nz * nz)
        for p in (a, b, c):
            for i in range(3):
                if p[i] < self.lo[i]:
                    self.lo[i] = p[i]
                if p[i] > self.hi[i]:
                    self.hi[i] = p[i]
        self.tris += 1

    def result(self, scale_vol: float = 1.0) -> dict:
        if not self.tris:
            return {}
        v = abs(self.vol6) / 6.0 * scale_vol
        return {"volume_mm3": v, "area_mm2": self.area2 / 2.0, "triangles": self.tris,
                "bbox_mm": [round(self.hi[i] - self.lo[i], 1) for i in range(3)]}


def stl(path: Path) -> dict:
    m = Mesh()
    size = path.stat().st_size
    with open(path, "rb") as f:
        head = f.read(84)
        n = struct.unpack("<I", head[80:84])[0] if len(head) == 84 else 0
        if len(head) == 84 and 84 + n * 50 == size:            # binary
            if n > MAX_TRIANGLES:
                return {"skipped": "too many triangles"}
            rec = struct.Struct("<12x9f2x")
            buf = f.read()
            for i in range(n):
                x = rec.unpack_from(buf, i * 50)
                m.add(x[0:3], x[3:6], x[6:9])
            return m.result()
    # ASCII
    pts = []
    num = re.compile(rb"vertex\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)")
    with open(path, "rb") as f:
        for line in f:
            mm = num.search(line)
            if mm:
                pts.append(tuple(float(v) for v in mm.groups()))
                if len(pts) == 3:
                    m.add(*pts)
                    pts = []
                    if m.tris > MAX_TRIANGLES:
                        return {"skipped": "too many triangles"}
    return m.result()


def _mat(s: str | None):
    if not s:
        return None
    v = [float(x) for x in s.split()]
    return v if len(v) == 12 else None


def _apply(t, p):
    return (t[0] * p[0] + t[3] * p[1] + t[6] * p[2] + t[9],
            t[1] * p[0] + t[4] * p[1] + t[7] * p[2] + t[10],
            t[2] * p[0] + t[5] * p[1] + t[8] * p[2] + t[11])


def _compose(a, b):
    """Apply a first, then b (3MF row-vector 4x3 matrices)."""
    if a is None:
        return b
    if b is None:
        return a
    A = [a[0:3] + [0.0], a[3:6] + [0.0], a[6:9] + [0.0], a[9:12] + [1.0]]
    B = [b[0:3] + [0.0], b[3:6] + [0.0], b[6:9] + [0.0], b[9:12] + [1.0]]
    C = [[sum(A[r][k] * B[k][c] for k in range(4)) for c in range(4)] for r in range(4)]
    return [C[r][c] for r in range(4) for c in range(3)]


def threemf(path: Path) -> dict:
    """Sum every build item (with transforms and nested components) across all model parts in the package."""
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".model")]
        objects, builds, unit = {}, [], "millimeter"
        for n in names:
            root = etree.fromstring(z.read(n), parser=etree.XMLParser(huge_tree=True, recover=True))
            if root.get("unit"):
                unit = root.get("unit")
            ns = root.nsmap.get(None, "")
            q = (lambda t: f"{{{ns}}}{t}") if ns else (lambda t: t)
            for obj in root.iter(q("object")):
                oid = (n, obj.get("id"))
                mesh = obj.find(q("mesh"))
                if mesh is not None:
                    vs = [(float(v.get("x")), float(v.get("y")), float(v.get("z"))) for v in mesh.iter(q("vertex"))]
                    ts = [(int(t.get("v1")), int(t.get("v2")), int(t.get("v3"))) for t in mesh.iter(q("triangle"))]
                    objects[oid] = ("mesh", vs, ts)
                comps = obj.find(q("components"))
                if comps is not None:
                    lst = []
                    for c in comps.iter(q("component")):
                        ppath = c.get("{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}path")
                        target = (ppath.lstrip("/") if ppath else n, c.get("objectid"))
                        lst.append((target, _mat(c.get("transform"))))
                    objects[oid] = ("comps", lst)
            for item in root.iter(q("item")):
                builds.append(((n, item.get("objectid")), _mat(item.get("transform"))))
    scale = {"micron": 0.001, "millimeter": 1.0, "centimeter": 10.0, "inch": 25.4, "foot": 304.8, "meter": 1000.0}.get(unit, 1.0)
    m = Mesh()

    def walk(oid, t, depth=0):
        o = objects.get(oid)
        if not o or depth > 8 or m.tris > MAX_TRIANGLES:
            return
        if o[0] == "mesh":
            vs = [_apply(t, v) if t else v for v in o[1]]
            vs = [(x * scale, y * scale, z * scale) for x, y, z in vs]
            for a, b, c in o[2]:
                m.add(vs[a], vs[b], vs[c])
        else:
            for target, ct in o[1]:
                walk(target, _compose(ct, t), depth + 1)

    if not builds:       # some exporters omit <build>; count every top-level mesh once
        builds = [(oid, None) for oid, o in objects.items() if o[0] == "mesh"]
    for oid, t in builds:
        walk(oid, t)
    return m.result()


def measure(path: Path) -> dict:
    p = Path(path)
    try:
        if p.suffix.lower() == ".stl":
            return stl(p)
        if p.suffix.lower() == ".3mf":
            return threemf(p)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:200]}
    return {}


def estimate_grams(g: dict, material: str = "PLA", infill: float = INFILL, shell_mm: float = SHELL_MM) -> float | None:
    v = g.get("volume_mm3")
    a = g.get("area_mm2")
    if not v or not a:
        return None
    shell = min(v, a * shell_mm)
    printed = shell + infill * max(0.0, v - shell)
    rho = DENSITY.get((material or "PLA").upper().replace(" ", "-"), DENSITY["PLA"])
    return round(printed / 1000.0 * rho, 1)


def estimate_model(db, model, overwrite: bool = False) -> dict:
    """Measure the model's STL/3MF files; fill product filament grams when nobody has set them."""
    from ..config import settings
    lib = Path(settings().library_root)
    files = [f for f in model.files if f.library_rel and f.filename.lower().endswith((".stl", ".3mf"))]
    out = []
    for f in files:
        geo = (f.settings or {}).get("geometry")
        if not geo or overwrite:
            geo = measure(lib / f.library_rel)
            f.settings = {**(f.settings or {}), "geometry": geo}
        if geo.get("volume_mm3"):
            out.append((f, geo))
    if not out:
        return {"measured": 0}
    prod = model.product
    material = (prod.filaments[0].material if prod and prod.filaments else "PLA") or "PLA"
    for f, geo in out:
        geo["est_grams"] = estimate_grams(geo, material)
        f.settings = {**(f.settings or {}), "geometry": geo}
    # a 3MF project usually already contains every part; otherwise the STLs are the parts
    pick = [x for x in out if x[0].filename.lower().endswith(".3mf")] or out
    total = round(sum(g["est_grams"] or 0 for _, g in pick), 1)
    report = {"measured": len(out), "estimate_g": total, "from": [f.filename for f, _ in pick]}
    if not model.model_dims and len(pick) == 1:
        b = pick[0][1]["bbox_mm"]
        model.model_dims = f"{b[0]:.0f} x {b[1]:.0f} x {b[2]:.0f} mm"
    if prod and total:
        have = sum(b.grams or 0 for b in prod.filaments)
        if have == 0 or overwrite:
            if prod.filaments:
                prod.filaments[0].grams = total
                for extra in prod.filaments[1:]:
                    extra.grams = extra.grams or 0
            else:
                from ..models import BomFilament
                prod.filaments.append(BomFilament(slot=1, label="Body", material=material, grams=total, waste_pct=5))
            model.print_settings = {**(model.print_settings or {}),
                                    "filament_estimate": f"about {total:.0f} g ({material}, estimated from the model "
                                                         f"geometry at {int(INFILL * 100)}% infill; replace with the slicer figure)"}
            report["applied"] = True
    return report
