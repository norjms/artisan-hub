"""Label-reading AI for the hub: the team's AI connections (Connected services), tried in the order saved on the
Connections page until one gives a usable answer.

Kinds: ai_ollama (a local Ollama server), ai_openai (OpenAI or any OpenAI-compatible API), ai_anthropic
(Claude) and ai_gemini (Google Gemini). Each has a model (picked from the provider's own model list on the
Connections page) and, for the cloud ones, an API key stored encrypted with the other connection secrets.

A team with none of them connected still works the old way: the vision_url / vision_model settings (or the hub's
config defaults) as an implicit Ollama connection.
"""
import base64
import json
import logging
import re

import httpx
from sqlalchemy.orm import Session

from . import connections as conns

log = logging.getLogger("hub.vision")

KINDS = ["ai_ollama", "ai_openai", "ai_anthropic", "ai_gemini"]
SHORT = {"ai_ollama": "ollama", "ai_openai": "openai", "ai_anthropic": "claude", "ai_gemini": "gemini"}
OPENAI_URL = "https://api.openai.com/v1"
ANTHROPIC_URL = "https://api.anthropic.com/v1"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta"
CLOUD_TIMEOUT = 60.0


class ProviderError(RuntimeError):
    pass


# ---------------------------------------------------------------- order and configuration

def order(db: Session) -> list[str]:
    """The saved order, then any AI kind it does not mention yet."""
    from ..kv import setting
    saved = [k for k in (setting(db, "vision_order") or []) if k in KINDS]
    return saved + [k for k in KINDS if k not in saved]


def save_order(db: Session, kinds: list[str]) -> list[str]:
    from ..kv import set_setting
    clean = []
    for k in kinds:
        if k in KINDS and k not in clean:
            clean.append(k)
    set_setting(db, "vision_order", clean + [k for k in KINDS if k not in clean])
    return order(db)


def _legacy(db: Session) -> dict | None:
    """The pre-connection settings as an Ollama config, when set."""
    from ..config import settings
    from ..kv import setting
    s = settings()
    url = (setting(db, "vision_url") or s.vision_url or "").rstrip("/")
    model = setting(db, "vision_model") or s.vision_model
    return {"url": url, "model": model} if url and model else None


def seed_from_legacy(db: Session) -> bool:
    """Once per team: turn the old vision_url / vision_model settings into a Local AI (Ollama) connection, so the
    Connections page shows what the hub already uses. True when it created one."""
    from sqlalchemy import select

    from ..kv import set_setting, setting
    from ..models import Connection
    if setting(db, "vision_seeded"):
        return False
    set_setting(db, "vision_seeded", True)
    if db.execute(select(Connection).where(Connection.kind == "ai_ollama")).scalar():
        return False
    old = _legacy(db)
    if not old:
        return False
    conns.save(db, "ai_ollama", {"url": old["url"], "model": old["model"]}, None, True)
    return True


def providers(db: Session) -> list[tuple[str, dict]]:
    """(kind, config with secrets) for each enabled AI connection with a model, in order."""
    out = []
    for k in order(db):
        cfg = conns.get(k, required=False)
        if cfg and str(cfg.get("model") or "").strip():
            out.append((k, cfg))
    if not out and not any(conns.get(k, required=False) for k in KINDS):
        old = _legacy(db)
        if old:
            out.append(("ai_ollama", old))
    return out


def label(kind: str, cfg: dict) -> str:
    return f"{SHORT[kind]}:{cfg.get('model')}"


# ---------------------------------------------------------------- calls

def _mime(photo: bytes) -> str:
    if photo[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if photo[:4] == b"RIFF" and photo[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _raise_for(r: httpx.Response, who: str):
    if r.status_code >= 400:
        detail = ""
        try:
            j = r.json()
            e = j.get("error") if isinstance(j, dict) else None
            detail = (e.get("message") if isinstance(e, dict) else e) or j.get("message") or ""
        except ValueError:
            detail = r.text[:200]
        raise ProviderError(f"{who} answered {r.status_code}: {str(detail)[:200]}".rstrip(": "))


def _key(cfg: dict, who: str) -> str:
    k = str(cfg.get("api_key") or "").strip()
    if not k:
        raise ProviderError(f"{who} has no API key saved")
    return k


def ask(kind: str, cfg: dict, prompt: str, photo: bytes | None, timeout: float | None = None) -> str:
    """The model's text answer. Raises ProviderError or httpx errors."""
    model = str(cfg.get("model") or "").strip()
    b64 = base64.b64encode(photo).decode() if photo else None
    mime = _mime(photo) if photo else None
    if kind == "ai_ollama":
        from ..config import settings
        url = str(cfg.get("url") or "").rstrip("/")
        if not url:
            raise ProviderError("Local AI has no address")
        msg = {"role": "user", "content": prompt}
        if b64:
            msg["images"] = [b64]
        kw = {"json": {"model": model, "messages": [msg], "stream": False, "format": "json", "think": False,
                       "options": {"temperature": 0}},
              "timeout": timeout or float(settings().vision_timeout)}
        if cfg.get("api_key"):
            kw["headers"] = {"Authorization": f"Bearer {cfg['api_key']}"}
        r = httpx.post(url + "/api/chat", **kw)
        _raise_for(r, "Local AI")
        return ((r.json().get("message") or {}).get("content") or "").strip()
    if kind == "ai_openai":
        base = str(cfg.get("base_url") or OPENAI_URL).rstrip("/")
        content = [{"type": "text", "text": prompt}]
        if b64:
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        body = {"model": model, "messages": [{"role": "user", "content": content}], "response_format": {"type": "json_object"}}
        headers = {"Authorization": f"Bearer {_key(cfg, 'OpenAI')}"}
        r = httpx.post(base + "/chat/completions", headers=headers, json=body, timeout=timeout or CLOUD_TIMEOUT)
        if r.status_code == 400 and "response_format" in r.text:
            body.pop("response_format")            # some compatible servers do not take it
            r = httpx.post(base + "/chat/completions", headers=headers, json=body, timeout=timeout or CLOUD_TIMEOUT)
        _raise_for(r, "OpenAI")
        choices = r.json().get("choices") or [{}]
        return str((choices[0].get("message") or {}).get("content") or "").strip()
    if kind == "ai_anthropic":
        content = []
        if b64:
            content.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}})
        content.append({"type": "text", "text": prompt})
        r = httpx.post(ANTHROPIC_URL + "/messages", timeout=timeout or CLOUD_TIMEOUT,
                       headers={"x-api-key": _key(cfg, "Claude"), "anthropic-version": "2023-06-01"},
                       json={"model": model, "max_tokens": 800, "messages": [{"role": "user", "content": content}]})
        _raise_for(r, "Claude")
        return "".join(b.get("text", "") for b in (r.json().get("content") or []) if b.get("type") == "text").strip()
    if kind == "ai_gemini":
        parts = [{"text": prompt}]
        if b64:
            parts.append({"inline_data": {"mime_type": mime, "data": b64}})
        r = httpx.post(f"{GEMINI_URL}/models/{model}:generateContent", timeout=timeout or CLOUD_TIMEOUT,
                       headers={"x-goog-api-key": _key(cfg, "Gemini")},
                       json={"contents": [{"role": "user", "parts": parts}],
                             "generationConfig": {"responseMimeType": "application/json", "temperature": 0,
                                                  "thinkingConfig": {"thinkingLevel": "low"}}})
        _raise_for(r, "Gemini")
        cands = r.json().get("candidates") or [{}]
        return "".join(p.get("text", "") for p in ((cands[0].get("content") or {}).get("parts") or [])).strip()
    raise ProviderError(f"unknown AI connection {kind}")


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    doc = json.loads(m.group(0) if m else text)
    if not isinstance(doc, dict):
        raise ValueError("not a JSON object")
    return doc


