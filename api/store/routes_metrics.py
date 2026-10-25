"""Metrics route: the four KPIs from the problem statement. STAND-IN by Person 1; Person 4 owns it."""

import asyncio
from collections import Counter
from datetime import datetime
from typing import Any

from fastapi import APIRouter

from api.store import repo

router = APIRouter()
MEMORY_STATUSES = {"resolved", "rolled_back"}
SEED_SOURCES = {"seed", "nudgebee-seed"}


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def compute_metrics() -> dict[str, Any]:
    incidents = repo.all_incidents()
    live = [i for i in incidents if i.get("source") not in SEED_SOURCES]
    resolved = [i for i in live if i["status"] == "resolved"]

    def ttr(src: str) -> list[float]:
        return [float(i["outcome"]["time_to_resolve_sec"]) for i in resolved
                if i.get("source") == src and (i.get("outcome") or {}).get("time_to_resolve_sec") is not None]

    counts = repo.approval_counts()
    decided = counts["approved"] + counts["rejected"]
    memory_rows = [i for i in incidents if i.get("source") in SEED_SOURCES or i["status"] in MEMORY_STATUSES]

    buckets: Counter = Counter()
    for i in memory_rows:
        at = datetime.fromisoformat(i.get("resolved_at") or i["detected_at"])
        buckets[at.replace(minute=at.minute - at.minute % 5, second=0, microsecond=0)] += 1
    growth, total = [], 0
    for at in sorted(buckets):
        total += buckets[at]
        growth.append({"at": at.isoformat(), "total": total})

    return {
        "first_time_avg_ttr_sec": _avg(ttr("fresh")),
        "repeat_avg_ttr_sec": _avg(ttr("memory")),
        "pct_resolved_by_memory": round(len([i for i in resolved if i.get("source") == "memory"]) / len(resolved), 3)
        if resolved else None,
        "suggestion_accuracy": round(counts["approved"] / decided, 3) if decided else None,
        "recurred_count": len([i for i in incidents if (i.get("outcome") or {}).get("recurred")]),
        "memory_total": len(memory_rows),
        "memory_growth": growth,
        "incidents_total": len(live),
    }


@router.get("/api/metrics")
async def metrics() -> dict[str, Any]:
    return await asyncio.to_thread(compute_metrics)
