# V7 System Architecture

V7 is the active kernel and control plane in this repository. It replaces V6 entirely with an async-first, module-decomposed architecture.

## Kernel

- **Async pipeline**: all I/O is non-blocking via `asyncio` + `httpx`
- **Typed domain models**: Pydantic v2 with `extra="forbid"` — no untyped dicts
- **Wave-parallel execution**: architecture sub-phases run concurrently; packages within waves run concurrently
- **Three-level concurrency control**: global → provider → run semaphores
- **Circuit breaker**: closed → open → half-open with jittered backoff
- **Event sourcing**: checkpoint/resume for crash recovery

## Modules

| Module | Purpose |
|--------|---------|
| `models.py` | Domain types, enums, utility functions |
| `store.py` | AbstractStore protocol + InMemoryStore |
| `scheduler.py` | Async AI scheduler with semaphore concurrency |
| `pipeline.py` | Pipeline orchestrator, phase dispatch |
| `phases/` | Phase handlers (requirements, architecture, planning, implementation, integration, quality, release) |
| `contracts.py` | Strict Pydantic contracts for agent output |
| `resilience.py` | Circuit breaker + error classification |
| `observability.py` | Structured logging with structlog |
| `worker.py` | Async worker + supervisor with graceful shutdown |
| `api.py` | Async FastAPI with SSE endpoints |
| `runtime.py` | File manifest application |
| `artifacts.py` | Artifact persistence |
| `kernel.py` | Event sourcing + checkpoint resume |
| `context.py` | Context building (no silent truncation) |
| `profiles.py` | Scale profiles |
| `memory.py` | Layered mission memory |

## Control Plane

- Public APIs live under `/api/v7/*`
- System check enforces version policy
- Storage lifecycle manages runtime workspaces as cache

## Rollout Policy

- The active version is the version in `VERSION`
- Retired version artifacts are removed, not kept as compatibility layers
