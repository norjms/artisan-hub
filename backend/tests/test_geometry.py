"""Mesh volume / area / bbox for STL and 3MF, and the filament estimate."""
import struct
import zipfile

CUBE = [((0, 0, 0), (1, 1, 0), (1, 0, 0)), ((0, 0, 0), (0, 1, 0), (1, 1, 0)),
        ((0, 0, 1), (1, 0, 1), (1, 1, 1)), ((0, 0, 1), (1, 1, 1), (0, 1, 1)),
        ((0, 0, 0), (1, 0, 0), (1, 0, 1)), ((0, 0, 0), (1, 0, 1), (0, 0, 1)),
        ((0, 1, 0), (1, 1, 1), (1, 1, 0)), ((0, 1, 0), (0, 1, 1), (1, 1, 1)),
        ((0, 0, 0), (0, 0, 1), (0, 1, 1)), ((0, 0, 0), (0, 1, 1), (0, 1, 0)),
        ((1, 0, 0), (1, 1, 0), (1, 1, 1)), ((1, 0, 0), (1, 1, 1), (1, 0, 1))]


def scaled(s):
    return [tuple(tuple(c * s for c in v) for v in t) for t in CUBE]


def test_binary_and_ascii_stl(tmp_path):
    from app.services.geometry import stl
    tris = scaled(20)
    b = tmp_path / "cube.stl"
    with open(b, "wb") as f:
        f.write(b"\0" * 80 + struct.pack("<I", len(tris)))
        for t in tris:
            f.write(struct.pack("<12f", 0, 0, 0, *t[0], *t[1], *t[2]) + b"\0\0")
    g = stl(b)
    assert round(g["volume_mm3"]) == 8000 and round(g["area_mm2"]) == 2400 and g["bbox_mm"] == [20, 20, 20]
    a = tmp_path / "cube_ascii.stl"
    a.write_text("solid c\n" + "".join("facet normal 0 0 0\nouter loop\n" + "".join(f"vertex {x} {y} {z}\n" for x, y, z in t)
                                     + "endloop\nendfacet\n" for t in tris) + "endsolid c\n")
    assert round(stl(a)["volume_mm3"]) == 8000


def test_3mf_with_components_and_transform(tmp_path):
    from app.services.geometry import threemf
    verts = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0), (0, 0, 10), (10, 0, 10), (10, 10, 10), (0, 10, 10)]
    idx = {v: i for i, v in enumerate(verts)}
    tri_xml = "".join(f'<triangle v1="{idx[tuple(c*10 for c in t[0])]}" v2="{idx[tuple(c*10 for c in t[1])]}" v3="{idx[tuple(c*10 for c in t[2])]}"/>' for t in CUBE)
    vert_xml = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in verts)
    obj = (f'<?xml version="1.0"?><model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
           f'<resources><object id="1" type="model"><mesh><vertices>{vert_xml}</vertices><triangles>{tri_xml}</triangles></mesh></object></resources><build/></model>')
    main = ('<?xml version="1.0"?><model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
            'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"><resources>'
            '<object id="2" type="model"><components><component p:path="/3D/Objects/object_1.model" objectid="1" transform="2 0 0 0 2 0 0 0 2 0 0 0"/></components></object>'
            '</resources><build><item objectid="2" transform="1 0 0 0 1 0 0 0 1 50 50 0"/><item objectid="2"/></build></model>')
    p = tmp_path / "parts.3mf"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("3D/3dmodel.model", main)
        z.writestr("3D/Objects/object_1.model", obj)
    g = threemf(p)
    assert round(g["volume_mm3"]) == 2 * 8000        # two items, each a 20 mm cube
    assert g["bbox_mm"] == [70.0, 70.0, 20.0]


def test_estimate():
    from app.services.geometry import estimate_grams
    solid_small = {"volume_mm3": 1000, "area_mm2": 600}        # 10 mm cube: mostly shell
    assert estimate_grams(solid_small) == 0.9
    big = {"volume_mm3": 1_000_000, "area_mm2": 60_000}       # 100 mm cube
    g = estimate_grams(big)
    assert 250 < g < 290
    assert estimate_grams(big, "PETG") > g
