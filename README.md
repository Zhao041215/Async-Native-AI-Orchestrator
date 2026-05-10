# V7 Async-Native AI Orchestrator

Async-first multi-agent AI development orchestration system. Projects from 500 LOC to 100k+ LOC are built by AI agents through a wave-parallel pipeline with typed contracts, structured logging, and circuit-breaker resilience.

## Architecture

- **Async-first**: `asyncio` + `httpx` for LLM calls, `asyncio.Semaphore` for concurrency control
- **33 focused modules** under `dev_orchestrator/v7/` — no God Objects
- **Typed domain models**: Pydantic v2 `extra="forbid"` on every contract
- **Wave-parallel pipeline**: concurrent architecture sub-phases, concurrent packages within waves
- **Three-level concurrency**: global → provider → run semaphores, never bypassed
- **Circuit breaker**: closed → open → half-open with jittered exponential backoff
- **Structured logging**: `structlog` with correlation IDs (run_id, job_id)
- **Server-Sent Events**: real-time frontend progress

## Quick Start

```powershell
# Start API server (in-memory store, no Postgres needed)
python run_server.py --api

# Or with Docker
docker compose up -d --build
```

Open: http://127.0.0.1:8787

## Docker

```powershell
docker compose up -d --build
```

Services:
- `api` — V7 FastAPI control plane on port 8787
- `worker` — V7 async workers (role-based)
- `postgres` — PostgreSQL 16

## CLI

```powershell
# API server
python run_server.py --api
python run_server.py --api --host 0.0.0.0 --port 8787

# Workers
python run_server.py --v7-worker
python run_server.py --v7-worker --roles backend,frontend,qa

# System check
python run_server.py --system-check
```

## Verification

```powershell
python -m pytest tests/ -v
python run_server.py --system-check
```

## API Endpoints

All endpoints are under `/api/v7/`:

- `GET /api/v7/health` — system health
- `GET /api/v7/scale-profiles` — available scale profiles
- `GET /api/v7/provider-health` — provider circuit breaker state
- `POST /api/v7/projects` — create project
- `GET /api/v7/projects` — list projects
- `GET /api/v7/projects/{id}` — get project
- `DELETE /api/v7/projects/{id}` — delete project
- `POST /api/v7/projects/{id}/runs` — create run
- `GET /api/v7/projects/{id}/runs` — list runs
- `GET /api/v7/runs/{id}` — get run
- `POST /api/v7/runs/{id}/pause` — pause run
- `POST /api/v7/runs/{id}/resume` — resume run
- `POST /api/v7/runs/{id}/cancel` — cancel run
- `GET /api/v7/runs/{id}/jobs` — list jobs
- `GET /api/v7/runs/{id}/waves` — list waves
- `GET /api/v7/runs/{id}/packages` — list work packages
- `GET /api/v7/runs/{id}/artifacts` — list artifacts
- `GET /api/v7/runs/{id}/events` — list events
- `GET /api/v7/runs/{id}/mission` — mission state
- `POST /api/v7/runs/{id}/export-delivery` — export deployment files

## Environment

- `DATABASE_URL` — PostgreSQL connection string
- `OPENAI_API_KEY` — AI provider API key
- `OPENAI_API_BASE` — AI provider base URL
- `OPENAI_MODEL` — model name
- `AI_AGENT_RUNTIME_ROOT` — workspace root
- `AI_AGENT_WORKSPACE_MAX_BYTES` — max workspace size

## Scale Profiles

| Profile | Wave Width | Package Workers | Use Case |
|---------|-----------|----------------|----------|
| small | 1 | 1 per role | < 2k LOC |
| medium | 2 | 1 per role | 2k–10k LOC |
| large | 3 | 2 per role | 10k–50k LOC |
| xlarge_100k | 4 | 3 per role | 50k–100k+ LOC |

Use `auto` to let the system infer scale from requirements.
