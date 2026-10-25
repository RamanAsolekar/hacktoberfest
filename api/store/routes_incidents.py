"""Incident routes. STAND-IN by Person 1; Person 4 owns this file (contract in their task doc)."""

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.agent.temporal_client import send_decision
from api.store import repo

router = APIRouter()


class ApproveIn(BaseModel):
    approved_by: str = "unknown"
    edited_fix: dict | None = None
    channel: str = "web"


class RejectIn(BaseModel):
    rejected_by: str = "unknown"
    reason: str | None = None
    channel: str = "web"


@router.get("/api/incidents")
async def list_incidents(status: str | None = None, source: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    return await asyncio.to_thread(repo.list_incidents, status, source, limit)


@router.get("/api/incidents/{incident_id}")
async def get_incident(incident_id: str) -> dict[str, Any]:
    inc = await asyncio.to_thread(repo.get_incident, incident_id)
    if inc is None:
        raise HTTPException(404, "incident not found")
    return inc


async def _awaiting(incident_id: str) -> dict[str, Any]:
    inc = await asyncio.to_thread(repo.get_incident, incident_id)
    if inc is None:
        raise HTTPException(404, "incident not found")
    if inc["status"] != "awaiting_approval":
        raise HTTPException(409, f"incident is {inc['status']}, not awaiting approval")
    return inc


@router.post("/api/incidents/{incident_id}/approve")
async def approve(incident_id: str, body: ApproveIn) -> dict[str, Any]:
    await _awaiting(incident_id)
    await asyncio.to_thread(repo.record_approval, incident_id, "approved", body.approved_by, body.channel, body.edited_fix)
    await send_decision(incident_id, True, body.approved_by, body.edited_fix, body.channel)
    return {"ok": True}


@router.post("/api/incidents/{incident_id}/reject")
async def reject(incident_id: str, body: RejectIn) -> dict[str, Any]:
    await _awaiting(incident_id)
    await asyncio.to_thread(repo.record_approval, incident_id, "rejected", body.rejected_by, body.channel, None, body.reason)
    await send_decision(incident_id, False, body.rejected_by, None, body.channel, body.reason)
    return {"ok": True}


@router.post("/api/memory/seed")
async def seed_memory() -> dict[str, Any]:
    from seed.load import load_library

    return {"loaded": await asyncio.to_thread(load_library)}
