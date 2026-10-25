"""LLM and embedding clients.

Both talk to OpenAI-compatible endpoints. In this project those run on a JarvisLabs GPU
(vLLM for chat, a small embedding server), but any compatible URL works via env vars.
"""

import json
import re
from functools import lru_cache
from typing import Any

from openai import OpenAI

from api.config import settings


class LLMNotConfigured(RuntimeError):
    pass


@lru_cache(maxsize=4)
def _client(base_url: str, api_key: str) -> OpenAI:
    if not base_url:
        raise LLMNotConfigured("LLM/embedding base URL is not set; run `python -m scripts.jarvis up` first")
    return OpenAI(base_url=base_url, api_key=api_key or "none", timeout=120, max_retries=2)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in reply")
    return json.loads(text[start : end + 1])


def chat_json(system: str, user: str, *, model: str | None = None, base_url: str | None = None,
              api_key: str | None = None, max_tokens: int = 1200) -> dict[str, Any]:
    """Ask the chat model for a JSON object. Retries once if the reply isn't valid JSON."""
    client = _client(base_url or settings.llm_base_url, api_key or settings.llm_api_key)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    last_error: Exception | None = None
    for _ in range(2):
        resp = client.chat.completions.create(
            model=model or settings.llm_model,
            messages=messages,
            temperature=0.1,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content or ""
        try:
            return _extract_json(content)
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            messages += [
                {"role": "assistant", "content": content},
                {"role": "user", "content": "Reply with one valid JSON object only, no prose."},
            ]
    raise ValueError(f"model did not return valid JSON: {last_error}")


def embed(texts: list[str], *, model: str | None = None, base_url: str | None = None) -> list[list[float]]:
    client = _client(base_url or settings.embed_base_url, settings.embed_api_key)
    resp = client.embeddings.create(model=model or settings.embed_model, input=texts)
    return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]


def health() -> dict[str, Any]:
    """Quick check used by /api/agent/status."""
    out: dict[str, Any] = {}
    for name, url, key in (
        ("llm", settings.llm_base_url, settings.llm_api_key),
        ("embed", settings.embed_base_url, settings.embed_api_key),
    ):
        try:
            if not url:
                raise LLMNotConfigured("not configured yet; run `python -m scripts.jarvis up`")
            quick = OpenAI(base_url=url, api_key=key or "none", timeout=8, max_retries=0)
            models = [m.id for m in quick.models.list().data]
            out[name] = {"ok": True, "url": url, "models": models}
        except Exception as exc:
            out[name] = {"ok": False, "url": url, "error": str(exc)[:200]}
    return out
