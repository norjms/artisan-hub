"""Licensing rules, credit lines, evidence PDF, and MakerWorld parsing (no network)."""
import pytest



def test_normalize():
    from app.services.licensing import normalize
    assert normalize("BY") == "BY"
    assert normalize("CC BY-SA 4.0") == "BY-SA"
    assert normalize("BY-NC-ND") == "BY-NC-ND"
    assert normalize("CC0") == "CC0"
    assert normalize("Standard Digital File License") == "SDFL"
    assert normalize("MakerWorld Exclusive License") == "MW-EXCLUSIVE"
    assert normalize("") == "UNKNOWN"
    assert normalize("something odd") == "UNKNOWN"


def test_sellable_list_matches_rules():
    from app.services.licensing import LICENSES, SELLABLE_MAKERWORLD
    assert all(LICENSES[c]["sell"] for c in SELLABLE_MAKERWORLD)
    assert not any(LICENSES[c]["sell"] for c in ("BY-NC", "BY-NC-SA", "BY-NC-ND", "SDFL", "MW-EXCLUSIVE", "UNKNOWN"))


def _model(code="BY", designer=True, **kw):
    from app.models import Model, Designer
    m = Model(code="T-1", name="Test Dragon", slug="t-1", source="makerworld", license_code=code,
              source_url="https://makerworld.com/en/models/1", **kw)
    if designer:
        m.designer = Designer(name="Jane Maker", slug="jane", url="https://makerworld.com/en/@jane")
    return m


def test_credit_and_sale_rules():
    from app.services import licensing
    m = _model("BY")
    assert licensing.needs_credit(m)
    line = licensing.credit_line(m)
    assert "Jane Maker" in line and "CC BY 4.0" in line
    html = licensing.credit_html(m)
    assert "creativecommons.org/licenses/by/4.0" in html and "original design" in html
    assert licensing.sale_problems(m) == []

    assert licensing.credit_line(_model("CC0")) is None
    assert licensing.sale_problems(_model("BY-NC"))
    assert licensing.sale_problems(_model("SDFL"))
    assert "designer" in licensing.sale_problems(_model("BY", designer=False))[0]
    # override text satisfies credit without a designer record
    assert licensing.sale_problems(_model("BY", designer=False, credit_override="Design by X")) == []
    # designer that always wants credit
    m = _model("COMMERCIAL")
    m.designer.always_credit = True
    assert licensing.credit_line(m).startswith("Design by Jane Maker")
    m.designer.credit_template = "Made with permission from {designer}"
    assert licensing.credit_line(m) == "Made with permission from Jane Maker"


def test_pdf_render(tmp_path):
    from app.services import licensing
    m = _model("BY")
    out = tmp_path / "rec.pdf"
    licensing.render_pdf(out, m, {"captured_at": "2026-10-18T12:00:00+00:00", "source_url": m.source_url,
                                  "license_raw": "BY", "license_code": "BY", "license_title": "CC BY 4.0",
                                  "license_text": "<p>You may <b>sell</b> prints with credit.</p>",
                                  "designer": "Jane Maker", "design_updated": None, "wayback_url": None,
                                  "json_sha256": "a" * 64, "html_sha256": None}, None)
    data = out.read_bytes()
    assert data.startswith(b"%PDF") and len(data) > 1500


def test_html_to_text_and_profile():
    from app.services.makerworld import html_to_text, parse_profile_title
    t = html_to_text("<p>Hello <b>world</b></p><ul><li>one</li><li>two</li></ul><p>a<br>b</p>")
    assert "Hello world" in t and "- one" in t and "a\nb" in t
    p = parse_profile_title("0.2mm layer, 3 walls, 15% infill, no supports")
    assert p["layer_height"] == "0.2" and p["walls"] == "3" and p["infill"] == "15%" and p["supports"] == "no"


@pytest.mark.parametrize("line,qty,title", [
    ("4 x M3x8 screws", 4, "M3x8 screws"),
    ("Magnet 6x3mm x 8", 8, "Magnet 6x3mm"),
    ("M3x10 bolt", 1, "M3x10 bolt"),
    ("Bolt M3 x 10", 1, "Bolt M3 x 10"),
    ("2 bearings 608zz", 2, "bearings 608zz"),
    ("Heat insert M3: 6", 6, "Heat insert M3"),
    ("Spring (x2)", 2, "Spring"),
])
def test_qty_lines(line, qty, title):
    from app.services.makerworld import parse_qty_line
    assert parse_qty_line(line) == (qty, title)


def test_supplies_and_instance():
    from app.services.makerworld import supplies_from, pick_instance
    d = {"defaultInstanceId": 11, "instances": [{"id": 10, "profileId": 500}, {"id": 11, "profileId": 501}],
         "designExtension": {"boms": [{"displayTitle": "Magnets 6x2mm (50PCS)", "pieces": 4, "url": "https://x", "sku": "A1"}],
                             "boms_of_other_parts": "<p>4 x M3x8 screws</p><p>Glue</p>"}}
    s = supplies_from(d)
    assert s[0]["title"].startswith("Magnets") and s[0]["qty"] == 4 and s[0]["sku"] == "A1"
    assert {"title": "M3x8 screws", "qty": 4} .items() <= s[1].items()
    assert s[2]["title"] == "Glue"
    assert pick_instance(d)["id"] == 11
    assert pick_instance(d, profile_id=500)["id"] == 10
    assert pick_instance(d, instance_id=10)["id"] == 10


def test_parse_ids():
    from app.clients.makerworld_api import parse_design_id, parse_profile_id
    assert parse_design_id("https://makerworld.com/en/models/1234567-cute-thing#profileId-998877") == 1234567
    assert parse_design_id("1234567") == 1234567
    assert parse_profile_id("https://makerworld.com/en/models/1234567-cute-thing#profileId-998877") == 998877
    with pytest.raises(ValueError):
        parse_design_id("https://example.com/nope")


def test_ip_flags():
    from app.services.licensing import ip_flags
    assert ip_flags("Pokemon TCG Mini Tin") == ["pokemon", "tcg"]
    assert ip_flags("Mid Century Modern Ribbed Planter") == []
    assert ip_flags("Marioneta stand") == []   # whole words only
