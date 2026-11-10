import httpx

from app.services import product_search as ps


def test_google_redirect_links_become_the_shops_own(monkeypatch):
    calls = []

    def fake_get(url, follow_redirects=True, timeout=None):
        calls.append(url)
        return httpx.Response(302, headers={"location": "https://www.3dfuel.com/products/pctg-pro"})

    monkeypatch.setattr(ps.httpx, "get", fake_get)
    r = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AbC123"
    out = ps.resolve_links([r, r, "https://store.bambulab.com/x"])
    assert out == {r: "https://www.3dfuel.com/products/pctg-pro"}
    assert calls == [r]                                  # once per link, and only Google's


def test_a_failed_redirect_keeps_the_link(monkeypatch):
    def boom(url, follow_redirects=True, timeout=None):
        raise httpx.ConnectError("no")
    monkeypatch.setattr(ps.httpx, "get", boom)
    assert ps.resolve_links(["https://vertexaisearch.cloud.google.com/grounding-api-redirect/x"]) == {}
