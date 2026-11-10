"""Model-site link detection, license mapping and text parsing (no network)."""
import json

import pytest


@pytest.mark.parametrize("raw,platform,code", [
    ("CC-BY", "printables", "BY"), ("CC-BY-SA", "printables", "BY-SA"), ("CC-BY-ND", "printables", "BY-ND"),
    ("CC-BY-NC-SA", "printables", "BY-NC-SA"), ("CC0", "printables", "CC0"),
    ("Standard Digital File", "printables", "PR-SDFL"), ("Commercial Use", "printables", "PR-COMMERCIAL"),
    ("Comm. - No Derivative", "printables", "PR-COMMERCIAL-ND"), ("GPL 3.0", "printables", "GPL"),
    ("LGPL", "printables", "LGPL"), ("BSD", "printables", "BSD"), ("Apache-2.0", "printables", "APACHE"),
    ("OCL v1.1 + GAtt v1 + Micro v1", "printables", "OCL"), ("CERN-OHL-S v2", "printables", "CERN-OHL"),
    ("Creative Commons - Attribution", "thingiverse", "BY"),
    ("Creative Commons - Attribution - Share Alike", "thingiverse", "BY-SA"),
    ("Creative Commons - Attribution - Non-Commercial", "thingiverse", "BY-NC"),
    ("Creative Commons - Attribution - Non-Commercial - No Derivatives", "thingiverse", "BY-NC-ND"),
    ("Creative Commons - Public Domain Dedication", "thingiverse", "CC0"), ("Public Domain", "thingiverse", "CC0"),
    ("GNU - GPL", "thingiverse", "GPL"), ("GNU - LGPL", "thingiverse", "LGPL"), ("BSD License", "thingiverse", "BSD"),
    ("All Rights Reserved", "thingiverse", "ARR"),
    ("CC BY", "creality", "BY"), ("CC BY-NC-SA", "creality", "BY-NC-SA"), ("CXY-SL", "creality", "CXY-SL"),
    ("BY-SA", "makerworld", "BY-SA"), ("Standard Digital File License", "makerworld", "SDFL"),
])
def test_normalize_all_sites(raw, platform, code):
    from app.services.licensing import normalize, LICENSES
    assert normalize(raw, platform) == code
    assert code in LICENSES


def test_sellable_set():
    from app.services.licensing import SELLABLE
    for c in ("CC0", "BY", "BY-SA", "BY-ND", "PR-COMMERCIAL", "PR-COMMERCIAL-ND", "GPL", "BSD"):
        assert c in SELLABLE
    for c in ("BY-NC", "OCL", "PR-SDFL", "CXY-SL", "ARR", "SDFL", "UNKNOWN"):
        assert c not in SELLABLE


@pytest.mark.parametrize("url,platform,ext", [
    ("https://makerworld.com/en/models/1190761-planter#profileId-1", "makerworld", "1190761"),
    ("https://www.printables.com/model/3161-3d-benchy", "printables", "3161"),
    ("https://www.printables.com/de/model/643364-planter", "printables", "643364"),
    ("https://www.thingiverse.com/thing:763622", "thingiverse", "763622"),
    ("https://www.crealitycloud.com/model-detail/6a0714fce0b3fd73ac06ed93", "creality", "6a0714fce0b3fd73ac06ed93"),
])
def test_detect(url, platform, ext):
    from app.sources import detect
    assert detect(url) == (platform, ext)


def test_detect_rejects():
    from app.sources import detect
    with pytest.raises(ValueError):
        detect("thing")
    assert detect("https://example.com/thing")[0] == "web"      # any other link: the generic importer


def test_supplies_from_text():
    from app.sources.base import supplies_from_text
    html = """<p>Print it in PLA with 3 walls. You can also use a screw to hang it.</p>
    <h3>Hardware needed</h3><ul><li>4x M3x8 screws</li><li>Magnets 6x3mm x 8</li><li>1 x 608 bearing</li></ul>
    <p>Enjoy!</p>"""
    s = supplies_from_text(html)
    titles = {x["title"]: x["qty"] for x in s}
    assert titles.get("M3x8 screws") == 4 and titles.get("Magnets 6x3mm") == 8 and titles.get("608 bearing") == 1
    assert not any("hang" in t for t in titles)        # prose mention is not a parts list


def test_creality_nuxt_decode():
    from app.sources.creality import _nuxt
    data = [["Reactive", 1], {"data": 2}, ["ShallowReactive", 3], {"model-info_x": 4}, {"result": 5}, {"groupItem": 6},
            {"id": 7, "groupName": 8, "license": 9}, "abc123", "Planter", "CC BY"]
    html = '<script type="application/json" id="__NUXT_DATA__">' + json.dumps(data) + "</script>"
    root = _nuxt(html)
    assert root["data"]["model-info_x"]["result"]["groupItem"] == {"id": "abc123", "groupName": "Planter", "license": "CC BY"}