def models(kind: str, cfg: dict) -> list[str]:
    """Model names the provider offers this key, newest-looking first where the API says nothing."""
    if kind == "ai_ollama":
        url = str(cfg.get("url") or "").rstrip("/")
        if not url:
            raise ProviderError("enter the Local AI address first")
        r = httpx.get(url + "/api/tags", timeout=10)
        _raise_for(r, "Local AI")
        return sorted(m.get("name") for m in r.json().get("models") or [] if m.get("name"))
    if kind == "ai_openai":
        base = str(cfg.get("base_url") or OPENAI_URL).rstrip("/")
        r = httpx.get(base + "/models", headers={"Authorization": f"Bearer {_key(cfg, 'OpenAI')}"}, timeout=15)
        _raise_for(r, "OpenAI")
        ids = [m.get("id") for m in r.json().get("data") or [] if m.get("id")]
        skip = ("embedding", "whisper", "tts", "dall-e", "moderation", "transcribe", "audio", "realtime", "image", "search")
        return sorted((i for i in ids if not any(s in i for s in skip)), reverse=True)
    if kind == "ai_anthropic":
        r = httpx.get(ANTHROPIC_URL + "/models?limit=100", timeout=15,
                      headers={"x-api-key": _key(cfg, "Claude"), "anthropic-version": "2023-06-01"})
        _raise_for(r, "Claude")
        return [m.get("id") for m in r.json().get("data") or [] if m.get("id")]
    if kind == "ai_gemini":
        r = httpx.get(GEMINI_URL + "/models?pageSize=200", headers={"x-goog-api-key": _key(cfg, "Gemini")}, timeout=15)
        _raise_for(r, "Gemini")
        out = []
        for m in r.json().get("models") or []:
            if "generateContent" in (m.get("supportedGenerationMethods") or []):
                n = str(m.get("name") or "").removeprefix("models/")
                if n and not any(s in n for s in ("embedding", "tts", "image-generation", "aqa")):
                    out.append(n)
        return sorted(out, reverse=True)
    raise ProviderError(f"unknown AI connection {kind}")


def test(kind: str) -> tuple[bool, str]:
    """Connections page Test: the key works and the chosen model is on offer."""
    cfg = conns.get(kind, required=False)
    if not cfg:
        return False, "not connected"
    try:
        names = models(kind, cfg)
    except (ProviderError, httpx.HTTPError) as e:
        return False, str(e) if isinstance(e, ProviderError) else f"could not reach it: {type(e).__name__}"
    model = str(cfg.get("model") or "").strip()
    if not model:
        return False, f"connected ({len(names)} models); pick a model"
    if names and model not in names:
        return False, f"connected, but {model} is not among its {len(names)} models"
    return True, f"connected; {model} is available"


def read_label(db: Session, prompt: str, photo: bytes | None, usable) -> tuple[dict | None, str | None, list[str]]:
    """Tries each connection in order. (parsed answer, engine label, notes on the ones that failed)."""
    notes = []
    for kind, cfg in providers(db):
        who = label(kind, cfg)
        try:
            doc = parse_json(ask(kind, cfg, prompt, photo))
        except (ProviderError, httpx.HTTPError, ValueError, TypeError, KeyError) as e:
            why = str(e) if isinstance(e, ProviderError) else type(e).__name__
            if isinstance(e, httpx.TimeoutException):
                why = "timed out"
            log.info("vision %s: %s", who, why)
            notes.append(f"{who}: {why}")
            continue
        if usable(doc):
            return doc, who, notes
        notes.append(f"{who}: could not read enough")
    return None, None, notes
