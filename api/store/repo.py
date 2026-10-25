"""Incident storage functions used by the agent and the API.

STAND-IN by Person 1; Person 4 owns this file. Keep these signatures: the agent's
Temporal activities call them.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select

from api.store.db import session_scope
from api.store.models import Approval, Incident, IncidentEvent

TERMINAL = {"resolved", "rolled_back", "rejected", "expired"}
JSON_PARTS = ("symptom", "context", "evidence", "fix", "match", "outcome")


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _to_dict(row: Incident) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "external_id": row.external_id,
        "status": row.status,
        "source": row.source,
        "is_negative": row.is_negative,
        "root_cause_key": row.root_cause_key,
        "workflow_id": row.workflow_id,
        "detected_at": _iso(row.detected_at),
        "resolved_at": _iso(row.resolved_at),
        "last_confirmed_at": _iso(row.last_confirmed_at),
        "symptom": row.symptom or {},
        "context": row.context or {},
        "evidence": row.evidence or {},
        "root_cause": row.root_cause,
        "fix": row.fix or {},
        "match": row.match or {},
        "outcome": row.outcome or {},
    }


def _sync_columns(row: Incident) -> None:
    ctx = row.context or {}
    row.compose_project = ctx.get("compose_project")
    row.compose_service = ctx.get("compose_service")
    row.image = ctx.get("image")
    row.image_tag = ctx.get("image_tag")


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return None


def create_incident(data: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        row = Incident(
            id=uuid.UUID(data["id"]) if data.get("id") else uuid.uuid4(),
            external_id=data.get("external_id"),
            status=data.get("status", "detected"),
            source=data.get("source"),
            is_negative=bool(data.get("is_negative", False)),
            root_cause_key=data.get("root_cause_key"),
            workflow_id=data.get("workflow_id"),
            detected_at=_parse_dt(data.get("detected_at")) or now,
            resolved_at=_parse_dt(data.get("resolved_at")),
            root_cause=data.get("root_cause"),
            **{k: data.get(k) or {} for k in JSON_PARTS},
        )
        _sync_columns(row)
        s.add(row)
        s.flush()
        s.add(IncidentEvent(incident_id=row.id, step="detect", status=row.status, message="Failure recorded"))
        _mark_recurrence(s, row)
        return _to_dict(row)


def _mark_recurrence(s, row: Incident) -> None:
    """If the same service + root cause was resolved in the last 30 min, flag that earlier fix as recurred."""
    if not (row.compose_service and row.root_cause_key):
        return
    since = datetime.now(timezone.utc) - timedelta(minutes=30)
    earlier = s.scalars(
        select(Incident).where(
            Incident.id != row.id,
            Incident.compose_service == row.compose_service,
            Incident.root_cause_key == row.root_cause_key,
            Incident.status == "resolved",
            Incident.resolved_at >= since,
        )
    ).all()
    for e in earlier:
        e.outcome = {**(e.outcome or {}), "recurred": True}


def update_incident(incident_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(Incident, uuid.UUID(incident_id))
        if row is None:
            raise KeyError(incident_id)
        for key, value in patch.items():
            if key in JSON_PARTS:
                setattr(row, key, value or {})
            elif key in ("detected_at", "resolved_at", "last_confirmed_at"):
                setattr(row, key, _parse_dt(value))
            elif key in ("status", "source", "is_negative", "root_cause", "root_cause_key", "workflow_id"):
                setattr(row, key, value)
        if patch.get("status") in ("resolved", "rolled_back") and row.resolved_at is None:
            row.resolved_at = datetime.now(timezone.utc)
        _sync_columns(row)
        if "root_cause_key" in patch:
            _mark_recurrence(s, row)
        return _to_dict(row)


def get_incident(incident_id: str) -> dict[str, Any] | None:
    try:
        key = uuid.UUID(incident_id)
    except ValueError:
        return None
    with session_scope() as s:
        row = s.get(Incident, key)
        if row is None:
            return None
        data = _to_dict(row)
        events = s.scalars(
            select(IncidentEvent).where(IncidentEvent.incident_id == key).order_by(IncidentEvent.at, IncidentEvent.id)
        ).all()
        data["timeline"] = [
            {"step": e.step, "status": e.status, "message": e.message, "at": _iso(e.at)} for e in events
        ]
        matched_id = (data.get("match") or {}).get("matched_incident_id")
        if matched_id:
            try:
                past = s.get(Incident, uuid.UUID(matched_id))
            except ValueError:
                past = None
            if past is not None:
                data["match"]["matched_incident"] = _to_dict(past)
        return data


def get_by_external_id(external_id: str) -> dict[str, Any] | None:
    with session_scope() as s:
        row = s.scalars(select(Incident).where(Incident.external_id == external_id)).first()
        return _to_dict(row) if row else None


def add_timeline_event(incident_id: str, step: str, status: str, message: str = "") -> None:
    with session_scope() as s:
        s.add(IncidentEvent(incident_id=uuid.UUID(incident_id), step=step, status=status, message=message))


def list_incidents(status: str | None = None, source: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    with session_scope() as s:
        q = select(Incident).order_by(Incident.detected_at.desc()).limit(limit)
        if status:
            q = q.where(Incident.status == status)
        if source:
            q = q.where(Incident.source == source)
        return [_to_dict(r) for r in s.scalars(q).all()]


def find_open_incident(compose_project: str | None, compose_service: str | None) -> dict[str, Any] | None:
    """An incident for the same service that is still being worked on (used to de-duplicate crash loops)."""
    if not compose_service:
        return None
    with session_scope() as s:
        row = s.scalars(
            select(Incident)
            .where(
                Incident.compose_project == compose_project,
                Incident.compose_service == compose_service,
                Incident.status.not_in(TERMINAL),
                Incident.source.is_distinct_from("seed"),
            )
            .order_by(Incident.detected_at.desc())
        ).first()
        return _to_dict(row) if row else None


def record_approval(
    incident_id: str,
    decision: str,
    by: str | None,
    channel: str = "web",
    edited_fix: dict | None = None,
    reason: str | None = None,
) -> None:
    with session_scope() as s:
        s.add(
            Approval(
                incident_id=uuid.UUID(incident_id),
                decision=decision,
                by=by,
                channel=channel or "web",
                edited_fix=edited_fix,
                reason=reason,
            )
        )


def all_incidents() -> list[dict[str, Any]]:
    with session_scope() as s:
        return [_to_dict(r) for r in s.scalars(select(Incident).order_by(Incident.detected_at)).all()]


def approval_counts() -> dict[str, int]:
    with session_scope() as s:
        rows = s.scalars(select(Approval.decision)).all()
        return {"approved": rows.count("approved"), "rejected": rows.count("rejected")}


def delete_all(keep_seed: bool = True) -> int:
    """Demo reset. Removes incidents (and their events/approvals by cascade)."""
    with session_scope() as s:
        q = delete(Incident)
        if keep_seed:
            q = q.where(Incident.source.not_in(["seed", "nudgebee-seed"]))
        return s.execute(q).rowcount or 0
