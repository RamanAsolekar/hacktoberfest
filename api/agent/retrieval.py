"""Memory retrieval: Qdrant collection `incident_memory`, embeddings from JarvisLabs."""

import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from api.agent import llm
from api.agent.guard import guard
from api.config import settings

COLLECTION = "incident_memory"
_NS = uuid.UUID("6b1d8a52-5c1f-4c55-9a4f-3f0d2f8e9a10")


@lru_cache(maxsize=1)
def client() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url, timeout=30)


def point_id(incident_id: str) -> str:
    try:
        return str(uuid.UUID(incident_id))
    except ValueError:
        return str(uuid.uuid5(_NS, incident_id))


def ensure_collection(size: int) -> None:
    c = client()
    if not c.collection_exists(COLLECTION):
        c.create_collection(COLLECTION, vectors_config=qm.VectorParams(size=size, distance=qm.Distance.COSINE))


def build_embedding_text(incident: dict[str, Any]) -> str:
    """What a failure 'looks like'. IDs and timestamps are left out on purpose: they add noise."""
    sym = incident.get("symptom") or {}
    ctx = incident.get("context") or {}
    ev = incident.get("evidence") or {}
    parts = [
        f"error: {sym.get('error_message', '')}",
        f"exit code: {sym.get('exit_code', '')}",
        f"restart pattern: {sym.get('restart_pattern', '')}",
        f"image: {ctx.get('image', '')} base: {ctx.get('base_image', '')}",
        f"service: {ctx.get('compose_service', '')}",
        f"env keys present: {', '.join(ctx.get('env_keys') or [])}",
        "logs:\n" + "\n".join((ev.get("log_lines") or [])[-10:]),
    ]
    if incident.get("root_cause"):
        parts.append(f"root cause: {incident['root_cause']}")
    return "\n".join(parts)


def index_incident(incident: dict[str, Any]) -> None:
    """Embed an incident and upsert it into memory."""
    vec = llm.embed([build_embedding_text(incident)])[0]
    ensure_collection(len(vec))
    ctx = incident.get("context") or {}
    client().upsert(
        COLLECTION,
        points=[
            qm.PointStruct(
                id=point_id(incident["id"]),
                vector=vec,
                payload={
                    "incident_id": incident["id"],
                    "is_negative": bool(incident.get("is_negative")),
                    "image": ctx.get("image"),
                    "image_tag": ctx.get("image_tag"),
                    "compose_service": ctx.get("compose_service"),
                    "created_at": incident.get("resolved_at") or incident.get("detected_at"),
                    "last_confirmed_at": incident.get("last_confirmed_at"),
                    "root_cause": incident.get("root_cause"),
                    "root_cause_key": incident.get("root_cause_key"),
                    "fix_type": (incident.get("fix") or {}).get("type"),
                    "source": incident.get("source"),
                },
            )
        ],
    )


def _age_days(iso: str | None) -> float:
    if not iso:
        return 0.0
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)


def score(raw: float, payload: dict[str, Any], image_tag: str | None) -> float:
    """Cosine score x age decay (half-life 90 days, floor 0.5) + small bonus for the same image tag."""
    age = _age_days(payload.get("last_confirmed_at") or payload.get("created_at"))
    decay = max(0.5, 0.5 ** (age / 90))
    bonus = 0.05 if image_tag and payload.get("image_tag") == image_tag else 0.0
    return round(raw * decay + bonus, 4)


def search_text(text: str, k: int = 5, include_negative: bool = True) -> list[dict[str, Any]]:
    c = client()
    if not c.collection_exists(COLLECTION):
        return []
    vec = llm.embed([text])[0]
    flt = None
    if not include_negative:
        flt = qm.Filter(must_not=[qm.FieldCondition(key="is_negative", match=qm.MatchValue(value=True))])
    hits = c.query_points(COLLECTION, query=vec, limit=k, query_filter=flt, with_payload=True).points
    return [{"incident_id": h.payload.get("incident_id"), "raw_score": h.score, "payload": h.payload} for h in hits]


def search_similar(incident: dict[str, Any], k: int = 5) -> dict[str, list[dict[str, Any]]]:
    """Closest past incidents. Positives become candidates; failed fixes become `avoid`."""
    from api.store import repo  # late import: store is owned by Person 4

    image_tag = (incident.get("context") or {}).get("image_tag")
    hits = search_text(build_embedding_text(incident), k=k + 3)
    candidates, avoid = [], []
    for h in hits:
        if h["incident_id"] == incident.get("id"):
            continue
        past = repo.get_incident(h["incident_id"]) if h["incident_id"] else None
        if past is None:
            continue
        past = guard(past)  # retrieved memory is untrusted too
        item = {
            "incident": past,
            "raw_score": round(h["raw_score"], 4),
            "score": score(h["raw_score"], h["payload"], image_tag),
        }
        (avoid if past.get("is_negative") else candidates).append(item)
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return {"candidates": candidates[:k], "avoid": avoid[:3]}


def delete_points(incident_ids: list[str]) -> None:
    if incident_ids and client().collection_exists(COLLECTION):
        client().delete(COLLECTION, points_selector=qm.PointIdsList(points=[point_id(i) for i in incident_ids]))


def reset_collection() -> None:
    if client().collection_exists(COLLECTION):
        client().delete_collection(COLLECTION)
