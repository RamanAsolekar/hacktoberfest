# DockerGPT Memory: build progress

Updated after every change. Newest entries at the top of the log.

**Status, 2026-10-25:** the backend is built and tested, and all files now live in `D:\hacktober\hacktoberfest`. Temporal, Qdrant, Kepler and NudgeBee are connected and working. **Blocked on one thing:** a JarvisLabs API key in `D:\hacktober\hacktoberfest\.env` (`JL_API_KEY=`) to start the GPU models; then the full demo can run end to end.

## Technology status

| Technology | Status | Evidence |
| --- | --- | --- |
| Docker | Working | Watcher reads Docker events; demo stacks A and B fail with the exact planned errors |
| Kepler | Working | `kepler exec` investigated a live stack-A crash in 14 s: "missing password configuration (high confidence)"; lessons are written to `~/.kepler/memory/` |
| Temporal | Working | Worker connected to NudgeBee's Temporal on `localhost:7233`; workflow tests pass for approve, rollback, reject and expiry |
| Qdrant | Working | `incident_memory` collection is created on first index (NudgeBee's Qdrant, `localhost:6333`) |
| NudgeBee | Working | Runs on NudgeBee's compose infra; RCA import reads `event_log_analysis` (0 reports in a fresh local install) |
| JarvisLabs | **Waiting for API key** | `scripts/jarvis.py` + `jarvis/startup.sh` ready: L4 + Qwen2.5-7B + bge-small; A100 + Qwen2.5-14B for the benchmark |
| LLM | Ready, needs JarvisLabs | Prompts for match / fresh diagnosis / memory entry, with confidence threshold 0.6 |

## Parts

| Part | Owner | State |
| --- | --- | --- |
| `contracts/` schema, API doc, mocks | P1 | Done |
| `api/agent/` llm, retrieval, reasoning, guard, fixer, kepler, nudgebee, workflow, worker, routes | P1 | Done, unit-tested |
| `watcher/` | P1 | Done, payload tested on a live crash |
| `jarvis/`, `scripts/jarvis.py` | P1 | Done, not yet run (needs key) |
| `api/store/` | P4 | **Stand-in** by P1 (working); P4 replaces |
| `seed/library.jsonl` | P4 | **Stand-in**, 7 cases; P4 expands to 18. Leaves out "missing DB password" on purpose so the demo's first failure is fresh |
| `demo/stack-a`, `demo/stack-b` | P3 | **Stand-in** by P1 (working) |
| `infra/nudgebee-infra.override.yml` | P1 | Done: Postgres also on host port 5433 |
| `web/` | P2 | Not started (P2) |
| `docker-compose.yml`, AWS, `eval/` | P3 | Not started (P3) |

## Next steps

1. Put your JarvisLabs key in `.env` as `JL_API_KEY=...`.
2. `python -m scripts.jarvis up`, then `python -m scripts.jarvis wait` (first boot downloads models, 5–15 min).
3. Seed memory (`POST /api/memory/seed`) and run the demo end to end: stack A fresh, stack B from memory.
4. Start the large model briefly for the benchmark, then pause it.
5. Pause the JarvisLabs instance whenever you're not testing (`python -m scripts.jarvis pause`).

## Notes and decisions

- **Kepler is a local SRE agent app** (v0.1.11-beta), not the CNCF power-metrics exporter the task docs assumed. It investigates and remembers. An unattended `kepler exec` cannot approve shell commands, so the evidence the watcher collected goes into the prompt and Kepler may read its own memory wiki.
- **Port 5432 is taken** by a native Windows PostgreSQL service on this PC, so NudgeBee's Postgres container is also published on **5433** (override file, no change to NudgeBee's compose).
- Python processes (api, worker, watcher) run on the host so the worker can call Kepler and `docker compose` directly; infra runs in Docker. Person 3's compose/AWS setup runs the same code in containers (Kepler then simply reports "skipped").
- Crash loops are de-duplicated: a crash of a service that already has an open incident is attached to it, and the watcher ignores containers it saw being stopped or killed (a fix's recreate is not a new failure).

## Log

- **2026-10-25 (move):** Moved the whole project from `D:\hacktober\nudgebee\dockergpt-memory` to `D:\hacktober\hacktoberfest` (not a git repo). Recreated `.venv` there; 29/29 tests pass. Updated `DEMO_ROOT` in `.env`. NudgeBee infra is still started from the nudgebee repo root, now with `-f ../hacktoberfest/infra/nudgebee-infra.override.yml`. Empty locked folders may remain at the old path until VS Code releases them; safe to delete.
- **2026-10-25:** Initial build of all Person 1 parts plus working stand-ins. 29 tests (scrubber, guard, fixer safety, reasoning fallback, retrieval decay, 4 Temporal workflow paths). Verified live: NudgeBee infra up, API and worker running, `/api/agent/status` green for Temporal, Qdrant, Kepler, NudgeBee; Kepler diagnosis of a real stack-A crash. Moved Postgres to 5433 because of the native PostgreSQL on 5432.
