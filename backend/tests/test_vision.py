"""Label-reading AI connections (services/vision.py): order, fallback, and each provider's request shape."""
import base64
import json

import httpx
import pytest

from app.services import vision

JPEG = b"\xff\xd8\xff\xe0jpegdata"
ANSWER = {"vendor": "Acme", "material": "PLA", "color_name": "Red", "color_hex": "ff0000", "weight_g": 1000}


def reply(url, payload, status=200):
    return httpx.Response(status, json=payload, request=httpx.Request("POST", url))


@pytest.fixture
def sent(monkeypatch):
    """Records every httpx.post the providers make and answers like each real API would."""
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers or {}})
        text = jsonlib.dumps(ANSWER)
        if "/api/chat" in url:
            return reply(url, {"message": {"content": text}})
        if url.endswith("/chat/completions"):
            return reply(url, {"choices": [{"message": {"content": text}}]})
        if url.endswith("/messages"):
            return reply(url, {"content": [{"type": "text", "text": text}]})
        if ":generateContent" in url:
            return reply(url, {"candidates": [{"content": {"parts": [{"text": text}]}}]})
        return reply(url, {"error": {"message": "unexpected"}}, 404)

    jsonlib = json
    monkeypatch.setattr(vision.httpx, "post", post)
    return calls


def test_each_provider_sends_the_photo_its_own_way(sent):
    b64 = base64.b64encode(JPEG).decode()
    for kind, cfg in [
        ("ai_ollama", {"url": "http://ollama.local:11434", "model": "gemma4:e4b"}),
        ("ai_openai", {"model": "gpt-test", "api_key": "sk-1"}),
        ("ai_anthropic", {"model": "claude-test", "api_key": "ak-1"}),
        ("ai_gemini", {"model": "gemini-test", "api_key": "gk-1"}),
    ]:
        assert vision.parse_json(vision.ask(kind, cfg, "read it", JPEG)) == ANSWER
    ollama, openai, claude, gemini = sent
    assert ollama["url"] == "http://ollama.local:11434/api/chat" and ollama["json"]["messages"][0]["images"] == [b64]
    assert ollama["json"]["format"] == "json" and "Authorization" not in ollama["headers"]
    assert openai["url"] == "https://api.openai.com/v1/chat/completions" and openai["headers"]["Authorization"] == "Bearer sk-1"
    assert openai["json"]["messages"][0]["content"][1]["image_url"]["url"] == f"data:image/jpeg;base64,{b64}"
    assert claude["url"] == "https://api.anthropic.com/v1/messages" and claude["headers"]["x-api-key"] == "ak-1"
    assert claude["json"]["messages"][0]["content"][0]["source"] == {"type": "base64", "media_type": "image/jpeg", "data": b64}
    assert gemini["url"].endswith("/models/gemini-test:generateContent") and gemini["headers"]["x-goog-api-key"] == "gk-1"
    assert gemini["json"]["contents"][0]["parts"][1]["inline_data"]["data"] == b64


def test_cloud_providers_need_a_key(sent):
    with pytest.raises(vision.ProviderError, match="no API key"):
        vision.ask("ai_openai", {"model": "gpt-test"}, "read it", JPEG)
    assert sent == []


def test_tries_the_next_connection_when_one_fails_or_cannot_read(monkeypatch):
    tried = []

    def ask(kind, cfg, prompt, photo, timeout=None):
        tried.append(kind)
        if kind == "ai_ollama":
            raise httpx.ReadTimeout("slow")
        if kind == "ai_openai":
            return '{"vendor": null, "material": null}'          # answered, but read nothing
        return json.dumps(ANSWER)

    monkeypatch.setattr(vision, "ask", ask)
    monkeypatch.setattr(vision, "providers", lambda db: [("ai_ollama", {"model": "gemma4:e4b"}),
                                                          ("ai_openai", {"model": "gpt-test"}),
                                                          ("ai_gemini", {"model": "gemini-test"})])
    doc, engine, notes = vision.read_label(None, "read it", JPEG, lambda d: bool(d.get("material")))
    assert doc == ANSWER and engine == "gemini:gemini-test"
    assert tried == ["ai_ollama", "ai_openai", "ai_gemini"]
    assert notes == ["ollama:gemma4:e4b: timed out", "openai:gpt-test: could not read enough"]


def test_nothing_usable_reports_every_attempt(monkeypatch):
    monkeypatch.setattr(vision, "providers", lambda db: [("ai_ollama", {"model": "m"})])
    monkeypatch.setattr(vision, "ask", lambda *a, **k: (_ for _ in ()).throw(vision.ProviderError("Local AI answered 500")))
    doc, engine, notes = vision.read_label(None, "p", JPEG, lambda d: True)
    assert doc is None and engine is None and notes == ["ollama:m: Local AI answered 500"]


def test_order_keeps_the_saved_choice_and_adds_new_kinds(monkeypatch):
    from app import kv
    store = {}
    monkeypatch.setattr(kv, "setting", lambda db, k, default=None: store.get(k, default))
    monkeypatch.setattr(kv, "set_setting", lambda db, k, v: store.__setitem__(k, v))
    assert vision.order(None) == vision.KINDS                     # nothing saved: local first
    assert vision.save_order(None, ["ai_anthropic", "bogus", "ai_ollama", "ai_anthropic"]) == \
        ["ai_anthropic", "ai_ollama", "ai_openai", "ai_gemini"]
    store["vision_order"] = ["ai_gemini"]                         # an older saved order still lists the rest
    assert vision.order(None) == ["ai_gemini", "ai_ollama", "ai_openai", "ai_anthropic"]


def test_model_lists(monkeypatch):
    def get(url, headers=None, timeout=None):
        if url.endswith("/api/tags"):
            body = {"models": [{"name": "gemma4:e4b"}, {"name": "gemma4:26b"}]}
        elif url.endswith("/models"):
            body = {"data": [{"id": "gpt-test"}, {"id": "text-embedding-3-small"}, {"id": "whisper-1"}]}
        elif "/models?limit" in url:
            body = {"data": [{"id": "claude-b"}, {"id": "claude-a"}]}
        else:
            body = {"models": [{"name": "models/gemini-test", "supportedGenerationMethods": ["generateContent"]},
                               {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]}]}
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(vision.httpx, "get", get)
    assert vision.models("ai_ollama", {"url": "http://x:11434"}) == ["gemma4:26b", "gemma4:e4b"]
    assert vision.models("ai_openai", {"api_key": "k"}) == ["gpt-test"]
    assert vision.models("ai_anthropic", {"api_key": "k"}) == ["claude-b", "claude-a"]
    assert vision.models("ai_gemini", {"api_key": "k"}) == ["gemini-test"]


def test_provider_errors_carry_the_providers_message(monkeypatch):
    monkeypatch.setattr(vision.httpx, "post", lambda url, **kw: reply(url, {"error": {"message": "invalid x-api-key"}}, 401))
    with pytest.raises(vision.ProviderError, match="Claude answered 401: invalid x-api-key"):
        vision.ask("ai_anthropic", {"model": "m", "api_key": "bad"}, "p", JPEG)


def test_png_photos_say_so():
    assert vision._mime(b"\x89PNG\r\n\x1a\nrest") == "image/png" and vision._mime(JPEG) == "image/jpeg"
