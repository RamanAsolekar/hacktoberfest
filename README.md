# DockerGPT Memory

An agent that treats every resolved container failure as a lesson. When a new container fails, it
searches past incidents, explains why one matches and how sure it is, proposes the fix that worked
before (adapted to the new stack), applies it after a human approves, verifies it, rolls back if
it fails, and stores the outcome, including failed fixes as negative examples.

Live progress and status: **[PROGRESS.md](PROGRESS.md)**.

## How each technology is used

| Technology | Job in DockerGPT Memory | Code |
| --- | --- | --- |
| **Docker** | Failure trigger (Docker events), evidence (logs, inspect, stats), the fix itself (`docker compose up -d`), and packaging | `watcher/`, `api/agent/fixer.py` |
| **Kepler** (local SRE agent) | Investigates every failure read-only and adds its findings to the evidence; every lesson is also written to Kepler's memory wiki so Kepler knows it next time | `api/agent/kepler.py` |
| **LLM** | Judges whether a past case has the same root cause, states confidence, adapts the old fix, diagnoses fresh when memory has no trusted match, writes the memory entry | `api/agent/reasoning.py` |
| **JarvisLabs** | Hosts the models privately on a rented GPU: Qwen2.5-7B (vLLM) + bge-small embeddings; a larger model for the benchmark | `jarvis/startup.sh`, `scripts/jarvis.py` |
| **Temporal** | `ResolveAndLearnWorkflow` makes resolve-then-learn durable, waits for approval as a signal, rolls back on a failed health check, and keeps an audit trail | `api/agent/workflows.py`, `activities.py` |
| **NudgeBee** | Runs on NudgeBee's own infra (Postgres, Qdrant, Temporal from the repo's compose) and seeds memory from NudgeBee's RCA history (`event_log_analysis`) | `api/agent/nudgebee.py` |

## Run it locally (Windows, PowerShell or Git Bash)

```bash
# 1. NudgeBee infra (from the repo root). Adds host port 5433 for Postgres.
docker compose -f docker-compose.yaml -f ../hacktoberfest/infra/nudgebee-infra.override.yml up -d postgres qdrant temporal temporal-ui migrations

# 2. Python env (from D:/hacktober/hacktoberfest)
py -3.13 -m venv .venv
.venv/Scripts/python -m pip install -r api/requirements.txt jarvislabs pytest-asyncio
cp .env.example .env          # then put your JL_API_KEY in .env

# 3. Models on JarvisLabs (L4 GPU, ~US$0.44/h; pause when idle)
.venv/Scripts/python -m scripts.jarvis up      # writes LLM_* and EMBED_* into .env
.venv/Scripts/python -m scripts.jarvis wait    # until both APIs answer

# 4. Three processes, each in its own terminal
.venv/Scripts/python -m uvicorn api.main:app --port 8000
.venv/Scripts/python -m api.agent.worker
.venv/Scripts/python -m watcher.main

# 5. Seed memory, check every technology is green
curl -X POST localhost:8000/api/memory/seed
curl localhost:8000/api/agent/status
```

Temporal UI: http://localhost:8233 · API docs: http://localhost:8000/docs

## Demo

```bash
./demo/stack-a/break.sh     # Python app, DB password missing -> "fe_sendauth: no password supplied"
#   -> fresh diagnosis -> approve:  curl -X POST localhost:8000/api/incidents/<id>/approve -H "content-type: application/json" -d '{"approved_by":"raman"}'
./demo/stack-b/break.sh     # Node app, same cause, different text -> "SASL: ... client password must be a string"
#   -> matched from memory with an explanation, fix adapted to DB_PASSWORD -> approve -> verified
./demo/stack-a/reset.sh; ./demo/stack-b/reset.sh   # back to working
curl -X POST "localhost:8000/api/demo/reset"        # clear demo incidents, keep seeds
```

## Safety

- Secrets are scrubbed before anything is stored, embedded or sent to the LLM (`api/store/scrubber.py`).
- Logs and retrieved memories pass an injection guard (`api/agent/guard.py`); the LLM is told logs are data.
- Fixes run only after human approval, only on compose files under `DEMO_ROOT`, with a backup first;
  commands come from an allowlist and nothing is run through a shell.
- Below 60% confidence the agent says so and diagnoses fresh instead of reusing a memory.

## Team ownership

`contracts/`, `api/agent/`, `api/main.py`, `watcher/`, `jarvis/`, `scripts/` – Person 1 ·
`web/` – Person 2 · `docker-compose.yml`, `deploy/`, `demo/`, `eval/` – Person 3 ·
`api/store/`, `seed/` – Person 4. Files marked **STAND-IN** are working placeholders by Person 1;
their owner replaces them and keeps the function signatures in [contracts/api.md](contracts/api.md).
