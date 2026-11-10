"""Finding the product online (services/product_search.py): a grounded search in plain words, then the answer
turned into fields; what counts as verified; busy models; caching."""
import json

import httpx
import pytest

from app.services import product_search as ps

CFG = {"model": "gemini-test", "api_key": "gk-1"}
PROSE = "The barcode 6977252424738 belongs to Bambu Lab PLA Wood, Clay Brown, 1.75 mm, 1 kg, about $24.99."
FIELDS = {"matches": [{"vendor": "Bambu Lab", "name": "PLA Wood Clay Brown", "material": "pla", "color_hex": "#995F11",
                       "weight_g": "1000", "diameter_mm": 1.75, "price": 24.99, "currency": "USD",
                       "barcode": "6977252424738", "confidence": 0.95}]}


def fake(calls, sites=("allegro.pl", "m79.lv"), search_status=200, busy=()):
    """Answers the grounded call (has tools) with prose and sources, and the structuring call with JSON."""

    def post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "body": json})
        req = httpx.Request("POST", url)
        if "tools" in json:
            model = url.split("/models/")[1].split(":")[0]
            if model in busy:
                return httpx.Response(503, json={"error": {"message": "high demand"}}, request=req)
            if search_status != 200:
                return httpx.Response(search_status, json={"error": {"message": "quota"}}, request=req)
            gm = {"webSearchQueries": ["6977252424738"],
                  "groundingChunks": [{"web": {"uri": f"https://redirect.example/{i}", "title": s}} for i, s in enumerate(sites)]}
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": PROSE}]},
                                                             "groundingMetadata": gm if sites else {}}]}, request=req)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": jsonlib.dumps(FIELDS)}]}}]}, request=req)

    jsonlib = json
    return post


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    ps._cache.clear()
    monkeypatch.setattr(ps, "gemini", lambda db=None: CFG)


def test_searches_in_plain_words_then_structures(monkeypatch):
    calls = []
    monkeypatch.setattr(ps.httpx, "post", fake(calls))
    out = ps.by_barcode("697-7252424738")
    search, structure = calls
    assert search["url"].endswith("/models/gemini-test:generateContent") and search["body"]["tools"] == [{"google_search": {}}]
    text = search["body"]["contents"][0]["parts"][0]["text"]
    assert "6977252424738" in text and "JSON" not in text          # asking for JSON makes Gemini skip the search
    assert "tools" not in structure["body"] and PROSE in structure["body"]["contents"][0]["parts"][0]["text"]
    (m,) = out["matches"]
    assert m["vendor"] == "Bambu Lab" and m["material"] == "PLA" and m["color_hex"] == "995f11" and m["weight_g"] == 1000.0
    assert m["verified"] is True and m["url"] == "https://redirect.example/0"
    assert out["note"] is None and [s["title"] for s in out["sources"]] == ["allegro.pl", "m79.lv"]


def test_an_answer_without_a_search_is_unverified(monkeypatch):
    monkeypatch.setattr(ps.httpx, "post", fake([], sites=()))
    out = ps.by_barcode("6977252424738")
    assert out["matches"][0]["verified"] is False and "from memory" in out["note"]


def test_a_busy_model_falls_back_to_flash_lite(monkeypatch):
    calls = []
    monkeypatch.setattr(ps.httpx, "post", fake(calls, busy=("gemini-test",)))
    out = ps.by_barcode("6977252424738")
    assert out["engine"] == f"gemini:{ps.BUSY_FALLBACK}" and out["matches"]
    assert [c["url"].split("/models/")[1].split(":")[0] for c in calls] == ["gemini-test", ps.BUSY_FALLBACK, ps.BUSY_FALLBACK]


def test_answers_are_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(ps.httpx, "post", fake(calls))
    ps.by_guess({"vendor": "Bambu Lab", "material": "PLA", "color_name": "Clay Brown"})
    ps.by_guess({"vendor": "bambu lab", "material": "pla", "color_name": "clay brown"})
    assert len(calls) == 2                                          # one search and one structuring, once


def test_failures_and_missing_setup_explain_themselves(monkeypatch):
    monkeypatch.setattr(ps.httpx, "post", fake([], search_status=429))
    out = ps.by_barcode("6977252424738")
    assert out["matches"] == [] and "quota" in out["note"]
    monkeypatch.setattr(ps, "gemini", lambda db=None: None)
    assert "paid-tier key" in ps.by_guess({"vendor": "SUNLU"})["note"]
    assert ps.by_barcode("123")["matches"] == []


def test_needs_a_brand_or_name_to_search():
    assert "brand or product name" in ps.by_guess({"material": "PLA"})["note"]
