from app.routers.mobile import _job_label, brand_model


def test_brand_and_model_from_the_slicer_preset():
    assert brand_model("Bambu Lab H2C 0.4 nozzle", "bambu") == ("Bambu Lab", "H2C")
    assert brand_model("Prusa MK4S 0.4 nozzle", "prusalink") == ("Prusa", "MK4S")
    assert brand_model("Voron 2.4 350 0.6 nozzle", "klipper") == ("Voron", "2.4 350")
    assert brand_model(None, "bambu") == ("Bambu Lab", None)
    assert brand_model(None, "klipper") == ("Other", None)


def test_the_job_label_is_always_text():
    assert _job_label({"job": {"id": 83, "status": "printing"}, "file": "Bambu+Lab+Spool_ABS_0.4n.gcode"}) == "Bambu Lab Spool ABS 0.4n"
    assert _job_label({"job": "Reindeer", "file": "x.gcode"}) == "Reindeer"
    assert _job_label({"job": None, "file": None}) is None
