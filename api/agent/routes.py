"""Person 1's HTTP routes: failure intake, memory search, NudgeBee import, agent status, demo reset."""

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.agent import kepler, nudgebee, retrieval, temporal_client
from api.agent import llm
from api.agent.guard import guard
from api.config import settings
from api.store import repo
from api.store.scrubber import scrub

router = APIRouter()


class FailureIn(BaseModel):
    symptom: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)


@router.post("/api/failures")
async def report_failure(body: FailureIn) -> dict[str, Any]:
    """Called by the watcher. Creates the incident and starts its Temporal workflow."""
    record = guard(scrub(body.model_dump()))
    ctx = record.get("context") or {}
    open_inc = await asyncio.to_thread(repo.find_open_incident, ctx.get("compose_project"), ctx.get("compose_service"))
    if open_inc:
        sym = {**open_inc["symptom"], "restart_count": (record.get("symptom") or {}).get("restart_count")}
        await asyncio.to_thread(repo.update_incident, open_inc["id"], {"symptom": sym})
        await asyncio.to_thread(repo.add_timeline_event, open_inc["id"], "detect", open_inc["status"],
                                "Container crashed again; already being handled")
        return {"incident_id": open_inc["id"], "workflow_id": open_inc["workflow_id"], "deduplicated": True}
    inc = await asyncio.to_thread(repo.create_incident, {**record, "status": "detected", "outcome": {"recurred": False}})
    wf_id = await temporal_client.start_incident_workflow(inc["id"])
    await asyncio.to_thread(repo.update_incident, inc["id"], {"workflow_id": wf_id})
    return {"incident_id": inc["id"], "workflow_id": wf_id, "deduplicated": False}


@router.get("/api/memory/search")
async def search_memory(q: str, include_negative: bool = False, k: int = 10) -> list[dict[str, Any]]:
    try:
        hits = await asyncio.to_thread(retrieval.search_text, q, k, include_negative)
    except llm.LLMNotConfigured as exc:
        raise HTTPException(503, str(exc))
    out = []
    for h in hits:
        inc = await asyncio.to_thread(repo.get_incident, h["incident_id"]) if h["incident_id"] else None
        if inc:
            inc.pop("timeline", None)
            out.append({**inc, "score": round(h["raw_score"], 4)})
    return out


@router.post("/api/nudgebee/import")
async def import_from_nudgebee(limit: int = 200) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(nudgebee.import_rca, limit)
    except Exception as exc:
        raise HTTPException(502, f"NudgeBee import failed: {exc}")


@router.get("/api/agent/status")
async def agent_status() -> dict[str, Any]:
    """One place to see that every technology is wired up."""
    llm_h, nb = await asyncio.gather(asyncio.to_thread(llm.health), asyncio.to_thread(nudgebee.status))
    try:
        qd = retrieval.client()
        points = qd.count(retrieval.COLLECTION).count if qd.collection_exists(retrieval.COLLECTION) else 0
        qdrant = {"ok": True, "url": settings.qdrant_url, "memory_points": points}
    except Exception as exc:
        qdrant = {"ok": False, "error": str(exc)[:200]}
    return {
        "jarvislabs_llm": llm_h["llm"],
        "jarvislabs_embed": llm_h["embed"],
        "temporal": await temporal_client.health(),
        "qdrant": qdrant,
        "kepler": {"ok": kepler.available(), "cli": settings.kepler_cli, "wiki": settings.kepler_home},
        "nudgebee": nb,
        "confidence_threshold": settings.confidence_threshold,
    }


@router.post("/api/demo/reset")
async def demo_reset(keep_seed: bool = True) -> dict[str, Any]:
    """Clear demo incidents (keeps seeded memory by default) so a demo starts clean."""
    before = await asyncio.to_thread(repo.all_incidents)
    removed_ids = [i["id"] for i in before if not keep_seed or i.get("source") not in ("seed", "nudgebee-seed")]
    n = await asyncio.to_thread(repo.delete_all, keep_seed)
    await asyncio.to_thread(retrieval.delete_points, removed_ids)
    return {"removed": n}
