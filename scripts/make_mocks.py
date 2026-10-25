"""Regenerate contracts/mock/*.json (example data for the frontend). Run: python -m scripts.make_mocks"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "contracts" / "mock"
T0 = datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc)


def ts(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


A_ID = "1a2b3c4d-0000-4000-8000-00000000000a"
B_ID = "6f1c2a9e-0000-4000-8000-00000000000b"

stack_a = {
    "id": A_ID, "external_id": None, "status": "resolved", "source": "fresh", "is_negative": False,
    "root_cause_key": "missing-db-password", "workflow_id": f"incident-{A_ID}",
    "detected_at": ts(0), "resolved_at": ts(6.9), "last_confirmed_at": ts(14),
    "symptom": {"error_message": "FATAL: database connection failed: connection failed: connection to server at \"[REDACTED:host]\", port 5432 failed: fe_sendauth: no password supplied",
                "exit_code": 1, "restart_count": 3, "restart_pattern": "crashed; 4 exits in the last 5 min", "oom_killed": False},
    "context": {"container": "stack-a-app-1", "image": "demo-orders-service", "image_tag": "1.0", "base_image": "python:3.11-slim",
                "compose_project": "stack-a", "compose_service": "app", "compose_dir": "/demo/stack-a", "compose_files": [],
                "mem_limit": None, "cpu_limit": None, "restart_policy": "on-failure", "env_keys": ["DB_HOST", "DB_NAME", "DB_USER", "PATH"]},
    "evidence": {"log_lines": ["orders-service starting, connecting to db",
                               "FATAL: database connection failed: connection failed: connection to server at \"[REDACTED:host]\", port 5432 failed: fe_sendauth: no password supplied"],
                 "resource_stats": {"cpu_pct": 0.4, "mem_mb": 21.5, "power_watts": None},
                 "kepler_analysis": "Observed: stack-a-app-1 exits with code 1 every few seconds.\nLikely cause (high): the app service has no POSTGRES_PASSWORD, so libpq sends no password.\nNext step: pass the db password variable to the app service.",
                 "guard_flags": 0, "trigger": "crashed"},
    "root_cause": "The app service is missing the database password variable, so the Postgres client connects with no password.",
    "fix": {"type": "compose_patch", "content": "services.app.environment.POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}",
            "rationale": "Pass the db's password variable to the app service.", "held": True},
    "match": {"matched_incident_id": None, "confidence": 0.2, "explanation": "No remembered incident shares this root cause.",
              "memory_check": {"confidence": 0.2, "matched_incident_id": None}},
    "outcome": {"time_to_resolve_sec": 412, "approved_by": "raman", "recurred": False, "resolved_by_memory": False},
}

stack_b = {
    "id": B_ID, "external_id": None, "status": "awaiting_approval", "source": "memory", "is_negative": False,
    "root_cause_key": "missing-db-password", "workflow_id": f"incident-{B_ID}",
    "detected_at": ts(14), "resolved_at": None, "last_confirmed_at": None,
    "symptom": {"error_message": "Error: SASL: SCRAM-SERVER-FIRST-MESSAGE: client password must be a string",
                "exit_code": 1, "restart_count": 4, "restart_pattern": "crashed; 5 exits in the last 5 min", "oom_killed": False},
    "context": {"container": "stack-b-api-1", "image": "demo-inventory-api", "image_tag": "1.0", "base_image": "node:20-alpine",
                "compose_project": "stack-b", "compose_service": "api", "compose_dir": "/demo/stack-b", "compose_files": [],
                "mem_limit": None, "cpu_limit": None, "restart_policy": "on-failure", "env_keys": ["DB_HOST", "DB_NAME", "DB_USER", "NODE_VERSION", "PATH"]},
    "evidence": {"log_lines": ["inventory-api starting, connecting to db",
                               "Error: SASL: SCRAM-SERVER-FIRST-MESSAGE: client password must be a string"],
                 "resource_stats": {"cpu_pct": 0.3, "mem_mb": 38.2, "power_watts": None},
                 "kepler_analysis": "Observed: stack-b-api-1 restarts with exit 1.\nLikely cause (high): DB_PASSWORD is not set on the api service.\nNext step: set DB_PASSWORD from the db's POSTGRES_PASSWORD.",
                 "guard_flags": 0, "trigger": "crashed"},
    "root_cause": "The api service has no DB_PASSWORD, so the pg client sends an undefined password.",
    "fix": {"type": "compose_patch", "content": "services.api.environment.DB_PASSWORD: ${POSTGRES_PASSWORD}",
            "rationale": "Same fix as the remembered stack-a incident, adapted to this stack's variable name.", "held": None},
    "match": {"matched_incident_id": A_ID, "confidence": 0.88, "score": 0.71,
              "explanation": "Both are app containers that crash right after start because the database password variable is missing from the app service; the different error text comes from different client libraries (libpq vs node-postgres)."},
    "outcome": {"time_to_resolve_sec": None, "approved_by": None, "recurred": False, "resolved_by_memory": True},
}


def small(id_, status, source, err, svc, key, minutes, negative=False, ttr=None):
    return {"id": id_, "external_id": None, "status": status, "source": source, "is_negative": negative, "root_cause_key": key,
            "workflow_id": f"incident-{id_}", "detected_at": ts(minutes), "resolved_at": ts(minutes + 3) if ttr else None,
            "last_confirmed_at": None,
            "symptom": {"error_message": err, "exit_code": 137 if "OOM" in err else 1, "restart_count": 2, "restart_pattern": "crashed"},
            "context": {"image": f"demo-{svc}", "image_tag": "1.0", "compose_project": "shop", "compose_service": svc, "env_keys": []},
            "evidence": {"log_lines": [err], "resource_stats": {}, "guard_flags": 0, "trigger": "crashed"},
            "root_cause": None, "fix": {}, "match": {"matched_incident_id": None, "confidence": 0.0, "explanation": ""},
            "outcome": {"time_to_resolve_sec": ttr, "approved_by": "raman" if ttr else None, "recurred": False, "resolved_by_memory": source == "memory"}}


others = [
    small("aaaa0000-0000-4000-8000-000000000001", "reasoning", None, "KeyError: 'REDIS_URL'", "sessions", None, 20),
    small("aaaa0000-0000-4000-8000-000000000002", "rolled_back", "fresh", "container killed: OOMKilled (exit 137)", "worker",
          "oom-killed-memory-limit", 8, negative=True),
    small("aaaa0000-0000-4000-8000-000000000003", "rejected", "memory", "exec /usr/local/bin/app: exec format error", "proxy",
          "wrong-cpu-architecture", 4),
    small("aaaa0000-0000-4000-8000-000000000004", "verifying", "memory", "Bind for 0.0.0.0:8080 failed: port is already allocated", "web",
          "host-port-in-use", 18),
]

incidents = sorted([stack_b, stack_a, *others], key=lambda i: i["detected_at"], reverse=True)

timeline = [
    ("detect", "detected", "Failure recorded", 14.0),
    ("kepler", "done", "Kepler investigated: Observed: stack-b-api-1 restarts with exit 1.", 14.4),
    ("retrieve", "retrieving", "Searching memory for similar failures", 14.45),
    ("retrieve", "done", "3 similar past incidents: missing-db-password (0.71), starts-before-db-ready (0.52), missing-redis-url (0.49)", 14.5),
    ("reason", "reasoning", "Comparing with past incidents", 14.5),
    ("reason", "done", "Matched a remembered incident (88% confident). Proposed compose_patch: services.api.environment.DB_PASSWORD: ${POSTGRES_PASSWORD}", 14.6),
    ("approve", "awaiting_approval", "Waiting for a human to approve the fix", 14.6),
]
detail = {**stack_b, "match": {**stack_b["match"], "matched_incident": stack_a},
          "timeline": [{"step": s, "status": st, "message": m, "at": ts(t)} for s, st, m, t in timeline]}

metrics = {"first_time_avg_ttr_sec": 412.0, "repeat_avg_ttr_sec": 38.0, "pct_resolved_by_memory": 0.5,
           "suggestion_accuracy": 0.75, "recurred_count": 0, "memory_total": 9, "incidents_total": 6,
           "memory_growth": [{"at": ts(-30), "total": 7}, {"at": ts(5), "total": 8}, {"at": ts(10), "total": 9}]}

search = [{**stack_a, "score": 0.81}, {**others[1], "score": 0.43}]

OUT.mkdir(parents=True, exist_ok=True)
for name, data in [("incidents", incidents), ("incident_detail", detail), ("metrics", metrics), ("search", search)]:
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    print("wrote", OUT / f"{name}.json")
