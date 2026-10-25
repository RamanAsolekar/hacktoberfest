"""Temporal activities: every side effect of the resolve-then-learn flow lives here.

Activities are plain sync functions run in a thread pool by the worker. Each one writes a
timeline event so the UI (and the audit trail) shows every step.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from temporalio import activity

from api.agent import fixer, kepler, reasoning, retrieval
from api.agent.guard import guard
from api.store import repo, slack
from api.store.scrubber import scrub

log = logging.getLogger(__name__)


def _incident(incident_id: str) -> dict[str, Any]:
    inc = repo.get_incident(incident_id)
    if inc is None:
        raise ValueError(f"incident {incident_id} not found")
    return inc


@activity.defn
def set_status(incident_id: str, status: str, step: str, message: str) -> None:
    repo.update_incident(incident_id, {"status": status})
    repo.add_timeline_event(incident_id, step, status, message)


@activity.defn
def kepler_investigate(incident_id: str) -> bool:
    """Kepler (local SRE agent) investigates read-only; its findings join the evidence."""
    inc = _incident(incident_id)
    if not kepler.available():
        repo.add_timeline_event(incident_id, "kepler", "skipped", "Kepler is not installed on this host")
        return False
    activity.heartbeat("asking Kepler")
    answer = kepler.investigate(inc)
    if not answer:
        repo.add_timeline_event(incident_id, "kepler", "skipped", "Kepler gave no answer in time")
        return False
    evidence = {**(inc.get("evidence") or {}), "kepler_analysis": answer}
    repo.update_incident(incident_id, {"evidence": guard({"evidence": evidence})["evidence"]})
    repo.add_timeline_event(incident_id, "kepler", "done", "Kepler investigated: " + answer.splitlines()[0][:200])
    return True


@activity.defn
def retrieve(incident_id: str) -> dict[str, Any]:
    inc = _incident(incident_id)
    found = retrieval.search_similar(inc, k=5)
    top = found["candidates"][:3]
    summary = ", ".join(f"{c['incident'].get('root_cause_key') or c['incident']['id'][:8]} ({c['score']})" for c in top)
    repo.add_timeline_event(
        incident_id, "retrieve", "done",
        f"{len(found['candidates'])} similar past incidents" + (f": {summary}" if summary else "")
        + (f"; {len(found['avoid'])} failed fixes to avoid" if found["avoid"] else ""),
    )
    return found


@activity.defn
def reason(incident_id: str, found: dict[str, Any]) -> dict[str, Any]:
    inc = _incident(incident_id)
    stack = fixer.read_stack(inc.get("context") or {})
    result = reasoning.judge_match(inc, found.get("candidates") or [], found.get("avoid") or [], stack)
    source = result["source"]
    match = (
        {"matched_incident_id": result["matched_incident_id"], "confidence": result["confidence"],
         "explanation": result.get("explanation", ""), "score": next(
            (c["score"] for c in found.get("candidates") or [] if c["incident"]["id"] == result["matched_incident_id"]), None)}
        if source == "memory"
        else {"matched_incident_id": None, "confidence": result.get("confidence", 0.0),
              "explanation": result.get("explanation", ""), "memory_check": result.get("memory_check")}
    )
    fix = {**result["fix"], "held": None}
    repo.update_incident(
        incident_id,
        {"source": source, "match": match, "root_cause": result.get("root_cause"),
         "root_cause_key": result.get("root_cause_key"), "fix": fix,
         "outcome": {**(inc.get("outcome") or {}), "resolved_by_memory": source == "memory"}},
    )
    label = (f"Matched a remembered incident ({round(result['confidence'] * 100)}% confident)"
             if source == "memory" else "No trusted memory match; diagnosed from scratch")
    repo.add_timeline_event(incident_id, "reason", "done", f"{label}. Proposed {fix['type']}: {fix['content'][:160]}")
    return {"source": source, "fix": fix}


@activity.defn
def notify_approval(incident_id: str) -> None:
    repo.update_incident(incident_id, {"status": "awaiting_approval"})
    repo.add_timeline_event(incident_id, "approve", "awaiting_approval", "Waiting for a human to approve the fix")
    slack.notify_approval(_incident(incident_id))


@activity.defn
def apply_fix(incident_id: str, fix: dict[str, Any]) -> dict[str, Any]:
    inc = _incident(incident_id)
    repo.update_incident(incident_id, {"status": "applying", "fix": {**fix, "held": None}})
    try:
        applied = fixer.apply_fix(inc.get("context") or {}, fix)
    except fixer.FixRefused as exc:
        repo.add_timeline_event(incident_id, "apply", "refused", str(exc))
        return {"refused": str(exc)}
    repo.add_timeline_event(incident_id, "apply", "done", f"Applied {fix.get('type')} and restarted {applied['service']}")
    return applied


@activity.defn
def verify_health(incident_id: str, applied: dict[str, Any]) -> dict[str, Any]:
    repo.update_incident(incident_id, {"status": "verifying"})
    activity.heartbeat("checking health")
    result = fixer.verify_health(applied.get("project"), applied["service"])
    repo.add_timeline_event(incident_id, "verify", "done" if result["healthy"] else "failed", result["detail"])
    return result


@activity.defn
def rollback(incident_id: str, applied: dict[str, Any]) -> None:
    fixer.rollback(applied)
    repo.add_timeline_event(incident_id, "rollback", "done", "Restored the backup and restarted the service")


@activity.defn
def learn(incident_id: str, outcome: dict[str, Any]) -> dict[str, Any]:
    """Store the lesson: memory entry, scrub, guard, Postgres, Qdrant, and Kepler's wiki."""
    inc = _incident(incident_id)
    held = bool(outcome.get("held"))
    fix = {**(inc.get("fix") or {}), "held": held}
    detected = datetime.fromisoformat(inc["detected_at"])
    ttr = int((datetime.now(timezone.utc) - detected).total_seconds())
    status = "resolved" if held else "rolled_back"
    merged_outcome = {
        **(inc.get("outcome") or {}),
        "time_to_resolve_sec": ttr if held else None,
        "approved_by": outcome.get("approved_by"),
        "recurred": (inc.get("outcome") or {}).get("recurred", False),
        "resolved_by_memory": held and inc.get("source") == "memory",
    }
    draft = {**inc, "fix": fix, "outcome": merged_outcome, "is_negative": not held}
    entry = reasoning.write_memory_entry(draft)
    clean = guard(scrub({**draft, "root_cause": entry.get("root_cause") or inc.get("root_cause")}))
    stored = repo.update_incident(
        incident_id,
        {"status": status, "is_negative": not held, "fix": clean["fix"], "outcome": merged_outcome,
         "root_cause": clean.get("root_cause"), "evidence": clean.get("evidence")},
    )
    retrieval.index_incident(stored)
    matched = (inc.get("match") or {}).get("matched_incident_id")
    if held and matched:  # the remembered fix worked again: refresh its age
        repo.update_incident(matched, {"last_confirmed_at": datetime.now(timezone.utc).isoformat()})
        past = repo.get_incident(matched)
        if past:
            retrieval.index_incident(past)
    files = kepler.remember(stored, entry)
    msg = ("Lesson stored in memory" if held else "Failed fix stored as a negative example") + (
        f"; written to Kepler's wiki ({len(files)} file{'s' if len(files) != 1 else ''})" if files else "")
    repo.add_timeline_event(incident_id, "learn", status, msg)
    return {"status": status, "time_to_resolve_sec": ttr, "kepler_files": files}
