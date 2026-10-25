"""Temporal client helpers used by the API: start a workflow per incident and send approval decisions."""

from typing import Any

from temporalio.client import Client, WorkflowExecutionStatus

from api.agent.workflows import ResolveAndLearnWorkflow, workflow_id_for
from api.config import settings

_client: Client | None = None


async def client() -> Client:
    global _client
    if _client is None:
        _client = await Client.connect(settings.temporal_address)
    return _client


async def start_incident_workflow(incident_id: str) -> str:
    c = await client()
    wf_id = workflow_id_for(incident_id)
    await c.start_workflow(
        ResolveAndLearnWorkflow.run,
        args=[incident_id, settings.approval_timeout_min],
        id=wf_id,
        task_queue=settings.task_queue,
    )
    return wf_id


async def send_decision(incident_id: str, approved: bool, by: str, edited_fix: dict | None = None,
                        channel: str = "web", reason: str | None = None) -> None:
    """Signal the incident's workflow with a human decision (contract used by Person 4's routes)."""
    c = await client()
    handle = c.get_workflow_handle(workflow_id_for(incident_id))
    await handle.signal(
        ResolveAndLearnWorkflow.decision,
        {"approved": approved, "by": by, "edited_fix": edited_fix, "channel": channel, "reason": reason},
    )


async def health() -> dict[str, Any]:
    try:
        c = await client()
        running = 0
        async for wf in c.list_workflows('WorkflowType="ResolveAndLearnWorkflow"'):
            if wf.status == WorkflowExecutionStatus.RUNNING:
                running += 1
        return {"ok": True, "address": settings.temporal_address, "running_workflows": running}
    except Exception as exc:
        return {"ok": False, "address": settings.temporal_address, "error": str(exc)[:200]}
