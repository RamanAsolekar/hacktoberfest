"""Secret scrubber: nothing secret may reach Postgres, Qdrant or the LLM.

STAND-IN by Person 1 covering the patterns in Person 4's task doc; Person 4 owns it.
"""

import copy
import re
from typing import Any

_PATTERNS: list[tuple[str, re.Pattern, str | None]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), None),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), None),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"), None),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), None),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"), None),
    ("bearer", re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1[REDACTED:bearer]"),
    # scheme://user:password@host -> keep user and host
    ("url_password", re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s:/@]+:)([^\s@/]+)(@)"), r"\1[REDACTED:url_password]\3"),
    # KEY=value / KEY: value where KEY looks secret. Values like ${VAR} references are kept.
    (
        "secret_value",
        re.compile(
            r"(?i)\b([A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|APIKEY|ACCESS_KEY)[A-Z0-9_]*)"
            r"(\s*[=:]\s*)(?!\$\{)(?!\[REDACTED)([\"']?)([^\s\"',;}]+)\3"
        ),
        r"\1\2\3[REDACTED:secret_value]\3",
    ),
    ("aws_secret", re.compile(r"(?i)(aws_secret_access_key\s*[=:]\s*)[A-Za-z0-9/+=]{40}"), r"\1[REDACTED:aws_secret]"),
    (
        "host",
        re.compile(
            r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
            r"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:internal|local|corp)\b"
        ),
        None,
    ),
]


def scrub_text(text: str) -> str:
    if not text:
        return text
    out = text
    for name, pattern, repl in _PATTERNS:
        out = pattern.sub(repl if repl else f"[REDACTED:{name}]", out)
    return out


def _walk(value: Any) -> Any:
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, list):
        return [_walk(v) for v in value]
    if isinstance(value, dict):
        return {k: _walk(v) for k, v in value.items()}
    return value


def scrub(record: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy with secrets replaced by [REDACTED:<type>]. Never raises."""
    try:
        clean = _walk(copy.deepcopy(record))
        ctx = clean.get("context") if isinstance(clean, dict) else None
        if isinstance(ctx, dict) and isinstance(ctx.get("env_keys"), list):
            ctx["env_keys"] = [str(k).split("=", 1)[0] for k in ctx["env_keys"]]
        return clean
    except Exception:  # never block the pipeline on the scrubber
        return {"scrub_error": True}
