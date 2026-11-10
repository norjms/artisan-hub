from app.services.material_names import clean_filament, clean_material, material_from_text, strip_marks


def test_marks_removed():
    assert strip_marks("Fiberon™ ASA-CF08 Navy Blue") == "Fiberon ASA-CF08 Navy Blue"
    assert strip_marks("PolyLite(TM) PLA ®") == "PolyLite PLA"


def test_other_becomes_the_named_material():
    assert clean_material("OTHER", "Fiberon™ ASA-CF08 Navy Blue") == "ASA-CF"
    assert clean_material("OTHER", "Fiberon PA6-GF25 Grey") == "PA6-GF"
    assert clean_material("", "PolyLite PLA+ Red") == "PLA+"


def test_more_specific_name_wins_but_unrelated_does_not():
    assert clean_material("ASA", "Fiberon ASA-CF08") == "ASA-CF"
    assert clean_material("PA", "Fiberon PA6-GF25") == "PA6-GF"
    assert clean_material("pla", "Matte Scarlet Red") == "PLA"
    assert clean_material("PETG", "PLA lookalike") == "PETG"


def test_no_false_matches_in_words():
    assert material_from_text("Panchroma Plastic Pastel") is None
    assert clean_material("OTHER", "Mystery") == "OTHER"


def test_clean_filament():
    f = clean_filament({"vendor": "Polymaker®", "name": "Fiberon™ ASA-CF08 Navy Blue", "material": "OTHER"})
    assert f == {"vendor": "Polymaker", "name": "Fiberon ASA-CF08 Navy Blue", "material": "ASA-CF"}
