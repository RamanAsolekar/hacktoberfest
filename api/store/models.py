"""Tables. STAND-IN by Person 1; Person 4 owns this file (see their task doc)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.store.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id: Mapped[str | None] = mapped_column(String(200), unique=True)  # e.g. seed id
    status: Mapped[str] = mapped_column(String(32), default="detected")
    source: Mapped[str | None] = mapped_column(String(32))
    is_negative: Mapped[bool] = mapped_column(Boolean, default=False)
    compose_project: Mapped[str | None] = mapped_column(String(200))
    compose_service: Mapped[str | None] = mapped_column(String(200))
    image: Mapped[str | None] = mapped_column(String(300))
    image_tag: Mapped[str | None] = mapped_column(String(200))
    root_cause_key: Mapped[str | None] = mapped_column(String(120))
    workflow_id: Mapped[str | None] = mapped_column(String(200))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    symptom: Mapped[dict] = mapped_column(JSONB, default=dict)
    context: Mapped[dict] = mapped_column(JSONB, default=dict)
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict)
    root_cause: Mapped[str | None] = mapped_column(Text)
    fix: Mapped[dict] = mapped_column(JSONB, default=dict)
    match: Mapped[dict] = mapped_column(JSONB, default=dict)
    outcome: Mapped[dict] = mapped_column(JSONB, default=dict)

    __table_args__ = (
        Index("ix_incidents_status", "status"),
        Index("ix_incidents_detected_at", "detected_at"),
        Index("ix_incidents_service_cause", "compose_service", "root_cause_key"),
    )


class IncidentEvent(Base):
    __tablename__ = "incident_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"))
    step: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    message: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_incident_events_incident_at", "incident_id", "at"),)


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"))
    decision: Mapped[str] = mapped_column(String(16))  # approved | rejected
    by: Mapped[str | None] = mapped_column(String(200))
    channel: Mapped[str] = mapped_column(String(16), default="web")
    edited_fix: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_approvals_incident", "incident_id"),)
