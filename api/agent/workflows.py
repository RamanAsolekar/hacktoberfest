"""ResolveAndLearnWorkflow: detect -> Kepler -> retrieve -> reason -> approve -> apply -> verify -> learn.

Temporal makes this durable: if the worker crashes mid-fix, the workflow resumes where it stopped,
and the event history is a full audit trail. A failed health check rolls the change back and the
failed fix is still learned, as a negative example.
"""

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from api.agent import activities as act

TASK_QUEUE = "dockergpt-memory"
_RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=2))
_NO_RETRY = RetryPolicy(maximum_attempts=1)


def workflow_id_for(incident_id: str) -> str:
    return f"incident-{incident_id}"


@workflow.defn
class ResolveAndLearnWorkflow:
    def __init__(self) -> None:
        self._decision: dict[str, Any] | None = None
        self._step = "detected"

    @workflow.signal
    def decision(self, decision: dict[str, Any]) -> None:
        """{approved: bool, by: str, edited_fix: dict | None, channel: str}"""
        if self._decision is None:
            self._decision = decision

    @workflow.query
    def status(self) -> str:
        return self._step

    async def _run(self, fn, *args, seconds: int = 60, retry: RetryPolicy = _RETRY, heartbeat: int | None = None):
        return await workflow.execute_activity(
            fn, args=list(args), start_to_close_timeout=timedelta(seconds=seconds), retry_policy=retry,
            heartbeat_timeout=timedelta(seconds=heartbeat) if heartbeat else None,
        )

    @workflow.run
    async def run(self, incident_id: str, approval_timeout_min: int = 15) -> dict[str, Any]:
        self._step = "kepler"
        await self._run(act.kepler_investigate, incident_id, seconds=150, retry=_NO_RETRY)

        self._step = "retrieving"
        await self._run(act.set_status, incident_id, "retrieving", "retrieve", "Searching memory for similar failures")
        found = await self._run(act.retrieve, incident_id, seconds=90)

        self._step = "reasoning"
        await self._run(act.set_status, incident_id, "reasoning", "reason", "Comparing with past incidents")
        proposal = await self._run(act.reason, incident_id, found, seconds=240)

        self._step = "awaiting_approval"
        await self._run(act.notify_approval, incident_id)
        try:
            await workflow.wait_condition(lambda: self._decision is not None,
                                          timeout=timedelta(minutes=approval_timeout_min))
        except TimeoutError:
            await self._run(act.set_status, incident_id, "expired", "approve", "No decision in time; nothing changed")
            return {"status": "expired"}

        decision = self._decision or {}
        by = decision.get("by") or "unknown"
        if not decision.get("approved"):
            self._step = "rejected"
            await self._run(act.set_status, incident_id, "rejected", "approve",
                            f"Rejected by {by}" + (f": {decision.get('reason')}" if decision.get("reason") else ""))
            return {"status": "rejected"}

        fix = decision.get("edited_fix") or proposal["fix"]
        await self._run(act.set_status, incident_id, "applying", "approve",
                        f"Approved by {by} via {decision.get('channel') or 'web'}"
                        + (" (fix edited)" if decision.get("edited_fix") else ""))

        self._step = "applying"
        applied = await self._run(act.apply_fix, incident_id, fix, seconds=240, retry=_NO_RETRY)
        if applied.get("refused"):
            await self._run(act.set_status, incident_id, "rejected", "apply",
                            "The fix was refused by safety checks; nothing was changed")
            return {"status": "refused", "reason": applied["refused"]}

        self._step = "verifying"
        health = await self._run(act.verify_health, incident_id, applied, seconds=120, retry=_NO_RETRY, heartbeat=60)
        if not health.get("healthy"):
            self._step = "rolling_back"
            await self._run(act.rollback, incident_id, applied, seconds=180)

        self._step = "learning"
        result = await self._run(act.learn, incident_id, {"held": bool(health.get("healthy")), "approved_by": by},
                                 seconds=180)
        self._step = result["status"]
        return result
