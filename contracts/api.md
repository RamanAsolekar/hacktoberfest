# DockerGPT Memory API

Base URL locally: `http://localhost:8000`. Interactive docs: `http://localhost:8000/docs`.
Record shape: [incident.schema.json](incident.schema.json). Mock responses: [mock/](mock/).

| Method + path | Owner | Request | Response |
| --- | --- | --- | --- |
| `GET /health` | P1 | – | `{"ok": true}` |
| `POST /api/failures` | P1 | `{symptom, context, evidence}` from the watcher | `{incident_id, workflow_id, deduplicated}`. A crash of a service that already has an open incident is attached to it (`deduplicated: true`) |
| `GET /api/memory/search?q=&include_negative=false&k=10` | P1 | – | Incidents with `score` (0–1) |
| `GET /api/agent/status` | P1 | – | Health of every technology: JarvisLabs LLM + embeddings, Temporal, Qdrant, Kepler, NudgeBee |
| `POST /api/nudgebee/import?limit=200` | P1 | – | `{found, imported, skipped, already}` |
| `POST /api/demo/reset?keep_seed=true` | P1 | – | `{removed}` |
| `GET /api/incidents?status=&source=&limit=50` | P4 | – | Incidents, newest first |
| `GET /api/incidents/{id}` | P4 | – | Incident + `timeline[]` + `match.matched_incident` |
| `POST /api/incidents/{id}/approve` | P4 | `{approved_by, edited_fix?, channel?}` | `{ok: true}`; 409 unless `awaiting_approval` |
| `POST /api/incidents/{id}/reject` | P4 | `{rejected_by, reason?, channel?}` | `{ok: true}`; 409 unless `awaiting_approval` |
| `POST /api/memory/seed` | P4 | – | `{loaded}` (safe to run twice) |
| `GET /api/metrics` | P4 | – | See below |

## Metrics

```json
{
  "first_time_avg_ttr_sec": 412.0,
  "repeat_avg_ttr_sec": 38.0,
  "pct_resolved_by_memory": 0.5,
  "suggestion_accuracy": 0.83,
  "recurred_count": 0,
  "memory_total": 9,
  "memory_growth": [{"at": "2026-10-25T10:00:00+00:00", "total": 7}],
  "incidents_total": 2
}
```

Values are `null` when there is no data yet. Seeded incidents count toward `memory_total` but not the time-to-resolve averages.

## Status flow

`detected → retrieving → reasoning → awaiting_approval → applying → verifying → resolved | rolled_back`, or `rejected` / `expired` at approval. Every step adds a `timeline` entry; the Kepler step adds `kepler` (done or skipped).

## Python functions between owners

| Function | Owner | Called by |
| --- | --- | --- |
| `api.store.scrubber.scrub(record) -> record` | P4 | P1 before storing or embedding anything |
| `api.agent.guard.guard(record) -> record` | P1 | before storage and after retrieval |
| `api.agent.retrieval.index_incident(incident)` | P1 | P4's seed loader, P1's learn step |
| `api.agent.temporal_client.send_decision(incident_id, approved, by, edited_fix=None, channel="web", reason=None)` (async) | P1 | P4's approve/reject routes |
| `api.store.repo.create_incident / update_incident / get_incident / add_timeline_event / list_incidents` | P4 | P1's activities |
| `api.store.slack.notify_approval(incident)` | P4 | P1's workflow |
