"""LLM and embedding clients.

Both talk to OpenAI-compatible endpoints. In this project those run on a JarvisLabs GPU
(vLLM for chat, a small embedding server), but any compatible URL works via env vars.
"""

import json
import re
from functools import lru_cache
from typing import Any

from openai import BadRequestError, OpenAI

from api.config import settings

_json_mode_ok = True


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
    global _json_mode_ok
    client = _client(base_url or settings.llm_base_url, api_key or settings.llm_api_key)
    # Some servers (and Gemma chat templates) reject a separate system role; merging is harmless everywhere.
    messages = [{"role": "user", "content": f"{system}\n\n---\n\n{user}"}]
    last_error: Exception | None = None
    for _ in range(2):
        # Thinking models (e.g. Gemma 4) spend tokens on reasoning before answering: add an allowance.
        kwargs: dict[str, Any] = dict(model=model or settings.llm_model, messages=messages, temperature=0.1,
                                      max_tokens=max_tokens + settings.reasoning_budget)
        try:
            if _json_mode_ok:
                kwargs["response_format"] = {"type": "json_object"}
            resp = client.chat.completions.create(**kwargs)
        except BadRequestError:
            if not _json_mode_ok:
                raise
            _json_mode_ok = False  # server doesn't support JSON mode; rely on the prompt + parser
            kwargs.pop("response_format", None)
            resp = client.chat.completions.create(**kwargs)
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


@lru_cache(maxsize=2)
def _local_embedder(model: str):
    from fastembed import TextEmbedding  # ONNX on CPU; no GPU or torch needed

    return TextEmbedding(model)


def embed(texts: list[str], *, model: str | None = None, base_url: str | None = None) -> list[list[float]]:
    """Remote OpenAI-compatible embeddings when EMBED_BASE_URL is set, otherwise bge-small locally on CPU."""
    url = base_url or settings.embed_base_url
    name = model or settings.embed_model
    if not url:
        return [list(map(float, v)) for v in _local_embedder(name).embed(texts)]
    client = _client(url, settings.embed_api_key)
    resp = client.embeddings.create(model=name, input=texts)
    return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]


def health() -> dict[str, Any]:
    """Quick check used by /api/agent/status."""
    out: dict[str, Any] = {}
    url, key = settings.llm_base_url, settings.llm_api_key
    try:
        if not url:
            raise LLMNotConfigured("LLM_BASE_URL is not set")
        quick = OpenAI(base_url=url, api_key=key or "none", timeout=30, max_retries=0)
        reply = quick.chat.completions.create(model=settings.llm_model, max_tokens=300,
                                              messages=[{"role": "user", "content": "Say ok"}])
        out["llm"] = {"ok": True, "url": url, "model": settings.llm_model,
                      "reply": (reply.choices[0].message.content or "")[:20]}
    except Exception as exc:
        out["llm"] = {"ok": False, "url": url, "model": settings.llm_model, "error": str(exc)[:200]}
    try:
        vec = embed(["health check"])[0]
        out["embed"] = {"ok": True, "where": settings.embed_base_url or "local CPU (fastembed)",
                        "model": settings.embed_model, "dim": len(vec)}
    except Exception as exc:
        out["embed"] = {"ok": False, "where": settings.embed_base_url or "local CPU", "error": str(exc)[:200]}
    return out
