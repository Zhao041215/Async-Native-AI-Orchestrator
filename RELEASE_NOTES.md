# V7.0.0 Release Notes

Complete architectural rewrite from V6 to V7.

## What changed

- **Async-first**: replaced `urllib.request` + threading with `asyncio` + `httpx`
- **Module decomposition**: 3085-line God Object split into 33 focused modules
- **Typed contracts**: all `dict[str, Any]` replaced with Pydantic v2 `extra="forbid"` models
- **Wave-parallel pipeline**: architecture sub-phases concurrent, packages within waves concurrent
- **Three-level concurrency**: global → provider → run `asyncio.Semaphore`, never bypassed
- **Circuit breaker**: three-state machine with jittered exponential backoff, error classification by exception type
- **Structured logging**: `structlog` with contextvars for correlation IDs
- **Event sourcing**: checkpoint/resume for crash recovery
- **Server-Sent Events**: real-time frontend progress updates
- **Graceful shutdown**: SIGINT/SIGTERM signal handlers drain in-flight work
- **Frontend redesign**: dark industrial command-center aesthetic

## Breaking changes

- All `/api/v6/*` endpoints removed — use `/api/v7/*`
- `V6_DATABASE_URL` env var removed — use `DATABASE_URL`
- `docker-compose.v6.yml` renamed to `docker-compose.yml`
- `Dockerfile.v6` renamed to `Dockerfile`
- `requirements-v6.txt` renamed to `requirements.txt`

## Migration

```powershell
# Old
docker compose -f docker-compose.v6.yml up -d --build

# New
docker compose up -d --build
```
