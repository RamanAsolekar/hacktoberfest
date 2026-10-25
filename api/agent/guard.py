"""Injection guard: container logs are untrusted text and must never steer the LLM.

Runs before an incident is stored and again on every retrieved past incident.
"""

import copy
import re
from typing import Any

MAX_LINES = 50
MAX_LINE_CHARS = 500

_INJECTION = re.compile(
    r"(?i)(ignore (all |any |the )?(previous|prior|above) (instructions|prompts?)"
    r"|disregard (all |the )?(previous|above)"
    r"|you are now|act as (an?|the) "
    r"|system prompt|\bsystem:\s|\bassistant:\s|\buser:\s"
    r"|<\|[^|]*\|>|\[/?INST\]|BEGIN PROMPT|END PROMPT"
    r"|approve this fix|rm -rf /|curl [^ ]+ ?\| ?(ba)?sh)"
)


def _clean_line(line: str) -> str | None:
    line = str(line)[:MAX_LINE_CHARS]
    if _INJECTION.search(line):
        return None
    return line


def guard(record: dict[str, Any]) -> dict[str, Any]:
    """Cap log size and drop lines that look like instructions. Counts what was removed."""
    rec = copy.deepcopy(record)
    evidence = rec.setdefault("evidence", {})
    lines = evidence.get("log_lines") or []
    kept, flagged = [], 0
    for line in lines[-MAX_LINES:]:
        cleaned = _clean_line(line)
        if cleaned is None:
            flagged += 1
        else:
            kept.append(cleaned)
    evidence["log_lines"] = kept
    evidence["guard_flags"] = int(evidence.get("guard_flags") or 0) + flagged
    sym = rec.get("symptom") or {}
    if sym.get("error_message") and _INJECTION.search(sym["error_message"]):
        sym["error_message"] = "[removed: looked like an instruction]"
        evidence["guard_flags"] += 1
    for key in ("root_cause",):
        if isinstance(rec.get(key), str) and _INJECTION.search(rec[key]):
            rec[key] = "[removed: looked like an instruction]"
            evidence["guard_flags"] += 1
    return rec


def wrap_untrusted(text: str) -> str:
    return f"<untrusted_logs>\n{text}\n</untrusted_logs>"
