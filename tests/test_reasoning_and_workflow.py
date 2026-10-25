"""Reasoning fallback rules, retrieval scoring, and the Temporal workflow paths (with fake activities)."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from api.agent import reasoning, retrieval
from api.agent.workflows import ResolveAndLearnWorkflow

NEW = {"id": "new", "symptom": {"error_message": "SASL: client password must be a string"}, "context": {}, "evidence": {}}
PAST = {"id": "past", "symptom": {"error_message": "fe_sendauth: no password supplied"}, "context": {}, "evidence": {},
        "root_cause": "missing password", "fix": {"type": "compose_patch", "content": "x"}}


def test_high_confidence_match_uses_memory(monkeypatch):
    monkeypatch.setattr(reasoning.llm, "chat_json", lambda *a, **k: {
        "matched_incident_id": "past", "confidence": 0.9, "explanation": "same cause", "root_cause": "r",
        "root_cause_key": "Missing DB Password!", "fix": {"type": "compose_patch", "content": "services.api.environment.A: ${B}"}})
    out = reasoning.judge_match(NEW, [{"incident": PAST, "score": 0.7}], [])
    assert out["source"] == "memory" and out["matched_incident_id"] == "past"
    assert out["root_cause_key"] == "missing-db-password"


def test_low_confidence_falls_back_to_fresh(monkeypatch):
    replies = iter([
        {"matched_incident_id": "past", "confidence": 0.4, "explanation": "unsure", "fix": {}},
        {"root_cause": "fresh cause", "root_cause_key": "x", "confidence": 0.7, "fix": {"type": "command", "content": "docker compose restart api"}},
    ])
    monkeypatch.setattr(reasoning.llm, "chat_json", lambda *a, **k: next(replies))
    out = reasoning.judge_match(NEW, [{"incident": PAST, "score": 0.7}], [])
    assert out["source"] == "fresh" and out["matched_incident_id"] is None
    assert out["memory_check"]["confidence"] == 0.4


def test_hallucinated_match_id_is_ignored(monkeypatch):
    replies = iter([
        {"matched_incident_id": "made-up", "confidence": 0.95, "fix": {}},
        {"root_cause": "r", "confidence": 0.5, "fix": {}},
    ])
    monkeypatch.setattr(reasoning.llm, "chat_json", lambda *a, **k: next(replies))
    assert reasoning.judge_match(NEW, [{"incident": PAST, "score": 0.7}], [])["source"] == "fresh"


def test_score_decays_with_age_and_rewards_same_tag():
    now = datetime.now(timezone.utc)
    fresh = retrieval.score(0.8, {"created_at": now.isoformat(), "image_tag": "1.0"}, "1.0")
    old = retrieval.score(0.8, {"created_at": (now - timedelta(days=180)).isoformat()}, "1.0")
    assert fresh == pytest.approx(0.85, abs=0.01)
    assert old == pytest.approx(0.4, abs=0.01)  # decay is floored at 0.5


# ---------- workflow paths with fake activities ----------

CALLS: list[str] = []
HEALTHY = {"value": True}


def _fake(name, result=None):
    @activity.defn(name=name)
    def fn(*args):
        CALLS.append(name if name != "set_status" else f"status:{args[1]}")
        return result() if callable(result) else result
    return fn


FAKES = [
    _fake("set_status"), _fake("kepler_investigate", True), _fake("retrieve", {"candidates": [], "avoid": []}),
    _fake("reason", {"source": "fresh", "fix": {"type": "command", "content": "docker compose restart api"}}),
    _fake("notify_approval"), _fake("apply_fix", {"service": "api", "project": "p", "compose_file": "c"}),
    _fake("verify_health", lambda: {"healthy": HEALTHY["value"], "detail": "x"}), _fake("rollback"),
    _fake("learn", lambda: {"status": "resolved" if HEALTHY["value"] else "rolled_back", "time_to_resolve_sec": 5}),
]


async def _run(decision, healthy=True):
    CALLS.clear()
    HEALTHY["value"] = healthy
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(env.client, task_queue="t", workflows=[ResolveAndLearnWorkflow], activities=FAKES,
                          activity_executor=__import__("concurrent.futures").futures.ThreadPoolExecutor(4)):
            handle = await env.client.start_workflow(ResolveAndLearnWorkflow.run, args=["inc-1", 15],
                                                     id=f"wf-{uuid.uuid4()}", task_queue="t")
            if decision is not None:
                while "notify_approval" not in CALLS:
                    await env.sleep(1)
                await handle.signal(ResolveAndLearnWorkflow.decision, decision)
            return await handle.result()


@pytest.mark.asyncio
async def test_workflow_approve_and_verify():
    result = await _run({"approved": True, "by": "raman"})
    assert result["status"] == "resolved"
    assert CALLS.index("apply_fix") < CALLS.index("verify_health") < CALLS.index("learn")
    assert "rollback" not in CALLS


@pytest.mark.asyncio
async def test_workflow_rolls_back_and_still_learns():
    result = await _run({"approved": True, "by": "raman"}, healthy=False)
    assert result["status"] == "rolled_back"
    assert CALLS.index("rollback") < CALLS.index("learn")


@pytest.mark.asyncio
async def test_workflow_reject_changes_nothing():
    result = await _run({"approved": False, "by": "raman"})
    assert result["status"] == "rejected"
    assert "apply_fix" not in CALLS and "learn" not in CALLS


@pytest.mark.asyncio
async def test_workflow_expires_without_decision():
    result = await _run(None)
    assert result["status"] == "expired"
    assert "apply_fix" not in CALLS
